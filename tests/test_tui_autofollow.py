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

"""Unit tests for Smart Auto-Follow and Scroll Boundary Detection in AgyWatchApp."""

import os
import json
import time
import tempfile
import shutil
import pytest
from textual.widgets import Tree
from agy_watch.tui import AgyWatchApp
from agy_watch.brain_watcher import BrainTranscriptWatcher


@pytest.mark.asyncio
async def test_tui_smart_autofollow_and_pause_resume():
    temp_dir = tempfile.mkdtemp(prefix="test_tui_follow_")
    try:
        session_id = "test-live-session-1234"
        app_dir = os.path.join(temp_dir, "antigravity")
        session_dir = os.path.join(app_dir, "brain", session_id)
        logs_dir = os.path.join(session_dir, ".system_generated", "logs")
        conv_dir = os.path.join(app_dir, "conversations")
        os.makedirs(logs_dir, exist_ok=True)
        os.makedirs(conv_dir, exist_ok=True)

        log_path = os.path.join(logs_dir, "transcript_full.jsonl")
        conv_db_path = os.path.join(conv_dir, f"{session_id}.db")

        # Initial steps
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "Hello"}) + "\n")
            f.write(json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "content": "Hi step 1"}) + "\n")

        import sqlite3
        conn = sqlite3.connect(conv_db_path)
        conn.execute("CREATE TABLE steps (idx integer, step_type integer, status integer, error_details blob, step_payload blob, PRIMARY KEY(idx))")
        conn.execute("INSERT INTO steps VALUES (0, 14, 2, NULL, NULL)")
        conn.commit()
        conn.close()

        app = AgyWatchApp(brain_root=temp_dir)
        async with app.run_test() as pilot:
            # 1. Attach to the live session
            app.attach_to_session(session_id)
            await pilot.pause()

            tree = app.query_one("#steps-tree", Tree)
            nodes = list(app.step_nodes.values())
            assert len(nodes) == 2

            # Active session on open should auto-follow and select the newest node (step 1)
            assert app.is_following is True
            assert app.selected_event["step_index"] == 1

            # 2. Simulate new streaming event arriving while follow is enabled
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"step_index": 2, "type": "PLANNER_RESPONSE", "content": "Hi step 2"}) + "\n")

            app.poll_live_updates()
            await pilot.pause()

            nodes = list(app.step_nodes.values())
            assert len(nodes) == 3
            assert app.is_following is True
            assert app.selected_event["step_index"] == 2

            # 3. User navigates back to step 0 (scrolling up)
            earlier_node = nodes[0]
            tree.move_cursor(earlier_node)
            await pilot.pause()

            assert app.selected_event["step_index"] == 0
            # Auto-follow must be PAUSED
            assert app.is_following is False

            # 4. Another new event arrives while user is inspecting step 0
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"step_index": 3, "type": "PLANNER_RESPONSE", "content": "Hi step 3"}) + "\n")

            app.poll_live_updates()
            await pilot.pause()

            # The user's selection should NOT be stolen
            assert app.selected_event["step_index"] == 0
            assert app.is_following is False

            # 5. User scrolls all the way back down to the most recent event (step 3)
            latest_node = list(app.step_nodes.values())[-1]
            tree.move_cursor(latest_node)
            await pilot.pause()

            assert app.selected_event["step_index"] == 3
            # Auto-follow must be RESUMED
            assert app.is_following is True
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_tui_session_switching_new_events_jump_to_tail_and_historical_restore():
    """Verifies that returning to a session with new events jumps to tail, while unchanged sessions restore saved historical position."""
    temp_dir = tempfile.mkdtemp(prefix="test_tui_switch_")
    try:
        app_dir = os.path.join(temp_dir, "antigravity")

        # Session A: Historical session with 200 steps
        sess_a = "session-aaa-111"
        sess_a_dir = os.path.join(app_dir, "brain", sess_a)
        logs_a = os.path.join(sess_a_dir, ".system_generated", "logs")
        os.makedirs(logs_a, exist_ok=True)
        sess_a_log = os.path.join(logs_a, "transcript_full.jsonl")
        with open(sess_a_log, "w", encoding="utf-8") as f:
            for i in range(200):
                f.write(json.dumps({"step_index": i, "type": "PLANNER_RESPONSE", "content": f"A step {i}"}) + "\n")
        os.utime(sess_a_log, (time.time() - 3600, time.time() - 3600))

        # Session B: Active session
        sess_b = "session-bbb-222"
        sess_b_dir = os.path.join(app_dir, "brain", sess_b)
        logs_b = os.path.join(sess_b_dir, ".system_generated", "logs")
        os.makedirs(logs_b, exist_ok=True)
        with open(os.path.join(logs_b, "transcript_full.jsonl"), "w", encoding="utf-8") as f:
            for i in range(20):
                f.write(json.dumps({"step_index": i, "type": "PLANNER_RESPONSE", "content": f"B step {i}"}) + "\n")

        app = AgyWatchApp(brain_root=temp_dir)
        async with app.run_test() as pilot:
            # 1. Open Session A -> select historical step 42
            app.attach_to_session(sess_a)
            await pilot.pause()

            tree = app.query_one("#steps-tree", Tree)

            # Save selection at step 42 in Session A
            app.session_selected_keys[sess_a] = ("session-aaa-111", 42, "PLANNER_RESPONSE")

            # 2. Switch to Session B (has 20 steps)
            app.attach_to_session(sess_b)
            await pilot.pause()
            assert app.selected_event["step_index"] == 19
            # User looks at step 5 in Session B
            app.session_selected_keys[sess_b] = ("session-bbb-222", 5, "PLANNER_RESPONSE")
            app.session_last_seen_step_count[sess_b] = 20

            # 3. Switch back to Session A (unchanged historical session)
            # Should center sliding window on step 42 and restore selection!
            app.attach_to_session(sess_a)
            await pilot.pause()
            assert app.selected_event["step_index"] == 42
            assert app.is_following is False

            # 4. Meanwhile, Session B receives new steps 20..50 in background
            with open(os.path.join(logs_b, "transcript_full.jsonl"), "a", encoding="utf-8") as f:
                for i in range(20, 50):
                    f.write(json.dumps({"step_index": i, "type": "PLANNER_RESPONSE", "content": f"B step {i}"}) + "\n")

            # 5. Switch back to Session B
            # Because Session B received new steps (count 50 > last_seen 20), it MUST jump to the new tail (step 49)!
            app.attach_to_session(sess_b)
            await pilot.pause()
            assert app.selected_event["step_index"] == 49
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
