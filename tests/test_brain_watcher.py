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

import os
import json
import sqlite3
import shutil
import tempfile
import pytest
from agy_watch.brain_watcher import (
    BrainTranscriptWatcher,
    discover_gemini_brain_sessions,
    is_valid_gemini_app_dir,
)
from agy_watch.registry import GlobalRegistry


def test_is_valid_gemini_app_dir():
    """Verifies that non-session standard folders are correctly filtered out."""
    assert is_valid_gemini_app_dir("antigravity") is True
    assert is_valid_gemini_app_dir("antigravity-cli") is True
    assert is_valid_gemini_app_dir("antigravity-ide") is True
    assert is_valid_gemini_app_dir("custom-app") is True

    # Excluded standard folders
    assert is_valid_gemini_app_dir("config") is False
    assert is_valid_gemini_app_dir("history") is False
    assert is_valid_gemini_app_dir("policies") is False
    assert is_valid_gemini_app_dir("tmp") is False
    assert is_valid_gemini_app_dir(".hidden") is False
    assert is_valid_gemini_app_dir("chrome-browser-profile") is False
    assert is_valid_gemini_app_dir("old-backup") is False


def test_brain_transcript_watcher_incremental_streaming():
    """Verifies incremental seek-offset streaming and step normalization."""
    temp_dir = tempfile.mkdtemp(prefix="agy_test_brain_")
    try:
        session_id = "test-session-uuid-1234"
        session_dir = os.path.join(temp_dir, session_id)
        logs_dir = os.path.join(session_dir, ".system_generated", "logs")
        os.makedirs(logs_dir, exist_ok=True)

        log_path = os.path.join(logs_dir, "transcript_full.jsonl")
        summary_path = os.path.join(logs_dir, "summary.json")

        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump({"shortTitle": "Test Brain Session Title"}, f)

        # Write Step 0 (USER_INPUT)
        step0 = {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "status": "DONE",
            "content": "Create a new project",
            "created_at": "2026-08-07T12:00:00Z",
        }
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(step0) + "\n")

        watcher = BrainTranscriptWatcher(session_dir, source_tag="test_antigravity")
        info, events = watcher.poll()

        assert info["session_id"] == session_id
        assert info["title"] == "Test Brain Session Title"
        assert info["source_tag"] == "test_antigravity"
        assert len(events) == 1
        assert events[0]["step_type"] == "USER_INPUT"
        assert events[0]["prompt"] == "Create a new project"

        # Append Step 1 (PLANNER_RESPONSE with tool call)
        step1 = {
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "thinking": "I need to write a file.",
            "tool_calls": [
                {
                    "name": "write_to_file",
                    "args": {"TargetFile": "/tmp/test.txt", "CodeContent": "hello"},
                }
            ],
            "created_at": "2026-08-07T12:00:05Z",
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(step1) + "\n")

        # Second poll should return newly appended in-flight Step 1
        info2, new_events = watcher.poll()
        assert len(new_events) == 1
        assert new_events[0]["step_type"] == "TOOL_CALL"
        assert new_events[0]["tool_name"] == "write_to_file"
        assert new_events[0]["thinking"] == "I need to write a file."
        assert new_events[0]["state"] == "STATE_RUNNING"
        assert len(watcher.all_events) == 2
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_brain_transcript_watcher_subagent_linking():
    """Verifies that invoke_subagent calls dynamically attach and stream child subagent transcripts."""
    temp_dir = tempfile.mkdtemp(prefix="agy_test_subagent_")
    try:
        parent_id = "parent-uuid-1111"
        child_id = "child-uuid-2222"

        parent_dir = os.path.join(temp_dir, parent_id)
        child_dir = os.path.join(temp_dir, child_id)

        parent_logs = os.path.join(parent_dir, ".system_generated", "logs")
        child_logs = os.path.join(child_dir, ".system_generated", "logs")
        os.makedirs(parent_logs, exist_ok=True)
        os.makedirs(child_logs, exist_ok=True)

        # Create child transcript
        child_step0 = {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "content": "Perform subtask in worker",
            "created_at": "2026-08-07T12:01:00Z",
        }
        with open(os.path.join(child_logs, "transcript_full.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(child_step0) + "\n")

        # Create parent transcript invoking child subagent
        parent_step0 = {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "content": "Launch subagent",
            "created_at": "2026-08-07T12:00:00Z",
        }
        parent_step1 = {
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "content": f'Created the following subagents:\n{{\n  "conversationId": "{child_id}"\n}}',
            "tool_calls": [{"name": "invoke_subagent", "args": {}}],
            "created_at": "2026-08-07T12:00:10Z",
        }
        parent_step2 = {
            "step_index": 2,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "content": "All subagent tasks finished",
            "created_at": "2026-08-07T12:02:00Z",
        }
        with open(os.path.join(parent_logs, "transcript_full.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(parent_step0) + "\n")
            f.write(json.dumps(parent_step1) + "\n")
            f.write(json.dumps(parent_step2) + "\n")

        watcher = BrainTranscriptWatcher(parent_dir, source_tag="antigravity")
        info, events = watcher.poll()

        assert info["subagent_count"] == 1
        assert child_id in watcher.child_watchers

        main_events = [e for e in events if e.get("is_main")]
        sub_events = [e for e in events if not e.get("is_main")]

        assert len(main_events) == 3
        assert len(sub_events) == 1
        assert sub_events[0]["subagent_id"] == child_id
        assert sub_events[0]["step_type"] == "USER_INPUT"
        assert sub_events[0]["prompt"] == "Perform subtask in worker"

        # Verify strict chronological interleaving
        # Event 0: Parent Step 0 (12:00:00)
        # Event 1: Parent Step 1 (12:00:10)
        # Event 2: Child Step 0  (12:01:00) -> Interleaved between step 1 and 2
        # Event 3: Parent Step 2 (12:02:00)
        assert len(events) == 4
        assert events[0]["step_index"] == 0 and events[0]["is_main"] is True
        assert events[1]["step_index"] == 1 and events[1]["is_main"] is True
        assert events[2]["subagent_id"] == child_id and events[2]["is_main"] is False
        assert events[3]["step_index"] == 2 and events[3]["is_main"] is True
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_discover_gemini_brain_sessions():
    """Verifies discover_gemini_brain_sessions scans app directories and tags child sessions."""
    temp_root = tempfile.mkdtemp(prefix="agy_test_gemini_root_")
    try:
        # Create antigravity app with a parent and child session
        agy_brain = os.path.join(temp_root, "antigravity", "brain")
        parent_id = "00000000-1111-2222-3333-444444444444"
        child_id = "55555555-6666-7777-8888-999999999999"

        p_logs = os.path.join(agy_brain, parent_id, ".system_generated", "logs")
        c_logs = os.path.join(agy_brain, child_id, ".system_generated", "logs")
        os.makedirs(p_logs, exist_ok=True)
        os.makedirs(c_logs, exist_ok=True)

        with open(os.path.join(p_logs, "short_title.txt"), "w") as f:
            f.write("Parent Session")
        with open(os.path.join(p_logs, "transcript_full.jsonl"), "w") as f:
            f.write(json.dumps({"step_index": 0, "content": f'Spawned "conversationId": "{child_id}"'}) + "\n")

        with open(os.path.join(c_logs, "transcript_full.jsonl"), "w") as f:
            f.write(json.dumps({"step_index": 0, "content": "Child task"}) + "\n")

        # Create ignored folder
        config_brain = os.path.join(temp_root, "config", "brain", "fake-session", ".system_generated", "logs")
        os.makedirs(config_brain, exist_ok=True)
        with open(os.path.join(config_brain, "transcript_full.jsonl"), "w") as f:
            f.write(json.dumps({"step_index": 0, "content": "Should be ignored"}) + "\n")

        sessions = discover_gemini_brain_sessions(gemini_root=temp_root)
        assert len(sessions) == 2  # config is ignored

        roots = [s for s in sessions if s.get("parent_id") is None]
        children = [s for s in sessions if s.get("parent_id") is not None]

        assert len(roots) == 1
        assert roots[0]["session_id"] == parent_id
        assert roots[0]["title"] == "[antigravity] Parent Session"
        assert roots[0]["subagent_count"] == 1

        assert len(children) == 1
        assert children[0]["session_id"] == child_id
        assert children[0]["parent_id"] == parent_id
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_all_step_types_normalization():
    """Verifies that all discovered step types (lifecycle + tool execution) normalize cleanly."""
    temp_dir = tempfile.mkdtemp(prefix="agy_test_types_")
    try:
        session_id = "test-all-types-session"
        session_dir = os.path.join(temp_dir, session_id)
        logs_dir = os.path.join(session_dir, ".system_generated", "logs")
        os.makedirs(logs_dir, exist_ok=True)
        log_path = os.path.join(logs_dir, "transcript_full.jsonl")

        sample_steps = [
            {"step_index": 0, "type": "USER_INPUT", "content": "Hello", "source": "USER_EXPLICIT"},
            {"step_index": 1, "type": "PLANNER_RESPONSE", "thinking": "Planning", "tool_calls": [{"name": "search_web", "args": {}}]},
            {"step_index": 2, "type": "SEARCH_WEB", "content": "Search results for Python"},
            {"step_index": 3, "type": "MCP_TOOL", "content": '{"result": "mcp data"}'},
            {"step_index": 4, "type": "READ_URL_CONTENT", "content": "Fetched markdown"},
            {"step_index": 5, "type": "BROWSER_SUBAGENT", "content": "Navigated to URL"},
            {"step_index": 6, "type": "FIND", "content": "Found 3 files"},
            {"step_index": 7, "type": "GENERATE_IMAGE", "content": "Generated image"},
            {"step_index": 8, "type": "ERROR_MESSAGE", "error": "Invalid argument", "content": "Validation error"},
            {"step_index": 9, "type": "CHECKPOINT", "content": "Context compacted"},
            {"step_index": 10, "type": "EPHEMERAL_MESSAGE", "content": "Status update"},
            {"step_index": 11, "type": "CONVERSATION_HISTORY", "content": "Prior turns"},
            {"step_index": 12, "type": "KNOWLEDGE_ARTIFACTS", "content": "Available artifacts"},
            {"step_index": 13, "type": "CUSTOM_FUTURE_TOOL", "content": "Custom output"},
        ]

        with open(log_path, "w", encoding="utf-8") as f:
            for s in sample_steps:
                f.write(json.dumps(s) + "\n")

        watcher = BrainTranscriptWatcher(session_dir)
        info, events = watcher.poll()

        # Step 1 (PLANNER_RESPONSE with search_web) and Step 2 (SEARCH_WEB output) are merged into 1 unified TOOL_CALL
        assert len(events) == 13
        assert events[0]["step_type"] == "USER_INPUT"
        assert events[1]["step_type"] == "TOOL_CALL" and events[1]["tool_name"] == "search_web"
        assert events[2]["step_type"] == "TOOL_CALL" and events[2]["tool_action"] == "MCP_TOOL"
        assert events[3]["step_type"] == "TOOL_CALL" and events[3]["tool_action"] == "READ_URL_CONTENT"
        assert events[4]["step_type"] == "TOOL_CALL" and events[4]["tool_action"] == "BROWSER_SUBAGENT"
        assert events[5]["step_type"] == "TOOL_CALL" and events[5]["tool_action"] == "FIND"
        assert events[6]["step_type"] == "TOOL_CALL" and events[6]["tool_action"] == "GENERATE_IMAGE"
        assert events[7]["step_type"] == "ERROR_MESSAGE" and events[7]["state"] == "STATE_ERROR"
        assert events[8]["step_type"] == "CHECKPOINT"
        assert events[9]["step_type"] == "EPHEMERAL_MESSAGE"
        assert events[10]["step_type"] == "CONVERSATION_HISTORY"
        assert events[11]["step_type"] == "KNOWLEDGE_ARTIFACTS"
        assert events[12]["step_type"] == "TOOL_CALL" and events[12]["tool_action"] == "CUSTOM_FUTURE_TOOL"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_background_command_running_status_mapping():
    """Verifies that background commands with status RUNNING are parsed as STATE_RUNNING without error banners."""
    temp_dir = tempfile.mkdtemp(prefix="agy_test_bg_cmd_")
    try:
        session_id = "test-bg-cmd-session"
        session_dir = os.path.join(temp_dir, session_id)
        logs_dir = os.path.join(session_dir, ".system_generated", "logs")
        os.makedirs(logs_dir, exist_ok=True)
        log_path = os.path.join(logs_dir, "transcript_full.jsonl")

        step0 = {
            "step_index": 6182,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "tool_calls": [
                {
                    "name": "run_command",
                    "args": {"CommandLine": "uv run pytest", "NotificationTimeoutSeconds": 30}
                }
            ],
            "created_at": "2026-08-08T00:35:00Z",
        }
        step1 = {
            "step_index": 6183,
            "source": "MODEL",
            "type": "GENERIC",
            "status": "RUNNING",
            "created_at": "2026-08-08T00:35:02Z",
            "content": "Tool is running as a background task with task id: task-6183\nTask Description: uv run pytest\nTask logs are available at: file:///tmp/task.log",
        }

        with open(log_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(step0) + "\n")
            f.write(json.dumps(step1) + "\n")

        watcher = BrainTranscriptWatcher(session_dir)
        info, events = watcher.poll()

        assert len(events) == 1
        ev = events[0]
        assert ev["step_type"] == "TOOL_CALL"
        assert ev["tool_name"] == "run_command"
        assert ev["state"] == "STATE_RUNNING"
        assert "error" not in ev

        # Also verify that _render_state_banner does NOT produce an error panel
        from agy_watch.tool_renderers import _render_state_banner
        banner = _render_state_banner(ev)
        assert banner is None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_sqlite_authoritative_status_and_token_extraction():
    """Verifies that sibling SQLite conversations database provides authoritative status and exact token metrics."""
    temp_dir = tempfile.mkdtemp(prefix="agy_test_hybrid_")
    try:
        session_id = "test-hybrid-session-9999"
        app_dir = os.path.join(temp_dir, "antigravity")
        session_dir = os.path.join(app_dir, "brain", session_id)
        conv_dir = os.path.join(app_dir, "conversations")
        logs_dir = os.path.join(session_dir, ".system_generated", "logs")

        os.makedirs(logs_dir, exist_ok=True)
        os.makedirs(conv_dir, exist_ok=True)
        log_path = os.path.join(logs_dir, "transcript_full.jsonl")
        conv_db_path = os.path.join(conv_dir, f"{session_id}.db")

        # 1. Create JSONL steps
        step0 = {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "status": "DONE",
            "content": "Perform web research",
            "created_at": "2026-08-08T12:00:00Z",
        }
        step1 = {
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "tool_calls": [{"name": "search_web", "args": {"query": "Antigravity"}}],
            "created_at": "2026-08-08T12:00:05Z",
        }

        with open(log_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(step0) + "\n")
            f.write(json.dumps(step1) + "\n")

        # 2. Create SQLite database with status=7 (cancelled) and encoded protobuf token payload
        import sqlite3
        conn = sqlite3.connect(conv_db_path)
        conn.execute("CREATE TABLE steps (idx integer, step_type integer, status integer, error_details blob, step_payload blob, PRIMARY KEY(idx))")

        # Encode a synthetic protobuf payload with prompt_tokens=1500, candidates_tokens=300, cached_tokens=50000
        # Field 5 (Metadata) -> Field 9 (Usage) -> 1:1500, 2:300, 5:50000
        from google.protobuf.internal import encoder
        usage_buf = bytearray()
        encoder._EncodeVarint(usage_buf.extend, (1 << 3) | 0)
        encoder._EncodeVarint(usage_buf.extend, 1500)
        encoder._EncodeVarint(usage_buf.extend, (2 << 3) | 0)
        encoder._EncodeVarint(usage_buf.extend, 300)
        encoder._EncodeVarint(usage_buf.extend, (5 << 3) | 0)
        encoder._EncodeVarint(usage_buf.extend, 50000)

        meta_buf = bytearray()
        encoder._EncodeVarint(meta_buf.extend, (9 << 3) | 2)
        encoder._EncodeVarint(meta_buf.extend, len(usage_buf))
        meta_buf.extend(usage_buf)

        top_buf = bytearray()
        encoder._EncodeVarint(top_buf.extend, (5 << 3) | 2)
        encoder._EncodeVarint(top_buf.extend, len(meta_buf))
        top_buf.extend(meta_buf)

        # Step 0
        conn.execute("INSERT INTO steps VALUES (0, 14, 3, NULL, NULL)")
        # Step 1 with status 7 (cancelled) and token payload
        err_blob = b'context canceled by user'
        conn.execute("INSERT INTO steps VALUES (1, 15, 7, ?, ?)", (err_blob, bytes(top_buf)))
        conn.commit()
        conn.close()

        watcher = BrainTranscriptWatcher(session_dir)
        info, events = watcher.poll()

        assert info["status"] == "STATE_CANCELLED"
        assert info["is_live"] is False
        assert info["prompt_tokens"] == 1500
        assert info["candidates_tokens"] == 300
        assert info["cached_tokens"] == 50000
        assert info["total_tokens"] == 1800

        assert len(events) == 2
        ev_tool = events[1]
        assert ev_tool["step_type"] == "TOOL_CALL"
        assert ev_tool["state"] == "STATE_CANCELLED"
        assert ev_tool["tokens"] == {
            "prompt_tokens": 1500,
            "candidates_tokens": 300,
            "thoughts_tokens": 0,
            "cached_tokens": 50000,
            "total_tokens": 1800,
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_invoke_subagent_event_ordering_and_nesting():
    """Verifies that invoke_subagent merged tool events inherit the call timestamp and sort before subagent steps."""
    temp_dir = tempfile.mkdtemp(prefix="agy_test_sub_order_")
    try:
        parent_id = "parent-session-1234"
        subagent_id = "child-subagent-5678"
        app_dir = os.path.join(temp_dir, "antigravity")
        parent_dir = os.path.join(app_dir, "brain", parent_id)
        child_dir = os.path.join(app_dir, "brain", subagent_id)

        parent_logs = os.path.join(parent_dir, ".system_generated", "logs")
        child_logs = os.path.join(child_dir, ".system_generated", "logs")
        os.makedirs(parent_logs, exist_ok=True)
        os.makedirs(child_logs, exist_ok=True)

        # 1. Parent transcript
        # Step 0: User prompt (12:00:00)
        # Step 1: Tool call invoke_subagent (12:00:02)
        # Step 2: Tool result for invoke_subagent (12:00:10)
        p_step0 = {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "status": "DONE",
            "content": "Run worker",
            "created_at": "2026-08-08T12:00:00Z",
        }
        p_step1 = {
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": [{"TypeName": "worker", "Role": "Worker", "Prompt": "Do work"}]}}],
            "created_at": "2026-08-08T12:00:02Z",
        }
        p_step2 = {
            "step_index": 2,
            "source": "MODEL",
            "type": "GENERIC",
            "status": "DONE",
            "content": f"Subagent conversation ID: {subagent_id}",
            "created_at": "2026-08-08T12:00:10Z",
        }

        with open(os.path.join(parent_logs, "transcript_full.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(p_step0) + "\n")
            f.write(json.dumps(p_step1) + "\n")
            f.write(json.dumps(p_step2) + "\n")

        # 2. Child transcript (starts at 12:00:05, between invoke call at 12:00:02 and result at 12:00:10)
        c_step0 = {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "status": "DONE",
            "content": "Do work",
            "created_at": "2026-08-08T12:00:05Z",
        }
        c_step1 = {
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "content": "Work done",
            "created_at": "2026-08-08T12:00:08Z",
        }

        with open(os.path.join(child_logs, "transcript_full.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(c_step0) + "\n")
            f.write(json.dumps(c_step1) + "\n")

        watcher = BrainTranscriptWatcher(parent_dir)
        info, events = watcher.poll()

        # Events must be in order:
        # 1. Parent USER_INPUT (12:00:00, step 0)
        # 2. Parent invoke_subagent TOOL_CALL (12:00:02, step 1)
        # 3. Child USER_INPUT (12:00:05, step 0)
        # 4. Child TEXT_RESPONSE (12:00:08, step 1)
        assert len(events) == 4
        assert events[0]["step_index"] == 0 and events[0]["is_main"] is True
        assert events[1]["step_type"] == "TOOL_CALL" and events[1]["tool_name"] == "invoke_subagent"
        assert events[1]["step_index"] == 1 and events[1]["is_main"] is True
        assert events[1]["created_at"] == "2026-08-08T12:00:02Z"

        assert events[2]["is_main"] is False and events[2]["subagent_id"] == subagent_id
        assert events[2]["created_at"] == "2026-08-08T12:00:05Z"
        assert events[3]["is_main"] is False and events[3]["subagent_id"] == subagent_id
        assert events[3]["created_at"] == "2026-08-08T12:00:08Z"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_multimodal_artifact_extraction_and_response_classification():
    """Verifies that user-uploaded pictures/audio are extracted as artifacts and responses with thinking are classified as TEXT_RESPONSE."""
    temp_dir = tempfile.mkdtemp()
    try:
        session_id = "test-multimodal-session"
        session_dir = os.path.join(temp_dir, session_id)
        logs_dir = os.path.join(session_dir, ".system_generated", "logs")
        upload_dir = os.path.join(session_dir, ".user_uploaded")
        os.makedirs(logs_dir, exist_ok=True)
        os.makedirs(upload_dir, exist_ok=True)

        # Create dummy user uploaded image and audio files on disk
        img_path = os.path.join(upload_dir, "media_1786234356702.png")
        with open(img_path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\nfake_image_bytes")

        audio_path = os.path.join(upload_dir, "media_1786234356703.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"ID3\x03\x00\x00\x00\x00fake_audio_bytes")

        transcript_path = os.path.join(logs_dir, "transcript_full.jsonl")
        with open(transcript_path, "w", encoding="utf-8") as f:
            # 1. USER_INPUT with uploaded picture path in prompt
            step0 = {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "status": "DONE",
                "content": f"<USER_REQUEST>\nCheck this diagram\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\n- {img_path}\n- {audio_path}\n</ADDITIONAL_METADATA>",
                "created_at": "2026-08-08T12:00:00Z",
            }
            # 2. PLANNER_RESPONSE with both content (user-facing markdown) AND thinking
            step1 = {
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "content": "### Diagram Analysis\nHere is the answer to your question.",
                "thinking": "**Analyzing image**\nI see the architecture diagram.",
                "created_at": "2026-08-08T12:00:05Z",
            }
            # 3. PLANNER_RESPONSE with ONLY thinking (no content)
            step2 = {
                "step_index": 2,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "content": "",
                "thinking": "**Still computing**",
                "created_at": "2026-08-08T12:00:10Z",
            }
            # 4. PLANNER_RESPONSE with empty content AND empty thinking (terminal stop-turn)
            step3 = {
                "step_index": 3,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "content": "",
                "thinking": "",
                "created_at": "2026-08-08T12:00:15Z",
            }
            f.write(json.dumps(step0) + "\n")
            f.write(json.dumps(step1) + "\n")
            f.write(json.dumps(step2) + "\n")
            f.write(json.dumps(step3) + "\n")

        watcher = BrainTranscriptWatcher(session_dir)
        info, events = watcher.poll()

        assert len(events) == 4

        # Check step 0 (USER_INPUT with artifacts extracted)
        ev0 = events[0]
        assert ev0["step_type"] == "USER_INPUT"
        assert len(ev0["artifacts"]) == 2
        art_types = {a["type"]: a for a in ev0["artifacts"]}
        assert "image" in art_types
        assert art_types["image"]["filename"] == "media_1786234356702.png"
        assert art_types["image"]["exists"] is True
        assert "audio" in art_types
        assert art_types["audio"]["filename"] == "media_1786234356703.mp3"
        assert art_types["audio"]["exists"] is True

        # Check step 1 (PLANNER_RESPONSE with content + thinking -> TEXT_RESPONSE)
        ev1 = events[1]
        assert ev1["step_type"] == "TEXT_RESPONSE"
        assert "Diagram Analysis" in ev1["text"]
        assert "Analyzing image" in ev1["thinking"]

        # Check step 2 (PLANNER_RESPONSE with only thinking -> MODEL_REASONING)
        ev2 = events[2]
        assert ev2["step_type"] == "MODEL_REASONING"
        assert "Still computing" in ev2["thinking"]

        # Check step 3 (PLANNER_RESPONSE with empty content and empty thinking -> TEXT_RESPONSE)
        ev3 = events[3]
        assert ev3["step_type"] == "TEXT_RESPONSE"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_discover_gemini_brain_sessions_authoritative_status():
    """Verifies that discover_gemini_brain_sessions retrieves exact SQLite status for cancelled/interrupted sessions."""
    temp_dir = tempfile.mkdtemp()
    try:
        app_dir = os.path.join(temp_dir, "antigravity")
        brain_dir = os.path.join(app_dir, "brain")
        conv_dir = os.path.join(app_dir, "conversations")
        os.makedirs(brain_dir, exist_ok=True)
        os.makedirs(conv_dir, exist_ok=True)

        session_id = "interrupted-app-session-1234"
        session_dir = os.path.join(brain_dir, session_id)
        logs_dir = os.path.join(session_dir, ".system_generated", "logs")
        os.makedirs(logs_dir, exist_ok=True)

        # Write dummy transcript
        with open(os.path.join(logs_dir, "transcript_full.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "hello"}) + "\n")

        # Create sibling conversations/<session_id>.db with status=7 (cancelled/interrupted)
        conv_db_path = os.path.join(conv_dir, f"{session_id}.db")
        conn = sqlite3.connect(conv_db_path)
        conn.execute("CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER, error_details BLOB);")
        conn.execute("INSERT INTO steps (idx, step_type, status, error_details) VALUES (0, 14, 3, NULL);")
        conn.execute("INSERT INTO steps (idx, step_type, status, error_details) VALUES (1, 15, 7, 'context canceled by user');")
        conn.commit()
        conn.close()

        from agy_watch.brain_watcher import discover_gemini_brain_sessions
        sessions = discover_gemini_brain_sessions(gemini_root=temp_dir)

        assert len(sessions) == 1
        s = sessions[0]
        assert s["session_id"] == session_id
        assert s["status"] == "STATE_CANCELLED"
        assert s["is_live"] is False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


