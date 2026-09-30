"""
Configuration constants and load/save helpers for Audify.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sys
import tempfile
import threading

# -- Paths (relative to the script / exe location, not CWD) ---------------
if getattr(sys, "frozen", False):
    # Running as a PyInstaller --onefile bundle
    _SCRIPT_DIR: str = os.path.dirname(sys.executable)
else:
    _SCRIPT_DIR: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_FILE: str = os.path.join(_SCRIPT_DIR, "config.json")
CONFIG_BACKUP_FILE: str = CONFIG_FILE + ".bak"
HISTORY_FILE: str = os.path.join(_SCRIPT_DIR, "history.log")

# -- Large standard technical dictionary (150+ terms) ----------------------
DEFAULT_PRONUNCIATION: dict[str, str] = {
    # Tech Terms / Abbreviations
    "API": "A P I",
    "APIs": "A P I s",
    "CLI": "C L I",
    "GUI": "gooey",
    "GUIs": "gooeys",
    "UI": "U I",
    "UX": "U X",
    "URL": "U R L",
    "URLs": "U R L s",
    "SQL": "sequel",
    "NoSQL": "no sequel",
    "MySQL": "my sequel",
    "PostgreSQL": "post-gres sequel",
    "GraphQL": "graph Q L",
    "JSON": "jason",
    "YAML": "yamel",
    "TOML": "tom-ul",
    "HTML": "H T M L",
    "CSS": "C S S",
    "HTTP": "H T T P",
    "HTTPS": "H T T P S",
    "SSH": "S S H",
    "FTP": "F T P",
    "DNS": "D N S",
    "TCP": "T C P",
    "UDP": "U D P",
    "OAuth": "oh-auth",
    "REST": "rest",
    "CRUD": "crud",
    "AJAX": "ay-jax",
    "regex": "reg-ex",
    "sudo": "sue-doo",
    "nginx": "engine X",
    "async": "ay-sink",
    "await": "ay-wait",
    "npm": "N P M",
    "npx": "N P X",
    "PyPI": "pie P I",
    "pip": "pip",
    "venv": "V env",
    "kubectl": "kube control",
    "Kubernetes": "koo-ber-net-eez",
    "DevOps": "dev ops",
    "ChatGPT": "Chat G P T",
    "GPT": "G P T",
    "LLM": "L L M",
    "LLMs": "L L M s",
    "DALL-E": "dolly",
    "FastAPI": "fast A P I",
    "Django": "jango",
    "webpack": "web pack",
    "TypeScript": "type script",
    "JavaScript": "java script",
    "GitHub": "git hub",
    "GitLab": "git lab",
    "VSCode": "V S Code",
    "VS Code": "V S Code",
    "LinkedIn": "linked in",
    "macOS": "mac O S",
    "iOS": "I O S",
    "iPadOS": "I pad O S",
    "MongoDB": "mongo D B",
    "Redis": "reddis",
    "Vercel": "ver-sell",
    "Supabase": "super base",
    "LaTeX": "lay tech",
    "localhost": "local host",
    "README": "read me",
    "changelog": "change log",
    "middleware": "middle ware",
    "frontend": "front end",
    "backend": "back end",
    "fullstack": "full stack",
    "boolean": "boo-lee-an",
    "tuple": "too-pull",
    "tuples": "too-pulls",
    "innerHTML": "inner H T M L",
    "XMLHttpRequest": "X M L H T T P request",
    "AWS": "A W S",
    "GCP": "G C P",
    "AI": "A I",
    "ML": "M L",
    "NLP": "N L P",
    "GPU": "G P U",
    "CPU": "C P U",
    "RAM": "ram",
    "SSD": "S S D",
    "HDD": "H D D",
    "USB": "U S B",
    "BIOS": "buy-oss",
    "UEFI": "U E F I",
    "PDF": "P D F",
    "IDE": "I D E",
    "SDK": "S D K",
    "CDN": "C D N",
    "VPN": "V P N",
    "JWT": "J W T",
    "JWTs": "J W T s",
    "UUID": "U U I D",
    "CORS": "cors",
    "CSRF": "C S R F",
    "XSS": "X S S",
    "TLS": "T L S",
    "SSL": "S S L",
    "SMTP": "S M T P",
    "IMAP": "I M A P",
    "IoT": "I o T",
    "IEEE": "I triple E",
    "FIFO": "fife-oh",
    "LIFO": "life-oh",
    "ASAP": "A S A P",
    "EOF": "E O F",
    "OOP": "O O P",
    "MVC": "M V C",
    "MVVM": "M V V M",
    "SaaS": "sass",
    "PaaS": "pass",
    "IaaS": "I az",
    "CICD": "C I C D",
    "PR": "P R",
    "PRs": "P R s",
    "QA": "Q A",
    "UAT": "U A T",
    "ETL": "E T L",
    "CSV": "C S V",
    "XML": "X M L",
    "WASM": "waz-em",
    "ESLint": "E S lint",
    "OAuth2": "oh-auth 2",
    # Text shortcuts / punctuation
    "e.g.": "for example",
    "i.e.": "that is",
    "etc.": "etcetera",
    "vs.": "versus",
    "approx.": "approximately",
    "dept.": "department",
    "govt.": "government",
    "et al.": "et all",
    "Fig.": "figure",
    "fig.": "figure",
    "w/": "with ",
    "w/o": "without",
    # Symbols
    "=>": " arrow ",
    "->": " arrow ",
    "!==": " not equal to ",
    "===": " triple equals ",
    "!=": " not equal ",
    ">=": " greater or equal ",
    "<=": " less or equal ",
    "&&": " and ",
    "||": " or ",
}

DEFAULT_CONFIG: dict = {
    "voice": "en-US-JennyNeural",
    "rate": "+50%",
    "volume": 1.0,
    "skip_code_blocks": True,
    "stop_hotkey": "ctrl+alt+s",
    "pronunciation_dict": {},
    # Built-in rules the user deleted; never re-added on startup
    "removed_default_rules": [],
}

# -- Speech option definitions (Global Scope for Unified Access) -----------
VOICES: dict[str, str] = {
    "Jenny (Natural Female)": "en-US-JennyNeural",
    "Aria (Standard Female)": "en-US-AriaNeural",
    "Michelle (Expressive Female)": "en-US-MichelleNeural",
    "Ava (Multilingual Female)": "en-US-AvaMultilingualNeural",
    "Jane (Expressive Female)": "en-US-JaneNeural",
    "Ana (Child Female)": "en-US-AnaNeural",
    "Christopher (Natural Male)": "en-US-ChristopherNeural",
    "Guy (Standard Male)": "en-US-GuyNeural",
    "Steffan (Expressive Male)": "en-US-SteffanNeural",
    "Brian (Multilingual Male)": "en-US-BrianMultilingualNeural",
    "Andrew (Multilingual Male)": "en-US-AndrewMultilingualNeural",
    "Eric (Natural Male)": "en-US-EricNeural",
    "Roger (Natural Male)": "en-US-RogerNeural",
}

RATES: dict[str, str] = {
    "0.5x (Slow)": "-50%",
    "1.0x (Normal)": "+0%",
    "1.25x": "+25%",
    "1.5x (Fast)": "+50%",
    "1.7x (Faster)": "+70%",
    "2.0x (Very Fast)": "+100%",
    # High-speed playback rates for fast scanning and power listening.
    # Edge TTS expresses speech rate as a percentage delta relative to baseline (1.0x = +0%).
    # +150% corresponds to 2.5x playback speed (100% baseline + 150% boost = 250% = 2.5x).
    # +200% corresponds to 3.0x playback speed (100% baseline + 200% boost = 300% = 3.0x).
    "2.5x": "+150%",
    "3.0x": "+200%",
}

VOLUMES: dict[str, float] = {
    "20%": 0.2,
    "40%": 0.4,
    "60%": 0.6,
    "80%": 0.8,
    "100%": 1.0,
}


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

_save_lock = threading.Lock()


def _read_config_file(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        return loaded if isinstance(loaded, dict) else None
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"[WARN] Could not read {os.path.basename(path)}: {e}")
        return None


def load_config() -> dict:
    """Load user config from disk, falling back to the backup, then to defaults."""
    config = copy.deepcopy(DEFAULT_CONFIG)

    loaded = _read_config_file(CONFIG_FILE)
    loaded_from_main = loaded is not None
    if loaded is None and os.path.exists(CONFIG_FILE):
        # Keep the unreadable file for inspection instead of silently overwriting it
        try:
            os.replace(CONFIG_FILE, CONFIG_FILE + ".corrupt")
        except Exception:
            pass
        loaded = _read_config_file(CONFIG_BACKUP_FILE)
        if loaded is not None:
            print("[WARN] config.json was unreadable; restored settings from backup.")
    elif loaded is None:
        loaded = _read_config_file(CONFIG_BACKUP_FILE)
    if loaded:
        config.update(loaded)

    # Auto-merge built-in rules the user hasn't got yet, skipping ones they deleted
    user_dict = config.setdefault("pronunciation_dict", {})
    removed = set(config.setdefault("removed_default_rules", []))
    changed = not loaded_from_main  # rewrite config.json if it was missing or restored
    for k, v in DEFAULT_PRONUNCIATION.items():
        if k not in user_dict and k not in removed:
            user_dict[k] = v
            changed = True

    if changed:
        save_config(config)

    return config


def save_config(config: dict) -> None:
    """Persist configuration atomically, keeping the previous good file as a backup.

    Writes to a temp file and swaps it in, so a crash or power loss mid-save can
    never leave a half-written config.json behind.
    """
    try:
        data = json.dumps(config, indent=4)
    except Exception as e:  # e.g. dict mutated by another thread mid-serialization
        print(f"[WARN] Error serializing config: {e}")
        return

    with _save_lock:
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(prefix="config.", suffix=".tmp", dir=os.path.dirname(CONFIG_FILE))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            if _read_config_file(CONFIG_FILE) is not None:
                shutil.copyfile(CONFIG_FILE, CONFIG_BACKUP_FILE)
            os.replace(tmp_path, CONFIG_FILE)
            tmp_path = None
        except Exception as e:
            print(f"[WARN] Error saving config: {e}")
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass


def remove_rule(config: dict, word: str) -> None:
    """Delete a pronunciation rule; built-in rules are remembered so they stay deleted."""
    config.get("pronunciation_dict", {}).pop(word, None)
    if word in DEFAULT_PRONUNCIATION:
        removed = config.setdefault("removed_default_rules", [])
        if word not in removed:
            removed.append(word)


def set_rule(config: dict, word: str, spoken: str) -> None:
    """Add or update a pronunciation rule (un-deleting a built-in rule if needed)."""
    config.setdefault("pronunciation_dict", {})[word] = spoken
    removed = config.get("removed_default_rules", [])
    if word in removed:
        removed.remove(word)


def is_just_url(text: str) -> bool:
    """Return True if *text* is nothing but a URL (skip reading URLs aloud)."""
    text = text.strip()
    pattern = r"^(https?:\/\/)?(www\.)?([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(:\d+)?(\/[^\s]*)?$"
    return bool(re.match(pattern, text))
