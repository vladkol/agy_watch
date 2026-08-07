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

"""Interactive Terminal User Interface (TUI) for Antigravity Agent Observability.

Built with Textual and Rich, featuring a 3-pane split view (Sessions, Hierarchical Execution Tree, Inspector),
recursive sub-agent timelines, correlated invoke_subagent tool arguments, step-level stream deduplication,
interactive master-detail file preview with syntax highlighting, and full-screen in-terminal reader.
"""

import os
import sys
import json
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from rich.syntax import Syntax
from rich.text import Text
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll, Container
from textual.widgets import Header, Footer, Static, ListView, ListItem, Tree, TabbedContent, TabPane, Label, TextArea
from textual.widgets.tree import TreeNode
from textual.screen import ModalScreen
from textual.binding import Binding
from textual import events

from agy_watch.registry import get_global_registry, GlobalRegistry
from agy_watch.watcher import SessionWatcher
from agy_watch.settings import get_user_settings, UserSettings, SUPPORTED_THEMES, AVAILABLE_SYNTAX_THEMES
from agy_watch.tool_renderers import build_tool_tree_label, render_tool_event
from agy_watch.formatters import format_locale_time
from agy_watch.clipboard import copy_to_system_clipboard


def open_media_file_cross_platform(file_path: str) -> bool:
    """Opens a file in the system default application cross-platform (macOS, Linux, Windows)."""
    if not file_path or not os.path.exists(file_path):
        return False
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", file_path])
        elif sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", file_path])
        elif sys.platform.startswith("win32"):
            os.startfile(file_path)
        return True
    except Exception:
        return False


def detect_content_language(content: Optional[str]) -> str:
    """Infers best syntax highlighter from text content heuristics."""
    if not content:
        return "markdown"
    s = content.strip()
    if not s:
        return "markdown"

    # 1. JSON detection
    if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        try:
            import json
            json.loads(s)
            return "json"
        except Exception:
            pass

    lines = s.splitlines()[:25]

    # 2. Shell script detection
    if s.startswith("#!") or any(l.lstrip().startswith(("export ", "echo ", "source ", "set -e", "chmod ")) for l in lines[:5]):
        return "bash"

    # 3. Python detection
    if any(l.lstrip().startswith(("def ", "class ", "import ", "from ", "async def ", "if __name__ ==", "@dataclass", "@pytest")) for l in lines[:10]):
        return "python"

    # 4. XML / HTML detection
    if s.startswith("<") and s.endswith(">") and ("</" in s or "/>" in s):
        return "html"

    # 5. YAML detection
    if s.startswith("---") or any(l.startswith("apiVersion:") or l.startswith("kind:") for l in lines[:5]):
        return "yaml"

    # 6. SQL detection
    if any(l.lstrip().upper().startswith(("SELECT ", "INSERT INTO ", "UPDATE ", "CREATE TABLE ", "DELETE FROM ", "WITH ")) for l in lines[:5]):
        return "sql"

    # Default for text and notes: markdown provides rich syntax coloring for headers, lists, code, URLs, bold
    return "markdown"


def get_syntax_lexer_for_path(file_path: str, content: Optional[str] = None) -> str:
    """Infers Textual/Rich syntax language identifier from filename extension and content."""
    if not file_path:
        return detect_content_language(content) if content else "markdown"

    base = os.path.basename(file_path).lower()
    if base in ("dockerfile", "containerfile", "makefile", "gnumakefile"):
        return "bash"
    if base.startswith(".env"):
        return "bash"

    _, ext = os.path.splitext(file_path.lower())
    mapping = {
        ".py": "python",
        ".pyi": "python",
        ".pyw": "python",
        ".json": "json",
        ".jsonl": "json",
        ".js": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".mts": "typescript",
        ".cts": "typescript",
        ".tsx": "tsx",
        ".jsx": "jsx",
        ".html": "html",
        ".htm": "html",
        ".xhtml": "html",
        ".css": "css",
        ".scss": "css",
        ".sass": "css",
        ".less": "css",
        ".sh": "bash",
        ".zsh": "bash",
        ".bash": "bash",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".md": "markdown",
        ".markdown": "markdown",
        ".rst": "markdown",
        ".sql": "sql",
        ".go": "go",
        ".rs": "rust",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".cxx": "cpp",
        ".hpp": "cpp",
        ".h": "c",
        ".c": "c",
        ".java": "java",
        ".xml": "xml",
        ".svg": "xml",
        ".regex": "regex",
    }
    if ext in mapping:
        return mapping[ext]

    if content:
        return detect_content_language(content)

    return "markdown"


def _get_safe_text_area_theme(theme_name: Optional[str]) -> str:
    """Maps arbitrary application/Rich syntax themes to a valid Textual TextArea builtin theme."""
    if not theme_name:
        return "dracula"
    t = theme_name.lower()
    mapping = {
        "dracula": "dracula",
        "monokai": "monokai",
        "nord": "dracula",
        "tokyo-night": "dracula",
        "catppuccin-mocha": "vscode_dark",
        "one-dark": "vscode_dark",
        "gruvbox": "monokai",
        "solarized-dark": "vscode_dark",
        "github_light": "github_light",
        "light": "github_light",
        "textual-dark": "dracula",
        "vscode_dark": "vscode_dark",
    }
    return mapping.get(t, "dracula")


_guess_syntax_language = get_syntax_lexer_for_path


def _calculate_wrapped_height(text: str, pane_width: int = 65, min_h: int = 6, max_h: int = 35) -> int:
    """Estimates visual line height accounting for soft text wrapping across pane columns."""
    if not text:
        return min_h
    import math
    lines = str(text).splitlines() or [""]
    effective_col_width = max(20, pane_width - 8)
    total_visual_lines = sum(max(1, math.ceil(len(line) / effective_col_width)) for line in lines)
    return min(max(total_visual_lines + 2, min_h), max_h)


class SelectableTextArea(TextArea):
    """Subclass of TextArea with expanded standard copy keybindings and vi/page navigation."""

    BINDINGS = TextArea.BINDINGS + [
        Binding("c", "copy_selected", "Copy", show=False),
        Binding("ctrl+c", "copy_selected", "Copy", show=False),
        Binding("ctrl+a", "select_all", "Select All", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("d", "cursor_page_down", "Page Down", show=False),
        Binding("pagedown", "cursor_page_down", "Page Down", show=False),
        Binding("space", "cursor_page_down", "Page Down", show=False),
        Binding("u", "cursor_page_up", "Page Up", show=False),
        Binding("pageup", "cursor_page_up", "Page Up", show=False),
        Binding("g", "scroll_home", "Top", show=False),
        Binding("home", "scroll_home", "Top", show=False),
        Binding("G", "scroll_end", "Bottom", show=False),
        Binding("end", "scroll_end", "Bottom", show=False),
    ]

    def action_copy_selected(self) -> None:
        """Copies highlighted text if selected, or falls back to whole text/smart copy."""
        text_to_copy = self.selected_text or self.text
        if text_to_copy:
            if hasattr(self.app, "copy_to_clipboard"):
                self.app.copy_to_clipboard(text_to_copy)
            else:
                self.action_copy()
        elif hasattr(self.app, "action_copy_smart"):
            self.app.action_copy_smart()

    async def _on_key(self, event: events.Key) -> None:
        if self.read_only:
            key = event.key
            if key in ("a", "ctrl+a"):
                event.prevent_default()
                event.stop()
                self.select_all()
                return
            elif key in ("c", "ctrl+c"):
                event.prevent_default()
                event.stop()
                self.action_copy_selected()
                return
            elif key in ("j", "down"):
                event.prevent_default()
                event.stop()
                self.action_cursor_down()
                return
            elif key in ("k", "up"):
                event.prevent_default()
                event.stop()
                self.action_cursor_up()
                return
            elif key in ("d", "space", "pagedown"):
                event.prevent_default()
                event.stop()
                self.action_cursor_page_down()
                return
            elif key in ("u", "pageup"):
                event.prevent_default()
                event.stop()
                self.action_cursor_page_up()
                return
            elif key in ("g", "home"):
                event.prevent_default()
                event.stop()
                self.action_scroll_home()
                return
            elif key in ("G", "end"):
                event.prevent_default()
                event.stop()
                self.action_scroll_end()
                return
        await super()._on_key(event)


READER_LANGUAGES: List[str] = ["markdown", "python", "json", "yaml", "bash", "sql", "html", "css", "xml", "go", "rust", "text"]


class FullscreenReaderModal(ModalScreen):
    """Full-screen modal for reading files and prompt traces with selectable TextArea and syntax highlighting."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close", show=True, priority=True),
        Binding("q", "dismiss", "Close", show=True, priority=True),
        Binding("w", "toggle_wrap", "Toggle Wrap", show=True, priority=True),
        Binding("l", "cycle_language", "Syntax", show=True, priority=True),
        Binding("a", "select_all_modal", "Select All", show=True, priority=True),
        Binding("ctrl+a", "select_all_modal", "Select All", show=False, priority=True),
        Binding("c", "copy_modal_content", "Copy", show=True, priority=True),
        Binding("ctrl+c", "copy_modal_content", "Copy", show=False, priority=True),
    ]

    def __init__(
        self,
        title: str,
        content: Optional[str] = None,
        file_path: Optional[str] = None,
        is_markdown: bool = False,
        syntax_theme: str = "dracula",
        wrap_mode: bool = False,
    ):
        super().__init__()
        self.reader_title = title
        self.raw_content = content
        self.file_path = file_path
        self.is_markdown = is_markdown
        self.syntax_theme = _get_safe_text_area_theme(syntax_theme)
        self.wrap_mode = wrap_mode
        self.text_content = ""
        self.lang = "markdown"

        if self.file_path and os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8", errors="replace") as f:
                    self.text_content = f.read()
                self.lang = get_syntax_lexer_for_path(self.file_path, self.text_content)
            except Exception as e:
                self.text_content = f"Error reading file: {e}"
        elif self.raw_content is not None:
            self.text_content = self.raw_content
            self.lang = "markdown" if self.is_markdown else detect_content_language(self.text_content)

    def _build_header_text(self) -> str:
        lang_badge = f"[{self.lang.upper()}]"
        return f" [bold white]═══ {self.reader_title} {lang_badge} ═══[/bold white] (Press ESC/Q to close, L to change syntax, W to wrap, click & drag to select, A / Ctrl+A to select all, C / Ctrl+C to copy)"

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            yield Static(self._build_header_text(), id="modal-header")
            yield SelectableTextArea(
                self.text_content,
                language=self.lang,
                theme=self.syntax_theme,
                soft_wrap=self.wrap_mode,
                id="modal-text-area",
                read_only=True,
                show_line_numbers=True,
            )

    def on_mount(self) -> None:
        ta = self.query_one("#modal-text-area", TextArea)
        ta.cursor_blink = False
        try:
            ta.focus()
        except Exception:
            pass

    def action_toggle_wrap(self) -> None:
        self.wrap_mode = not self.wrap_mode
        ta = self.query_one("#modal-text-area", TextArea)
        ta.soft_wrap = self.wrap_mode
        self.notify(f"Soft Wrap: {'Enabled' if self.wrap_mode else 'Disabled'}")

    def action_cycle_language(self) -> None:
        """Cycles through available syntax highlighting modes in fullscreen viewer."""
        current = self.lang
        idx = READER_LANGUAGES.index(current) if current in READER_LANGUAGES else 0
        self.lang = READER_LANGUAGES[(idx + 1) % len(READER_LANGUAGES)]
        ta = self.query_one("#modal-text-area", TextArea)
        try:
            ta.language = self.lang
        except Exception:
            pass
        self.query_one("#modal-header", Static).update(self._build_header_text())
        self.notify(f"Syntax Highlighting: {self.lang.upper()}")

    def action_select_all_modal(self) -> None:
        """Selects all content in modal text area."""
        ta = self.query_one("#modal-text-area", TextArea)
        ta.select_all()

    def action_copy_modal_content(self) -> None:
        """Copies reader content directly to OS clipboard."""
        ta = self.query_one("#modal-text-area", TextArea)
        text_to_copy = ta.selected_text or ta.text
        if text_to_copy:
            try:
                self.app.copy_to_clipboard(text_to_copy)
                self.notify("✓ Copied selection to clipboard.")
            except Exception as e:
                self.notify(f"Copy error: {e}", severity="error")


class AgyWatchApp(App):
    """The main Textual Application for Antigravity Agent Observability."""

    TITLE = "Antigravity Watch (agy_watch)"
    SUB_TITLE = "Antigravity SDK Observability Console"

    CSS = """
    Screen {
        background: $background;
        color: $text;
    }

    #main-layout {
        height: 1fr;
        layout: horizontal;
    }

    #sessions-pane {
        width: 28%;
        min-width: 32;
        max-width: 44;
        border-right: solid $border;
        background: $surface;
        padding: 0 1;
    }

    #center-pane {
        width: 38%;
        min-width: 40;
        border-right: solid $border;
        background: $background;
        padding: 0 1;
        overflow-x: hidden;
    }

    #inspector-pane {
        width: 34%;
        min-width: 40;
        padding: 0 1;
        background: $surface;
        overflow-x: hidden;
    }

    .pane-title {
        background: $panel;
        color: $primary;
        text-align: center;
        padding: 0 1;
        text-style: bold;
        height: 1;
        margin-bottom: 1;
    }

    #sessions-list {
        height: 1fr;
        background: $surface;
    }

    #sessions-list > ListItem {
        padding: 0 1;
        margin-bottom: 1;
        background: $panel;
        border-left: solid $border;
        border-bottom: solid $border-blurred;
    }

    #sessions-list > ListItem:hover {
        background: $boost;
        border-left: solid $primary;
    }

    #sessions-list > ListItem.--highlight {
        background: $primary-darken-2;
        border-left: solid $accent;
        border-bottom: solid $accent;
    }

    #tree-container {
        height: 1fr;
    }

    #inspector-tabs {
        height: 1fr;
    }

    #tab-details {
        height: 1fr;
        padding: 0;
    }

    #inspector-scroll {
        height: 1fr;
        overflow-y: auto;
        scrollbar-gutter: stable;
        padding: 0 1;
    }

    #artifacts-master-detail {
        height: 1fr;
    }

    #artifacts-list-container {
        height: 40%;
        border-bottom: solid $border;
    }

    #artifacts-list {
        height: 1fr;
    }

    #artifacts-preview-container {
        height: 60%;
        padding-top: 1;
    }

    #artifacts-preview-header {
        background: $panel;
        color: $secondary;
        padding: 0 1;
        text-style: bold;
        height: 1;
    }

    #artifacts-preview-scroll {
        height: 1fr;
        background: $background;
        padding: 0 1;
    }

    .artifact-item {
        padding: 1 1;
        border-bottom: solid $border-blurred;
    }

    .artifact-item:hover {
        background: $boost;
    }

    #modal-container {
        width: 92%;
        height: 92%;
        background: $surface;
        border: thick $accent;
        padding: 1 1;
    }

    #modal-header {
        background: $panel;
        padding: 0 1;
        margin-bottom: 1;
        height: auto;
    }

    #modal-text-area {
        height: 1fr;
        border: none;
        background: $background;
    }

    .section-title {
        color: $primary;
        text-style: bold;
        margin-top: 1;
    }

    .selectable-area {
        height: auto;
        min-height: 4;
        max-height: 16;
        border: solid $accent;
        background: $panel;
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("space", "toggle_follow", "Follow/Pause", show=True),
        Binding("f", "fullscreen_inspect", "Fullscreen", show=True),
        Binding("p", "cycle_syntax_theme", "Theme", show=True),
        Binding("s", "cycle_syntax_theme", "Theme", show=False),
        Binding("o", "open_selected_media_external", "Open External", show=True),
        Binding("a", "toggle_inspector_tab", "Toggle Tab", show=True),
        Binding("t", "toggle_tree_mode", "Tree/Flat", show=True),
        Binding("c", "copy_smart", "Copy", show=True),
        Binding("ctrl+c", "copy_smart", "Copy", show=False),
        Binding("r", "force_refresh_sessions", "Refresh", show=True),
        Binding("0", "filter_all_agents", "All Agents", show=False),
        Binding("1", "filter_subagent_1", "Subagent 1", show=False),
        Binding("2", "filter_subagent_2", "Subagent 2", show=False),
        Binding("3", "filter_subagent_3", "Subagent 3", show=False),
    ]

    def __init__(
        self,
        initial_session_id: Optional[str] = None,
        registry_db: Optional[str] = None,
        settings: Optional[UserSettings] = None,
    ):
        super().__init__()
        self.registry: GlobalRegistry = get_global_registry()
        self.settings: UserSettings = settings or get_user_settings()
        self.initial_session_id = initial_session_id
        self.current_watcher: Optional[SessionWatcher] = None
        self.selected_event: Optional[Dict[str, Any]] = None
        self.selected_artifact_path: Optional[str] = None
        self.is_following = self.settings.auto_follow
        self.tree_mode = (self.settings.view_mode == "tree")
        self.subagent_filter: Optional[str] = None
        self.known_sessions: List[Dict[str, Any]] = []
        self._last_sessions_sig: Optional[Any] = None

        # Step deduplication and hierarchy tracking
        self.step_nodes: Dict[Tuple[str, Any, str], TreeNode] = {}
        self.subagent_branches: Dict[str, TreeNode] = {}
        self.latest_invoke_node: Optional[TreeNode] = None

        # Artifacts tracking
        self.session_artifacts: List[Dict[str, Any]] = []
        self.seen_artifact_paths: Set[str] = set()

    def compose(self) -> ComposeResult:
        from agy_watch.formatters import get_header_time_format
        yield Header(show_clock=True, time_format=get_header_time_format())
        with Horizontal(id="main-layout"):
            # 1. Left: Sessions list
            with Vertical(id="sessions-pane"):
                yield Static(" SESSIONS (Machine-Wide) ", classes="pane-title")
                yield ListView(id="sessions-list")

            # 2. Center: Hierarchical execution step tree
            with Vertical(id="center-pane"):
                yield Static(" EXECUTION TREE (Hierarchical) ", classes="pane-title", id="timeline-title")
                with VerticalScroll(id="tree-container"):
                    yield Tree("Root Agent Execution", id="steps-tree")

            # 3. Right: Tabbed Inspector pane (Details vs Master-Detail Interactive Artifacts)
            with Vertical(id="inspector-pane"):
                yield Static(" EVENT & ARTIFACT INSPECTOR ", classes="pane-title")
                with TabbedContent(id="inspector-tabs"):
                    with TabPane("Event Details", id="tab-details"):
                        with VerticalScroll(id="inspector-scroll"):
                            yield Static("Select an event from the timeline to view details.", id="inspector-meta")
                            yield Static("", id="inspector-prompt-title", classes="section-title")
                            yield SelectableTextArea("", id="inspector-prompt-area", classes="selectable-area", read_only=True, show_line_numbers=False)
                            yield Static("", id="inspector-tool-card")
                            yield Static("", id="inspector-response-title", classes="section-title")
                            yield SelectableTextArea("", id="inspector-response-area", classes="selectable-area", read_only=True, show_line_numbers=False)
                            yield Static("", id="inspector-thinking-title", classes="section-title")
                            yield SelectableTextArea("", id="inspector-thinking-area", classes="selectable-area", read_only=True, show_line_numbers=False)
                            yield Static("", id="inspector-json-title", classes="section-title")
                            yield SelectableTextArea("", id="inspector-json-area", classes="selectable-area", read_only=True, language="json")
                            yield Static("", id="inspector-artifacts-area")
                            yield Static("", id="inspector-tokens-area")
                    with TabPane("Artifacts & Files", id="tab-artifacts"):
                        with Vertical(id="artifacts-master-detail"):
                            with Vertical(id="artifacts-list-container"):
                                yield ListView(id="artifacts-list")
                            with Vertical(id="artifacts-preview-container"):
                                yield Static(" [dim]No file selected for preview[/dim]", id="artifacts-preview-header")
                                with VerticalScroll(id="artifacts-preview-scroll"):
                                    yield Static("Select a file above to preview with syntax highlighting.\nPress 'f' or Enter for fullscreen.", id="artifacts-preview-content")

        yield Footer()

    async def on_mount(self) -> None:
        """Initializes data loading, user settings restoration, and background polling timers."""
        try:
            tree = self.query_one("#steps-tree", Tree)
            tree.guide_depth = 2
        except Exception:
            pass
        if hasattr(self, "theme") and self.settings.theme:
            try:
                self.theme = self.settings.theme
            except Exception:
                pass

        if self.settings.active_tab in ("tab-details", "tab-artifacts"):
            try:
                self.query_one("#inspector-tabs", TabbedContent).active = self.settings.active_tab
            except Exception:
                pass

        self.refresh_sessions_list(force=True)
        self.set_interval(0.1, self.poll_live_updates)
        self.set_interval(1.5, self.refresh_sessions_list)

    @property
    def is_modal_active(self) -> bool:
        """Returns True if a modal screen is currently displayed over the main app."""
        return len(self.screen_stack) > 1 or isinstance(self.screen, ModalScreen)

    def refresh_sessions_list(self, force: bool = False) -> None:
        """Reloads the session list from global registry only if data changed."""
        if self.is_modal_active and not force:
            return

        sessions = self.registry.list_sessions()
        new_sig = tuple((s["session_id"], s["updated_at"], s["status"], s["total_tokens"], s["step_count"]) for s in sessions)

        if not force and new_sig == self._last_sessions_sig:
            return

        self._last_sessions_sig = new_sig
        self.known_sessions = sessions

        list_view = self.query_one("#sessions-list", ListView)
        current_idx = list_view.index

        list_view.clear()

        from agy_watch.formatters import format_locale_datetime, format_locale_time

        for s in sessions:
            is_live = s.get("is_live", False)
            status = s.get("status", "")
            if is_live:
                status_emoji = "🟢"
            elif status == "STATE_ERROR":
                status_emoji = "🔴"
            else:
                status_emoji = "⚪"

            time_str = format_locale_datetime(s.get("updated_at") or 0, two_digit_year=True)
            sub_count = s.get("subagent_count", 0)
            sub_label = f" • {sub_count} worker{'s' if sub_count > 1 else ''}" if sub_count > 0 else ""
            tokens_k = f"{s.get('total_tokens', 0) / 1000:.1f}k tok"
            title_snippet = (s.get("title") or "Session")[:30]

            item_text = (
                f"{status_emoji} [bold white]{s['session_id'][:8]}[/bold white]  [dim]{time_str}[/dim]\n"
                f" [bright_cyan]{title_snippet}[/bright_cyan]\n"
                f" [bold yellow]{tokens_k}[/bold yellow][dim]{sub_label}[/dim]"
            )
            item = ListItem(Static(item_text), name=s["session_id"])
            list_view.append(item)

        # Select initial or last saved session
        if not self.current_watcher and sessions:
            target_id = self.initial_session_id or self.settings.last_session_id
            if not any(s["session_id"] == target_id for s in sessions):
                target_id = sessions[0]["session_id"]
            self.attach_to_session(target_id)

            for idx, s in enumerate(sessions):
                if s["session_id"] == target_id:
                    list_view.index = idx
                    break
        elif current_idx is not None and current_idx < len(sessions):
            list_view.index = current_idx

    def attach_to_session(self, session_id: str) -> None:
        """Attaches observer to a specific session, saves to settings, and resets tree state."""
        session = self.registry.get_session(session_id)
        if not session or not session.get("db_path"):
            return

        self.settings.last_session_id = session_id
        self.settings.save()

        self.current_watcher = SessionWatcher(session["db_path"])
        self.step_nodes.clear()
        self.subagent_branches.clear()
        self.latest_invoke_node = None
        self.session_artifacts.clear()
        self.seen_artifact_paths.clear()
        self.selected_artifact_path = None

        self.query_one("#artifacts-list", ListView).clear()
        self.query_one("#artifacts-preview-header", Static).update(" [dim]No file selected for preview[/dim]")
        self.query_one("#artifacts-preview-content", Static).update("Select a file above to preview.\nPress 'f' or Enter for fullscreen.")

        tree = self.query_one("#steps-tree", Tree)
        tree.clear()
        tree.root.set_label(f"Root Agent ({session_id[:8]}) - {session['title'][:32]}")
        mode_str = "Tree" if self.tree_mode else "Flat"
        self.query_one("#timeline-title", Static).update(f" EXECUTION ({mode_str}): {session_id[:8]} ({session['status']}) ")

        # Switch right pane to Event Details tab
        try:
            tabs = self.query_one("#inspector-tabs", TabbedContent)
            if tabs.active != "tab-details":
                tabs.active = "tab-details"
                self.settings.active_tab = "tab-details"
                self.settings.save()
        except Exception:
            pass

        self.poll_live_updates()

    def poll_live_updates(self) -> None:
        """Incremental polling loop for real-time updates with in-place step deduplication."""
        if not self.current_watcher:
            return

        session_info, new_events = self.current_watcher.poll()
        if not new_events and not session_info:
            return

        try:
            tree = self.query_one("#steps-tree", Tree)
        except Exception:
            return

        for ev in new_events:
            # Update session-level artifacts
            for art in ev.get("artifacts", []):
                p = art["path"]
                if p not in self.seen_artifact_paths:
                    self.seen_artifact_paths.add(p)
                    self.session_artifacts.append(art)
                    self._add_artifact_to_list(art)

            # Check subagent filter
            if self.subagent_filter and ev.get("subagent_id") != self.subagent_filter and ev.get("trajectory_id") != self.subagent_filter:
                continue

            # Ignore empty intermediate deltas without text/thinking/tool
            step_type = ev.get("step_type")
            if step_type == "UNKNOWN" and ev.get("message_type") in ("STEP_UPDATE", "TRAJECTORY_STATE_UPDATE"):
                if not ev.get("text") and not ev.get("thinking") and not ev.get("tool_name"):
                    continue

            label = self._build_event_tree_label(ev)
            is_main = ev.get("is_main", True)
            traj_id = str(ev.get("trajectory_id") or "main")
            step_idx = ev.get("step_index")
            sub_id = ev.get("subagent_id") or ev.get("trajectory_id")
            step_key = (traj_id, step_idx, step_type)
            children = ev.get("child_events") or []

            def _populate_child_nodes(parent_node, child_list):
                if not parent_node.children and child_list:
                    parent_node.allow_expand = True
                    for child in child_list:
                        c_icon = "📤" if child.get("direction") == "TO_HARNESS" else "📥"
                        c_id = f"#{child.get('id')}" if child.get("id") is not None else ""
                        c_dir = child.get("direction", "WIRE")
                        c_type = child.get("message_type", "EVENT")
                        c_label = f"↳ {c_icon} {c_id} {c_dir}: {c_type}".strip()
                        parent_node.add_leaf(c_label, data=child)

            # Check if this step already has an active node in the tree (deduplicate streaming deltas)
            if step_idx is not None and step_key in self.step_nodes:
                existing_node = self.step_nodes[step_key]
                existing_node.set_label(label)
                existing_node.data = ev
                if children:
                    _populate_child_nodes(existing_node, children)
            else:
                if not self.tree_mode:
                    if children:
                        node = tree.root.add(label, data=ev, expand=False)
                        _populate_child_nodes(node, children)
                    else:
                        node = tree.root.add_leaf(label, data=ev)
                    if step_idx is not None:
                        self.step_nodes[step_key] = node
                else:
                    if is_main:
                        if step_type == "TOOL_CALL" and ev.get("tool_name") == "invoke_subagent":
                            sub_count = len(ev.get("tool_args", {}).get("Subagents", []))
                            count_label = f" ({sub_count} workers)" if sub_count > 0 else ""
                            invoke_node = tree.root.add(f"▼ [bold yellow]TOOL: invoke_subagent[/bold yellow]{count_label}", data=ev, expand=True)
                            self.latest_invoke_node = invoke_node
                            if step_idx is not None:
                                self.step_nodes[step_key] = invoke_node
                        else:
                            if children:
                                node = tree.root.add(label, data=ev, expand=False)
                                _populate_child_nodes(node, children)
                            else:
                                node = tree.root.add_leaf(label, data=ev)
                            if step_idx is not None:
                                self.step_nodes[step_key] = node
                    else:
                        if sub_id not in self.subagent_branches:
                            parent_container = self.latest_invoke_node if self.latest_invoke_node else tree.root
                            sub_branch = parent_container.add(f"🤖 [bold cyan]Subagent ({str(sub_id)[:8]})[/bold cyan] [Active]", expand=True)
                            self.subagent_branches[sub_id] = sub_branch

                        sub_branch = self.subagent_branches[sub_id]
                        if children:
                            node = sub_branch.add(label, data=ev, expand=False)
                            _populate_child_nodes(node, children)
                        else:
                            node = sub_branch.add_leaf(label, data=ev)
                        if step_idx is not None:
                            self.step_nodes[step_key] = node

                        state = ev.get("state") or ev.get("payload", {}).get("stepUpdate", {}).get("state")
                        if state in ("STATE_DONE", "STATE_ERROR"):
                            sub_branch.set_label(f"🤖 [bold cyan]Subagent ({str(sub_id)[:8]})[/bold cyan] [{state[6:]}]")

            # If user is following, automatically inspect latest step (unless a modal is active)
            is_modal_active = len(self.screen_stack) > 1 or isinstance(self.screen, ModalScreen)
            if self.is_following and not is_modal_active:
                self.selected_event = ev
                self._render_inspector_event(ev)

        tree.root.expand()

    def _add_artifact_to_list(self, art: Dict[str, Any]) -> None:
        """Adds an artifact item to the interactive Artifacts ListView."""
        artifacts_list = self.query_one("#artifacts-list", ListView)
        icon = "🖼️ " if art["type"] == "image" else ("🎬 " if art["type"] == "video" else ("📄 " if art["type"] == "markdown" else "💻 "))
        size_kb = f"{art['size_bytes'] / 1024:.1f} KB" if art["size_bytes"] > 0 else "0 KB"
        status_tag = "[green][Found][/green]" if art["exists"] else "[red][Missing][/red]"

        item_text = (
            f"{icon}[bold white]{art['filename']}[/bold white] ({art['type']}) {status_tag} - [yellow]{size_kb}[/yellow]\n"
            f" [bright_black]{art['path']}[/bright_black]"
        )
        item = ListItem(Static(item_text), name=art["path"])
        artifacts_list.append(item)

        if not self.selected_artifact_path:
            self._render_artifact_preview(art["path"])

    def _render_artifact_preview(self, file_path: str) -> None:
        """Renders the master-detail syntax-highlighted preview of the selected file."""
        self.selected_artifact_path = file_path
        header = self.query_one("#artifacts-preview-header", Static)
        body = self.query_one("#artifacts-preview-content", Static)

        if not file_path or not os.path.exists(file_path):
            header.update(f" [red]File Not Found: {os.path.basename(file_path)}[/red]")
            body.update(Text("File could not be found on disk.", style="red"))
            return

        filename = os.path.basename(file_path)
        size_bytes = os.path.getsize(file_path)
        size_kb = f"{size_bytes / 1024:.1f} KB"
        ext = os.path.splitext(file_path)[1].lower()

        # 1. Image / Video Media Preview
        if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".mp4", ".mov", ".webm"):
            kind_str = "Image" if ext not in (".mp4", ".mov", ".webm") else "Video"
            header.update(f" 🖼️ {kind_str}: [bold white]{filename}[/bold white] ({size_kb}) [Press 'o' for External Viewer]")
            body.update(Panel(
                f"[bold cyan]Media File:[/bold cyan] {filename}\n"
                f"[bold cyan]Type:[/bold cyan]       {kind_str}\n"
                f"[bold cyan]Size:[/bold cyan]       {size_kb} ({size_bytes:,} bytes)\n"
                f"[bold cyan]Path:[/bold cyan]       {file_path}\n\n"
                f"[yellow]▶ Press 'o' to open in your system viewer (macOS Preview / QuickLook)[/yellow]",
                title="Media Asset",
                border_style="green",
            ))
            return

        # 2. Markdown Preview
        if ext in (".md", ".markdown"):
            header.update(f" 📄 Markdown: [bold white]{filename}[/bold white] ({size_kb}) [Press 'f' for Fullscreen]")
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                body.update(Markdown(content))
            except Exception as e:
                body.update(Text(f"Error reading markdown: {e}", style="red"))
            return

        # 3. Code / Text Preview with Syntax Highlighting
        lexer = get_syntax_lexer_for_path(file_path)
        header.update(f" 💻 Code: [bold white]{filename}[/bold white] ({lexer}, {size_kb}) [Press 'f' for Fullscreen]")
        try:
            syntax = Syntax.from_path(
                file_path,
                lexer=lexer,
                line_numbers=True,
                theme=self.settings.syntax_theme,
                word_wrap=self.settings.wrap_text,
            )
            body.update(syntax)
        except Exception as e:
            body.update(Text(f"Error rendering code syntax: {e}", style="red"))

    def _build_event_tree_label(self, ev: Dict[str, Any]) -> Text:
        """Formats an event for the execution tree view."""
        from agy_watch.formatters import format_locale_time
        ts_str = format_locale_time(ev.get("timestamp"))
        direction = ev.get("direction")
        msg_type = ev.get("message_type")
        step_type = ev.get("step_type")
        is_main = ev.get("is_main", True)

        sub_tag = ev.get("subagent_id") or ev.get("trajectory_id") or "sub"
        actor_tag = "[ROOT]" if is_main else f"[SUB {str(sub_tag)[:6]}]"
        actor_style = "bold magenta" if is_main else "bold cyan"

        t = Text()
        t.append(f"[{ts_str}] ", style="bright_black")
        if not self.tree_mode:
            t.append(f"{actor_tag} ", style=actor_style)

        if step_type == "USER_INPUT":
            sc = ev.get("slash_command")
            if sc:
                t.append(f"⚡ {sc} PROMPT: ", style="bold cyan")
            else:
                t.append("PROMPT: ", style="bold green")
            t.append(f"{(ev.get('prompt') or '')[:35]}...", style="green")
        elif step_type == "TRIGGER_NOTIFICATION":
            t.append("⏰ TRIGGER: ", style="bold green")
            trig_s = str(ev.get("trigger_content") or ev.get("text") or "")
            t.append(f"{trig_s[:35]}...", style="bright_green")
        elif step_type in ("CANCELLATION", "CANCELLATION_REQUEST"):
            t.append("🛑 CANCELLED", style="bold red")
        elif step_type == "COMPACTION":
            t.append("🧹 COMPACTED", style="bold magenta")
        elif step_type == "USER_ANSWER":
            t.append("💬 USER_ANSWER: ", style="bold bright_green")
            ans_str = ev.get('text') or ev.get('prompt') or ""
            t.append(f'"{ans_str[:40]}"', style="bright_green")
        elif step_type in ("POLICY_DECISION", "PRE_TURN_DECISION"):
            decision = ev.get("decision", "ALLOW")
            tool_name = ev.get("tool_name") or ("Turn" if step_type == "PRE_TURN_DECISION" else "tool")
            reason = ev.get("reason") or ""
            if decision == "DENY":
                t.append("🔒 POLICY_DENIAL: ", style="bold yellow")
                t.append(f"{tool_name}", style="bold red")
                if reason:
                    t.append(f' ("{reason[:35]}")', style="italic yellow")
            else:
                t.append(f"✅ APPROVED: {tool_name}", style="green")
        elif step_type == "ON_SESSION_START_HOOK":
            t.append("🚀 SESSION_START", style="bold blue")
        elif step_type == "ON_SESSION_END_HOOK":
            t.append("🛑 SESSION_END", style="bold red")
        elif step_type in ("SESSION_END_REQUEST", "SESSION_END_RESPONSE"):
            t.append("🛑 SESSION_END", style="bold red")
        elif step_type in ("CLIENT_CONFIG", "CONFIG_HANDSHAKE"):
            t.append("⚙️ CLIENT_CONFIG", style="dim cyan")
        elif step_type == "PRE_TOOL_HOOK":
            tool_name = ev.get("tool_name") or "tool"
            t.append(f"⏳ PRE_TOOL: {tool_name} (evaluating...)", style="italic bright_black")
        elif step_type == "PRE_TURN_HOOK":
            t.append("⏳ PRE_TURN: (evaluating...)", style="italic bright_black")
        elif step_type == "POST_TURN_HOOK":
            t.append("ℹ️ POST_TURN", style="italic cyan")
        elif step_type == "POST_TOOL_HOOK":
            tool_name = ev.get("tool_name") or "tool"
            t.append(f"ℹ️ POST_TOOL: {tool_name}", style="italic cyan")
        elif step_type in ("ON_TOOL_ERROR_HOOK", "ON_TOOL_ERROR_RESULT"):
            tool_name = ev.get("tool_name") or "tool"
            t.append(f"🔄 TRANSFORM: {tool_name}", style="bold magenta")
        elif step_type == "ON_COMPACTION_HOOK":
            t.append("ℹ️ ON_COMPACTION", style="italic magenta")
        elif step_type == "TOOL_ERROR":
            tool_name = ev.get("tool_name") or "tool"
            err_msg = ev.get("error_message") or ev.get("text") or ""
            t.append("❌ TOOL_ERROR: ", style="bold red")
            t.append(f"{tool_name}", style="bold yellow")
            if err_msg:
                first_line = str(err_msg).strip().splitlines()[0]
                t.append(f' ("{first_line[:35]}")', style="italic bright_red")
        elif step_type == "SUBAGENT_PROMPT":
            t.append("SUBAGENT_PROMPT: ", style="bold green")
            t.append(f"{(ev.get('prompt') or '')[:35]}...", style="green")
        elif step_type == "TOOL_CALL":
            tool_label = build_tool_tree_label(ev)
            t.append_text(tool_label)
        elif step_type == "SUBAGENT_REPORT":
            t.append("SUBAGENT_REPORT", style="bold blue")
        elif step_type == "TEXT_RESPONSE":
            t.append("RESPONSE", style="bold white")
        elif step_type == "MODEL_REASONING":
            t.append("THINKING...", style="italic bright_black")
        else:
            t.append(f"{msg_type}", style="bright_black")

        return t

    def _render_inspector_event(self, ev: Dict[str, Any]) -> None:
        """Renders full scrollable inspection details in the Details tab with selectable controls."""
        meta = self.query_one("#inspector-meta", Static)
        p_title = self.query_one("#inspector-prompt-title", Static)
        p_area = self.query_one("#inspector-prompt-area", TextArea)
        t_card = self.query_one("#inspector-tool-card", Static)
        resp_title = self.query_one("#inspector-response-title", Static)
        resp_area = self.query_one("#inspector-response-area", TextArea)
        th_title = self.query_one("#inspector-thinking-title", Static)
        th_area = self.query_one("#inspector-thinking-area", TextArea)
        json_title = self.query_one("#inspector-json-title", Static)
        json_area = self.query_one("#inspector-json-area", TextArea)
        art_area = self.query_one("#inspector-artifacts-area", Static)
        tok_area = self.query_one("#inspector-tokens-area", Static)

        if not ev:
            meta.update("No event selected.")
            p_title.display = False
            p_area.display = False
            t_card.display = False
            resp_title.display = False
            resp_area.display = False
            th_title.display = False
            th_area.display = False
            json_title.display = False
            json_area.display = False
            art_area.display = False
            tok_area.display = False
            return

        # Meta overview table
        t = Table.grid(padding=(0, 2))
        t.add_column(style="bold cyan", width=14)
        t.add_column()
        t.add_row("Session ID:", str(ev.get("session_id") or "main"))
        t.add_row("Trajectory ID:", str(ev.get("trajectory_id") or "root"))

        children = ev.get("child_events") or []
        if len(children) > 1:
            seq_summary = " ➔ ".join([f"#{c.get('id')} ({c.get('direction')})" for c in children if c.get('id') is not None])
            t.add_row("Sequence #:", seq_summary or str(ev.get("id")))
            t.add_row("Direction:", "TWO_WAY (Merged Transaction)")
        else:
            t.add_row("Sequence #:", str(ev.get("id") or ev.get("seq_num") or "N/A"))
            t.add_row("Direction:", str(ev.get("direction", "N/A")))

        t.add_row("Timestamp:", format_locale_time(ev.get("timestamp")))
        t.add_row("Step Index:", str(ev.get("step_index", "N/A")))
        msg_type_str = ev.get("step_type") or ev.get("message_type") or "EVENT"
        if ev.get("message_type") and ev.get("message_type") != ev.get("step_type"):
            msg_type_str = f"{ev.get('step_type')} ({ev.get('message_type')})"
        t.add_row("Message Type:", msg_type_str)
        meta.update(t)

        tool_name = ev.get("tool_name")
        args = ev.get("tool_args") or {}

        # 1. Prompt (User prompt, subagent instruction prompt, or image prompt)
        prompt_text = ev.get("prompt")
        if not prompt_text and (tool_name == "generate_image" or "prompt" in args or "Prompt" in args):
            prompt_text = args.get("prompt") or args.get("Prompt")

        if prompt_text:
            header_title = "SUBAGENT INSTRUCTION PROMPT" if ev.get("step_type") == "SUBAGENT_PROMPT" else ("GENERATED IMAGE PROMPT" if tool_name == "generate_image" else "USER PROMPT")
            p_title.display = True
            p_title.update(f"─── {header_title} (Selectable) ───")
            p_area.display = True
            if p_area.text != str(prompt_text):
                p_area.text = str(prompt_text)
            try:
                p_area.theme = self.settings.syntax_theme
            except Exception:
                pass
            p_area.styles.height = _calculate_wrapped_height(prompt_text, min_h=6, max_h=20)
        else:
            p_title.display = False
            p_area.display = False

        # 2. Tool / Policy / Exception Visualizer Card
        is_tool_or_policy = (
            bool(tool_name)
            or ev.get("step_type") in (
                "TOOL_CALL", "TOOL_ERROR", "POLICY_DECISION", "PRE_TOOL_HOOK",
                "PRE_TURN_HOOK", "PRE_TURN_DECISION", "POST_TURN_HOOK", "POST_TOOL_HOOK",
                "ON_TOOL_ERROR_HOOK", "ON_TOOL_ERROR_RESULT", "ON_COMPACTION_HOOK",
                "TRIGGER_NOTIFICATION", "CANCELLATION", "CANCELLATION_REQUEST", "COMPACTION",
            )
            or str(ev.get("message_type", "")).startswith("CALL_HOOK_")
            or ev.get("message_type") in ("POLICY_DECISION", "TRIGGER_NOTIFICATION", "HALT_REQUEST")
        )
        if is_tool_or_policy:
            tool_card_renderable = render_tool_event(ev, syntax_theme=self.settings.syntax_theme)
            t_card.display = True
            t_card.update(tool_card_renderable)
        else:
            t_card.display = False

        # 3. Model Text Response (only for actual model output, not tool errors/policies)
        is_model_text = bool(ev.get("text")) and not is_tool_or_policy and ev.get("step_type") not in ("TOOL_RESPONSE", "TOOL_ERROR")
        if is_model_text:
            resp_title.display = True
            resp_title.update("─── MODEL RESPONSE (Selectable) ───")
            resp_area.display = True
            if resp_area.text != str(ev["text"]):
                resp_area.text = str(ev["text"])
            try:
                resp_area.theme = self.settings.syntax_theme
            except Exception:
                pass
            resp_area.styles.height = _calculate_wrapped_height(ev["text"], min_h=8, max_h=30)
        else:
            resp_title.display = False
            resp_area.display = False

        # 4. Model Thinking / Reasoning
        if ev.get("thinking"):
            th_title.display = True
            th_title.update("─── MODEL REASONING (Selectable) ───")
            th_area.display = True
            if th_area.text != str(ev["thinking"]):
                th_area.text = str(ev["thinking"])
            try:
                th_area.theme = self.settings.syntax_theme
            except Exception:
                pass
            th_area.styles.height = _calculate_wrapped_height(ev["thinking"], min_h=6, max_h=20)
        else:
            th_title.display = False
            th_area.display = False

        # 5. Universal Selectable JSON representation for EVERY event
        json_payload = ev.get("payload") or ev.get("tool_args") or ev
        try:
            formatted_json = json.dumps(json_payload, indent=2)
        except Exception:
            formatted_json = str(json_payload)

        json_title.display = True
        json_title.update("─── EVENT PAYLOAD & DATA (Selectable JSON) ───")
        json_area.display = True
        if json_area.text != formatted_json:
            json_area.text = formatted_json
        try:
            json_area.language = "json"
            json_area.theme = self.settings.syntax_theme
        except Exception:
            pass
        json_area.styles.height = _calculate_wrapped_height(formatted_json, min_h=8, max_h=35)

        # 6. Artifacts List
        artifacts = ev.get("artifacts") or []
        if artifacts:
            art_t = Text()
            art_t.append("─── STEP ARTIFACTS & MEDIA (Press 'f' to view, 'o' for external) ───\n", style="bold green")
            for art in artifacts:
                status_icon = "🖼️ " if art["type"] == "image" else ("🎬 " if art["type"] == "video" else ("📄 " if art["type"] == "markdown" else "💻 "))
                size_kb = f"{art['size_bytes'] / 1024:.1f} KB" if art["size_bytes"] > 0 else "0 KB"
                exists_str = "[Found on disk]" if art["exists"] else "[Missing]"
                exists_style = "green" if art["exists"] else "red"
                art_t.append_text(Text.from_markup(f"{status_icon}[bold white]{art['filename']}[/bold white] ({art['type']}) - [{exists_style}]{exists_str}[/{exists_style}] - {size_kb}\n"))
                art_t.append(f"  Location: {art['path']}\n", style="bright_black")
            art_area.display = True
            art_area.update(art_t)
        else:
            art_area.display = False

        # 7. Turn Tokens
        if ev.get("tokens"):
            tok_t = Text()
            tok_t.append("─── TURN TOKEN USAGE ───\n", style="bold cyan")
            tok_t.append(f"{json.dumps(ev['tokens'], indent=2)}\n", style="cyan")
            tok_area.display = True
            tok_area.update(tok_t)
        else:
            tok_area.display = False

        # Event details rendering complete

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handles list view selections from Sessions or Artifacts lists."""
        if event.list_view.id == "sessions-list":
            if event.item and event.item.name:
                self.attach_to_session(event.item.name)
        elif event.list_view.id == "artifacts-list":
            if event.item and event.item.name:
                file_path = event.item.name
                self._render_artifact_preview(file_path)

                ext = os.path.splitext(file_path)[1].lower()
                if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".mp4", ".mov", ".webm"):
                    if open_media_file_cross_platform(file_path):
                        self.notify(f"Opened in external viewer: {os.path.basename(file_path)}")
                else:
                    self.push_screen(FullscreenReaderModal(
                        title=f"File: {os.path.basename(file_path)}",
                        file_path=file_path,
                        is_markdown=(ext in (".md", ".markdown")),
                        syntax_theme=self.settings.syntax_theme,
                        wrap_mode=self.settings.wrap_text,
                    ))

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Handles tree node selection to inspect step payload and switch to Event Details tab."""
        if event.node.data:
            self.selected_event = event.node.data
            self.is_following = False
            if not self.is_modal_active:
                self._render_inspector_event(event.node.data)
                try:
                    tabs = self.query_one("#inspector-tabs", TabbedContent)
                    if tabs.active != "tab-details":
                        tabs.active = "tab-details"
                        self.settings.active_tab = "tab-details"
                        self.settings.save()
                except Exception:
                    pass

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        """Updates inspector and switches to Event Details tab as cursor navigates tree nodes."""
        if event.node.data:
            self.selected_event = event.node.data
            if not self.is_modal_active:
                self._render_inspector_event(event.node.data)
                try:
                    tabs = self.query_one("#inspector-tabs", TabbedContent)
                    if tabs.active != "tab-details":
                        tabs.active = "tab-details"
                        self.settings.active_tab = "tab-details"
                        self.settings.save()
                except Exception:
                    pass

    def action_toggle_follow(self) -> None:
        if self.is_modal_active:
            return
        self.is_following = not self.is_following
        self.settings.auto_follow = self.is_following
        self.settings.save()
        self.notify(f"Live Follow: {'Enabled' if self.is_following else 'Paused'}")

    def action_toggle_tree_mode(self) -> None:
        """Toggles between Hierarchical Recursive Tree Mode and Flat Timeline Mode."""
        if self.is_modal_active:
            return
        self.tree_mode = not self.tree_mode
        self.settings.view_mode = "tree" if self.tree_mode else "flat"
        self.settings.save()
        mode_str = "Hierarchical Tree" if self.tree_mode else "Flat Stream"
        self.notify(f"Timeline View: {mode_str}")
        if self.current_watcher:
            self.current_watcher.last_event_id = 0
            self.step_nodes.clear()
            self.subagent_branches.clear()
            self.latest_invoke_node = None
            tree = self.query_one("#steps-tree", Tree)
            tree.clear()
            self.query_one("#timeline-title", Static).update(f" EXECUTION ({mode_str}): {self.current_watcher.session_info['session_id'][:8]} ")
            self.poll_live_updates()

    def action_toggle_inspector_tab(self) -> None:
        """Toggles active inspector tab between Event Details and Artifacts & Files."""
        if self.is_modal_active:
            return
        try:
            tabs = self.query_one("#inspector-tabs", TabbedContent)
            if tabs.active == "tab-details":
                tabs.active = "tab-artifacts"
                self.settings.active_tab = "tab-artifacts"
                self.notify("Switched to Artifacts & Files tab.")
            else:
                tabs.active = "tab-details"
                self.settings.active_tab = "tab-details"
                self.notify("Switched to Event Details tab.")
            self.settings.save()
        except Exception:
            pass

    def action_cycle_syntax_theme(self) -> None:
        """Cycles through available app themes + syntax themes and saves to user settings."""
        if self.is_modal_active:
            return
        current_theme = getattr(self, "theme", None) or self.settings.theme
        matching_idx = 0
        for i, t in enumerate(SUPPORTED_THEMES):
            if t["app_theme"] == current_theme or t["name"] == current_theme:
                matching_idx = i
                break

        next_idx = (matching_idx + 1) % len(SUPPORTED_THEMES)
        theme_entry = SUPPORTED_THEMES[next_idx]

        # Apply to Textual App
        try:
            self.theme = theme_entry["app_theme"]
        except Exception:
            pass

        # Save to settings
        self.settings.theme = theme_entry["app_theme"]
        self.settings.syntax_theme = theme_entry["syntax_theme"]
        self.settings.save()

        self.notify(f"Theme: {theme_entry['name']} (Syntax: {theme_entry['syntax_theme']})")

        # Refresh preview and inspector with new syntax theme
        if self.selected_artifact_path:
            self._render_artifact_preview(self.selected_artifact_path)
        if self.selected_event:
            self._render_inspector_event(self.selected_event)

    def action_open_selected_media_external(self) -> None:
        """Opens the selected artifact or media file in the OS external viewer."""
        if self.is_modal_active:
            return
        tabs = self.query_one("#inspector-tabs", TabbedContent)

        if tabs.active == "tab-artifacts" and self.selected_artifact_path:
            if open_media_file_cross_platform(self.selected_artifact_path):
                self.notify(f"Opened: {os.path.basename(self.selected_artifact_path)}")
                return
            else:
                self.notify(f"Could not open: {self.selected_artifact_path}", severity="error")
                return

        if self.selected_event:
            artifacts = self.selected_event.get("artifacts") or []
            if artifacts:
                p = artifacts[0]["path"]
                if open_media_file_cross_platform(p):
                    self.notify(f"Opened: {os.path.basename(p)}")
                    return

        self.notify("Select a file from the Artifacts tab to open externally.", severity="information")

    def action_fullscreen_inspect(self) -> None:
        """Opens full-screen modal reader with syntax highlighting."""
        if self.is_modal_active:
            return
        tabs = self.query_one("#inspector-tabs", TabbedContent)

        if tabs.active == "tab-artifacts" and self.selected_artifact_path:
            ext = os.path.splitext(self.selected_artifact_path)[1].lower()
            self.push_screen(FullscreenReaderModal(
                title=f"File: {os.path.basename(self.selected_artifact_path)}",
                file_path=self.selected_artifact_path,
                is_markdown=(ext in (".md", ".markdown")),
                syntax_theme=self.settings.syntax_theme,
                wrap_mode=self.settings.wrap_text,
            ))
            return

        if not self.selected_event:
            self.notify("No event selected to inspect.", severity="warning")
            return

        content = ""
        is_md = False
        ev = self.selected_event
        title = f"Step {ev.get('step_index') or ev.get('id')} ({ev.get('step_type')})"

        if ev.get("text"):
            content = ev["text"]
            is_md = True
        elif ev.get("prompt"):
            content = ev["prompt"]
        elif ev.get("tool_args"):
            content = json.dumps(ev["tool_args"], indent=2)
        elif ev.get("subagent_report"):
            content = ev["subagent_report"]
        else:
            content = json.dumps(ev.get("payload") or {}, indent=2)

        self.push_screen(FullscreenReaderModal(
            title=title,
            content=content,
            is_markdown=is_md,
            syntax_theme=self.settings.syntax_theme,
            wrap_mode=self.settings.wrap_text,
        ))

    def copy_to_clipboard(self, text: str) -> None:
        """Copies text directly to native OS system clipboard (pbcopy, wl-copy, xclip, clip.exe) and emits OSC 52."""
        if not text:
            return

        # 1. Native OS system clipboard
        copy_to_system_clipboard(text)

        # 2. OSC 52 fallback for SSH/remote sessions
        try:
            super().copy_to_clipboard(text)
        except Exception:
            pass

    def _extract_event_copy_text(self, ev: Dict[str, Any]) -> str:
        """Extracts plain text / payload representation of an event for clipboard copy."""
        if not ev:
            return ""
        if ev.get("payload"):
            try:
                return json.dumps(ev["payload"], indent=2)
            except Exception:
                pass
        if ev.get("tool_response_raw"):
            return str(ev["tool_response_raw"])
        if ev.get("text"):
            return str(ev["text"])
        if ev.get("prompt"):
            return str(ev["prompt"])
        if ev.get("tool_args"):
            return json.dumps(ev["tool_args"], indent=2)
        if ev.get("subagent_report"):
            return str(ev["subagent_report"])
        if ev.get("thinking"):
            return str(ev["thinking"])
        return json.dumps(ev, indent=2)

    def action_copy_smart(self) -> None:
        """Copies highlighted text from screen selection, active widget, or full event payload to system clipboard."""
        # 1. Screen-level text selection (Static cards, Tool visualizers, Diffs)
        try:
            screen_text = self.screen.get_selected_text()
            if screen_text:
                self.copy_to_clipboard(screen_text)
                self.notify("✓ Copied screen selection to clipboard.")
                return
        except Exception:
            pass

        # 2. In-widget selection (Prompts, Responses, Reasoning, JSON areas)
        for ta in self.query(SelectableTextArea):
            if ta.display and ta.selected_text:
                self.copy_to_clipboard(ta.selected_text)
                self.notify("✓ Copied selection to clipboard.")
                return

        # 3. Active event payload fallback
        if not self.selected_event:
            self.notify("No event selected.", severity="warning")
            return

        content = self._extract_event_copy_text(self.selected_event)
        if content:
            self.copy_to_clipboard(content)
            self.notify("✓ Copied event details to clipboard.")

    action_copy_payload = action_copy_smart

    def force_refresh_sessions(self) -> None:
        self.refresh_sessions_list(force=True)
        self.notify("Refreshed sessions list.")

    def action_filter_all_agents(self) -> None:
        self.subagent_filter = None
        self.notify("Displaying all agents.")
        if self.current_watcher:
            self.current_watcher.last_event_id = 0
            self.step_nodes.clear()
            self.subagent_branches.clear()
            self.latest_invoke_node = None
            self.query_one("#steps-tree", Tree).clear()
            self.poll_live_updates()
