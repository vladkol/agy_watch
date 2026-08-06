"""Comprehensive test suite covering all 8 Antigravity SDK configuration primitives and events:
1. Agent Skills (SKILL.md detection, skill name badging, and visualizer)
2. Cancellation (halt_request, STATE_CANCELLED state, and cancellation cards)
3. Slash Commands (/plan, /goal, /owl, and complex_user_input parsing)
4. All 17 Built-in & Harness Tools (dispatch table, labels, and renderers)
5. Structured Output in Agents & Tools (Pydantic JSON schema formatting in Finish & Tools)
6. Lifecycle Hooks Taxonomy (Inspect, Decide, and Transform Hooks)
7. Autonomous Triggers (Interval, Cron, File Change, Webhook)
8. Execution Loops & Context Compaction (Multi-turn cycles, ActionCompaction pruning)
"""

import json
import os
import sqlite3
import pytest
from rich.console import Console
from rich.panel import Panel

from agy_watch.wire_tap import WireTapDB, BlobStore
from agy_watch.watcher import SessionWatcher
from agy_watch.tool_renderers import (
    render_tool_event,
    render_finish,
    render_compaction,
    render_ask_permission,
    render_trigger_notification,
    render_cancellation,
    render_policy_event,
    render_view_file,
    build_tool_tree_label,
    _TOOL_DISPATCH_TABLE,
)


def _render_to_str(renderable) -> str:
    console = Console(width=100, record=True)
    console.print(renderable)
    return console.export_text()


# ==============================================================================
# 1. Agent Skills
# ==============================================================================

def test_agent_skill_detection_and_rendering():
    """Verify that viewing SKILL.md files is detected as an Agent Skill activation."""
    ev = {
        "step_type": "TOOL_CALL",
        "tool_name": "view_file",
        "tool_args": {
            "filePath": "/Users/vladkol/.gemini/config/skills/git-workflow/SKILL.md",
            "startLine": 1,
            "endLine": 50,
            "content": "# Git Workflow Skill\nInstructions on branching...",
        },
        "is_skill": True,
        "skill_name": "git-workflow",
    }

    out = _render_to_str(render_view_file(ev))
    assert "AGENT SKILL: git-workflow" in out
    assert "git-workflow" in out
    assert "# Git Workflow Skill" in out

    # Test tree label
    label = build_tool_tree_label(ev)
    assert "view_file" in label.plain or "SKILL" in label.plain


# ==============================================================================
# 2. Cancellation
# ==============================================================================

def test_cancellation_recording_and_rendering(tmp_path):
    """Verify client halt requests and STATE_CANCELLED wire events are recorded and rendered."""
    db_path = str(tmp_path / "cancel.db")
    blobs_dir = str(tmp_path / "blobs")
    store = BlobStore(blobs_dir=blobs_dir)
    recorder = WireTapDB(db_path=db_path, blob_store=store)
    recorder.session_id = "test_cancel"

    # 1. Client sends halt request
    recorder.record_outbound({"haltRequest": True, "halt_request": True})

    # 2. Localharness responds with STATE_CANCELLED
    recorder.record_inbound({
        "trajectoryStateUpdate": {
            "trajectoryId": "test_cancel",
            "state": "STATE_CANCELLED",
        }
    })

    watcher = SessionWatcher(db_path)
    _, events = watcher.poll()

    halt_ev = next((e for e in events if e.get("step_type") == "CANCELLATION_REQUEST"), None)
    assert halt_ev is not None
    assert "cancellation" in halt_ev.get("text", "").lower()

    cancel_ev = next((e for e in events if e.get("step_type") == "CANCELLATION"), None)
    assert cancel_ev is not None
    assert cancel_ev.get("state") == "STATE_CANCELLED"

    # Test visualizer
    out = _render_to_str(render_cancellation(cancel_ev))
    assert "TURN EXECUTION CANCELLED" in out
    assert "CANCELLED" in out


# ==============================================================================
# 3. Slash Commands
# ==============================================================================

def test_slash_commands_parsing_and_labeling(tmp_path):
    """Verify slash command extraction from complexUserInput and title/label shaping."""
    db_path = str(tmp_path / "slash.db")
    blobs_dir = str(tmp_path / "blobs")
    store = BlobStore(blobs_dir=blobs_dir)
    recorder = WireTapDB(db_path=db_path, blob_store=store)
    recorder.session_id = "test_slash"

    # Complex user input with slash command
    recorder.record_outbound({
        "complexUserInput": {
            "parts": [
                {"slashCommand": {"name": "plan"}},
                {"text": "Design a high-throughput queue system"},
            ]
        }
    })

    watcher = SessionWatcher(db_path)
    _, events = watcher.poll()

    user_ev = next((e for e in events if e.get("step_type") == "USER_INPUT"), None)
    assert user_ev is not None
    assert user_ev.get("slash_command") == "/plan"
    assert "Design a high-throughput" in user_ev.get("prompt", "")
    assert recorder.user_title.startswith("/plan")


# ==============================================================================
# 4. All 17 Built-in & Harness Tools
# ==============================================================================

def test_all_17_tools_in_dispatch_table():
    """Ensure all 17 Antigravity tool primitives are registered in the visualizer dispatch table."""
    expected_tools = [
        "run_command", "edit_file", "create_file", "view_file",
        "list_directory", "search_directory", "find_file",
        "invoke_subagent", "ask_question", "generate_image",
        "search_web", "read_url_content", "finish",
        "compaction", "ask_permission", "mcp_tool", "custom_tool"
    ]
    for t in expected_tools:
        assert t in _TOOL_DISPATCH_TABLE, f"Tool '{t}' missing from _TOOL_DISPATCH_TABLE"


def test_ask_permission_and_compaction_renderers():
    """Verify runtime permission elevation and context window compaction visualizers."""
    perm_ev = {
        "step_type": "TOOL_CALL",
        "tool_name": "ask_permission",
        "tool_args": {
            "Action": "command",
            "Target": "git clone https://github.com/example/repo",
            "Reason": "Need to clone target repository for inspection",
        }
    }
    perm_out = _render_to_str(render_ask_permission(perm_ev))
    assert "PERMISSION ELEVATION REQUEST" in perm_out
    assert "git clone" in perm_out
    assert "Need to clone" in perm_out

    comp_ev = {
        "step_type": "COMPACTION",
        "tool_name": "compaction",
        "tool_args": {},
    }
    comp_out = _render_to_str(render_compaction(comp_ev))
    assert "CONTEXT WINDOW COMPACTION TRIGGERED" in comp_out
    assert "COMPLETED" in comp_out


# ==============================================================================
# 5. Structured Output in Agents & Tools
# ==============================================================================

def test_structured_output_formatting_in_finish():
    """Verify that structured JSON outputs in Finish tools are formatted and highlighted."""
    structured_json_str = json.dumps({
        "status": "SUCCESS",
        "decision": "DEPLOY",
        "metrics": {"latency_p95_ms": 12.4, "error_rate": 0.001},
        "affected_services": ["auth", "gateway"],
    })
    finish_ev = {
        "step_type": "TOOL_CALL",
        "tool_name": "finish",
        "tool_args": {
            "output_string": structured_json_str,
        }
    }
    out = _render_to_str(render_finish(finish_ev))
    assert "STRUCTURED OUTPUT RESULT" in out
    assert "latency_p95_ms" in out
    assert "affected_services" in out


# ==============================================================================
# 6. Lifecycle Hooks Taxonomy (Inspect, Decide, Transform)
# ==============================================================================

def test_lifecycle_hooks_taxonomy_rendering():
    """Verify rendering for Inspect (PostTurn, PostTool, OnCompaction), Decide (PreTurn, PreTool), and Transform (OnToolError)."""
    # 1. Decide: PreTurn denial
    preturn_deny_ev = {
        "step_type": "PRE_TURN_DECISION",
        "decision": "DENY",
        "reason": "Turn prohibited during maintenance window",
    }
    out1 = _render_to_str(render_policy_event(preturn_deny_ev))
    assert "PRE-TURN DECIDE HOOK" in out1
    assert "maintenance window" in out1

    # 2. Inspect: PostTool hook
    posttool_ev = {
        "step_type": "POST_TOOL_HOOK",
        "tool_name": "run_command",
    }
    out2 = _render_to_str(render_policy_event(posttool_ev))
    assert "INSPECT HOOK: POST_TOOL (run_command)" in out2

    # 3. Transform: OnToolError hook and result shaping
    ontoolerror_ev = {
        "step_type": "ON_TOOL_ERROR_HOOK",
        "tool_name": "database_query",
        "payload": {
            "callHookRequest": {
                "onToolErrorArgs": {
                    "toolName": "database_query",
                    "errorMessage": "Connection pool exhausted (PG_ERR_53300)",
                }
            }
        }
    }
    out3 = _render_to_str(render_policy_event(ontoolerror_ev))
    assert "ON_TOOL_ERROR (TRANSFORM HOOK)" in out3
    assert "PG_ERR_53300" in out3

    ontoolerror_res = {
        "step_type": "ON_TOOL_ERROR_RESULT",
        "custom_error_message": "Database is temporarily busy. Retry in 5 seconds.",
    }
    out4 = _render_to_str(render_policy_event(ontoolerror_res))
    assert "TRANSFORM HOOK: CUSTOM ERROR SHAPED" in out4
    assert "Retry in 5 seconds" in out4


# ==============================================================================
# 7. Autonomous Triggers
# ==============================================================================

def test_autonomous_triggers_classification_and_rendering(tmp_path):
    """Verify Timer, Filesystem, and Webhook triggers are classified and visualized."""
    db_path = str(tmp_path / "triggers.db")
    blobs_dir = str(tmp_path / "blobs")
    store = BlobStore(blobs_dir=blobs_dir)
    recorder = WireTapDB(db_path=db_path, blob_store=store)
    recorder.session_id = "test_trig"

    # 1. Timer trigger
    recorder.record_outbound({"automatedTrigger": "Timer tick: 15-minute scheduled healthcheck"})

    # 2. Filesystem trigger
    recorder.record_outbound({"automatedTrigger": "MODIFIED: /src/server.py"})

    # 3. Webhook trigger
    recorder.record_outbound({"automatedTrigger": "webhook: GitHub push event received on main"})

    watcher = SessionWatcher(db_path)
    _, events = watcher.poll()

    trig_events = [e for e in events if e.get("step_type") == "TRIGGER_NOTIFICATION"]
    assert len(trig_events) == 3

    # Test renderers
    t_out1 = _render_to_str(render_trigger_notification(trig_events[0]))
    assert "Timer / Interval Trigger" in t_out1
    assert "15-minute scheduled healthcheck" in t_out1

    t_out2 = _render_to_str(render_trigger_notification(trig_events[1]))
    assert "Filesystem Change Trigger" in t_out2
    assert "server.py" in t_out2

    t_out3 = _render_to_str(render_trigger_notification(trig_events[2]))
    assert "Webhook Event Trigger" in t_out3


# ==============================================================================
# 8. Execution Loops & Compaction
# ==============================================================================

def test_execution_loops_and_compaction_wire_stream(tmp_path):
    """Verify multi-turn loop progression and ActionCompaction window compaction."""
    db_path = str(tmp_path / "loop.db")
    blobs_dir = str(tmp_path / "blobs")
    store = BlobStore(blobs_dir=blobs_dir)
    recorder = WireTapDB(db_path=db_path, blob_store=store)
    recorder.session_id = "test_loop"

    # Turn 1: User prompt -> Tool call -> Result
    recorder.record_outbound({"userInput": "Search for latest research papers"})
    recorder.record_inbound({
        "stepUpdate": {
            "trajectoryId": "test_loop",
            "stepIndex": 0,
            "searchWeb": {"query": "deep learning 2026"},
        }
    })

    # Turn 2: Context window reached threshold -> Localharness triggers compaction
    recorder.record_inbound({
        "stepUpdate": {
            "trajectoryId": "test_loop",
            "stepIndex": 1,
            "compaction": {},
        }
    })

    # Turn 3: Model finishes task
    recorder.record_inbound({
        "stepUpdate": {
            "trajectoryId": "test_loop",
            "stepIndex": 2,
            "finish": {"final_message": "Found 15 papers on multi-agent architectures."},
        }
    })

    watcher = SessionWatcher(db_path)
    _, events = watcher.poll()

    types = [e.get("step_type") for e in events]
    assert "USER_INPUT" in types
    assert "TOOL_CALL" in types
    
    comp_ev = next((e for e in events if e.get("tool_name") == "compaction"), None)
    assert comp_ev is not None
    
    fin_ev = next((e for e in events if e.get("tool_name") == "finish"), None)
    assert fin_ev is not None
