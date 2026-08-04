"""agy_watch: Real-Time Observability Console and Wire-Tap SDK for Google Antigravity Agents.

Provides transparent WebSocket wire-tapping, content-addressable storage (CAS),
machine-wide session registry, and an interactive 3-pane TUI dashboard.
"""

from agy_watch.wire_tap import (
    BlobStore,
    WireTapDB,
    TappedWebSocket,
    install_wire_tap,
    read_trajectory,
    list_trajectories,
)
from agy_watch.registry import (
    GlobalRegistry,
    get_global_registry,
)
from agy_watch.watcher import (
    SessionWatcher,
)
from agy_watch.tui import (
    AgyWatchApp,
)
from agy_watch.settings import (
    UserSettings,
    get_user_settings,
    AVAILABLE_SYNTAX_THEMES,
)

# Friendly alias for Python agent scripts
enable_wire_tap = install_wire_tap

__version__ = "0.1.0"
__all__ = [
    "BlobStore",
    "WireTapDB",
    "TappedWebSocket",
    "install_wire_tap",
    "enable_wire_tap",
    "read_trajectory",
    "list_trajectories",
    "GlobalRegistry",
    "get_global_registry",
    "SessionWatcher",
    "AgyWatchApp",
    "UserSettings",
    "get_user_settings",
    "AVAILABLE_SYNTAX_THEMES",
]
