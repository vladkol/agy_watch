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

"""Persistent user settings and configuration manager for agy_watch."""

import os
import json
import logging
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, List

logger = logging.getLogger("agy_watch.settings")

def get_default_settings_path() -> str:
    """Returns the default settings file path, honoring AGY_WATCH_SETTINGS_PATH if set."""
    env_path = os.environ.get("AGY_WATCH_SETTINGS_PATH") or os.environ.get("AGY_WATCH_SETTINGS_FILE")
    if env_path:
        return os.path.abspath(os.path.expanduser(env_path))
    home = os.path.expanduser("~")
    return os.path.join(home, ".antigravity", "samples", "agy_watch", "settings.json")


DEFAULT_SETTINGS_PATH = get_default_settings_path()

# Paired themes supported by both Textual App engine and Rich Syntax highlighter
SUPPORTED_THEMES: List[Dict[str, str]] = [
    {"name": "dracula", "app_theme": "dracula", "syntax_theme": "dracula"},
    {"name": "nord", "app_theme": "nord", "syntax_theme": "nord"},
    {"name": "monokai", "app_theme": "monokai", "syntax_theme": "monokai"},
    {"name": "tokyo-night", "app_theme": "tokyo-night", "syntax_theme": "nord"},
    {"name": "gruvbox", "app_theme": "gruvbox", "syntax_theme": "monokai"},
    {"name": "catppuccin-mocha", "app_theme": "catppuccin-mocha", "syntax_theme": "one-dark"},
    {"name": "solarized-dark", "app_theme": "solarized-dark", "syntax_theme": "solarized-dark"},
    {"name": "textual-dark", "app_theme": "textual-dark", "syntax_theme": "dracula"},
]

AVAILABLE_SYNTAX_THEMES: List[str] = [t["syntax_theme"] for t in SUPPORTED_THEMES]


@dataclass
class UserSettings:
    """Persistent user preferences and state for agy_watch TUI and CLI."""

    theme: str = "dracula"
    syntax_theme: str = "dracula"
    last_session_id: Optional[str] = None
    view_mode: str = "tree"  # "tree" or "flat"
    auto_follow: bool = True
    active_tab: str = "tab-details"  # "tab-details" or "tab-artifacts"
    wrap_text: bool = True
    read_only: bool = False
    _path: Optional[str] = None

    @classmethod
    def load(cls, path: Optional[str] = None) -> "UserSettings":
        """Loads settings from disk, falling back to defaults if not found or invalid."""
        target_path = os.path.abspath(os.path.expanduser(path or get_default_settings_path()))
        if not os.path.exists(target_path):
            inst = cls()
            inst._path = target_path
            return inst
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    valid_keys = {f.name for f in cls.__dataclass_fields__.values() if not f.name.startswith("_")}
                    filtered = {k: v for k, v in data.items() if k in valid_keys}
                    inst = cls(**filtered)
                    inst._path = target_path
                    return inst
        except Exception as e:
            logger.warning("Failed to load settings from %s: %s", target_path, e)
        inst = cls()
        inst._path = target_path
        return inst

    def save(self, path: Optional[str] = None) -> bool:
        """Saves current settings to disk atomically, unless in read_only mode."""
        if self.read_only:
            return True
        target_path = os.path.abspath(os.path.expanduser(path or self._path or get_default_settings_path()))
        try:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            temp_path = f"{target_path}.tmp"
            data = asdict(self)
            data.pop("_path", None)
            data.pop("read_only", None)
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(temp_path, target_path)
            self._path = target_path
            return True
        except Exception as e:
            logger.warning("Failed to save settings to %s: %s", target_path, e)
            return False


_global_settings: Optional[UserSettings] = None


def get_user_settings(path: Optional[str] = None) -> UserSettings:
    """Returns a singleton instance of UserSettings."""
    global _global_settings
    if _global_settings is None:
        _global_settings = UserSettings.load(path)
    return _global_settings
