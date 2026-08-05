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

"""Automatic in-venv wire-tap hook for Antigravity Agents.

Loaded automatically on Python interpreter startup when agy_watch_hook.pth is installed in site-packages.
Transparently installs in-memory WebSocket interception on websockets.connect without requiring
any modifications to agent source code.
"""

import os
import logging

logger = logging.getLogger("agy_watch.auto_hook")

_installed = False


def install_auto_hook() -> None:
    """Installs the wire-tap hook into the active Python process if not already present."""
    global _installed
    if _installed:
        return

    # Check if disabled via environment variable
    if os.environ.get("AGY_WATCH_DISABLE", "").lower() in ("1", "true", "yes"):
        return

    try:
        from agy_watch.wire_tap import install_wire_tap
        install_wire_tap()
        _installed = True
        logger.debug("agy_watch in-venv auto-hook installed successfully.")
    except Exception as e:
        logger.debug("agy_watch auto-hook initialization skipped or failed: %s", e)


# Automatically execute on import (when loaded via .pth)
install_auto_hook()
