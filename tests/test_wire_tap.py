import os
import shutil
import tempfile
import pytest
from agy_watch.wire_tap import (
    BlobStore,
    WireTapDB,
    install_wire_tap,
    read_trajectory,
    list_trajectories,
)
from examples.yolo_agent.agent import _agent_func


def test_list_trajectories_and_read_fixture():
    """Verifies that list_trajectories discovers and parses wire_tap.db files."""
    temp_dir = tempfile.mkdtemp(prefix="agy_fixture_wiretap_")
    try:
        trajectories_dir = os.path.join(temp_dir, ".trajectories")
        blobs_dir = os.path.join(trajectories_dir, "blobs")
        db_path = os.path.join(trajectories_dir, "wire_tap.db")

        store = BlobStore(blobs_dir=blobs_dir)
        db = WireTapDB(db_path=db_path, blob_store=store)

        # Record a sample session
        db.record_outbound({"userInput": "Hello WireTap from Fixture"})
        db.record_inbound({
            "initializeConversationResponse": {"cascadeId": "fixture_cascade_123"},
            "stepUpdate": {
                "trajectoryId": "fixture_traj_123",
                "stepIndex": 1,
                "text": "Hello User",
                "state": "STATE_DONE",
            },
            "usageMetadata": {
                "promptTokenCount": "100",
                "candidatesTokenCount": "50",
                "totalTokenCount": "150",
            },
        })

        sessions = list_trajectories(temp_dir)
        assert len(sessions) == 1
        assert sessions[0]["trajectory_id"] == "fixture_traj_123"
        assert sessions[0]["cascade_id"] == "fixture_cascade_123"
        assert sessions[0]["status"] == "STATE_DONE"
        assert sessions[0]["total_tokens"] == 150

        data = read_trajectory(db_path)
        assert data["session"]["trajectory_id"] == "fixture_traj_123"
        assert data["session"]["cascade_id"] == "fixture_cascade_123"
        assert len(data["events"]) >= 2
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_read_trajectory_empty_session_meta_fallback():
    """Verifies read_trajectory gracefully handles databases with no session_meta rows."""
    temp_dir = tempfile.mkdtemp(prefix="agy_empty_meta_")
    try:
        db_path = os.path.join(temp_dir, "wire_tap.db")
        store = BlobStore(blobs_dir=os.path.join(temp_dir, "blobs"))
        WireTapDB(db_path=db_path, blob_store=store)  # Initializes empty tables

        data = read_trajectory(db_path)
        session = data["session"]
        assert session["trajectory_id"] == "wire_tap"
        assert session["cascade_id"] == "wire_tap"
        assert session["status"] == "STATE_ACTIVE"
        assert session["total_tokens"] == 0
        assert session["prompt_tokens"] == 0
        assert session["candidates_tokens"] == 0
        assert session["thoughts_tokens"] == 0
        assert session["cached_tokens"] == 0
        assert session["subagent_count"] == 0
        assert session["step_count"] == 0
        assert data["events"] == []
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_blob_store_offloading_and_deduplication():
    """Verifies that BlobStore offloads large files/videos to disk with SHA-256 deduplication."""
    temp_dir = tempfile.mkdtemp(prefix="agy_blob_test_")
    try:
        store = BlobStore(blobs_dir=temp_dir, threshold_bytes=1024)  # 1 KB threshold

        # 1. Small payload (not offloaded)
        small_data = {"text": "hello world", "count": 42}
        offloaded_small = store.maybe_offload(small_data)
        assert offloaded_small == small_data

        # 2. Large mock video payload (> 1 KB)
        large_video_bytes = b"\x00\x00\x00\x18ftypmp42" + b"A" * 5000
        video_blob_info = store.store_bytes(large_video_bytes, mime_type="video/mp4", filename="test_video.mp4")

        assert "_blob_ref" in video_blob_info
        assert video_blob_info["size_bytes"] == len(large_video_bytes)
        assert os.path.exists(video_blob_info["file_path"])
        assert video_blob_info["file_path"].endswith(".mp4")

        # 3. Deduplication: Storing same bytes produces same hash & path
        dup_blob_info = store.store_bytes(large_video_bytes, mime_type="video/mp4")
        assert dup_blob_info["_blob_ref"] == video_blob_info["_blob_ref"]
        assert dup_blob_info["file_path"] == video_blob_info["file_path"]

        # 4. Dictionary with large base64/video string
        large_str_data = {
            "prompt": "Analyze this video",
            "video_base64": "data:video/mp4;base64," + ("B" * 4000),
        }
        offloaded_dict = store.maybe_offload(large_str_data)
        assert offloaded_dict["prompt"] == "Analyze this video"
        assert "_blob_ref" in offloaded_dict["video_base64"]
        assert "preview" in offloaded_dict["video_base64"]

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_wire_tap_end_to_end_recording():
    """Executes a real Agent run with WireTap, verifying full bidirectional capture and session_meta."""
    temp_dir = tempfile.mkdtemp(prefix="agy_wiretap_e2e_")
    try:
        install_wire_tap(temp_dir)
        task_prompt = "Print 'WireTap Verification Successful' and complete the task."
        response_text = await _agent_func(
            workspace_dir=temp_dir,
            task_prompt=task_prompt,
            model="auto",
        )

        assert response_text is not None

        # Verify wire_tap.db was created in .trajectories
        wire_db_path = os.path.join(temp_dir, ".trajectories", "wire_tap.db")
        assert os.path.exists(wire_db_path), f"Expected wire_tap.db at {wire_db_path}"

        data = read_trajectory(wire_db_path)
        session = data["session"]
        events = data["events"]

        assert session["status"] == "STATE_DONE"
        assert session["total_tokens"] > 0
        assert len(events) >= 2

        # Verify bidirectional directions
        directions = {ev["direction"] for ev in events}
        assert "TO_HARNESS" in directions
        assert "FROM_HARNESS" in directions

        # Verify outbound prompt capture
        outbound_prompts = [ev for ev in events if ev["direction"] == "TO_HARNESS" and ev["step_type"] == "USER_INPUT"]
        assert len(outbound_prompts) >= 1
        assert "WireTap Verification" in str(outbound_prompts[0]["prompt"])

        # Verify inbound response capture
        inbound_responses = [ev for ev in events if ev["direction"] == "FROM_HARNESS" and ev["step_type"] == "TEXT_RESPONSE"]
        assert len(inbound_responses) >= 1

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_wire_tap_subagent_tool_visibility():
    """Executes a multi-subagent task and verifies that subagent internal tool calls are captured."""
    temp_dir = tempfile.mkdtemp(prefix="agy_wiretap_subagents_")
    try:
        install_wire_tap(temp_dir)
        task_prompt = (
            "Use start_subagent to create 2 subagents. "
            "Instruct each subagent to run an echo command and report back."
        )
        response_text = await _agent_func(
            workspace_dir=temp_dir,
            task_prompt=task_prompt,
            model="auto",
        )

        assert response_text is not None

        wire_db_path = os.path.join(temp_dir, ".trajectories", "wire_tap.db")
        assert os.path.exists(wire_db_path)

        data = read_trajectory(wire_db_path)
        session = data["session"]
        events = data["events"]

        assert session["status"] == "STATE_DONE"
        assert session["total_tokens"] > 0
        assert session["subagent_count"] >= 2

        # Verify subagent events and tool calls
        subagent_events = [ev for ev in events if not ev["is_main"]]
        assert len(subagent_events) > 0

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
