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
from textual.widgets import Header, Footer, Static, ListView, ListItem, Tree, TabbedContent, TabPane, Label
from textual.widgets.tree import TreeNode
from textual.screen import ModalScreen
from textual.binding import Binding

from agy_watch.registry import get_global_registry, GlobalRegistry
from agy_watch.watcher import SessionWatcher
from agy_watch.settings import get_user_settings, UserSettings, SUPPORTED_THEMES, AVAILABLE_SYNTAX_THEMES


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


def get_syntax_lexer_for_path(file_path: str) -> str:
    """Detects lexer name from file extension for rich.syntax.Syntax."""
    ext = os.path.splitext(file_path)[1].lower()
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".jsx": "jsx",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".html": "html",
        ".css": "css",
        ".sh": "bash",
        ".zsh": "bash",
        ".bash": "bash",
        ".md": "markdown",
        ".sql": "sql",
        ".go": "go",
        ".rs": "rust",
        ".cpp": "cpp",
        ".c": "c",
        ".java": "java",
    }
    return mapping.get(ext, "text")


class FullscreenReaderModal(ModalScreen):
    """Full-screen modal for reading files and prompt traces with syntax highlighting and line numbers."""

    BINDINGS = [
        Binding("escape,q", "dismiss", "Close", show=True),
        Binding("w", "toggle_wrap", "Toggle Wrap", show=True),
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
        self.syntax_theme = syntax_theme
        self.wrap_mode = wrap_mode

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            yield Static(f" [bold white]═══ {self.reader_title} ═══[/bold white] (Press ESC or Q to close, W to toggle wrap)", id="modal-header")
            with VerticalScroll(id="modal-scroll"):
                yield Static(id="modal-body")

    def on_mount(self) -> None:
        self._render_content()

    def action_toggle_wrap(self) -> None:
        self.wrap_mode = not self.wrap_mode
        self._render_content()

    def _render_content(self) -> None:
        body = self.query_one("#modal-body", Static)
        if self.file_path and os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8", errors="replace") as f:
                    text_content = f.read()

                if self.is_markdown:
                    body.update(Markdown(text_content))
                else:
                    lexer = get_syntax_lexer_for_path(self.file_path)
                    body.update(Syntax(
                        text_content,
                        lexer,
                        theme=self.syntax_theme,
                        line_numbers=True,
                        word_wrap=self.wrap_mode,
                    ))
            except Exception as e:
                body.update(f"Error reading file: {e}")
        elif self.raw_content is not None:
            if self.is_markdown:
                body.update(Markdown(self.raw_content))
            else:
                body.update(Syntax(
                    self.raw_content,
                    "text",
                    theme=self.syntax_theme,
                    line_numbers=False,
                    word_wrap=self.wrap_mode,
                ))


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
        width: 26%;
        min-width: 28;
        max-width: 38;
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
    }

    #inspector-pane {
        width: 36%;
        min-width: 42;
        padding: 0 1;
        background: $surface;
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
    }

    .session-item {
        padding: 1 1;
        border-bottom: solid $border-blurred;
    }

    .session-item:hover {
        background: $boost;
    }

    #tree-container {
        height: 1fr;
    }

    #inspector-tabs {
        height: 1fr;
    }

    #inspector-scroll {
        height: 1fr;
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
        width: 90%;
        height: 90%;
        background: $surface;
        border: thick $accent;
        padding: 1 2;
    }

    #modal-header {
        background: $panel;
        padding: 0 1;
        margin-bottom: 1;
    }

    #modal-scroll {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("space", "toggle_follow", "Follow/Pause", show=True),
        Binding("f", "fullscreen_inspect", "Fullscreen", show=True),
        Binding("p", "cycle_syntax_theme", "Theme", show=True),
        Binding("o", "open_selected_media_external", "Open External", show=True),
        Binding("a", "toggle_inspector_tab", "Toggle Tab", show=True),
        Binding("t", "toggle_tree_mode", "Tree/Flat View", show=True),
        Binding("c", "copy_payload", "Copy", show=True),
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
        yield Header(show_clock=True)
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
                            yield Static("Select an event from the timeline to view details.", id="inspector-content")
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

    def refresh_sessions_list(self, force: bool = False) -> None:
        """Reloads the session list from global registry only if data changed."""
        sessions = self.registry.list_sessions()
        new_sig = tuple((s["session_id"], s["updated_at"], s["status"], s["total_tokens"], s["step_count"]) for s in sessions)

        if not force and new_sig == self._last_sessions_sig:
            return

        self._last_sessions_sig = new_sig
        self.known_sessions = sessions

        list_view = self.query_one("#sessions-list", ListView)
        current_idx = list_view.index

        list_view.clear()

        for s in sessions:
            status_icon = "● LIVE" if s["is_live"] else "○ IDLE"
            status_color = "green" if s["is_live"] else "bright_black"
            time_str = datetime.fromtimestamp(s.get("updated_at") or 0).strftime("%H:%M:%S")

            sub_count = s.get("subagent_count", 0)
            sub_label = f" ({sub_count} workers)" if sub_count > 0 else ""
            tokens_k = f"{s.get('total_tokens', 0) / 1000:.1f}k tok"

            title_snippet = (s.get("title") or "Session")[:30]

            item_text = (
                f"[{status_color}]{status_icon}[/{status_color}] [bold]{s['session_id'][:8]}[/bold] {time_str}\n"
                f" [cyan]{title_snippet}[/cyan]{sub_label}\n"
                f" [yellow]{tokens_k}[/yellow]"
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

            # Check if this step already has an active node in the tree (deduplicate streaming deltas)
            if step_idx is not None and step_key in self.step_nodes:
                existing_node = self.step_nodes[step_key]
                existing_node.set_label(label)
                existing_node.data = ev
            else:
                if not self.tree_mode:
                    node = tree.root.add(label, data=ev)
                    node.allow_expand = True
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
                            node = tree.root.add(label, data=ev)
                            node.allow_expand = True
                            if step_idx is not None:
                                self.step_nodes[step_key] = node
                    else:
                        if sub_id not in self.subagent_branches:
                            parent_container = self.latest_invoke_node if self.latest_invoke_node else tree.root
                            sub_branch = parent_container.add(f"🤖 [bold cyan]Subagent ({str(sub_id)[:8]})[/bold cyan] [Active]", expand=True)
                            self.subagent_branches[sub_id] = sub_branch

                        sub_branch = self.subagent_branches[sub_id]
                        node = sub_branch.add(label, data=ev)
                        if step_idx is not None:
                            self.step_nodes[step_key] = node

                        state = ev.get("state") or ev.get("payload", {}).get("stepUpdate", {}).get("state")
                        if state in ("STATE_DONE", "STATE_ERROR"):
                            sub_branch.set_label(f"🤖 [bold cyan]Subagent ({str(sub_id)[:8]})[/bold cyan] [{state[6:]}]")

            # If user is following, automatically inspect latest step
            if self.is_following:
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
        ts_str = datetime.fromtimestamp(ev.get("timestamp") or 0).strftime("%H:%M:%S")
        direction = ev.get("direction")
        msg_type = ev.get("message_type")
        step_type = ev.get("step_type")
        is_main = ev.get("is_main", True)

        actor_tag = "[ROOT]" if is_main else f"[SUB {str(ev.get('subagent_id', ''))[:6]}]"
        actor_style = "bold magenta" if is_main else "bold cyan"

        t = Text()
        t.append(f"[{ts_str}] ", style="bright_black")
        if not self.tree_mode:
            t.append(f"{actor_tag} ", style=actor_style)

        if step_type == "USER_INPUT":
            t.append(" USER_PROMPT: ", style="bold green")
            t.append(f"{(ev.get('prompt') or '')[:35]}...", style="green")
        elif step_type == "SUBAGENT_PROMPT":
            t.append(" SUBAGENT_PROMPT: ", style="bold green")
            t.append(f"{(ev.get('prompt') or '')[:35]}...", style="green")
        elif step_type == "TOOL_CALL":
            tool_name = ev.get("tool_name") or "tool"
            t.append(f" TOOL: {tool_name}", style="bold yellow")
            if tool_name == "generate_image":
                img_name = ev.get("tool_args", {}).get("ImageName") or ev.get("tool_args", {}).get("imageName") or ""
                if img_name:
                    t.append(f" ({img_name})", style="italic bright_magenta")
            elif tool_name == "invoke_subagent":
                sub_count = len(ev.get("tool_args", {}).get("Subagents", []))
                if sub_count > 0:
                    t.append(f" ({sub_count} workers)", style="italic bright_yellow")
        elif step_type == "SUBAGENT_REPORT":
            t.append(" SUBAGENT_REPORT", style="bold blue")
        elif step_type == "TEXT_RESPONSE":
            t.append(" MODEL_RESPONSE", style="bold white")
        elif step_type == "MODEL_REASONING":
            t.append(" THINKING...", style="italic bright_black")
        else:
            t.append(f" {msg_type}", style="bright_black")

        return t

    def _render_inspector_event(self, ev: Dict[str, Any]) -> None:
        """Renders full scrollable inspection details in the Details tab."""
        inspector = self.query_one("#inspector-content", Static)
        if not ev:
            inspector.update("No event selected.")
            return

        t = Text()
        t.append(f"Event ID: {ev.get('id')} | Seq: {ev.get('seq_num')} | Direction: {ev.get('direction')}\n", style="bold cyan")
        t.append(f"Type: {ev.get('step_type')} ({ev.get('message_type')})\n", style="bold yellow")
        t.append(f"Actor: {'Root Agent' if ev.get('is_main') else f'Subagent ({ev.get('subagent_id')})'}\n\n", style="magenta")

        # 1. User / Subagent Prompt
        if ev.get("prompt"):
            header_title = "SUBAGENT INSTRUCTION PROMPT" if ev.get("step_type") == "SUBAGENT_PROMPT" else "USER PROMPT"
            t.append(f"─── {header_title} ───\n", style="bold green")
            t.append(f"{ev['prompt']}\n\n", style="white")

        # 2. Tool Arguments / Calls (including correlated invoke_subagent subagents)
        if ev.get("tool_name"):
            t.append(f"─── TOOL CALL: {ev['tool_name']} ───\n", style="bold yellow")
            tool_args = ev.get("tool_args") or {}
            if ev["tool_name"] == "invoke_subagent" and "Subagents" in tool_args:
                subagents = tool_args["Subagents"]
                t.append(f"Spawning {len(subagents)} Subagent(s):\n", style="bold bright_yellow")
                for i, sub in enumerate(subagents, 1):
                    t.append(f"  {i}. Role: {sub.get('Role', 'Worker')} | Type: {sub.get('TypeName', 'self')}\n", style="bright_cyan")
                    t.append(f"     Prompt: {sub.get('Prompt', '')}\n\n", style="white")
            else:
                args_formatted = json.dumps(tool_args, indent=2)
                t.append(f"Arguments:\n{args_formatted}\n\n", style="bright_yellow")

        # 3. Subagent Reports
        if ev.get("subagent_report"):
            t.append(f"─── SUBAGENT REPORT (Sender: {ev.get('subagent_id')}) ───\n", style="bold blue")
            t.append(f"{ev['subagent_report']}\n\n", style="bright_blue")

        # 4. Model Text Responses
        if ev.get("text"):
            t.append("─── MODEL RESPONSE ───\n", style="bold white")
            t.append(f"{ev['text']}\n\n", style="bright_white")

        # 5. Model Thinking
        if ev.get("thinking"):
            t.append("─── MODEL REASONING (THINKING) ───\n", style="bold bright_black")
            t.append(f"{ev['thinking']}\n\n", style="italic bright_black")

        # 6. Artifacts & Generated Media on this step
        artifacts = ev.get("artifacts") or []
        if artifacts:
            t.append("─── STEP ARTIFACTS & MEDIA (Press 'f' to view, 'o' for external) ───\n", style="bold green")
            for art in artifacts:
                status_icon = "🖼️ " if art["type"] == "image" else ("🎬 " if art["type"] == "video" else ("📄 " if art["type"] == "markdown" else "💻 "))
                size_kb = f"{art['size_bytes'] / 1024:.1f} KB" if art["size_bytes"] > 0 else "0 KB"
                exists_str = "[Found on disk]" if art["exists"] else "[Missing]"
                exists_style = "green" if art["exists"] else "red"
                t.append(f"{status_icon}[bold white]{art['filename']}[/bold white] ({art['type']}) - [{exists_style}]{exists_str}[/{exists_style}] - {size_kb}\n", style="bright_green")
                t.append(f"  Location: {art['path']}\n", style="bright_black")
            t.append("\n")

        # 7. Turn Tokens
        if ev.get("tokens"):
            t.append("─── TURN TOKEN USAGE ───\n", style="bold cyan")
            t.append(f"{json.dumps(ev['tokens'], indent=2)}\n\n", style="cyan")

        inspector.update(t)

        # Dynamically show/hide Artifacts & Files tab based on event artifacts
        try:
            tab_artifacts = self.query_one("#tab-artifacts", TabPane)
            event_artifacts = ev.get("artifacts") or []
            has_artifacts = bool(event_artifacts)
            tab_artifacts.display = has_artifacts

            if has_artifacts:
                art_list = self.query_one("#artifacts-list", ListView)
                art_list.clear()
                for art in event_artifacts:
                    self._add_artifact_to_list(art)
                if event_artifacts:
                    first_art = event_artifacts[0]["path"]
                    self.selected_artifact_path = first_art
                    self._render_artifact_preview(first_art)
            else:
                tabs = self.query_one("#inspector-tabs", TabbedContent)
                if tabs.active == "tab-artifacts":
                    tabs.active = "tab-details"
        except Exception:
            pass

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
        self.is_following = not self.is_following
        self.settings.auto_follow = self.is_following
        self.settings.save()
        self.notify(f"Live Follow: {'Enabled' if self.is_following else 'Paused'}")

    def action_toggle_tree_mode(self) -> None:
        """Toggles between Hierarchical Recursive Tree Mode and Flat Timeline Mode."""
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
        """Toggles active inspector tab between Event Details and Artifacts & Files if available."""
        try:
            tab_artifacts = self.query_one("#tab-artifacts", TabPane)
            if not tab_artifacts.display:
                self.notify("This event has no artifacts or files.", severity="information")
                return

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

    def action_copy_payload(self) -> None:
        """Copies active step content/JSON to system clipboard."""
        if not self.selected_event:
            self.notify("No event selected.", severity="warning")
            return

        try:
            import subprocess
            content = json.dumps(self.selected_event.get("payload") or {}, indent=2)
            if sys.platform == "darwin":
                p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
                p.communicate(content.encode("utf-8"))
                self.notify("Copied payload JSON to clipboard.")
            else:
                self.notify("Clipboard copy supported on macOS (use modal to select/copy).")
        except Exception as e:
            self.notify(f"Copy error: {e}", severity="error")

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
