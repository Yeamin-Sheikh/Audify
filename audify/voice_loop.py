"""
Live Voice Agent Loop for Audify:
  1. Speaks assistant responses out loud via Microsoft Edge TTS (Audify).
  2. Waits until speaker playback finishes so the microphone never records speaker echo.
  3. Automatically activates the 3-tier Speech-to-Text hierarchy:
       - Tier 1 (Highest Priority): Wispr Flow hands-free mode (Left Ctrl + Left Win + Space)
       - Tier 2 (Cloud Fallback):   Groq API (whisper-large-v3, 0 MB local model RAM)
       - Tier 3 (Last Resort):      Bark 3.0 Clean (loaded only if Groq fails, unloaded immediately after)
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import time

import pyperclip

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
    _kernel32,
    _user32,
    get_wispr_latest_error,
    is_wispr_flow_running,
    trigger_wispr_flow_handsfree,
)

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


def speak_and_trigger_hierarchy(
    text: str,
    trigger_listen_after: bool = True,
    max_wait_seconds: float = 90.0,
) -> dict[str, object]:
    """Speak *text* out loud via Audify and trigger the 3-tier STT hierarchy once playback completes."""
    audify_pids = _find_audify_pids()
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
            if silent_since is None:
                silent_since = time.monotonic()
            elif (time.monotonic() - silent_since) >= 0.55:
                # Playback finished cleanly
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

    stt_tier_used = "none"
    if trigger_listen_after:
        # Wait 180ms after speaker silence so room echo never bleeds into the microphone
        time.sleep(0.18)
        err_before_ts, _ = get_wispr_latest_error()
        if trigger_wispr_flow_handsfree():
            stt_tier_used = "tier1_wispr_flow"
            # Monitor Wispr Flow briefly for immediate launch/transcription failure
            for _ in range(5):
                time.sleep(0.35)
                if not is_wispr_flow_running():
                    trigger_audify_dictation_fallback()
                    stt_tier_used = "tier2_groq_whisper_large_v3"
                    break
                err_now_ts, err_key = get_wispr_latest_error()
                if err_now_ts > err_before_ts:
                    print(
                        f"[VoiceLoop] Wispr Flow reported {err_key}; switching to Tier 2 (Groq whisper-large-v3)..."
                    )
                    trigger_audify_dictation_fallback()
                    stt_tier_used = "tier2_groq_whisper_large_v3"
                    break
        else:
            # Wispr Flow is not running -> immediately trigger Tier 2 (Groq whisper-large-v3) -> Tier 3 (Bark 3.0)
            trigger_audify_dictation_fallback()
            stt_tier_used = "tier2_groq_whisper_large_v3"

    return {
        "started_audio": started_audio,
        "max_peak": round(max_peak, 4),
        "stt_tier_triggered": stt_tier_used,
    }
