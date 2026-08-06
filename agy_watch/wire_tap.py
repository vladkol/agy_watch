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

"""Bidirectional Wire Tap and Content-Addressable Storage (CAS) for Antigravity Agent & Localharness.

Captures all inbound and outbound WebSocket IPC frames between the Python SDK and localharness,
storing structured event timelines in SQLite and offloading large files (videos, images, large tool outputs)
to a content-addressable blob directory.
"""

import os
import time
import json
import glob
import re
import hashlib
import sqlite3
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("agy_watch.wire_tap")

DEFAULT_BLOB_THRESHOLD_BYTES = 64 * 1024  # 64 KB


class BlobStore:
    """Content-Addressable Storage (CAS) for large files and binary payloads."""

    def __init__(self, blobs_dir: str, threshold_bytes: int = DEFAULT_BLOB_THRESHOLD_BYTES):
        self.blobs_dir = os.path.abspath(blobs_dir)
        self.threshold_bytes = threshold_bytes
        os.makedirs(self.blobs_dir, exist_ok=True)

    def store_bytes(
        self,
        data: bytes,
        mime_type: str = "application/octet-stream",
        filename: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Stores bytes in content-addressable storage and returns a reference dict."""
        sha256 = hashlib.sha256(data).hexdigest()
        prefix_dir = os.path.join(self.blobs_dir, sha256[:2])
        os.makedirs(prefix_dir, exist_ok=True)

        ext = ""
        if filename and "." in filename:
            ext = "." + filename.rsplit(".", 1)[1].lower()
        elif "video" in mime_type:
            ext = ".mp4"
        elif "image" in mime_type:
            ext = ".png"

        blob_path = os.path.join(prefix_dir, f"{sha256}{ext}")
        if not os.path.exists(blob_path):
            with open(blob_path, "wb") as f:
                f.write(data)

        return {
            "_blob_ref": sha256,
            "size_bytes": len(data),
            "mime_type": mime_type,
            "filename": filename or f"{sha256[:12]}{ext}",
            "file_path": blob_path,
        }

    def maybe_offload(self, val: Any) -> Any:
        """Recursively inspects JSON data structures and offloads large strings/bytes to CAS."""
        if isinstance(val, bytes) and len(val) > self.threshold_bytes:
            return self.store_bytes(val)
        elif isinstance(val, str) and len(val.encode("utf-8")) > self.threshold_bytes:
            data_bytes = val.encode("utf-8")
            mime = "text/plain"
            if val.startswith("data:video/") or "video" in val[:50]:
                mime = "video/mp4"
            elif val.startswith("data:image/") or "image" in val[:50]:
                mime = "image/png"
            blob_info = self.store_bytes(data_bytes, mime_type=mime)
            blob_info["preview"] = val[:200] + "... [OFFLOADED TO BLOB STORAGE]"
            return blob_info
        elif isinstance(val, dict):
            return {k: self.maybe_offload(v) for k, v in val.items()}
        elif isinstance(val, list):
            return [self.maybe_offload(item) for item in val]
        return val

    def get_blob_path(self, sha256_hash: str) -> Optional[str]:
        """Resolves the disk path of a blob by its SHA-256 hash."""
        prefix_dir = os.path.join(self.blobs_dir, sha256_hash[:2])
        if not os.path.exists(prefix_dir):
            return None
        for fname in os.listdir(prefix_dir):
            if fname.startswith(sha256_hash):
                return os.path.join(prefix_dir, fname)
        return None


class WireTapDB:
    """SQLite Database manager for bidirectional wire events."""

    def __init__(self, db_path: str, blob_store: BlobStore):
        self.db_path = os.path.abspath(db_path)
        self.blob_store = blob_store
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_tables()

        # Session metadata cache
        self.session_id: Optional[str] = None
        self.cascade_id: Optional[str] = None
        self.user_title: Optional[str] = None
        self.status: str = "STATE_ACTIVE"
        self.subagents: Set[str] = set()
        self.step_count: int = 0
        self.total_tokens: int = 0
        self.prompt_tokens: int = 0
        self.candidates_tokens: int = 0
        self.thoughts_tokens: int = 0
        self.cached_tokens: int = 0

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_tables(self) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS wire_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seq_num INTEGER,
                timestamp REAL,
                direction TEXT,
                message_type TEXT,
                trajectory_id TEXT,
                step_index INTEGER,
                is_main INTEGER,
                payload_json TEXT
            );
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS session_meta (
                session_id TEXT PRIMARY KEY,
                cascade_id TEXT,
                title TEXT,
                status TEXT,
                total_tokens INTEGER DEFAULT 0,
                prompt_tokens INTEGER DEFAULT 0,
                candidates_tokens INTEGER DEFAULT 0,
                thoughts_tokens INTEGER DEFAULT 0,
                cached_tokens INTEGER DEFAULT 0,
                subagent_count INTEGER DEFAULT 0,
                step_count INTEGER DEFAULT 0,
                updated_at REAL
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wire_events_seq ON wire_events(seq_num);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wire_events_traj ON wire_events(trajectory_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wire_events_type ON wire_events(message_type);")
        conn.close()

    def record_outbound(self, payload: Dict[str, Any]) -> None:
        """Records an outbound message from Python SDK to Localharness."""
        now = time.time()
        seq = payload.get("seqNum") or payload.get("seq_num")

        msg_type = "OUTBOUND"
        traj_id = self.session_id
        step_idx = None

        if "userInput" in payload or "user_input" in payload or "complexUserInput" in payload:
            msg_type = "USER_PROMPT"
            if not self.user_title:
                prompt_text = payload.get("userInput") or payload.get("user_input")
                if not prompt_text and "complexUserInput" in payload:
                    parts = payload["complexUserInput"].get("parts", [])
                    prompt_text = " ".join([p.get("text", "") for p in parts if "text" in p])
                if prompt_text:
                    self.user_title = prompt_text[:80].strip().replace("\n", " ")
        elif "questionResponse" in payload or "question_response" in payload:
            msg_type = "USER_ANSWER"
            qr = payload.get("questionResponse") or payload.get("question_response") or {}
            if qr.get("trajectoryId") or qr.get("trajectory_id"):
                traj_id = qr.get("trajectoryId") or qr.get("trajectory_id")
            if "stepIndex" in qr or "step_index" in qr:
                step_idx = qr.get("stepIndex") if "stepIndex" in qr else qr.get("step_index")
        elif "callHookResponse" in payload or "call_hook_response" in payload:
            msg_type = "POLICY_DECISION"

        offloaded_payload = self.blob_store.maybe_offload(payload)
        payload_json = json.dumps(offloaded_payload)

        conn = self._get_connection()
        with conn:
            conn.execute("""
            INSERT INTO wire_events (seq_num, timestamp, direction, message_type, trajectory_id, step_index, is_main, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (seq, now, "TO_HARNESS", msg_type, traj_id, step_idx, 1, payload_json))
        conn.close()

        self._sync_session_meta()

    def record_inbound(self, payload: Dict[str, Any]) -> None:
        """Records an inbound message from Localharness to Python SDK."""
        now = time.time()
        seq = payload.get("seqNum") or payload.get("seq_num")

        msg_type = "INBOUND"
        traj_id = None
        step_idx = None
        is_main = 1

        if "initializeConversationResponse" in payload:
            msg_type = "INIT_CONVERSATION"
            ic_resp = payload["initializeConversationResponse"]
            self.cascade_id = ic_resp.get("cascadeId") or ic_resp.get("cascade_id")
            if not self.session_id:
                self.session_id = self.cascade_id
        elif "callHookRequest" in payload or "call_hook_request" in payload:
            msg_type = "CALL_HOOK_PRETOOL"
        elif "callHookResponse" in payload or "call_hook_response" in payload:
            msg_type = "CALL_HOOK_RESPONSE"
        elif "trajectoryStateUpdate" in payload or "trajectory_state_update" in payload:
            msg_type = "TRAJECTORY_STATE_UPDATE"

        if "stepUpdate" in payload or "step_update" in payload:
            if msg_type == "INBOUND":
                msg_type = "STEP_UPDATE"
            su = payload.get("stepUpdate") or payload.get("step_update") or {}
            traj_id = su.get("trajectoryId") or su.get("trajectory_id")
            step_idx = su.get("stepIndex") if "stepIndex" in su else su.get("step_index")
            state = su.get("state")
            if traj_id:
                if not self.session_id or self.session_id == self.cascade_id:
                    self.session_id = traj_id

                if traj_id != self.session_id:
                    is_main = 0
                    self.subagents.add(traj_id)
                else:
                    is_main = 1

            if is_main and state:
                self.status = state

            if step_idx is not None and is_main:
                self.step_count = max(self.step_count, step_idx + 1)

            # Check action fields
            for action_key in (
                "invokeSubagent", "invoke_subagent",
                "generateImage", "generate_image",
                "runCommand", "run_command",
                "viewFile", "view_file",
                "createFile", "create_file",
                "editFile", "edit_file",
                "listDirectory", "list_directory",
                "browseUrl", "browse_url",
                "readBrowserPage", "read_browser_page",
                "askQuestion", "ask_question",
            ):
                if action_key in su:
                    msg_type = "TOOL_CALL"
                    break

        def _to_int(v: Any) -> int:
            try:
                return int(v) if v is not None else 0
            except (ValueError, TypeError):
                return 0

        if "usageMetadata" in payload or "usage_metadata" in payload:
            um = payload.get("usageMetadata") or payload.get("usage_metadata") or {}
            self.total_tokens += _to_int(um.get("totalTokenCount") or um.get("total_token_count"))
            self.prompt_tokens += _to_int(um.get("promptTokenCount") or um.get("prompt_token_count"))
            self.candidates_tokens += _to_int(um.get("candidatesTokenCount") or um.get("candidates_token_count"))
            self.thoughts_tokens += _to_int(um.get("thoughtsTokenCount") or um.get("thoughts_token_count"))
            self.cached_tokens += _to_int(um.get("cachedContentTokenCount") or um.get("cached_content_token_count"))

        offloaded_payload = self.blob_store.maybe_offload(payload)
        payload_json = json.dumps(offloaded_payload)

        conn = self._get_connection()
        with conn:
            conn.execute("""
            INSERT INTO wire_events (seq_num, timestamp, direction, message_type, trajectory_id, step_index, is_main, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (seq, now, "FROM_HARNESS", msg_type, traj_id or self.session_id, step_idx, is_main, payload_json))
        conn.close()

        self._sync_session_meta()

    def _sync_session_meta(self) -> None:
        """Updates the session_meta table and registers with the machine-wide global registry."""
        real_sid = self.session_id or self.cascade_id
        sid = real_sid or os.path.splitext(os.path.basename(self.db_path))[0]
        now = time.time()
        title = self.user_title or f"Session {sid[:8]}"

        conn = self._get_connection()
        with conn:
            if sid != "wire_tap":
                conn.execute("DELETE FROM session_meta WHERE session_id = 'wire_tap';")

            conn.execute("""
            INSERT INTO session_meta (session_id, cascade_id, title, status, total_tokens, prompt_tokens,
                                      candidates_tokens, thoughts_tokens, cached_tokens, subagent_count, step_count, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                cascade_id=excluded.cascade_id,
                title=excluded.title,
                status=excluded.status,
                total_tokens=excluded.total_tokens,
                prompt_tokens=excluded.prompt_tokens,
                candidates_tokens=excluded.candidates_tokens,
                thoughts_tokens=excluded.thoughts_tokens,
                cached_tokens=excluded.cached_tokens,
                subagent_count=excluded.subagent_count,
                step_count=excluded.step_count,
                updated_at=excluded.updated_at
            """, (
                sid,
                self.cascade_id,
                title,
                self.status,
                self.total_tokens,
                self.prompt_tokens,
                self.candidates_tokens,
                self.thoughts_tokens,
                self.cached_tokens,
                len(self.subagents),
                self.step_count,
                now,
            ))
        conn.close()

        # Only update global machine-wide registry once real session ID is known
        if real_sid and real_sid != "wire_tap":
            try:
                from agy_watch.registry import get_global_registry
                registry = get_global_registry()
                registry.delete_session("wire_tap")
                workspace_dir = os.path.dirname(os.path.dirname(self.db_path))
                registry.register_or_update({
                    "session_id": real_sid,
                    "cascade_id": self.cascade_id,
                    "title": title,
                    "status": self.status,
                    "workspace_dir": workspace_dir,
                    "db_path": self.db_path,
                    "blobs_dir": self.blob_store.blobs_dir,
                    "pid": os.getpid(),
                    "total_tokens": self.total_tokens,
                    "prompt_tokens": self.prompt_tokens,
                    "candidates_tokens": self.candidates_tokens,
                    "thoughts_tokens": self.thoughts_tokens,
                    "cached_tokens": self.cached_tokens,
                    "subagent_count": len(self.subagents),
                    "step_count": self.step_count,
                })
            except Exception as e:
                logger.debug("Failed to sync session with global registry: %s", e)


class TappedWebSocket:
    """Transparent wrapper around websockets.client.WebSocketClientProtocol that records all messages."""

    def __init__(self, raw_ws: Any, wire_tap_db: WireTapDB):
        self._raw_ws = raw_ws
        self._db = wire_tap_db

    async def send(self, data: Any) -> None:
        try:
            if isinstance(data, (bytes, bytearray)):
                text = data.decode("utf-8", errors="replace")
            else:
                text = str(data)
            payload = json.loads(text)
            self._db.record_outbound(payload)
        except Exception as e:
            logger.debug("WireTap outbound parse error: %s", e)

        await self._raw_ws.send(data)

    async def recv(self) -> Any:
        msg = await self._raw_ws.recv()
        try:
            if isinstance(msg, (bytes, bytearray)):
                text = msg.decode("utf-8", errors="replace")
            else:
                text = str(msg)
            payload = json.loads(text)
            self._db.record_inbound(payload)
        except Exception as e:
            logger.debug("WireTap inbound parse error: %s", e)

        return msg

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.recv()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw_ws, name)


def install_wire_tap(save_dir: Optional[str] = None) -> Tuple[WireTapDB, BlobStore]:
    """Installs the transparent WireTap on the SDK's LocalConnectionStrategy._connect_websocket.

    If save_dir is None, a timestamped directory under ~/.antigravity/samples/agy_watch/workspaces is used.
    """
    from google.antigravity.connections.local import local_connection
    from datetime import datetime

    if not save_dir:
        home = os.path.expanduser("~")
        ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_dir = os.path.join(home, ".antigravity", "samples", "agy_watch", "workspaces", f"session_{ts_str}")
        os.makedirs(save_dir, exist_ok=True)

    trajectories_dir = os.path.join(save_dir, ".trajectories")
    blobs_dir = os.path.join(trajectories_dir, "blobs")
    db_path = os.path.join(trajectories_dir, "wire_tap.db")

    blob_store = BlobStore(blobs_dir=blobs_dir)
    wire_tap_db = WireTapDB(db_path=db_path, blob_store=blob_store)

    orig_connect_ws = local_connection.LocalConnectionStrategy._connect_websocket

    async def _tapped_connect_websocket(self, port: int, api_key: str, process: Any):
        raw_ws, ws_url = await orig_connect_ws(self, port, api_key, process)
        tapped_ws = TappedWebSocket(raw_ws, wire_tap_db)
        return tapped_ws, ws_url

    local_connection.LocalConnectionStrategy._connect_websocket = _tapped_connect_websocket
    return wire_tap_db, blob_store


def extract_event_artifacts(
    ev: Dict[str, Any],
    workspace_dir: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Resolves generated images, markdown files, and media files associated with an event.

    Searches:
    - Active workspace directory
    - Global SDK brain storage:
      ~/.gemini/antigravity/brain/
      ~/.gemini/jetski/brain/
      ~/.antigravity/brain/
    """
    artifacts: List[Dict[str, Any]] = []
    seen_paths: Set[str] = set()

    def add_file_if_valid(p: str, kind: str = "file") -> None:
        if not p:
            return
        clean_path = p.replace("file://", "").strip()
        if not os.path.isabs(clean_path):
            base_dir = workspace_dir or os.getcwd()
            clean_path = os.path.abspath(os.path.join(base_dir, clean_path))

        if clean_path in seen_paths:
            return
        seen_paths.add(clean_path)

        exists = os.path.exists(clean_path)
        size = os.path.getsize(clean_path) if exists else 0

        ext = os.path.splitext(clean_path)[1].lower()
        if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"):
            kind = "image"
        elif ext in (".mp4", ".mov", ".webm", ".mkv"):
            kind = "video"
        elif ext == ".md":
            kind = "markdown"

        artifacts.append({
            "type": kind,
            "path": clean_path,
            "filename": os.path.basename(clean_path),
            "size_bytes": size,
            "exists": exists,
        })

    # 1. Check tool arguments for generateImage / generate_image
    tool_args = ev.get("tool_args") or {}
    tool_name = ev.get("tool_name") or ""
    if tool_name in ("generate_image", "generateImage"):
        img_name = tool_args.get("ImageName") or tool_args.get("imageName") or tool_args.get("image_name") or ""
        valid_exts = (".png", ".jpg", ".jpeg", ".webp")

        # Search roots: SDK brain paths + workspace dirs
        found_matches: List[str] = []

        # 1. First priority: session-specific brain directory
        if session_id:
            for brain_root in (
                os.path.expanduser("~/.gemini/antigravity/brain"),
                os.path.expanduser("~/.gemini/jetski/brain"),
                os.path.expanduser("~/.antigravity/brain"),
            ):
                session_dir = os.path.join(brain_root, session_id)
                if os.path.isdir(session_dir):
                    pattern = os.path.join(session_dir, "**", f"*{img_name}*") if img_name else os.path.join(session_dir, "**", "*")
                    for match in glob.glob(pattern, recursive=True):
                        if os.path.isfile(match) and os.path.splitext(match)[1].lower() in valid_exts:
                            found_matches.append(match)

        # 2. Fallback: search all brain roots + workspace dirs
        if not found_matches:
            search_roots = [
                os.path.expanduser("~/.gemini/antigravity/brain"),
                os.path.expanduser("~/.gemini/jetski/brain"),
                os.path.expanduser("~/.antigravity/brain"),
            ]
            if workspace_dir:
                search_roots.extend([
                    os.path.join(workspace_dir, "brain"),
                    workspace_dir,
                ])

            for root in search_roots:
                if not os.path.isdir(root):
                    continue
                pattern = os.path.join(root, "**", f"*{img_name}*") if img_name else os.path.join(root, "**", "*")
                for match in glob.glob(pattern, recursive=True):
                    if os.path.isfile(match) and os.path.splitext(match)[1].lower() in valid_exts:
                        found_matches.append(match)

        # Sort by newest modification time if multiple found
        if found_matches:
            found_matches.sort(key=os.path.getmtime, reverse=True)
            for m in found_matches[:1]:
                add_file_if_valid(m, "image")

    # 2. Check editFile, createFile, viewFile arguments
    for k in (
        "filePath", "file_path", "filePath", "TargetFile", "targetFile", "target_file",
        "AbsolutePath", "absolutePath", "absolute_path", "path",
    ):
        if k in tool_args and tool_args[k]:
            add_file_if_valid(str(tool_args[k]))

    # 3. Check for markdown image links in payload/text/prompt/thinking
    content_strings = [
        str(ev.get("text") or ""),
        str(ev.get("prompt") or ""),
        str(ev.get("thinking") or ""),
        str(ev.get("payload") or ""),
    ]
    img_pattern = re.compile(r"!\[.*?\]\((file:///)?([^)]+)\)")
    for c in content_strings:
        for match in img_pattern.finditer(c):
            matched_path = match.group(2)
            add_file_if_valid(matched_path, "image")

    return artifacts


def read_trajectory(db_path: str) -> Dict[str, Any]:
    """Reads a wire_tap.db SQLite database and extracts structured execution events."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Trajectory database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT session_id, cascade_id, title, status, total_tokens, prompt_tokens, candidates_tokens, thoughts_tokens, cached_tokens, subagent_count, step_count FROM session_meta ORDER BY updated_at DESC LIMIT 1")
    meta_row = cursor.fetchone()

    fallback_id = os.path.splitext(os.path.basename(db_path))[0]
    workspace_dir = os.path.dirname(os.path.dirname(os.path.abspath(db_path)))
    session_info: Dict[str, Any] = {
        "trajectory_id": (meta_row["session_id"] if meta_row and meta_row["session_id"] else fallback_id),
        "cascade_id": (meta_row["cascade_id"] if meta_row and meta_row["cascade_id"] else fallback_id),
        "db_path": os.path.abspath(db_path),
        "title": (meta_row["title"] if meta_row and meta_row["title"] else f"Session ({fallback_id[:8]})"),
        "status": (meta_row["status"] if meta_row and meta_row["status"] else "STATE_ACTIVE"),
        "total_tokens": meta_row["total_tokens"] if meta_row and meta_row["total_tokens"] is not None else 0,
        "prompt_tokens": meta_row["prompt_tokens"] if meta_row and meta_row["prompt_tokens"] is not None else 0,
        "candidates_tokens": meta_row["candidates_tokens"] if meta_row and meta_row["candidates_tokens"] is not None else 0,
        "thoughts_tokens": meta_row["thoughts_tokens"] if meta_row and meta_row["thoughts_tokens"] is not None else 0,
        "cached_tokens": meta_row["cached_tokens"] if meta_row and meta_row["cached_tokens"] is not None else 0,
        "subagents": [],
        "subagent_count": meta_row["subagent_count"] if meta_row and meta_row["subagent_count"] is not None else 0,
        "step_count": meta_row["step_count"] if meta_row and meta_row["step_count"] is not None else 0,
    }

    cursor.execute("SELECT id, seq_num, timestamp, direction, message_type, trajectory_id, step_index, is_main, payload_json FROM wire_events ORDER BY id ASC")
    rows = cursor.fetchall()

    events = []
    subagents_set = set()
    pending_pretool_args: Dict[str, Any] = {}

    for row in rows:
        direction = row["direction"]
        msg_type = row["message_type"]
        is_main = row["is_main"]
        traj_id = row["trajectory_id"]

        try:
            payload = json.loads(row["payload_json"])
        except Exception:
            payload = {}

        # Buffer PreTool hook arguments
        if "callHookRequest" in payload or "call_hook_request" in payload:
            chr_obj = payload.get("callHookRequest") or payload.get("call_hook_request") or {}
            if chr_obj.get("name") == "PreTool":
                pt_args = chr_obj.get("preToolArgs") or chr_obj.get("pre_tool_args") or {}
                tool_name = pt_args.get("toolName") or pt_args.get("tool_name")
                args_json = pt_args.get("argumentsJson") or pt_args.get("arguments_json")
                if tool_name and args_json:
                    try:
                        pending_pretool_args[tool_name] = json.loads(args_json)
                    except Exception:
                        pending_pretool_args[tool_name] = args_json

        event = {
            "id": row["id"],
            "seq_num": row["seq_num"],
            "timestamp": row["timestamp"],
            "direction": direction,
            "message_type": msg_type,
            "trajectory_id": traj_id,
            "step_index": row["step_index"],
            "is_main": bool(is_main),
            "step_type": "UNKNOWN",
            "prompt": None,
            "text": None,
            "thinking": None,
            "tool_name": None,
            "tool_id": None,
            "tool_args": None,
            "subagent_report": None,
            "subagent_id": traj_id if (not is_main and traj_id) else None,
            "artifacts": [],
            "payload": payload,
        }

        if "toolCall" in payload or "tool_call" in payload:
            tc_obj = payload.get("toolCall") or payload.get("tool_call") or {}
            event["step_type"] = "TOOL_CALL"
            event["tool_name"] = tc_obj.get("name")
            event["tool_id"] = tc_obj.get("id")
            args_json = tc_obj.get("argumentsJson") or tc_obj.get("arguments_json")
            if args_json:
                try:
                    event["tool_args"] = json.loads(args_json)
                except Exception:
                    event["tool_args"] = args_json
            else:
                event["tool_args"] = tc_obj.get("arguments") or {}
            event["artifacts"] = extract_event_artifacts(event, workspace_dir=workspace_dir, session_id=session_info["trajectory_id"])
            events.append(event)
            continue

        if "toolResponse" in payload or "tool_response" in payload:
            tr_obj = payload.get("toolResponse") or payload.get("tool_response") or {}
            event["step_type"] = "TOOL_RESPONSE"
            event["tool_id"] = tr_obj.get("id")
            resp_json = tr_obj.get("responseJson") or tr_obj.get("response_json")
            event["text"] = resp_json or tr_obj.get("response") or ""
            for prev_ev in reversed(events):
                if prev_ev.get("tool_id") == event["tool_id"]:
                    if isinstance(prev_ev.get("payload"), dict):
                        if "stepUpdate" not in prev_ev["payload"] or not isinstance(prev_ev["payload"]["stepUpdate"], dict):
                            prev_ev["payload"]["stepUpdate"] = {}
                        prev_ev["payload"]["stepUpdate"]["responseJson"] = resp_json
                    break
            events.append(event)
            continue

        if direction == "TO_HARNESS":
            if msg_type == "USER_PROMPT":
                event["step_type"] = "USER_INPUT"
                if "userInput" in payload or "user_input" in payload:
                    event["prompt"] = payload.get("userInput") or payload.get("user_input")
                elif "complexUserInput" in payload or "complex_user_input" in payload:
                    cui = payload.get("complexUserInput") or payload.get("complex_user_input") or {}
                    parts = cui.get("parts", [])
                    text_parts = [p.get("text", "") for p in parts if "text" in p]
                    event["prompt"] = " ".join(text_parts)
                else:
                    event["prompt"] = payload.get("prompt") or payload.get("content")
        else:
            su = payload.get("stepUpdate") or payload.get("step_update") or {}
            if su:
                if is_main and su.get("state"):
                    session_info["status"] = su["state"]

                source = su.get("source")
                target = su.get("target")

                if source == "SOURCE_USER" and target == "TARGET_MODEL":
                    event["step_type"] = "SUBAGENT_PROMPT" if not is_main else "USER_INPUT"
                    event["prompt"] = su.get("text") or ""
                elif su.get("text"):
                    event["step_type"] = "TEXT_RESPONSE"
                    event["text"] = su["text"]
                elif su.get("thinking"):
                    event["step_type"] = "MODEL_REASONING"
                    event["thinking"] = su["thinking"]

                if "content" in su and "sender=" in str(su["content"]):
                    event["step_type"] = "SUBAGENT_REPORT"
                    event["subagent_report"] = su["content"]

                # Check action fields
                action_detected = False
                for action_key, action_name in [
                    ("invokeSubagent", "invoke_subagent"),
                    ("invoke_subagent", "invoke_subagent"),
                    ("generateImage", "generate_image"),
                    ("generate_image", "generate_image"),
                    ("runCommand", "run_command"),
                    ("run_command", "run_command"),
                    ("viewFile", "view_file"),
                    ("view_file", "view_file"),
                    ("createFile", "create_file"),
                    ("create_file", "create_file"),
                    ("editFile", "edit_file"),
                    ("edit_file", "edit_file"),
                    ("listDirectory", "list_directory"),
                    ("list_directory", "list_directory"),
                    ("searchDirectory", "search_directory"),
                    ("search_directory", "search_directory"),
                    ("findFile", "find_file"),
                    ("find_file", "find_file"),
                    ("questionsRequest", "ask_question"),
                    ("questions_request", "ask_question"),
                    ("searchWeb", "search_web"),
                    ("search_web", "search_web"),
                    ("readUrlContent", "read_url_content"),
                    ("read_url_content", "read_url_content"),
                    ("finish", "finish"),
                    ("customTool", "custom_tool"),
                    ("custom_tool", "custom_tool"),
                    ("mcpTool", "mcp_tool"),
                    ("mcp_tool", "mcp_tool"),
                ]:
                    if action_key in su:
                        action_detected = True
                        tc_ev = dict(event)
                        tc_ev["step_type"] = "TOOL_CALL"
                        tc_ev["tool_name"] = action_name
                        raw_args = dict(su[action_key]) if isinstance(su[action_key], dict) else {}
                        if action_name in pending_pretool_args:
                            pt_data = pending_pretool_args[action_name]
                            if isinstance(pt_data, dict):
                                merged = dict(pt_data)
                                merged.update(raw_args)
                                raw_args = merged
                        tc_ev["tool_args"] = raw_args
                        if not is_main and traj_id:
                            tc_ev["subagent_id"] = traj_id
                            subagents_set.add(traj_id)
                        tc_ev["artifacts"] = extract_event_artifacts(tc_ev, workspace_dir=workspace_dir, session_id=session_info["trajectory_id"])
                        events.append(tc_ev)
                        break

                if action_detected:
                    continue

                tool_calls = su.get("toolCalls") or su.get("tool_calls")
                if tool_calls and isinstance(tool_calls, list):
                    for tc in tool_calls:
                        tc_ev = dict(event)
                        tc_ev["step_type"] = "TOOL_CALL"
                        tc_ev["tool_name"] = tc.get("name")
                        tc_ev["tool_id"] = tc.get("id")
                        t_args = tc.get("args") or {}
                        if not t_args and tc_ev["tool_name"] in pending_pretool_args:
                            t_args = pending_pretool_args[tc_ev["tool_name"]]
                        tc_ev["tool_args"] = t_args
                        if not is_main and traj_id:
                            tc_ev["subagent_id"] = traj_id
                            subagents_set.add(traj_id)
                        tc_ev["artifacts"] = extract_event_artifacts(tc_ev, workspace_dir=workspace_dir, session_id=session_info["trajectory_id"])
                        events.append(tc_ev)
                    continue

        if not is_main and traj_id:
            subagents_set.add(traj_id)

        event["artifacts"] = extract_event_artifacts(event, workspace_dir=workspace_dir, session_id=session_info["trajectory_id"])
        events.append(event)

    conn.close()
    session_info["subagents"] = list(subagents_set)
    session_info["subagent_count"] = len(subagents_set)

    return {
        "session": session_info,
        "events": events,
    }


def list_trajectories(search_root: str) -> List[Dict[str, Any]]:
    """Searches for all wire_tap.db files under search_root and returns summary metadata."""
    import glob
    search_pattern = os.path.join(search_root, "**", ".trajectories", "wire_tap.db")
    db_files = glob.glob(search_pattern, recursive=True)

    if not db_files:
        db_files = glob.glob(os.path.join(search_root, "**", "wire_tap.db"), recursive=True)

    sessions = []
    for db_path in sorted(db_files, key=os.path.getmtime, reverse=True):
        try:
            data = read_trajectory(db_path)
            sessions.append(data["session"])
        except Exception:
            continue

    return sessions
