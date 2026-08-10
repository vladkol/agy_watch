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

"""Unified SQLite Telemetry Cache for Antigravity & Brain Agent Sessions.

Provides per-session SQLite caching (.system_generated/agy_watch/telemetry_cache.db)
for JSONL transcripts, enabling incremental file syncing, sub-millisecond tail-first
windowed queries (limit 150 steps), and zero in-memory overhead for long-running sessions.
"""

import os
import json
import time
import sqlite3
import re
from typing import Any, Dict, List, Optional, Tuple


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


class SessionTelemetryCache:
    """Manages SQLite telemetry caching for a single agent session."""

    def __init__(self, session_dir: str, session_id: Optional[str] = None):
        self.session_dir = os.path.abspath(os.path.expanduser(session_dir))
        self.session_id = session_id or os.path.basename(self.session_dir)
        
        self.cache_dir = os.path.join(self.session_dir, ".system_generated", "agy_watch")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.db_path = os.path.join(self.cache_dir, "telemetry_cache.db")
        
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initializes SQLite schema with WAL mode and indexes."""
        with self._get_connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    step_index INTEGER,
                    subagent_id TEXT DEFAULT '',
                    seq_num INTEGER,
                    timestamp REAL,
                    created_at TEXT,
                    direction TEXT,
                    message_type TEXT,
                    trajectory_id TEXT,
                    is_main INTEGER,
                    step_type TEXT,
                    state TEXT,
                    prompt TEXT,
                    text TEXT,
                    thinking TEXT,
                    tool_name TEXT,
                    tool_id TEXT,
                    tool_args_json TEXT,
                    tool_action TEXT,
                    tool_summary TEXT,
                    tokens INTEGER,
                    has_artifacts INTEGER,
                    artifacts_json TEXT,
                    payload_json TEXT,
                    PRIMARY KEY (step_index, subagent_id)
                );
            """)

            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp ASC);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_subagent ON events(subagent_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_step ON events(step_index ASC);")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS subagents (
                    subagent_id TEXT PRIMARY KEY,
                    parent_id TEXT,
                    created_at REAL,
                    status TEXT,
                    title TEXT
                );
            """)

    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(value)))

    def sync_jsonl(
        self,
        log_path: str,
        is_main: bool = True,
        subagent_id: Optional[str] = None,
        step_token_map: Optional[Dict[int, int]] = None,
    ) -> int:
        """Incrementally reads new JSONL lines and inserts normalized events into SQLite."""
        if not os.path.exists(log_path):
            return 0

        sub_key = subagent_id or "main"
        offset_meta_key = f"last_offset_{sub_key}"
        last_offset = int(self.get_meta(offset_meta_key, "0") or "0")
        
        file_size = os.path.getsize(log_path)
        if file_size <= last_offset:
            return 0

        step_token_map = step_token_map or {}
        new_events: List[Dict[str, Any]] = []
        pending_tool_call: Optional[Dict[str, Any]] = None

        # Load any previously saved pending tool call for this stream
        pending_json = self.get_meta(f"pending_tool_{sub_key}")
        if pending_json:
            try:
                pending_tool_call = json.loads(pending_json)
            except Exception:
                pending_tool_call = None

        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(last_offset)
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    d = json.loads(line_str)
                    stype = d.get("type", "")
                    tool_calls = d.get("tool_calls") or []

                    # 1. PLANNER_RESPONSE with tool calls
                    if stype == "PLANNER_RESPONSE" and tool_calls:
                        if pending_tool_call:
                            # Flush orphaned pending tool call
                            prev_ev = self._normalize_step(
                                pending_tool_call["step_dict"],
                                is_main=is_main,
                                sub_id=subagent_id,
                                step_token_map=step_token_map,
                            )
                            new_events.append(prev_ev)

                        pending_tool_call = {
                            "step_index": d.get("step_index"),
                            "created_at": d.get("created_at"),
                            "thinking": d.get("thinking") or "",
                            "tool_name": tool_calls[0].get("name"),
                            "tool_args": tool_calls[0].get("args"),
                            "step_dict": d,
                        }
                        continue

                    # 2. Check for tool result matching pending tool call
                    if pending_tool_call and stype not in (
                        "USER_INPUT", "PLANNER_RESPONSE", "SYSTEM_MESSAGE",
                        "CHECKPOINT", "EPHEMERAL_MESSAGE", "CONVERSATION_HISTORY", "KNOWLEDGE_ARTIFACTS"
                    ):
                        event = self._normalize_merged_tool_event(
                            tool_dict=pending_tool_call,
                            result_dict=d,
                            is_main=is_main,
                            sub_id=subagent_id,
                            step_token_map=step_token_map,
                        )
                        pending_tool_call = None
                        new_events.append(event)
                        continue

                    # 3. Non-tool step after a pending tool call -> flush previous
                    if pending_tool_call:
                        prev_ev = self._normalize_step(
                            pending_tool_call["step_dict"],
                            is_main=is_main,
                            sub_id=subagent_id,
                            step_token_map=step_token_map,
                        )
                        new_events.append(prev_ev)
                        pending_tool_call = None

                    event = self._normalize_step(
                        d,
                        is_main=is_main,
                        sub_id=subagent_id,
                        step_token_map=step_token_map,
                    )
                    new_events.append(event)
                except Exception:
                    pass

            new_offset = f.tell()

        # Save pending tool call state if present at EOF
        if pending_tool_call:
            self.set_meta(f"pending_tool_{sub_key}", json.dumps(pending_tool_call))
            # Also emit as currently in-flight running event
            in_flight = self._normalize_step(
                pending_tool_call["step_dict"],
                is_main=is_main,
                sub_id=subagent_id,
                step_token_map=step_token_map,
            )
            in_flight["state"] = "STATE_RUNNING"
            new_events.append(in_flight)
        else:
            self.set_meta(f"pending_tool_{sub_key}", "")

        # Atomic insert into SQLite
        if new_events:
            with self._get_connection() as conn:
                for ev in new_events:
                    conn.execute("""
                        INSERT OR REPLACE INTO events (
                            step_index, subagent_id, seq_num, timestamp, created_at,
                            direction, message_type, trajectory_id, is_main, step_type,
                            state, prompt, text, thinking, tool_name, tool_id,
                            tool_args_json, tool_action, tool_summary, tokens,
                            has_artifacts, artifacts_json, payload_json
                        ) VALUES (
                            ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?,
                            ?, ?, ?
                        )
                    """, (
                        ev.get("step_index", 0),
                        subagent_id or "",
                        ev.get("seq_num", 0),
                        ev.get("timestamp", 0.0),
                        ev.get("created_at") or "",
                        ev.get("direction", "FROM_HARNESS"),
                        ev.get("message_type", ""),
                        ev.get("trajectory_id", ""),
                        1 if ev.get("is_main", True) else 0,
                        ev.get("step_type", "GENERIC"),
                        ev.get("state", "STATE_DONE"),
                        ev.get("prompt", ""),
                        ev.get("text", ""),
                        ev.get("thinking", ""),
                        ev.get("tool_name"),
                        ev.get("tool_id"),
                        json.dumps(ev.get("tool_args") or {}),
                        ev.get("tool_action", ""),
                        ev.get("tool_summary", ""),
                        ev.get("tokens", 0) or 0,
                        1 if ev.get("artifacts") else 0,
                        json.dumps(ev.get("artifacts") or []),
                        json.dumps(ev.get("payload") or {}),
                    ))

        self.set_meta(offset_meta_key, str(new_offset))
        return len(new_events)

    def _normalize_merged_tool_event(
        self,
        tool_dict: Dict[str, Any],
        result_dict: Dict[str, Any],
        is_main: bool = True,
        sub_id: Optional[str] = None,
        step_token_map: Optional[Dict[int, int]] = None,
    ) -> Dict[str, Any]:
        """Merges a PLANNER_RESPONSE tool call with its subsequent tool result into a unified event."""
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
        step_token_map = step_token_map or {}

        event: Dict[str, Any] = {
            "seq_num": step_idx,
            "timestamp": ts_float,
            "created_at": created_at,
            "direction": "FROM_HARNESS",
            "message_type": result_type,
            "trajectory_id": sub_id or self.session_id,
            "step_index": step_idx,
            "is_main": is_main,
            "subagent_id": sub_id or "",
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
            "tokens": step_token_map.get(step_idx) or step_token_map.get(tool_dict.get("step_index")),
            "artifacts": [],
            "payload": result_dict,
        }

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

    def _normalize_step(
        self,
        d: Dict[str, Any],
        is_main: bool = True,
        sub_id: Optional[str] = None,
        step_token_map: Optional[Dict[int, int]] = None,
    ) -> Dict[str, Any]:
        """Normalizes a raw transcript step into a standard agy_watch event."""
        step_idx = d.get("step_index")
        step_type_raw = d.get("type", "GENERIC")
        source = d.get("source", "SYSTEM")
        content = d.get("content") or ""
        thinking = d.get("thinking") or ""
        tool_calls = d.get("tool_calls") or []
        created_at = d.get("created_at")

        ts_float = time.time()
        if created_at:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                ts_float = dt.timestamp()
            except Exception:
                pass

        step_token_map = step_token_map or {}

        event: Dict[str, Any] = {
            "seq_num": step_idx,
            "timestamp": ts_float,
            "created_at": created_at,
            "direction": "FROM_HARNESS" if source == "MODEL" else "TO_HARNESS",
            "message_type": step_type_raw,
            "trajectory_id": sub_id or self.session_id,
            "step_index": step_idx,
            "is_main": is_main,
            "subagent_id": sub_id or "",
            "step_type": step_type_raw,
            "state": _map_transcript_status(d.get("status")),
            "prompt": content if step_type_raw == "USER_INPUT" else "",
            "text": content,
            "thinking": thinking,
            "tool_name": tool_calls[0].get("name") if tool_calls else None,
            "tool_id": None,
            "tool_args": tool_calls[0].get("args") if tool_calls else None,
            "subagent_report": None,
            "tokens": step_token_map.get(step_idx),
            "artifacts": [],
            "payload": d,
        }

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
                event["step_type"] = "TEXT_RESPONSE"
            elif thinking and thinking.strip():
                event["step_type"] = "MODEL_REASONING"
            else:
                event["step_type"] = "TEXT_RESPONSE"
        elif step_type_raw == "ERROR_MESSAGE":
            event["step_type"] = "ERROR_MESSAGE"
            event["state"] = "STATE_ERROR"
            event["error"] = d.get("error") or content
        elif step_type_raw == "CHECKPOINT":
            event["step_type"] = "CHECKPOINT"
        elif step_type_raw == "SYSTEM_MESSAGE":
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
            event["step_type"] = "TOOL_CALL"
            event["action_type"] = step_type_raw.lower()
            event["has_tool"] = True
            event["tool_action"] = step_type_raw
            event["tool_summary"] = content[:60] if content else ""

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

    def _row_to_event_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Converts an SQLite row back into an in-memory event dictionary."""
        d = dict(row)
        d["is_main"] = bool(d.get("is_main", 1))
        d["tool_args"] = json.loads(d.get("tool_args_json") or "{}")
        d["artifacts"] = json.loads(d.get("artifacts_json") or "[]")
        d["payload"] = json.loads(d.get("payload_json") or "{}")
        d["id"] = d.get("step_index", 0) + 1
        d["action_type"] = (d.get("tool_name") or "").lower()
        d["has_tool"] = bool(d.get("tool_name"))
        return d

    def get_window(
        self,
        limit: int = 150,
        before_step_index: Optional[int] = None,
        after_step_index: Optional[int] = None,
        center_on_step_index: Optional[int] = None,
        subagent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retrieves an indexed window of at most `limit` events from SQLite."""
        with self._get_connection() as conn:
            # Determine total count
            where_clauses = []
            params: List[Any] = []

            if subagent_id is not None:
                where_clauses.append("subagent_id = ?")
                params.append(subagent_id)

            where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
            total_count = conn.execute(f"SELECT COUNT(*) FROM events {where_sql}", params).fetchone()[0]

            if total_count == 0:
                return {
                    "events": [],
                    "total_count": 0,
                    "min_step_index": None,
                    "max_step_index": None,
                    "has_earlier": False,
                    "has_later": False,
                }

            # Query window
            query_clauses = list(where_clauses)
            query_params = list(params)

            if center_on_step_index is not None:
                half = max(1, limit // 2)
                upper_bound = center_on_step_index + half
                query_clauses.append("step_index <= ?")
                query_params.append(upper_bound)
                q_sql = ("WHERE " + " AND ".join(query_clauses)) if query_clauses else ""
                sql = f"SELECT * FROM events {q_sql} ORDER BY step_index DESC LIMIT ?"
                query_params.append(limit)
                rows = conn.execute(sql, query_params).fetchall()
                rows = list(reversed(rows))
            elif before_step_index is not None:
                query_clauses.append("step_index < ?")
                query_params.append(before_step_index)
                q_sql = ("WHERE " + " AND ".join(query_clauses)) if query_clauses else ""
                sql = f"SELECT * FROM events {q_sql} ORDER BY step_index DESC LIMIT ?"
                query_params.append(limit)
                rows = conn.execute(sql, query_params).fetchall()
                # Reverse to get chronological ascending order
                rows = list(reversed(rows))
            elif after_step_index is not None:
                query_clauses.append("step_index > ?")
                query_params.append(after_step_index)
                q_sql = ("WHERE " + " AND ".join(query_clauses)) if query_clauses else ""
                sql = f"SELECT * FROM events {q_sql} ORDER BY step_index ASC LIMIT ?"
                query_params.append(limit)
                rows = conn.execute(sql, query_params).fetchall()
            else:
                # Default: Latest tail window
                q_sql = ("WHERE " + " AND ".join(query_clauses)) if query_clauses else ""
                sql = f"SELECT * FROM events {q_sql} ORDER BY step_index DESC LIMIT ?"
                query_params.append(limit)
                rows = conn.execute(sql, query_params).fetchall()
                rows = list(reversed(rows))

            events = [self._row_to_event_dict(r) for r in rows]

            min_idx = events[0]["step_index"] if events else None
            max_idx = events[-1]["step_index"] if events else None

            has_earlier = False
            has_later = False

            if min_idx is not None:
                c_earlier = conn.execute("SELECT COUNT(*) FROM events WHERE step_index < ?", (min_idx,)).fetchone()[0]
                has_earlier = c_earlier > 0

            if max_idx is not None:
                c_later = conn.execute("SELECT COUNT(*) FROM events WHERE step_index > ?", (max_idx,)).fetchone()[0]
                has_later = c_later > 0

            return {
                "events": events,
                "total_count": total_count,
                "min_step_index": min_idx,
                "max_step_index": max_idx,
                "has_earlier": has_earlier,
                "has_later": has_later,
            }

    def get_all_artifacts(self) -> List[Dict[str, Any]]:
        """Extracts all distinct artifacts recorded across events in the cache."""
        artifacts: List[Dict[str, Any]] = []
        seen_paths = set()
        with self._get_connection() as conn:
            rows = conn.execute("SELECT artifacts_json FROM events WHERE has_artifacts = 1 ORDER BY step_index ASC").fetchall()
            for r in rows:
                try:
                    arts = json.loads(r["artifacts_json"] or "[]")
                    for a in arts:
                        path = a.get("path")
                        if not path or path in seen_paths:
                            continue
                        exists = os.path.exists(path)
                        # Discard single-segment bogus root paths like '/foo.md' that don't exist
                        if not exists and path.count("/") <= 1:
                            continue
                        if not exists and not a.get("exists"):
                            continue
                        a["exists"] = exists
                        a["size_bytes"] = os.path.getsize(path) if exists else 0
                        seen_paths.add(path)
                        artifacts.append(a)
                except Exception:
                    pass
        return artifacts

    def get_total_count(self) -> int:
        with self._get_connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def get_all_events(self) -> List[Dict[str, Any]]:
        """Returns all events sorted chronologically (for export / flat view)."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM events ORDER BY timestamp ASC, step_index ASC").fetchall()
            return [self._row_to_event_dict(r) for r in rows]
