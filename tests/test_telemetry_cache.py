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

"""Unit tests for SessionTelemetryCache and SQLite telemetry streaming."""

import os
import json
import tempfile
import shutil
import pytest
from agy_watch.telemetry_cache import SessionTelemetryCache
from agy_watch.brain_watcher import BrainTranscriptWatcher


@pytest.fixture
def temp_brain_session():
    temp_dir = tempfile.mkdtemp(prefix="test_brain_cache_")
    session_id = "test-session-uuid-1234"
    session_dir = os.path.join(temp_dir, "brain", session_id)
    logs_dir = os.path.join(session_dir, ".system_generated", "logs")
    os.makedirs(logs_dir, exist_ok=True)

    log_path = os.path.join(logs_dir, "transcript_full.jsonl")

    # Generate 300 steps
    with open(log_path, "w", encoding="utf-8") as f:
        for i in range(300):
            if i % 2 == 0:
                step = {
                    "step_index": i,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "content": f"Response step {i}",
                    "created_at": f"2026-08-10T10:00:{i%60:02d}Z",
                }
            else:
                step = {
                    "step_index": i,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "content": "",
                    "thinking": f"Thinking at step {i}",
                    "tool_calls": [{
                        "name": "search_web",
                        "args": {"query": f"search item {i}", "toolAction": "Searching"},
                    }],
                    "created_at": f"2026-08-10T10:00:{i%60:02d}Z",
                }
            f.write(json.dumps(step) + "\n")

    yield session_dir, log_path
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_session_telemetry_cache_init_and_sync(temp_brain_session):
    session_dir, log_path = temp_brain_session
    cache = SessionTelemetryCache(session_dir)

    # 1. Initial sync
    count = cache.sync_jsonl(log_path, is_main=True)
    assert count == 300
    assert cache.get_total_count() == 300

    # 2. Second sync with no changes should be instant 0 new events
    count_second = cache.sync_jsonl(log_path, is_main=True)
    assert count_second == 0


def test_session_telemetry_cache_strict_windowing(temp_brain_session):
    session_dir, log_path = temp_brain_session
    cache = SessionTelemetryCache(session_dir)
    cache.sync_jsonl(log_path, is_main=True)

    # 1. Default latest window of 150 events
    win = cache.get_window(limit=150)
    assert len(win["events"]) == 150
    assert win["total_count"] == 300
    assert win["has_earlier"] is True
    assert win["has_later"] is False
    assert win["min_step_index"] == 150
    assert win["max_step_index"] == 299

    # 2. Previous window before step 150
    prev_win = cache.get_window(limit=150, before_step_index=win["min_step_index"])
    assert len(prev_win["events"]) == 150
    assert prev_win["has_earlier"] is False
    assert prev_win["has_later"] is True
    assert prev_win["min_step_index"] == 0
    assert prev_win["max_step_index"] == 149

    # 3. Next window after step 149
    next_win = cache.get_window(limit=150, after_step_index=prev_win["max_step_index"])
    assert len(next_win["events"]) == 150
    assert next_win["min_step_index"] == 150
    assert next_win["max_step_index"] == 299


def test_session_telemetry_cache_center_on_step_index(temp_brain_session):
    session_dir, log_path = temp_brain_session
    cache = SessionTelemetryCache(session_dir)
    cache.sync_jsonl(log_path, is_main=True)

    # Center window around step 100 with limit 50
    win = cache.get_window(limit=50, center_on_step_index=100)
    assert len(win["events"]) == 50
    assert win["min_step_index"] <= 100 <= win["max_step_index"]
    assert win["has_earlier"] is True
    assert win["has_later"] is True


def test_brain_watcher_telemetry_cache_integration(temp_brain_session):
    session_dir, _ = temp_brain_session
    watcher = BrainTranscriptWatcher(session_dir)
    info, events = watcher.poll()

    assert info["step_count"] >= 300
    assert len(events) >= 150

    # Query window directly from watcher
    win = watcher.get_window(limit=150)
    assert len(win["events"]) == 150
    assert win["total_count"] == 300
