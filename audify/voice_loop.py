"""
Live Voice Agent Loop for Audify:
  1. Speaks assistant responses out loud via Microsoft Edge TTS (Audify).
  2. Waits until speaker playback finishes so the microphone never records speaker echo.
  3. Automatically activates the 3-tier Speech-to-Text hierarchy:
       - Tier 1 (Highest Priority): Wispr Flow hands-free mode (Left Ctrl + Left Win + Space)
         with automatic microphone VAD stop when the user stops talking and pitch/level drops,
         plus direct SQLite transcript capture from ``flow.sqlite`` so the user never has to
         focus Antigravity, stop Wispr Flow manually, or press Enter.
       - Tier 2 (Cloud Fallback):   Groq API (``whisper-large-v3``, 0 MB local model RAM)
         using a parallel shared-mode WAV buffer so the user never has to repeat themselves.
       - Tier 3 (Last Resort):      Bark 3.0 Clean (loaded only if Groq fails, unloaded immediately after).
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import json
import os
import sys
import tempfile
import threading
import time

import pyperclip

from audify.config import apply_stt_vocabulary, load_config
from audify.engine import (
    _CLSID_MMDeviceEnumerator,
    _GUID,
    _IID_IAudioSessionControl2,
    _IID_IAudioSessionManager2,
    _IID_IMMDeviceEnumerator,
    _com_release,
    _com_vcall,
    _ole32,
)
from audify.stt import (
    KEYEVENTF_KEYUP,
    VK_LCONTROL,
    Win32MicRecorder,
    _kernel32,
    _user32,
    dismiss_wispr_flow_if_active,
    get_wispr_capture_state_and_peak,
    get_wispr_latest_error,
    get_wispr_latest_history_row,
    is_wispr_flow_running,
    resolve_groq_api_key,
    stop_wispr_flow_if_active,
    transcribe_with_groq,
    trigger_wispr_flow_handsfree,
)

# Standby Gemini API key saved for optional standalone use outside Antigravity
# (intentionally commented out and unused; full literal also in standby_keys.local.py):
# STANDBY_GEMINI_API_KEY = "AQ." + "Ab8RN6Km-1gPtRqpzXe4H1R-7yoS6hkV9kc3Zpj2z0cJyHDqGg"

_IID_IAudioMeterInformation = _GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")


def _find_audify_pids() -> set[int]:
    """Return all running ``Audify.exe`` process IDs via Win32 Toolhelp32 snapshot."""
    pids: set[int] = set()
    try:
        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wt.DWORD),
                ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", wt.LONG),
                ("dwFlags", wt.DWORD),
                ("szExeFile", wt.WCHAR * 260),
            ]

        h_snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if h_snap == wt.HANDLE(-1).value or not h_snap:
            return pids
        try:
            pe = PROCESSENTRY32W()
            pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if _kernel32.Process32FirstW(h_snap, ctypes.byref(pe)):
                while True:
                    if pe.szExeFile.lower() == "audify.exe":
                        pids.add(int(pe.th32ProcessID))
                    if not _kernel32.Process32NextW(h_snap, ctypes.byref(pe)):
                        break
        finally:
            _kernel32.CloseHandle(h_snap)
    except Exception:
        pass
    return pids


def _get_audify_speaker_peak(audify_pids: set[int]) -> tuple[bool, float]:
    """Return ``(session_active, max_peak)`` for any running Audify process on the default speaker."""
    if not audify_pids:
        return False, 0.0
    hr_init = _ole32.CoInitializeEx(None, 0)
    enum_ptr = ctypes.c_void_p()
    dev_ptr = ctypes.c_void_p()
    mgr_ptr = ctypes.c_void_p()
    senum_ptr = ctypes.c_void_p()
    try:
        if (
            _ole32.CoCreateInstance(
                ctypes.byref(_CLSID_MMDeviceEnumerator),
                None,
                7,
                ctypes.byref(_IID_IMMDeviceEnumerator),
                ctypes.byref(enum_ptr),
            )
            != 0
            or not enum_ptr.value
        ):
            return False, 0.0
        if (
            _com_vcall(
                enum_ptr,
                4,
                ctypes.c_long,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_void_p),
            )(0, 0, ctypes.byref(dev_ptr))
            != 0
            or not dev_ptr.value
        ):
            return False, 0.0
        if (
            _com_vcall(
                dev_ptr,
                3,
                ctypes.c_long,
                ctypes.POINTER(_GUID),
                wt.DWORD,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p),
            )(ctypes.byref(_IID_IAudioSessionManager2), 7, None, ctypes.byref(mgr_ptr))
            != 0
            or not mgr_ptr.value
        ):
            return False, 0.0
        if (
            _com_vcall(mgr_ptr, 5, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))(
                ctypes.byref(senum_ptr)
            )
            != 0
            or not senum_ptr.value
        ):
            return False, 0.0

        scnt = ctypes.c_int()
        if (
            _com_vcall(senum_ptr, 3, ctypes.c_long, ctypes.POINTER(ctypes.c_int))(
                ctypes.byref(scnt)
            )
            != 0
        ):
            return False, 0.0

        active = False
        max_peak = 0.0
        for i in range(scnt.value):
            s_ptr = ctypes.c_void_p()
            s2_ptr = ctypes.c_void_p()
            m_ptr = ctypes.c_void_p()
            try:
                if (
                    _com_vcall(
                        senum_ptr,
                        4,
                        ctypes.c_long,
                        ctypes.c_int,
                        ctypes.POINTER(ctypes.c_void_p),
                    )(i, ctypes.byref(s_ptr))
                    != 0
                    or not s_ptr.value
                ):
                    continue
                if (
                    _com_vcall(
                        s_ptr,
                        0,
                        ctypes.c_long,
                        ctypes.POINTER(_GUID),
                        ctypes.POINTER(ctypes.c_void_p),
                    )(ctypes.byref(_IID_IAudioSessionControl2), ctypes.byref(s2_ptr))
                    != 0
                    or not s2_ptr.value
                ):
                    continue
                pid = wt.DWORD()
                _com_vcall(s2_ptr, 14, ctypes.c_long, ctypes.POINTER(wt.DWORD))(
                    ctypes.byref(pid)
                )
                if int(pid.value) not in audify_pids:
                    continue
                st = ctypes.c_int()
                _com_vcall(s2_ptr, 3, ctypes.c_long, ctypes.POINTER(ctypes.c_int))(
                    ctypes.byref(st)
                )
                if st.value == 1:
                    active = True
                if (
                    _com_vcall(
                        s_ptr,
                        0,
                        ctypes.c_long,
                        ctypes.POINTER(_GUID),
                        ctypes.POINTER(ctypes.c_void_p),
                    )(ctypes.byref(_IID_IAudioMeterInformation), ctypes.byref(m_ptr))
                    == 0
                    and m_ptr.value
                ):
                    pk = ctypes.c_float()
                    _com_vcall(m_ptr, 3, ctypes.c_long, ctypes.POINTER(ctypes.c_float))(
                        ctypes.byref(pk)
                    )
                    if pk.value > max_peak:
                        max_peak = float(pk.value)
            finally:
                _com_release(m_ptr)
                _com_release(s2_ptr)
                _com_release(s_ptr)
        return active, max_peak
    except Exception:
        return False, 0.0
    finally:
        _com_release(senum_ptr)
        _com_release(mgr_ptr)
        _com_release(dev_ptr)
        _com_release(enum_ptr)
        if hr_init in (0, 1):
            try:
                _ole32.CoUninitialize()
            except Exception:
                pass


def trigger_audify_dictation_fallback() -> None:
    """Simulate Double-Tap Left Ctrl so Audify starts recording with Tier 2 (Groq v3) -> Tier 3 (Bark 3.0)."""
    for _ in range(2):
        _user32.keybd_event(VK_LCONTROL, 0, 0, 0)
        time.sleep(0.03)
        _user32.keybd_event(VK_LCONTROL, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.09)


def _listen_and_capture_3tier(
    silence_delay_s: float = 2.0,
    no_speech_timeout_s: float = 120.0,
    warm_rec: Win32MicRecorder | None = None,
) -> tuple[str, str]:
    """Listen via Tier 1 (Wispr Flow + auto-stop on mic silence) with zero-repeat failover to Tier 2 (Groq v3) -> Tier 3 (Bark 3.0).

    Returns ``(transcript_text, tier_used)``.
    """
    lock_file = os.path.join(tempfile.gettempdir(), "audify_voiceloop.lock")
    try:
        with open(lock_file, "w", encoding="utf-8") as lf:
            lf.write(str(os.getpid()))
    except Exception:
        pass
    vl_mutex = ctypes.windll.kernel32.CreateMutexW(
        None, False, "Global\\AudifyVoiceLoopActive"
    )
    try:
        ts_before, _, _ = get_wispr_latest_history_row()
        err_before_ts, _ = get_wispr_latest_error()

        # Use pre-warmed Win32MicRecorder (or start a new one) so hardware AGC/NS is already converged
        rec = warm_rec if warm_rec is not None else Win32MicRecorder()
        stop_event = threading.Event()
        stop_reason_box: list[str] = ["timeout"]

        def _on_vad_stop(reason: str) -> None:
            stop_reason_box[0] = reason
            stop_event.set()

        if not rec.is_recording:
            rec.start(
                on_auto_stop=_on_vad_stop,
                auto_stop_on_silence=False,
                silence_delay_s=silence_delay_s,
                no_speech_timeout_s=no_speech_timeout_s,
            )

        wispr_started = trigger_wispr_flow_handsfree()
        # Ensure at least 2.6s since mic hardware open (so Intel Smart Sound / Realtek AGC/NS has converged)
        # and at least 0.65s after Wispr Flow start-chime before arming VAD!
        warm_elapsed = time.monotonic() - getattr(rec, "_started_at", 0.0)
        wait_needed = max(0.65, 2.65 - warm_elapsed)
        time.sleep(wait_needed)
        rec.arm_vad(
            on_auto_stop=_on_vad_stop,
            silence_delay_s=silence_delay_s,
            no_speech_timeout_s=no_speech_timeout_s,
        )
        early_wispr_text = ""

        # Wait while the user speaks (or until VAD auto-stops on silence / Escape is pressed)
        t_wait = time.monotonic()
        while not stop_event.wait(timeout=0.08):
            if bool(_user32.GetAsyncKeyState(0x1B) & 0x8000):  # VK_ESCAPE
                stop_reason_box[0] = "cancelled"
                rec.cancel()
                dismiss_wispr_flow_if_active()
                return "", "cancelled"

            # Only accept an early row from flow.sqlite if the user actually spoke (rec.speech_detected)
            if wispr_started and rec.speech_detected and (time.monotonic() - t_wait) > 0.8:
                ts_cur, txt_cur, status_cur = get_wispr_latest_history_row()
                if ts_cur and ts_cur > ts_before and txt_cur and status_cur == "formatted":
                    early_wispr_text = txt_cur
                    rec.cancel()
                    break

            if (time.monotonic() - t_wait) > (no_speech_timeout_s + 20.0):
                break

        if early_wispr_text:
            return early_wispr_text, "tier1_wispr_flow"

        # Save captured audio from Win32MicRecorder before stopping Wispr Flow
        fd, wav_path = tempfile.mkstemp(prefix="audify_voiceloop_", suffix=".wav")
        os.close(fd)
        try:
            duration_s = rec.stop_and_save(wav_path)
            has_speech = (
                stop_reason_box[0] != "no_speech"
                and rec.speech_detected
                and getattr(rec, "speech_chunks", 0) >= 3
                and duration_s >= 0.40
            )
            if not has_speech:
                if wispr_started:
                    dismiss_wispr_flow_if_active()
                return "", "no_speech"

            # Stop Wispr Flow immediately now that the user stopped speaking!
            if wispr_started:
                stop_wispr_flow_if_active()

            # Poll Wispr Flow's flow.sqlite for up to 4.5s to grab the formatted transcript directly
            if wispr_started:
                t_poll = time.monotonic()
                while (time.monotonic() - t_poll) < 4.5:
                    err_now_ts, err_key = get_wispr_latest_error()
                    if err_now_ts > err_before_ts:
                        print(
                            f"[VoiceLoop] Wispr Flow reported {err_key}; switching to Tier 2 (Groq whisper-large-v3)..."
                        )
                        break
                    ts_now, txt_now, status_now = get_wispr_latest_history_row()
                    if ts_now and ts_now > ts_before and txt_now:
                        if status_now == "formatted" or (time.monotonic() - t_poll) >= 2.2:
                            return txt_now, "tier1_wispr_flow"
                    time.sleep(0.08)

            # Tier 2: Groq whisper-large-v3 (0 MB local model RAM, zero-repeat using captured WAV)
            cfg = load_config()
            vocab = dict(cfg.get("stt_vocabulary", {}))
            groq_key = resolve_groq_api_key() or str(cfg.get("groq_api_key", "")).strip()
            groq_model = str(cfg.get("groq_model", "whisper-large-v3")).strip() or "whisper-large-v3"
            if groq_key:
                ok_groq, groq_txt = transcribe_with_groq(
                    wav_path, groq_key, model=groq_model, vocab=vocab
                )
                if ok_groq and groq_txt.strip():
                    cleaned = apply_stt_vocabulary(groq_txt.strip(), vocab)
                    return cleaned, "tier2_groq_whisper_large_v3"

            # Tier 3 (Last Resort): Bark 3.0 Clean on-demand (unloads immediately in finally)
            from audify.stt import BarkCleanController

            ctrl = BarkCleanController()
            ok_bark, bark_txt = ctrl.transcribe_file(wav_path)
            if ok_bark and bark_txt.strip():
                cleaned = apply_stt_vocabulary(bark_txt.strip(), vocab)
                return cleaned, "tier3_bark_3_0_clean"

            return "", "failed"
        finally:
            try:
                if os.path.exists(wav_path):
                    os.remove(wav_path)
            except Exception:
                pass
    finally:
        if vl_mutex:
            ctypes.windll.kernel32.CloseHandle(vl_mutex)
        try:
            if os.path.exists(lock_file):
                os.remove(lock_file)
        except Exception:
            pass


def speak_and_trigger_hierarchy(
    text: str,
    trigger_listen_after: bool = True,
    max_wait_seconds: float = 90.0,
) -> dict[str, object]:
    """Speak *text* out loud via Audify and capture the user's spoken reply via the 3-tier STT hierarchy."""
    result_file = os.environ.get("AUDIFY_VOICELOOP_RESULT_FILE", "")
    if os.environ.get("AUDIFY_ON_DEFAULT_DESKTOP") != "1":
        # Re-invoke on WinSta0\Default if called from an isolated background desktop session
        class STARTUPINFOW(ctypes.Structure):
            _fields_ = [
                ("cb", wt.DWORD),
                ("lpReserved", wt.LPWSTR),
                ("lpDesktop", wt.LPWSTR),
                ("lpTitle", wt.LPWSTR),
                ("dwX", wt.DWORD),
                ("dwY", wt.DWORD),
                ("dwXSize", wt.DWORD),
                ("dwYSize", wt.DWORD),
                ("dwXCountChars", wt.DWORD),
                ("dwYCountChars", wt.DWORD),
                ("dwFillAttribute", wt.DWORD),
                ("dwFlags", wt.DWORD),
                ("wShowWindow", wt.WORD),
                ("cbReserved2", wt.WORD),
                ("lpReserved2", ctypes.POINTER(wt.BYTE)),
                ("hStdInput", wt.HANDLE),
                ("hStdOutput", wt.HANDLE),
                ("hStdError", wt.HANDLE),
            ]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("hProcess", wt.HANDLE),
                ("hThread", wt.HANDLE),
                ("dwProcessId", wt.DWORD),
                ("dwThreadId", wt.DWORD),
            ]

        fd, res_path = tempfile.mkstemp(prefix="audify_vl_res_", suffix=".json")
        os.close(fd)
        os.environ["AUDIFY_ON_DEFAULT_DESKTOP"] = "1"
        os.environ["AUDIFY_VOICELOOP_RESULT_FILE"] = res_path
        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(si)
        si.lpDesktop = "WinSta0\\Default"
        pi = PROCESS_INFORMATION()
        cmd = f'"{sys.executable}" -m audify --speak-and-listen "{text.replace(chr(34), chr(39))}"'
        try:
            if _kernel32.CreateProcessW(
                None,
                ctypes.create_unicode_buffer(cmd),
                None,
                None,
                False,
                0x08000000,
                None,
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                ctypes.byref(si),
                ctypes.byref(pi),
            ):
                _kernel32.WaitForSingleObject(
                    pi.hProcess, int((max_wait_seconds + 150) * 1000)
                )
                _kernel32.CloseHandle(pi.hThread)
                _kernel32.CloseHandle(pi.hProcess)
                if os.path.exists(res_path):
                    try:
                        with open(res_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if isinstance(data, dict):
                            return data
                    except Exception:
                        pass
                return {"delegated_to_default_desktop": True}
        finally:
            try:
                if os.path.exists(res_path):
                    os.remove(res_path)
            except Exception:
                pass

    audify_pids = _find_audify_pids()
    warm_rec: Win32MicRecorder | None = None
    if trigger_listen_after:
        try:
            # Open microphone hardware now while Audify speaks so the 2.4s Intel Smart Sound / Realtek
            # AGC & Noise-Suppression warmup finishes before Wispr Flow even starts!
            warm_rec = Win32MicRecorder()
            warm_rec.start(auto_stop_on_silence=False)
        except Exception:
            warm_rec = None

    old_clip = ""
    try:
        old_clip = pyperclip.paste()
    except Exception:
        pass

    # Trigger Audify TTS via clipboard event while preserving previous clipboard after 1.85s
    _user32.keybd_event(0x11, 0, 0, 0)
    _user32.keybd_event(0x43, 0, 0, 0)
    time.sleep(0.03)
    _user32.keybd_event(0x43, 0, KEYEVENTF_KEYUP, 0)
    _user32.keybd_event(0x11, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.03)
    pyperclip.copy(text)

    t0 = time.monotonic()
    restored_clip = False
    started_audio = False
    max_peak = 0.0
    silent_since: float | None = None

    while (time.monotonic() - t0) < max_wait_seconds:
        elapsed = time.monotonic() - t0
        if not restored_clip and elapsed >= 1.85:
            restored_clip = True
            try:
                pyperclip.copy(old_clip)
            except Exception:
                pass

        _, pk = _get_audify_speaker_peak(audify_pids)
        if pk > max_peak:
            max_peak = pk

        if pk > 0.002:
            started_audio = True
            silent_since = None
        elif started_audio:
            min_expected_s = max(1.8, len(text.split()) * 0.24)
            if silent_since is None:
                silent_since = time.monotonic()
            elif (time.monotonic() - silent_since) >= 1.05 and elapsed >= min_expected_s:
                # Playback finished cleanly across all sentences
                break
        elif elapsed >= 4.5 and not started_audio:
            # Edge TTS either finished very short audio or was stopped
            break

        time.sleep(0.08)

    if not restored_clip:
        try:
            pyperclip.copy(old_clip)
        except Exception:
            pass

    transcript = ""
    stt_tier_used = "none"
    if trigger_listen_after:
        # Wait 250ms after speaker silence so room echo never bleeds into the microphone
        time.sleep(0.25)
        transcript, stt_tier_used = _listen_and_capture_3tier(
            silence_delay_s=2.0,
            no_speech_timeout_s=120.0,
            warm_rec=warm_rec,
        )
    elif warm_rec is not None:
        warm_rec.cancel()

    out: dict[str, object] = {
        "started_audio": started_audio,
        "max_peak": round(max_peak, 4),
        "stt_tier_triggered": stt_tier_used,
        "transcript": transcript,
    }
    if result_file:
        try:
            with open(result_file, "w", encoding="utf-8") as f:
                json.dump(out, f)
        except Exception:
            pass
    return out
