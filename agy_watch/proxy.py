#!/usr/bin/env python3
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

"""Universal Binary Proxy Shim for Antigravity localharness.

Serves as an executable replacement for ANTIGRAVITY_HARNESS_PATH across Python, Node.js, Go,
Rust, and other language runtimes. Intercepts the stdin/stdout handshake, allocates a local
ephemeral proxy WebSocket, records all frames into wire_tap.db & global registry, and passes
traffic transparently to the real localharness Go binary.
"""

import os
import sys
import json
import struct
import shutil
import asyncio
import logging
import platform
import subprocess
from typing import Optional, Tuple, Any

import websockets

from agy_watch.wire_tap import WireTapDB, BlobStore
from agy_watch.registry import get_global_registry

logger = logging.getLogger("agy_watch.proxy")


def find_real_localharness() -> str:
    """Discovers the real localharness binary on disk without touching or renaming it."""
    # 1. Check explicit environment override
    if override := os.environ.get("REAL_ANTIGRAVITY_HARNESS_PATH"):
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return os.path.abspath(override)

    # 2. Check within installed google-antigravity package in current environment
    try:
        import importlib.resources
        suffix = "bin/localharness.exe" if sys.platform == "win32" else "bin/localharness"
        cand = str(importlib.resources.files("google.antigravity").joinpath(suffix))
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return os.path.abspath(cand)
    except Exception:
        pass

    # 3. Check system PATH, taking care to skip this script itself
    current_script = os.path.abspath(__file__)
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        candidate = os.path.join(directory, "localharness.exe" if sys.platform == "win32" else "localharness")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            try:
                if os.path.samefile(candidate, current_script):
                    continue
            except Exception:
                pass
            return os.path.abspath(candidate)

    # 4. Check common cache locations
    home = os.path.expanduser("~")
    common_locations = [
        os.path.join(home, ".local", "share", "antigravity", "bin", "localharness"),
        os.path.join(home, ".cache", "antigravity", "bin", "localharness"),
    ]
    for loc in common_locations:
        if os.path.isfile(loc) and os.access(loc, os.X_OK):
            return loc

    raise RuntimeError(
        "Could not locate the real localharness binary.\n"
        "Please set REAL_ANTIGRAVITY_HARNESS_PATH=/path/to/localharness"
    )


def parse_protobuf_output_config(raw_bytes: bytes) -> Tuple[int, str]:
    """Parses OutputConfig protobuf bytes returning (port, api_key)."""
    try:
        from google.antigravity.proto import localharness_pb2
        cfg = localharness_pb2.OutputConfig()
        cfg.ParseFromString(raw_bytes)
        return cfg.port, cfg.api_key
    except Exception:
        # Fallback simple protobuf parser for OutputConfig:
        # field 1 (port): varint (tag 0x08)
        # field 2 (api_key): length-delimited string (tag 0x12)
        port = 0
        api_key = ""
        idx = 0
        while idx < len(raw_bytes):
            tag = raw_bytes[idx]
            idx += 1
            wire_type = tag & 0x07
            field_num = tag >> 3
            if field_num == 1 and wire_type == 0:  # varint port
                val = 0
                shift = 0
                while idx < len(raw_bytes):
                    b = raw_bytes[idx]
                    idx += 1
                    val |= (b & 0x7F) << shift
                    if not (b & 0x80):
                        break
                    shift += 7
                port = val
            elif field_num == 2 and wire_type == 2:  # string api_key
                length = 0
                shift = 0
                while idx < len(raw_bytes):
                    b = raw_bytes[idx]
                    idx += 1
                    length |= (b & 0x7F) << shift
                    if not (b & 0x80):
                        break
                    shift += 7
                api_key = raw_bytes[idx : idx + length].decode("utf-8", errors="replace")
                idx += length
            else:
                break
        return port, api_key


def build_protobuf_output_config(port: int, api_key: str) -> bytes:
    """Serializes OutputConfig(port, api_key) to protobuf bytes."""
    try:
        from google.antigravity.proto import localharness_pb2
        cfg = localharness_pb2.OutputConfig(port=port, api_key=api_key)
        return cfg.SerializeToString()
    except Exception:
        # Pure Python protobuf serializer
        # Field 1: tag 0x08 (field 1, wire 0)
        port_bytes = bytearray([0x08])
        p = port
        while p > 0x7F:
            port_bytes.append((p & 0x7F) | 0x80)
            p >>= 7
        port_bytes.append(p & 0x7F)

        # Field 2: tag 0x12 (field 2, wire 2)
        key_encoded = api_key.encode("utf-8")
        key_bytes = bytearray([0x12])
        klen = len(key_encoded)
        while klen > 0x7F:
            key_bytes.append((klen & 0x7F) | 0x80)
            klen >>= 7
        key_bytes.append(klen & 0x7F)
        key_bytes.extend(key_encoded)

        return bytes(port_bytes + key_bytes)


def extract_storage_directory(input_bytes: bytes) -> Optional[str]:
    """Attempts to extract storage_directory from InputConfig protobuf."""
    try:
        from google.antigravity.proto import localharness_pb2
        cfg = localharness_pb2.InputConfig()
        cfg.ParseFromString(input_bytes)
        if cfg.storage_directory:
            return cfg.storage_directory
    except Exception:
        pass
    return None


class HarnessWebSocketProxy:
    """Bidirectional WebSocket proxy recording all IPC messages to WireTapDB."""

    def __init__(self, real_port: int, api_key: str, save_dir: Optional[str] = None):
        self.real_port = real_port
        self.api_key = api_key
        self.save_dir = save_dir or os.path.expanduser(f"~/.antigravity/samples/agy_watch/workspaces/session_{os.getpid()}")

        trajectories_dir = os.path.join(self.save_dir, ".trajectories")
        blobs_dir = os.path.join(trajectories_dir, "blobs")
        db_path = os.path.join(trajectories_dir, "wire_tap.db")

        self.blob_store = BlobStore(blobs_dir=blobs_dir)
        self.db = WireTapDB(db_path=db_path, blob_store=self.blob_store)
        self.registry = get_global_registry()
        self.session_id: Optional[str] = None
        self.cascade_id: Optional[str] = None

    async def handle_client(self, client_ws: Any) -> None:
        """Handles connection from Python/Go/Node agent and proxies to real localharness."""
        upstream_url = f"ws://127.0.0.1:{self.real_port}/"
        headers = {"x-goog-api-key": self.api_key}

        async with websockets.connect(upstream_url, additional_headers=headers, max_size=None) as upstream_ws:
            async def forward_client_to_upstream():
                try:
                    async for message in client_ws:
                        # Record outbound message
                        if isinstance(message, (str, bytes)):
                            try:
                                payload = json.loads(message) if isinstance(message, str) else json.loads(message.decode("utf-8"))
                                self.db.record_outbound(payload)
                            except Exception:
                                pass
                        await upstream_ws.send(message)
                except Exception:
                    pass

            async def forward_upstream_to_client():
                try:
                    async for message in upstream_ws:
                        # Record inbound message
                        if isinstance(message, (str, bytes)):
                            try:
                                payload = json.loads(message) if isinstance(message, str) else json.loads(message.decode("utf-8"))
                                sid, cid = self.db.record_inbound(payload)
                                if sid and not self.session_id:
                                    self.session_id = sid
                                    self.cascade_id = cid
                                    self.registry.register_or_update({
                                        "session_id": sid,
                                        "cascade_id": cid or sid,
                                        "workspace_dir": self.save_dir,
                                        "db_path": self.db.db_path,
                                        "blobs_dir": self.blob_store.blobs_dir,
                                        "status": "STATE_ACTIVE",
                                        "pid": os.getpid(),
                                    })
                            except Exception:
                                pass
                        await client_ws.send(message)
                except Exception:
                    pass

            await asyncio.gather(
                forward_client_to_upstream(),
                forward_upstream_to_client(),
                return_exceptions=True,
            )

        # Mark session as done
        if self.session_id:
            self.registry.register_or_update({
                "session_id": self.session_id,
                "status": "STATE_DONE",
            })


async def run_proxy_server(real_port: int, api_key: str, save_dir: Optional[str]) -> Tuple[Any, int, HarnessWebSocketProxy]:
    """Starts the ephemeral proxy WebSocket server."""
    proxy_handler = HarnessWebSocketProxy(real_port=real_port, api_key=api_key, save_dir=save_dir)
    server = await websockets.serve(
        proxy_handler.handle_client,
        host="127.0.0.1",
        port=0,  # OS allocates unused ephemeral port
        max_size=None,
    )
    proxy_port = server.sockets[0].getsockname()[1]
    return server, proxy_port, proxy_handler


def main() -> None:
    """Main CLI entrypoint for agy-harness-proxy binary."""
    # 1. Read InputConfig length + payload from caller's stdin
    raw_len = sys.stdin.buffer.read(4)
    if not raw_len or len(raw_len) < 4:
        sys.stderr.write("agy-harness-proxy: failed to read InputConfig length from stdin\n")
        sys.exit(1)

    in_length = struct.unpack("<I", raw_len)[0]
    input_bytes = sys.stdin.buffer.read(in_length)
    save_dir = extract_storage_directory(input_bytes)

    # 2. Locate and spawn the real localharness binary
    real_binary = find_real_localharness()

    real_process = subprocess.Popen(
        [real_binary],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,  # Forward stderr directly
    )

    # 3. Forward InputConfig to real localharness stdin
    assert real_process.stdin is not None
    assert real_process.stdout is not None

    real_process.stdin.write(struct.pack("<I", len(input_bytes)) + input_bytes)
    real_process.stdin.flush()

    # 4. Read OutputConfig from real localharness stdout
    raw_out_len = real_process.stdout.read(4)
    if not raw_out_len or len(raw_out_len) < 4:
        sys.stderr.write("agy-harness-proxy: failed to read OutputConfig from real localharness\n")
        real_process.kill()
        sys.exit(1)

    out_length = struct.unpack("<I", raw_out_len)[0]
    out_bytes = real_process.stdout.read(out_length)
    real_port, api_key = parse_protobuf_output_config(out_bytes)

    if not real_port:
        sys.stderr.write("agy-harness-proxy: failed to parse port from real localharness\n")
        real_process.kill()
        sys.exit(1)

    # 5. Start WebSocket proxy server in asyncio event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    server, proxy_port, proxy_obj = loop.run_until_complete(
        run_proxy_server(real_port=real_port, api_key=api_key, save_dir=save_dir)
    )

    # 6. Emit rewritten OutputConfig with proxy_port to caller's stdout
    new_out_bytes = build_protobuf_output_config(port=proxy_port, api_key=api_key)
    sys.stdout.buffer.write(struct.pack("<I", len(new_out_bytes)) + new_out_bytes)
    sys.stdout.buffer.flush()

    # 7. Run proxy event loop until real process exits or server terminates
    try:
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        server.close()
        loop.run_until_complete(server.wait_closed())
        if real_process.poll() is None:
            real_process.terminate()
            try:
                real_process.wait(timeout=2)
            except Exception:
                real_process.kill()


if __name__ == "__main__":
    main()
