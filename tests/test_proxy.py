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

"""Tests for universal agy-harness-proxy binary, protobuf serialization, and WebSocket proxying."""

import os
import json
import shutil
import tempfile
import asyncio
import pytest
import websockets

from agy_watch.proxy import (
    parse_protobuf_output_config,
    build_protobuf_output_config,
    find_real_localharness,
    HarnessWebSocketProxy,
    run_proxy_server,
)
from agy_watch.registry import GlobalRegistry
from agy_watch.watcher import SessionWatcher


def test_protobuf_output_config_roundtrip():
    """Verifies that OutputConfig protobuf encoding and decoding are bit-exact."""
    encoded = build_protobuf_output_config(port=54321, api_key="secret-session-key-999")
    port, api_key = parse_protobuf_output_config(encoded)
    assert port == 54321
    assert api_key == "secret-session-key-999"


def test_find_real_localharness_with_override():
    """Verifies that REAL_ANTIGRAVITY_HARNESS_PATH override is honored."""
    temp_dir = tempfile.mkdtemp(prefix="agy_harness_test_")
    try:
        mock_binary = os.path.join(temp_dir, "localharness")
        with open(mock_binary, "w") as f:
            f.write("#!/bin/sh\necho mock\n")
        os.chmod(mock_binary, 0o755)

        os.environ["REAL_ANTIGRAVITY_HARNESS_PATH"] = mock_binary
        found = find_real_localharness()
        assert found == os.path.abspath(mock_binary)
    finally:
        os.environ.pop("REAL_ANTIGRAVITY_HARNESS_PATH", None)
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_websocket_proxy_recording_and_forwarding():
    """Verifies that HarnessWebSocketProxy forwards frames and records into wire_tap.db."""
    temp_dir = tempfile.mkdtemp(prefix="agy_proxy_ws_test_")
    try:
        reg_db = os.path.join(temp_dir, "registry.db")
        registry = GlobalRegistry(db_path=reg_db)
        import agy_watch.registry as reg_module
        reg_module._default_registry = registry

        # 1. Start a mock upstream "real localharness" server
        received_from_client = []
        async def mock_harness_handler(ws):
            async for msg in ws:
                received_from_client.append(json.loads(msg))
                # Send mock initialize and step update back to client
                await ws.send(json.dumps({"initializeConversationResponse": {"cascadeId": "proxy_cas_001"}}))
                await ws.send(json.dumps({
                    "stepUpdate": {
                        "trajectoryId": "proxy_traj_001",
                        "stepIndex": 1,
                        "text": "Hello through universal proxy!",
                        "state": "STATE_DONE",
                    }
                }))

        mock_harness_server = await websockets.serve(mock_harness_handler, "127.0.0.1", 0)
        real_port = mock_harness_server.sockets[0].getsockname()[1]

        # 2. Start HarnessWebSocketProxy pointing to mock upstream
        proxy_server, proxy_port, proxy_handler = await run_proxy_server(
            real_port=real_port,
            api_key="test-proxy-api-key",
            save_dir=temp_dir,
        )

        # 3. Simulate an Agent connecting to the proxy port
        client_received = []
        async with websockets.connect(f"ws://127.0.0.1:{proxy_port}/") as client_ws:
            # Send prompt from agent
            await client_ws.send(json.dumps({"userInput": "Test prompt via universal proxy"}))
            # Read responses
            async for msg in client_ws:
                client_received.append(json.loads(msg))
                if len(client_received) == 2:
                    break

        await asyncio.sleep(0.1)

        # 4. Verify mock harness received the client prompt
        assert len(received_from_client) == 1
        assert received_from_client[0]["userInput"] == "Test prompt via universal proxy"

        # 5. Verify client received both responses
        assert len(client_received) == 2
        assert "initializeConversationResponse" in client_received[0]
        assert client_received[1]["stepUpdate"]["text"] == "Hello through universal proxy!"

        # 6. Verify WireTapDB recorded both directions
        db_path = os.path.join(temp_dir, ".trajectories", "wire_tap.db")
        assert os.path.exists(db_path)
        watcher = SessionWatcher(db_path=db_path)
        info, events = watcher.poll()

        assert len(events) >= 2
        assert any(e["step_type"] == "USER_INPUT" and e.get("prompt") == "Test prompt via universal proxy" for e in events)
        assert any(e["step_type"] == "TEXT_RESPONSE" and e.get("text") == "Hello through universal proxy!" for e in events)

        # Clean up servers
        proxy_server.close()
        mock_harness_server.close()
        await proxy_server.wait_closed()
        await mock_harness_server.wait_closed()

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
