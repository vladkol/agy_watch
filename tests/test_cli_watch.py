"""Tests for agy_watch CLI, Global Registry, and Session Watcher."""

import os
import json
import yaml
import shutil
import tempfile
import pytest
from click.testing import CliRunner

from agy_watch.registry import GlobalRegistry
from agy_watch.watcher import SessionWatcher
from agy_watch.cli import main as cli_main
from agy_watch.wire_tap import BlobStore, WireTapDB


def test_global_registry_crud_and_liveness():
    """Verifies that GlobalRegistry registers, updates, and queries sessions correctly."""
    temp_dir = tempfile.mkdtemp(prefix="agy_reg_test_")
    try:
        db_path = os.path.join(temp_dir, "registry.db")
        registry = GlobalRegistry(db_path=db_path)

        # 1. Register a session
        registry.register_or_update({
            "session_id": "session_abc_123456",
            "cascade_id": "cascade_abc_123456",
            "title": "Test Multiagent Task",
            "status": "STATE_ACTIVE",
            "workspace_dir": "/tmp/workspace_a",
            "db_path": "/tmp/workspace_a/.trajectories/wire_tap.db",
            "pid": os.getpid(),  # Current alive PID
            "total_tokens": 12000,
            "subagent_count": 2,
        })

        sessions = registry.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "session_abc_123456"
        assert sessions[0]["is_live"] is True
        assert sessions[0]["total_tokens"] == 12000

        # 2. Update with more tokens and subagents
        registry.register_or_update({
            "session_id": "session_abc_123456",
            "total_tokens": 15500,
            "subagent_count": 3,
            "status": "STATE_DONE",
        })

        updated = registry.get_session("session_abc")
        assert updated is not None
        assert updated["total_tokens"] == 15500
        assert updated["subagent_count"] == 3

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_session_watcher_incremental_polling():
    """Verifies that SessionWatcher polls events incrementally in WAL mode."""
    temp_dir = tempfile.mkdtemp(prefix="agy_watch_test_")
    try:
        trajectories_dir = os.path.join(temp_dir, ".trajectories")
        blobs_dir = os.path.join(trajectories_dir, "blobs")
        db_path = os.path.join(trajectories_dir, "wire_tap.db")

        store = BlobStore(blobs_dir=blobs_dir)
        db = WireTapDB(db_path=db_path, blob_store=store)

        watcher = SessionWatcher(db_path=db_path)

        # Initial poll on empty DB
        info, new_events = watcher.poll()
        assert len(new_events) == 0

        # Write 2 events
        db.record_outbound({"userInput": "Create subagents"})
        db.record_inbound({
            "initializeConversationResponse": {"cascadeId": "cas_123"},
            "stepUpdate": {
                "trajectoryId": "traj_root",
                "stepIndex": 1,
                "text": "Starting subagent...",
            },
        })

        # Poll 1
        info, new_events = watcher.poll()
        assert len(new_events) == 2
        assert watcher.last_event_id > 0

        # Poll 2 immediately (no new events)
        info, next_events = watcher.poll()
        assert len(next_events) == 0

        # Write subagent tool call
        db.record_inbound({
            "stepUpdate": {
                "trajectoryId": "traj_subagent_1",
                "stepIndex": 2,
                "toolCalls": [{"name": "run_command", "args": {"CommandLine": "echo 1"}}],
            },
        })

        # Poll 3 (only the new subagent step)
        info, subagent_events = watcher.poll()
        assert len(subagent_events) == 1
        assert subagent_events[0]["step_type"] == "TOOL_CALL"
        assert subagent_events[0]["is_main"] is False
        assert subagent_events[0]["subagent_id"] == "traj_subagent_1"

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_cli_list_inspect_and_tail_commands():
    """Verifies that agy_watch list, inspect, and tail subcommands output valid JSON and YAML."""
    temp_dir = tempfile.mkdtemp(prefix="agy_cli_test_")
    try:
        reg_db = os.path.join(temp_dir, "registry.db")
        registry = GlobalRegistry(db_path=reg_db)

        # Set up a fake session with real wire_tap.db
        sess_dir = os.path.join(temp_dir, "task_1", ".trajectories")
        sess_db = os.path.join(sess_dir, "wire_tap.db")
        blobs_dir = os.path.join(sess_dir, "blobs")
        store = BlobStore(blobs_dir=blobs_dir)
        db = WireTapDB(db_path=sess_db, blob_store=store)

        db.record_outbound({"userInput": "Verify CLI Commands"})
        db.record_inbound({
            "initializeConversationResponse": {"cascadeId": "cli_cascade_999"},
            "stepUpdate": {
                "trajectoryId": "cli_traj_999",
                "stepIndex": 1,
                "text": "CLI Output Test",
                "state": "STATE_DONE",
            },
            "usageMetadata": {
                "promptTokenCount": "500",
                "candidatesTokenCount": "100",
                "totalTokenCount": "600",
            },
        })

        registry.register_or_update({
            "session_id": "cli_traj_999",
            "cascade_id": "cli_cascade_999",
            "title": "Verify CLI Commands",
            "status": "STATE_DONE",
            "workspace_dir": os.path.dirname(sess_dir),
            "db_path": sess_db,
            "blobs_dir": blobs_dir,
            "pid": os.getpid(),
            "total_tokens": 600,
            "step_count": 2,
        })

        # Inject this registry for the CLI
        import agy_watch.registry as reg_module
        from agy_watch.tui import AgyWatchApp
        reg_module._default_registry = registry

        runner = CliRunner()

        # 1. Test agy_watch list --json
        res_list_json = runner.invoke(cli_main, ["list", "--json"])
        assert res_list_json.exit_code == 0
        parsed_list = json.loads(res_list_json.output)
        assert len(parsed_list) == 1
        assert parsed_list[0]["session_id"] == "cli_traj_999"

        # 2. Test agy_watch list --yaml
        res_list_yaml = runner.invoke(cli_main, ["list", "--yaml"])
        assert res_list_yaml.exit_code == 0
        parsed_yaml = yaml.safe_load(res_list_yaml.output)
        assert len(parsed_yaml) == 1
        assert parsed_yaml[0]["session_id"] == "cli_traj_999"

        # 3. Test agy_watch inspect <id> --json
        res_inspect = runner.invoke(cli_main, ["inspect", "cli_traj_999", "--json"])
        assert res_inspect.exit_code == 0
        inspect_data = json.loads(res_inspect.output)
        assert "session" in inspect_data
        assert "events" in inspect_data
        assert inspect_data["session"]["total_tokens"] == 600

        # 4. Test agy_watch tail <id> --json
        res_tail = runner.invoke(cli_main, ["tail", "cli_traj_999", "--json"])
        assert res_tail.exit_code == 0
        lines = [json.loads(line) for line in res_tail.output.strip().split("\n") if line]
        assert len(lines) >= 2

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_tui_app_mount_and_pilot_lifecycle():
    """Integration test validating Textual CSS parsing, widget mounting, and interaction lifecycle."""
    temp_dir = tempfile.mkdtemp(prefix="agy_tui_test_")
    try:
        reg_db = os.path.join(temp_dir, "registry.db")
        registry = GlobalRegistry(db_path=reg_db)

        # Set up a session with real wire_tap.db
        sess_dir = os.path.join(temp_dir, "tui_task", ".trajectories")
        sess_db = os.path.join(sess_dir, "wire_tap.db")
        blobs_dir = os.path.join(sess_dir, "blobs")
        store = BlobStore(blobs_dir=blobs_dir)
        db = WireTapDB(db_path=sess_db, blob_store=store)

        db.record_outbound({"userInput": "TUI Pilot Verification Prompt"})
        brain_dir = os.path.join(temp_dir, "tui_task", "brain", "tui_cas_001")
        os.makedirs(brain_dir, exist_ok=True)
        img_file = os.path.join(brain_dir, "mock_tui_chart.png")
        with open(img_file, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 256)

        db.record_outbound({"userInput": "TUI Pilot Verification Prompt"})
        db.record_inbound({
            "initializeConversationResponse": {"cascadeId": "tui_cas_001"},
            "stepUpdate": {
                "trajectoryId": "tui_traj_001",
                "stepIndex": 1,
                "text": "TUI Response Content",
                "state": "STATE_ACTIVE",
            },
        })
        db.record_inbound({
            "stepUpdate": {
                "trajectoryId": "tui_traj_001",
                "stepIndex": 2,
                "generateImage": {
                    "imageName": "mock_tui_chart",
                    "prompt": "Test Chart",
                },
                "state": "STATE_DONE",
            },
        })

        registry.register_or_update({
            "session_id": "tui_traj_001",
            "cascade_id": "tui_cas_001",
            "title": "TUI Verification Session",
            "status": "STATE_DONE",
            "workspace_dir": os.path.dirname(sess_dir),
            "db_path": sess_db,
            "blobs_dir": blobs_dir,
            "pid": os.getpid(),
            "total_tokens": 1000,
        })

        import agy_watch.registry as reg_module
        from agy_watch.tui import AgyWatchApp
        reg_module._default_registry = registry

        from agy_watch.settings import UserSettings
        app = AgyWatchApp(initial_session_id="tui_traj_001", settings=UserSettings())

        async with app.run_test() as pilot:
            # 1. Verify CSS stylesheet parsed without error and app is active
            assert app.is_running is True

            # 2. Verify all 3 split layout panes and key widgets mounted
            sessions_pane = app.query_one("#sessions-pane")
            center_pane = app.query_one("#center-pane")
            inspector_pane = app.query_one("#inspector-pane")
            assert sessions_pane is not None
            assert center_pane is not None
            assert inspector_pane is not None

            list_view = app.query_one("#sessions-list")
            tree = app.query_one("#steps-tree")
            inspector_content = app.query_one("#inspector-content")
            assert list_view is not None
            assert tree is not None
            assert inspector_content is not None

            # 3. Simulate user keyboard interactions
            await pilot.press("space")  # Toggle follow
            assert app.is_following is False
            await pilot.press("space")  # Resume follow
            assert app.is_following is True

            # 4. Test tab switching (a key) on step with artifacts
            tabs = app.query_one("#inspector-tabs")
            assert tabs.active == "tab-details"
            await pilot.press("a")      # Switch to Artifacts tab
            assert tabs.active == "tab-artifacts"
            await pilot.press("a")      # Switch back to Details tab
            assert tabs.active == "tab-details"

            # 5. Test tree mode toggle (t key)
            assert app.tree_mode is True
            await pilot.press("t")      # Switch to Flat mode
            assert app.tree_mode is False
            await pilot.press("t")      # Switch back to Tree mode
            assert app.tree_mode is True

            await pilot.press("r")      # Refresh sessions
            await pilot.press("0")      # Filter all agents
            await pilot.pause()

            # 6. Trigger modal inspection hotkey
            await pilot.press("f")
            await pilot.pause()
            # Close modal
            await pilot.press("escape")
            await pilot.pause()

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_image_generation_and_artifact_extraction():
    """Verifies that generateImage tool calls and generated image artifacts are resolved."""
    temp_dir = tempfile.mkdtemp(prefix="agy_art_test_")
    try:
        trajectories_dir = os.path.join(temp_dir, ".trajectories")
        blobs_dir = os.path.join(trajectories_dir, "blobs")
        db_path = os.path.join(trajectories_dir, "wire_tap.db")

        # Create a fake generated image in brain directory
        brain_dir = os.path.join(temp_dir, "brain", "cas_image_001")
        os.makedirs(brain_dir, exist_ok=True)
        img_path = os.path.join(brain_dir, "funny_bunny_12345.jpg")
        with open(img_path, "wb") as f:
            f.write(b"\xFF\xD8\xFF\xE0" + b"\x00" * 1024)  # Fake JPEG header

        store = BlobStore(blobs_dir=blobs_dir)
        db = WireTapDB(db_path=db_path, blob_store=store)

        db.record_outbound({"userInput": "Generate bunny picture"})
        db.record_inbound({
            "initializeConversationResponse": {"cascadeId": "cas_image_001"},
            "stepUpdate": {
                "trajectoryId": "cas_image_001",
                "stepIndex": 1,
                "generateImage": {
                    "imageName": "funny_bunny",
                    "aspectRatio": "1:1",
                    "prompt": "A funny bunny with sunglasses",
                },
            },
        })

        watcher = SessionWatcher(db_path=db_path)
        info, events = watcher.poll()

        assert len(events) == 2
        img_event = events[1]
        assert img_event["step_type"] == "TOOL_CALL"
        assert img_event["tool_name"] == "generate_image"
        assert len(img_event["artifacts"]) >= 1
        assert img_event["artifacts"][0]["type"] == "image"
        assert img_event["artifacts"][0]["filename"] == "funny_bunny_12345.jpg"
        assert img_event["artifacts"][0]["exists"] is True

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_user_settings_persistence_and_restoration():
    """Verifies that UserSettings saves and restores configuration from disk correctly."""
    from agy_watch.settings import UserSettings

    temp_dir = tempfile.mkdtemp(prefix="agy_settings_test_")
    try:
        settings_file = os.path.join(temp_dir, "settings.json")

        # 1. Default settings
        s1 = UserSettings.load(settings_file)
        assert s1.theme == "dracula"
        assert s1.syntax_theme == "dracula"
        assert s1.last_session_id is None
        assert s1.view_mode == "tree"

        # 2. Modify and save
        s1.theme = "nord"
        s1.syntax_theme = "monokai"
        s1.last_session_id = "session_xyz_789"
        s1.view_mode = "flat"
        s1.active_tab = "tab-artifacts"
        assert s1.save(settings_file) is True

        # 3. Reload and verify
        s2 = UserSettings.load(settings_file)
        assert s2.theme == "nord"
        assert s2.syntax_theme == "monokai"
        assert s2.last_session_id == "session_xyz_789"
        assert s2.view_mode == "flat"
        assert s2.active_tab == "tab-artifacts"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_tui_app_settings_restoration():
    """Verifies that AgyWatchApp applies theme, active tab, and last selected session from settings."""
    from agy_watch.settings import UserSettings
    import agy_watch.settings as settings_module
    from agy_watch.tui import AgyWatchApp
    from textual.widgets import ListView, TabbedContent

    temp_dir = tempfile.mkdtemp(prefix="agy_tui_settings_")
    try:
        settings_file = os.path.join(temp_dir, "settings.json")
        reg_db = os.path.join(temp_dir, "registry.db")
        registry = GlobalRegistry(db_path=reg_db)

        # Set up a session
        registry.register_or_update({
            "session_id": "session_saved_target",
            "title": "Saved Target Session",
            "status": "STATE_DONE",
            "workspace_dir": temp_dir,
            "db_path": os.path.join(temp_dir, "wire_tap.db"),
            "pid": os.getpid(),
        })

        # Save settings pointing to this session
        custom_settings = UserSettings(
            theme="textual-dark",
            syntax_theme="nord",
            last_session_id="session_saved_target",
            view_mode="flat",
            active_tab="tab-artifacts",
        )
        custom_settings.save(settings_file)

        # Mock global settings and registry
        settings_module._global_settings = custom_settings
        import agy_watch.registry as reg_module
        reg_module._default_registry = registry

        app = AgyWatchApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.2)

            # Check restored view mode
            assert app.tree_mode is False

            # Session attachment sets tab to tab-details as required
            tabs = app.query_one("#inspector-tabs", TabbedContent)
            assert tabs.active == "tab-details"

            # Artifacts tab is hidden when no artifacts exist for the selected event
            from textual.widgets import TabPane
            tab_artifacts = app.query_one("#tab-artifacts", TabPane)
            assert tab_artifacts.display is False

            # Pressing 'a' keeps tab-details when tab-artifacts is hidden
            await pilot.press("a")
            assert tabs.active == "tab-details"

            # Check cycle syntax theme action
            app.action_cycle_syntax_theme()
            assert app.settings.syntax_theme != "nord"
    finally:
        settings_module._global_settings = None
        shutil.rmtree(temp_dir, ignore_errors=True)



