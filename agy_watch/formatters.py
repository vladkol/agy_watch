# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Locale-aware timestamp, date, and token formatting utilities for agy_watch."""

import locale
import os
import re
import subprocess
import sys
from datetime import datetime
from typing import Optional

_LOCALE_INITIALIZED = False
_IS_12_HOUR_LOCALE = True


def _ensure_locale() -> None:
    """Initializes user's system locale from environment or OS defaults."""
    global _LOCALE_INITIALIZED, _IS_12_HOUR_LOCALE
    if _LOCALE_INITIALIZED:
        return

    # 1. Check environment variables
    for var in ("LC_ALL", "LC_TIME", "LANG"):
        val = os.environ.get(var)
        if val and val not in ("C", "POSIX"):
            try:
                locale.setlocale(locale.LC_ALL, val)
                break
            except Exception:
                pass

    # 2. On macOS, query system preferences if env was unset or defaulted to C
    current = locale.getlocale(locale.LC_TIME)
    if (not current or not current[0] or current[0] == "C") and sys.platform == "darwin":
        try:
            res = subprocess.run(
                ["defaults", "read", "-g", "AppleLocale"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            raw = res.stdout.strip()
            if raw:
                base = raw.split("@")[0]
                for cand in (f"{base}.UTF-8", base, f"{base}.utf8"):
                    try:
                        locale.setlocale(locale.LC_ALL, cand)
                        break
                    except Exception:
                        continue
        except Exception:
            pass

    # 3. Determine if the resolved locale uses 12-hour or 24-hour time convention
    _IS_12_HOUR_LOCALE = _detect_12_hour_preference()
    _LOCALE_INITIALIZED = True


def _detect_12_hour_preference() -> bool:
    """Checks whether the resolved system locale or macOS preferences prefer 12-hour time."""
    # Check macOS explicit 24-hour setting if present
    if sys.platform == "darwin":
        try:
            res = subprocess.run(
                ["defaults", "read", "-g", "AppleICUForce24HourTime"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if res.stdout.strip() in ("1", "true", "YES"):
                return False
        except Exception:
            pass

    loc_tuple = locale.getlocale(locale.LC_TIME)
    loc_name = (loc_tuple[0] or "").lower() if loc_tuple else ""

    # Locales with standard 12-hour AM/PM convention
    twelve_hour_locales = ("en_us", "en_ca", "en_au", "en_nz", "en_ph", "en_in", "es_us", "es_mx")
    if any(loc_name.startswith(prefix) for prefix in twelve_hour_locales):
        return True

    # 24-hour European / Asian locales (de, fr, it, es_ES, ja, zh, etc.)
    twenty_four_locales = ("de_", "fr_", "it_", "es_es", "ru_", "pl_", "nl_", "sv_", "no_", "fi_")
    if any(loc_name.startswith(prefix) for prefix in twenty_four_locales):
        return False

    # Default fallback: 12-hour AM/PM clock
    return True


def get_header_time_format() -> str:
    """Returns the time format string for Textual Header widget clock."""
    _ensure_locale()
    return "%I:%M:%S %p" if _IS_12_HOUR_LOCALE else "%X"


def format_locale_time(ts: Optional[float] = None) -> str:
    """Formats timestamp into locale-appropriate time representation (e.g. 01:14:44 PM or 13:14:44)."""
    _ensure_locale()
    if ts is None or ts <= 0:
        return "--:--:--"
    try:
        dt = datetime.fromtimestamp(ts)
        fmt = "%I:%M:%S %p" if _IS_12_HOUR_LOCALE else "%X"
        return dt.strftime(fmt)
    except Exception:
        return datetime.fromtimestamp(ts or 0).strftime("%I:%M:%S %p")


def format_locale_date(ts: Optional[float] = None, two_digit_year: bool = True) -> str:
    """Formats timestamp into locale-appropriate date representation (%x) with optional 2-digit year."""
    _ensure_locale()
    if ts is None or ts <= 0:
        return "--/--/--" if two_digit_year else "----/--/--"
    try:
        dt = datetime.fromtimestamp(ts)
        raw_date = dt.strftime("%x")
        if two_digit_year:
            return re.sub(r"\b(?:19|20)(\d{2})\b", r"\1", raw_date)
        return raw_date
    except Exception:
        fmt = "%y-%m-%d" if two_digit_year else "%Y-%m-%d"
        return datetime.fromtimestamp(ts or 0).strftime(fmt)


def format_locale_datetime(ts: Optional[float] = None, two_digit_year: bool = True) -> str:
    """Formats timestamp into locale date and time (e.g. 08/05/26 01:14:44 PM or 08/05/26 13:14:44)."""
    _ensure_locale()
    if ts is None or ts <= 0:
        return "--/--/-- --:--:--" if two_digit_year else "----/--/-- --:--:--"
    try:
        dt = datetime.fromtimestamp(ts)
        raw_date = dt.strftime("%x")
        time_fmt = "%I:%M:%S %p" if _IS_12_HOUR_LOCALE else "%X"
        raw_time = dt.strftime(time_fmt)
        if two_digit_year:
            raw_date = re.sub(r"\b(?:19|20)(\d{2})\b", r"\1", raw_date)
        return f"{raw_date} {raw_time}"
    except Exception:
        time_fmt = "%I:%M:%S %p" if _IS_12_HOUR_LOCALE else "%H:%M:%S"
        date_fmt = "%y-%m-%d" if two_digit_year else "%Y-%m-%d"
        return datetime.fromtimestamp(ts or 0).strftime(f"{date_fmt} {time_fmt}")


VALID_TEXTAREA_LANGUAGES = frozenset({
    "markdown", "regex", "java", "html", "json", "go",
    "xml", "toml", "javascript", "sql", "css", "yaml",
    "rust", "python", "bash"
})


def normalize_textarea_language(lang: Optional[str]) -> Optional[str]:
    """Normalizes a language identifier to a supported Tree-Sitter language in Textual TextArea, or None for plain text."""
    if not lang:
        return None
    lang_lower = str(lang).lower().strip()
    if lang_lower in ("text", "plain", "plaintext", "none", "txt", "raw", "null", ""):
        return None
    if lang_lower in ("py", "python3", "pyw"):
        return "python"
    if lang_lower in ("sh", "zsh", "shell", "console"):
        return "bash"
    if lang_lower in ("js", "ts", "typescript", "jsx", "tsx", "mjs", "cjs"):
        return "javascript"
    if lang_lower in ("yml",):
        return "yaml"
    if lang_lower in ("md", "mdown"):
        return "markdown"
    if lang_lower in ("htm",):
        return "html"
    if lang_lower in VALID_TEXTAREA_LANGUAGES:
        return lang_lower
    return None

