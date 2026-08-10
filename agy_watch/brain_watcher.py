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

"""Real-time Incremental Gemini Brain Trajectory Watcher for agy_watch.

Reads `.system_generated/logs/transcript_full.jsonl` (and companion summary/message artifacts)
in zero-lock incremental streaming mode, providing real-time turn step normalization,
subagent trajectory discovery, and hierarchical execution tree assembly.
"""

import os
import re
import json
import time
import glob
from typing import Any, Dict, List, Optional, Set, Tuple
from agy_watch.telemetry_cache import SessionTelemetryCache


def is_valid_gemini_app_dir(dir_name: str) -> bool:
    """Filters out standard non-session folders from ~/.gemini."""
    excluded_names = {"config", "history", "policies", "tmp"}
    if dir_name in excluded_names or dir_name.startswith("."):
        return False
    if dir_name.endswith("-browser-profile") or dir_name.endswith("-backup"):
        return False
    return True


def get_all_gemini_brain_dirs(gemini_root: str = "~/.gemini") -> List[str]:
    """Dynamically discovers all valid brain directories across all subdirectories of ~/.gemini."""
    root = os.path.abspath(os.path.expanduser(gemini_root))
    brain_dirs: List[str] = []
    if os.path.isdir(root):
        try:
            for entry in os.listdir(root):
                if is_valid_gemini_app_dir(entry):
                    b_dir = os.path.join(root, entry, "brain")
                    if os.path.isdir(b_dir):
                        brain_dirs.append(b_dir)
        except Exception:
            pass

    # Alternate standalone root if present
    alt_antigravity = os.path.abspath(os.path.expanduser("~/.antigravity/brain"))
    if os.path.isdir(alt_antigravity) and alt_antigravity not in brain_dirs:
        brain_dirs.append(alt_antigravity)

    return brain_dirs


def normalize_source_tag(raw_tag: Optional[str]) -> str:
    """Normalizes internal/harness source tags into public user-facing tags."""
    if not raw_tag:
        return "antigravity"
    return raw_tag.lower()


def _map_transcript_status(status_str: Optional[str]) -> str:
    """Maps transcript JSONL step status string to standard agy_watch state."""
    if not status_str:
        return "STATE_ACTIVE"
    s = str(status_str).upper()
    if s in ("DONE", "COMPLETED", "SUCCESS", "STATE_DONE"):
        return "STATE_DONE"
    elif s in ("RUNNING", "IN_PROGRESS", "PENDING", "STATE_RUNNING", "STATE_ACTIVE"):
        return "STATE_RUNNING"
    elif s in ("WAITING_FOR_INPUT", "WAITING_FOR_USER", "AWAITING_INPUT", "STATE_WAITING_FOR_USER"):
        return "STATE_WAITING_FOR_USER"
    elif s in ("CANCELLED", "CANCELED", "STATE_CANCELLED"):
        return "STATE_CANCELLED"
    elif s in ("ERROR", "FAILED", "STATE_ERROR"):
        return "STATE_ERROR"
    return "STATE_ACTIVE"


def _decode_proto_fields(buffer: bytes) -> Dict[int, List[Any]]:
    """Decodes raw Protobuf wire tags without external schema dependencies."""
    from google.protobuf.internal import decoder
    pos = 0
    length = len(buffer)
    fields: Dict[int, List[Any]] = {}
    while pos < length:
        try:
            tag, pos = decoder._DecodeVarint32(buffer, pos)
        except Exception:
            break
        field_num = tag >> 3
        wire_type = tag & 0x07
        if wire_type == 0:  # Varint
            try:
                val, pos = decoder._DecodeVarint(buffer, pos)
            except Exception:
                break
        elif wire_type == 2:  # Length-delimited
            try:
                size, pos = decoder._DecodeVarint32(buffer, pos)
                if pos + size > length:
                    break
                val = buffer[pos:pos + size]
                pos += size
            except Exception:
                break
        elif wire_type == 1:  # 64-bit
            if pos + 8 > length:
                break
            val = buffer[pos:pos + 8]
            pos += 8
        elif wire_type == 5:  # 32-bit
            if pos + 4 > length:
                break
            val = buffer[pos:pos + 4]
            pos += 4
        else:
            break
        fields.setdefault(field_num, []).append(val)
    return fields


class BrainTranscriptWatcher:
    """Watches an Antigravity Agent/CLI Brain session's transcript_full.jsonl incrementally."""

    def __init__(
        self,
        session_dir: str,
        source_tag: str = "antigravity",
        parent_id: Optional[str] = None,
    ):
        self.session_dir = os.path.abspath(os.path.expanduser(session_dir))
        self.session_id = os.path.basename(self.session_dir)
        self.source_tag = normalize_source_tag(source_tag)
        self.parent_id = parent_id

        # Candidate paths
        full_log = os.path.join(self.session_dir, ".system_generated", "logs", "transcript_full.jsonl")
        short_log = os.path.join(self.session_dir, ".system_generated", "logs", "transcript.jsonl")
        self.log_path = full_log if os.path.exists(full_log) else short_log
        self.summary_path = os.path.join(self.session_dir, ".system_generated", "logs", "summary.json")
        self.short_title_path = os.path.join(self.session_dir, ".system_generated", "logs", "short_title.txt")

        app_dir = os.path.dirname(os.path.dirname(self.session_dir))
        self.conv_db_path = os.path.join(app_dir, "conversations", f"{self.session_id}.db")

        self.file_offset = 0
        self.last_mtime = 0.0
        self.all_events: List[Dict[str, Any]] = []
        self.child_watchers: Dict[str, "BrainTranscriptWatcher"] = {}
        self.known_subagent_ids: Set[str] = set()
        self.pending_tool_call: Optional[Dict[str, Any]] = None

        self.max_scanned_db_idx = -1
        self.step_token_map: Dict[int, Dict[str, int]] = {}
        self.cache = SessionTelemetryCache(self.session_dir, self.session_id)

        self.session_info: Dict[str, Any] = {
            "session_id": self.session_id,
            "cascade_id": self.session_id,
            "title": self._read_initial_title(),
            "status": "STATE_ACTIVE",
            "workspace_dir": self.session_dir,
            "db_path": self.session_dir,  # Maps to session_dir for brain sessions
            "total_tokens": 0,
            "prompt_tokens": 0,
            "candidates_tokens": 0,
            "thoughts_tokens": 0,
            "cached_tokens": 0,
            "subagents": set(),
            "subagent_count": 0,
            "step_count": 0,
            "source_tag": self.source_tag,
            "updated_at": 0.0,
            "is_live": False,
            "session_type": "brain",
        }

    def _read_initial_title(self) -> str:
        """Extracts initial title from summary.json or short_title.txt if present."""
        if os.path.exists(self.summary_path):
            try:
                with open(self.summary_path, "r", encoding="utf-8", errors="replace") as f:
                    d = json.load(f)
                    return d.get("shortTitle") or self.session_id[:8]
            except Exception:
                pass
        if os.path.exists(self.short_title_path):
            try:
                with open(self.short_title_path, "r", encoding="utf-8", errors="replace") as f:
                    return f.read().strip() or self.session_id[:8]
            except Exception:
                pass
        return self.session_id[:8]

    def _refresh_session_meta(self) -> None:
        """Refreshes summary title, modification timestamps, live status, and exact token metrics."""
        # 1. Update title from summary.json if available
        if os.path.exists(self.summary_path):
            try:
                with open(self.summary_path, "r", encoding="utf-8", errors="replace") as f:
                    d = json.load(f)
                    title = d.get("shortTitle")
                    if title:
                        self.session_info["title"] = title
            except Exception:
                pass
        elif os.path.exists(self.short_title_path):
            try:
                with open(self.short_title_path, "r", encoding="utf-8", errors="replace") as f:
                    title = f.read().strip()
                    if title:
                        self.session_info["title"] = title
            except Exception:
                pass

        # 2. Check mtime of log
        if os.path.exists(self.log_path):
            try:
                mtime = os.path.getmtime(self.log_path)
                self.last_mtime = mtime
                self.session_info["updated_at"] = mtime

                now = time.time()
                is_recent = (now - mtime) < 30.0

                # Default liveness from mtime
                if is_recent:
                    self.session_info["is_live"] = True
                    self.session_info["status"] = "STATE_RUNNING"
                else:
                    self.session_info["is_live"] = False
                    if self.session_info["status"] in ("STATE_ACTIVE", "STATE_RUNNING"):
                        self.session_info["status"] = "STATE_DONE"
            except Exception:
                pass

        # 3. Check sibling conversation SQLite database for authoritative lifecycle status & token metrics
        if os.path.exists(self.conv_db_path):
            try:
                import sqlite3
                conn = sqlite3.connect(f"file:{self.conv_db_path}?mode=ro", uri=True)
                cursor = conn.cursor()

                # Authoritative status check
                last_row = cursor.execute("SELECT status, error_details FROM steps ORDER BY idx DESC LIMIT 1").fetchone()
                if last_row:
                    db_status = last_row[0]
                    err_msg = last_row[1].decode("utf-8", errors="ignore") if isinstance(last_row[1], bytes) else str(last_row[1] or "")
                    if db_status == 7 or "cancel" in err_msg.lower():
                        self.session_info["status"] = "STATE_CANCELLED"
                        self.session_info["is_live"] = False
                    elif db_status == 6:
                        self.session_info["status"] = "STATE_ERROR"
                        self.session_info["is_live"] = False
                    elif db_status == 2:
                        self.session_info["status"] = "STATE_RUNNING"
                        self.session_info["is_live"] = True
                    elif db_status == 3:
                        if not is_recent:
                            self.session_info["status"] = "STATE_DONE"
                            self.session_info["is_live"] = False

                # Incremental token extraction from steps
                new_rows = cursor.execute(
                    "SELECT idx, step_payload FROM steps WHERE idx > ? AND status != 5 AND step_payload IS NOT NULL ORDER BY idx ASC",
                    (self.max_scanned_db_idx,),
                ).fetchall()
                for idx, payload in new_rows:
                    if idx > self.max_scanned_db_idx:
                        self.max_scanned_db_idx = idx
                    p = _decode_proto_fields(payload)
                    if 5 in p:
                        for b in p[5]:
                            f5 = _decode_proto_fields(b)
                            if 9 in f5:
                                u = _decode_proto_fields(f5[9][0])
                                pr = u.get(1, [0])[0]
                                ca = u.get(2, [0])[0]
                                th = u.get(3, [0])[0]
                                cd = u.get(5, [0])[0]
                                if pr or ca or th or cd:
                                    self.step_token_map[idx] = {
                                        "prompt_tokens": pr,
                                        "candidates_tokens": ca,
                                        "thoughts_tokens": th,
                                        "cached_tokens": cd,
                                        "total_tokens": pr + ca,
                                    }
                                    self.session_info["prompt_tokens"] += pr
                                    self.session_info["candidates_tokens"] += ca
                                    self.session_info["thoughts_tokens"] += th
                                    self.session_info["cached_tokens"] += cd
                                    self.session_info["total_tokens"] += (pr + ca)
                conn.close()
            except Exception:
                pass

    def _normalize_merged_tool_event(
        self,
        tool_dict: Dict[str, Any],
        result_dict: Dict[str, Any],
        is_main: bool = True,
        sub_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Merges a PLANNER_RESPONSE tool call with its subsequent tool result into a single unified event."""
        # Use tool call invocation step index and start timestamp
        step_idx = tool_dict.get("step_index") if tool_dict.get("step_index") is not None else result_dict.get("step_index")
        result_type = result_dict.get("type", "TOOL_CALL")
        content = result_dict.get("content") or ""
        created_at = tool_dict.get("created_at") or result_dict.get("created_at")
        status_str = result_dict.get("status", "DONE")

        ts_float = time.time()
        if created_at:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                ts_float = dt.timestamp()
            except Exception:
                pass

        tool_name = tool_dict.get("tool_name") or result_type.lower()
        tool_args = tool_dict.get("tool_args") or {}

        event: Dict[str, Any] = {
            "id": len(self.all_events) + 1,
            "seq_num": step_idx,
            "timestamp": ts_float,
            "created_at": created_at,
            "direction": "FROM_HARNESS",
            "message_type": result_type,
            "trajectory_id": sub_id or self.session_id,
            "step_index": step_idx,
            "is_main": is_main,
            "subagent_id": sub_id,
            "step_type": "TOOL_CALL",
            "state": _map_transcript_status(status_str),
            "prompt": "",
            "text": content,
            "thinking": tool_dict.get("thinking") or "",
            "tool_name": tool_name,
            "tool_id": None,
            "tool_args": tool_args,
            "action_type": result_type.lower(),
            "has_tool": True,
            "tool_action": tool_args.get("toolAction") or tool_name,
            "tool_summary": tool_args.get("toolSummary") or (content[:60] if content else ""),
            "subagent_report": None,
            "tokens": self.step_token_map.get(step_idx) or self.step_token_map.get(tool_dict.get("step_index")),
            "artifacts": [],
            "payload": result_dict,
            "child_events": [tool_dict.get("step_dict", {}), result_dict],
        }

        # Resolve structured artifacts
        try:
            from agy_watch.wire_tap import extract_event_artifacts
            event["artifacts"] = extract_event_artifacts(
                event,
                workspace_dir=self.session_dir,
                session_id=self.session_id,
            )
        except Exception:
            event["artifacts"] = []

        return event

    def _normalize_step(self, d: Dict[str, Any], is_main: bool = True, sub_id: Optional[str] = None) -> Dict[str, Any]:
        """Normalizes a raw transcript_full.jsonl step into an agy_watch event."""
        step_idx = d.get("step_index")
        step_type_raw = d.get("type", "GENERIC")
        source = d.get("source", "SYSTEM")
        content = d.get("content") or ""
        thinking = d.get("thinking") or ""
        tool_calls = d.get("tool_calls") or []
        created_at = d.get("created_at")

        # Parse timestamp
        ts_float = time.time()
        if created_at:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                ts_float = dt.timestamp()
            except Exception:
                pass

        event: Dict[str, Any] = {
            "id": len(self.all_events) + 1,
            "seq_num": step_idx,
            "timestamp": ts_float,
            "created_at": created_at,
            "direction": "FROM_HARNESS" if source == "MODEL" else "TO_HARNESS",
            "message_type": step_type_raw,
            "trajectory_id": sub_id or self.session_id,
            "step_index": step_idx,
            "is_main": is_main,
            "subagent_id": sub_id,
            "step_type": step_type_raw,
            "state": _map_transcript_status(d.get("status")),
            "prompt": content if step_type_raw == "USER_INPUT" else "",
            "text": content,
            "thinking": thinking,
            "tool_name": tool_calls[0].get("name") if tool_calls else None,
            "tool_id": None,
            "tool_args": tool_calls[0].get("args") if tool_calls else None,
            "subagent_report": None,
            "tokens": self.step_token_map.get(step_idx),
            "artifacts": [],
            "payload": d,
        }

        # Map step_type to agy_watch standard types
        if step_type_raw == "USER_INPUT":
            event["step_type"] = "USER_INPUT"
            event["prompt"] = content
        elif step_type_raw == "PLANNER_RESPONSE":
            if tool_calls:
                event["step_type"] = "TOOL_CALL"
                event["tool_name"] = tool_calls[0].get("name")
                event["tool_args"] = tool_calls[0].get("args")
                event["action_type"] = tool_calls[0].get("name", "").lower()
                event["has_tool"] = True
                event["tool_action"] = (tool_calls[0].get("args") or {}).get("toolAction") or tool_calls[0].get("name")
                event["tool_summary"] = (tool_calls[0].get("args") or {}).get("toolSummary") or ""
            elif content and content.strip():
                # User-facing text response (even if model reasoning/thinking is also present)
                event["step_type"] = "TEXT_RESPONSE"
            elif thinking and thinking.strip():
                # Intermediate or standalone model reasoning/thinking trace
                event["step_type"] = "MODEL_REASONING"
            else:
                # Completed / terminal end-of-turn response without text body
                event["step_type"] = "TEXT_RESPONSE"
        elif step_type_raw == "ERROR_MESSAGE":
            event["step_type"] = "ERROR_MESSAGE"
            event["state"] = "STATE_ERROR"
            event["error"] = d.get("error") or content
        elif step_type_raw == "CHECKPOINT":
            event["step_type"] = "CHECKPOINT"
        elif step_type_raw == "SYSTEM_MESSAGE":
            # Check for subagent notification
            if "sender=" in content:
                event["step_type"] = "SUBAGENT_NOTIFICATION"
                sender_match = re.search(r"sender=([a-zA-Z0-9_\-]+)", content)
                if sender_match:
                    event["subagent_id"] = sender_match.group(1)
            else:
                event["step_type"] = "SYSTEM_MESSAGE"
        elif step_type_raw in ("EPHEMERAL_MESSAGE", "CONVERSATION_HISTORY", "KNOWLEDGE_ARTIFACTS"):
            event["step_type"] = step_type_raw
        else:
            # Standalone Tool Output Fallback
            event["step_type"] = "TOOL_CALL"
            event["action_type"] = step_type_raw.lower()
            event["has_tool"] = True
            event["tool_action"] = step_type_raw
            event["tool_summary"] = content[:60] if content else ""

        # Resolve structured artifacts (images, markdown, files)
        try:
            from agy_watch.wire_tap import extract_event_artifacts
            event["artifacts"] = extract_event_artifacts(
                event,
                workspace_dir=self.session_dir,
                session_id=self.session_id,
            )
        except Exception:
            event["artifacts"] = []

        return event

    def _discover_child_subagents(self, content: str) -> None:
        """Detects child subagent conversation UUIDs from invoke_subagent return payloads."""
        if not content:
            return
        if "conversation" in content.lower() or "Created the following subagents:" in content:
            found_ids = re.findall(r'(?:conversation[_-]?id|conversation\s+id)[\\"]*:\s*[\\"]*([a-zA-Z0-9_\-]+)', content, re.IGNORECASE)
            for sub_id in found_ids:
                if sub_id != self.session_id and sub_id not in self.known_subagent_ids:
                    self.known_subagent_ids.add(sub_id)
                    self.session_info["subagents"].add(sub_id)
                    self.session_info["subagent_count"] = len(self.session_info["subagents"])

                    # Check if child folder exists in brain/
                    parent_brain = os.path.dirname(self.session_dir)
                    child_dir = os.path.join(parent_brain, sub_id)
                    if os.path.exists(child_dir):
                        child_watcher = BrainTranscriptWatcher(
                            child_dir,
                            source_tag=self.source_tag,
                            parent_id=self.session_id,
                        )
                        self.child_watchers[sub_id] = child_watcher

    def poll(self) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Polls for new events since last poll with paired 2-step tool transaction merging.

        Returns (updated_session_info, new_events_list).
        """
        self._refresh_session_meta()
        new_events: List[Dict[str, Any]] = []

        if not os.path.exists(self.log_path):
            # Try to see if transcript.jsonl exists
            short_log = os.path.join(self.session_dir, ".system_generated", "logs", "transcript.jsonl")
            if os.path.exists(short_log):
                self.log_path = short_log

        if os.path.exists(self.log_path):
            try:
                with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(self.file_offset)
                    for line in f:
                        line_str = line.strip()
                        if not line_str:
                            continue
                        try:
                            d = json.loads(line_str)
                            stype = d.get("type", "")
                            tool_calls = d.get("tool_calls") or []

                            step_idx = d.get("step_index")
                            if step_idx is not None:
                                self.session_info["step_count"] = max(self.session_info["step_count"], step_idx + 1)

                            # Check for subagents in content
                            c = d.get("content") or ""
                            self._discover_child_subagents(c)

                            # 1. Check for PLANNER_RESPONSE with tool calls
                            if stype == "PLANNER_RESPONSE" and tool_calls:
                                if self.pending_tool_call:
                                    # Flush previous pending tool call if any
                                    prev_ev = self._normalize_step(self.pending_tool_call["step_dict"], is_main=True)
                                    new_events.append(prev_ev)

                                thinking = d.get("thinking") or ""
                                content = d.get("content") or ""
                                self.pending_tool_call = {
                                    "tool_name": tool_calls[0].get("name"),
                                    "tool_args": tool_calls[0].get("args") or {},
                                    "thinking": thinking or content,
                                    "step_index": step_idx,
                                    "timestamp": d.get("timestamp"),
                                    "created_at": d.get("created_at"),
                                    "step_dict": d,
                                }
                                continue

                            # 2. Check for tool result matching pending tool call
                            if self.pending_tool_call and stype not in (
                                "USER_INPUT", "PLANNER_RESPONSE", "SYSTEM_MESSAGE",
                                "CHECKPOINT", "EPHEMERAL_MESSAGE", "CONVERSATION_HISTORY", "KNOWLEDGE_ARTIFACTS"
                            ):
                                event = self._normalize_merged_tool_event(
                                    tool_dict=self.pending_tool_call,
                                    result_dict=d,
                                    is_main=True,
                                )
                                self.pending_tool_call = None
                                new_events.append(event)
                                continue

                            # 3. If there was a pending tool call followed by non-tool step (e.g. user input), flush it
                            if self.pending_tool_call:
                                prev_ev = self._normalize_step(self.pending_tool_call["step_dict"], is_main=True)
                                new_events.append(prev_ev)
                                self.pending_tool_call = None

                            event = self._normalize_step(d, is_main=True)
                            new_events.append(event)
                        except Exception:
                            pass
                    self.file_offset = f.tell()
            except Exception:
                pass

        # If a tool call is pending at EOF (in-flight execution), emit as running
        if self.pending_tool_call:
            in_flight = self._normalize_step(self.pending_tool_call["step_dict"], is_main=True)
            in_flight["state"] = "STATE_RUNNING"
            new_events.append(in_flight)

        # Poll child subagent watchers
        for sub_id, child_w in list(self.child_watchers.items()):
            child_info, child_new = child_w.poll()
            for cev in child_new:
                cev["is_main"] = False
                cev["subagent_id"] = sub_id
                new_events.append(cev)

        # Sort all new events chronologically by timestamp
        if new_events:
            new_events.sort(key=lambda e: (e.get("timestamp") or 0.0, e.get("step_index") or 0))
            for idx, ev in enumerate(new_events):
                ev["id"] = len(self.all_events) + 1 + idx
            self.all_events.extend(new_events)
            self.all_events.sort(key=lambda e: (e.get("timestamp") or 0.0, e.get("step_index") or 0))
        self._refresh_session_meta()

        # If session is cancelled or errored, ensure trailing event reflects terminal state
        if self.session_info.get("status") == "STATE_CANCELLED" and self.all_events:
            last_ev = self.all_events[-1]
            last_ev["state"] = "STATE_CANCELLED"
            last_ev["error_message"] = "Session stopped by user."
            if new_events:
                new_events[-1]["state"] = "STATE_CANCELLED"

        # Keep telemetry cache synced in background
        try:
            if os.path.exists(self.log_path):
                self.cache.sync_jsonl(
                    self.log_path,
                    is_main=True,
                    step_token_map=self.step_token_map,
                )
        except Exception:
            pass

        return self.session_info, new_events

    def get_window(
        self,
        limit: int = 150,
        before_step_index: Optional[int] = None,
        after_step_index: Optional[int] = None,
        center_on_step_index: Optional[int] = None,
        subagent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Returns indexed event window from SQLite telemetry cache."""
        return self.cache.get_window(
            limit=limit,
            before_step_index=before_step_index,
            after_step_index=after_step_index,
            center_on_step_index=center_on_step_index,
            subagent_id=subagent_id,
        )

    def get_all_artifacts(self) -> List[Dict[str, Any]]:
        """Returns all distinct artifacts extracted across events from SQLite cache."""
        return self.cache.get_all_artifacts()

    def get_total_count(self) -> int:
        """Returns total number of events recorded in the SQLite telemetry cache."""
        return self.cache.get_total_count()


_conv_db_status_cache: Dict[str, Tuple[float, str, bool]] = {}


def _get_authoritative_brain_session_status(conv_db_path: str, is_recent: bool) -> tuple[str, bool]:
    """Inspects sibling conversations/<session_id>.db to retrieve exact lifecycle status with fast mtime caching."""
    if not os.path.exists(conv_db_path):
        return ("STATE_RUNNING" if is_recent else "STATE_DONE", is_recent)

    try:
        mtime = os.path.getmtime(conv_db_path)
        if conv_db_path in _conv_db_status_cache and _conv_db_status_cache[conv_db_path][0] == mtime:
            return (_conv_db_status_cache[conv_db_path][1], _conv_db_status_cache[conv_db_path][2])

        import sqlite3
        conn = sqlite3.connect(f"file:{conv_db_path}?mode=ro", uri=True)
        row = conn.execute("SELECT status, error_details FROM steps ORDER BY idx DESC LIMIT 1").fetchone()
        conn.close()
        res = ("STATE_RUNNING" if is_recent else "STATE_DONE", is_recent)
        if row:
            db_status = row[0]
            db_err = row[1].decode("utf-8", errors="ignore") if isinstance(row[1], bytes) else str(row[1] or "")
            if db_status == 7 or "cancel" in db_err.lower():
                res = ("STATE_CANCELLED", False)
            elif db_status == 6:
                res = ("STATE_ERROR", False)
            elif db_status == 2:
                res = ("STATE_RUNNING", True)
            elif db_status == 3:
                res = ("STATE_DONE", False)

        _conv_db_status_cache[conv_db_path] = (mtime, res[0], res[1])
        return res
    except Exception:
        pass

    return ("STATE_RUNNING" if is_recent else "STATE_DONE", is_recent)


_brain_discovery_cache: Dict[str, Tuple[float, Dict[str, Any], List[str]]] = {}


def discover_gemini_brain_sessions(gemini_root: str = "~/.gemini") -> List[Dict[str, Any]]:
    """Discovers all valid Gemini Brain sessions across ~/.gemini runtime directories using fast mtime caching."""
    root = os.path.abspath(os.path.expanduser(gemini_root))
    if not os.path.exists(root):
        return []

    sessions: Dict[str, Dict[str, Any]] = {}
    child_parent_map: Dict[str, str] = {}
    now = time.time()

    try:
        entries = os.listdir(root)
    except Exception:
        return []

    for entry in entries:
        if not is_valid_gemini_app_dir(entry):
            continue

        app_dir = os.path.join(root, entry)
        if not os.path.isdir(app_dir):
            continue

        brain_dir = os.path.join(app_dir, "brain")
        if not os.path.isdir(brain_dir):
            continue

        try:
            session_uuids = os.listdir(brain_dir)
        except Exception:
            continue

        for sid in session_uuids:
            s_dir = os.path.join(brain_dir, sid)
            if not os.path.isdir(s_dir):
                continue

            full_log = os.path.join(s_dir, ".system_generated", "logs", "transcript_full.jsonl")
            short_log = os.path.join(s_dir, ".system_generated", "logs", "transcript.jsonl")
            log_path = full_log if os.path.exists(full_log) else (short_log if os.path.exists(short_log) else None)
            if not log_path:
                continue

            try:
                mtime = os.path.getmtime(log_path)
            except Exception:
                continue

            conv_db = os.path.join(app_dir, "conversations", f"{sid}.db")

            # Fast Cache Check: If file hasn't changed, reuse parsed metadata instantly
            if s_dir in _brain_discovery_cache and _brain_discovery_cache[s_dir][0] == mtime:
                _, cached_s, child_subs = _brain_discovery_cache[s_dir]
                is_live = (now - mtime) < 30.0
                status, is_live = _get_authoritative_brain_session_status(conv_db, is_live)
                s_copy = dict(cached_s)
                s_copy["is_live"] = is_live
                s_copy["status"] = status
                sessions[sid] = s_copy
                for cid in child_subs:
                    child_parent_map[cid] = sid
                continue

            # Cache miss: parse title and scan subagents once
            summary_path = os.path.join(s_dir, ".system_generated", "logs", "summary.json")
            short_title_path = os.path.join(s_dir, ".system_generated", "logs", "short_title.txt")

            title = sid[:8]
            if os.path.exists(summary_path):
                try:
                    with open(summary_path, "r", encoding="utf-8", errors="replace") as sf:
                        title = json.load(sf).get("shortTitle") or title
                except Exception:
                    pass
            elif os.path.exists(short_title_path):
                try:
                    with open(short_title_path, "r", encoding="utf-8", errors="replace") as stf:
                        title = stf.read().strip() or title
                except Exception:
                    pass

            step_count = 0
            child_subs: List[str] = []
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        step_count += 1
                        if "conversationId" in line:
                            for sub_id in re.findall(r'conversationId[\\"]*:\s*[\\"]*([a-zA-Z0-9_\-]+)', line):
                                if sub_id != sid:
                                    child_subs.append(sub_id)
            except Exception:
                pass

            is_live = (now - mtime) < 30.0
            status, is_live = _get_authoritative_brain_session_status(conv_db, is_live)
            tag = normalize_source_tag(entry)
            s_dict = {
                "session_id": sid,
                "cascade_id": sid,
                "title": f"[{tag}] {title}",
                "status": status,
                "workspace_dir": s_dir,
                "db_path": s_dir,
                "updated_at": mtime,
                "is_live": is_live,
                "source_tag": tag,
                "subagent_count": len(child_subs),
                "total_tokens": 0,
                "step_count": step_count,
                "session_type": "brain",
                "parent_id": None,
            }

            _brain_discovery_cache[s_dir] = (mtime, s_dict, child_subs)
            sessions[sid] = s_dict
            for cid in child_subs:
                child_parent_map[cid] = sid

    # Tag child sessions with their parent_id
    for child_id, parent_id in child_parent_map.items():
        if child_id in sessions:
            sessions[child_id]["parent_id"] = parent_id

    return list(sessions.values())
