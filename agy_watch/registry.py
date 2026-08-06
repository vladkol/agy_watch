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

"""Central machine-wide session registry for Antigravity Agents.

Maintains registry.db in ~/.antigravity/samples/agy_watch/registry.db, tracking all live and idle
agent sessions across the host machine with PID liveness detection, token counts, and file locations.
"""

import os
import time
import sqlite3
from typing import Any, Dict, List, Optional


def is_pid_alive(pid: int) -> bool:
    """Checks whether a process with the given PID is currently active on the host OS."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class GlobalRegistry:
    """Manages the host-wide registry database for all Antigravity Agent sessions."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path:
            self.db_path = os.path.abspath(db_path)
        elif os.environ.get("AGY_WATCH_REGISTRY_DB"):
            self.db_path = os.path.abspath(os.environ["AGY_WATCH_REGISTRY_DB"])
        else:
            home = os.path.expanduser("~")
            reg_dir = os.path.join(home, ".antigravity", "samples", "agy_watch")
            os.makedirs(reg_dir, exist_ok=True)
            self.db_path = os.path.join(reg_dir, "registry.db")

        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_tables()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_tables(self) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS global_sessions (
                session_id TEXT PRIMARY KEY,
                cascade_id TEXT,
                title TEXT,
                status TEXT,
                workspace_dir TEXT,
                db_path TEXT,
                blobs_dir TEXT,
                pid INTEGER,
                total_tokens INTEGER DEFAULT 0,
                prompt_tokens INTEGER DEFAULT 0,
                candidates_tokens INTEGER DEFAULT 0,
                thoughts_tokens INTEGER DEFAULT 0,
                cached_tokens INTEGER DEFAULT 0,
                subagent_count INTEGER DEFAULT 0,
                step_count INTEGER DEFAULT 0,
                created_at REAL,
                updated_at REAL
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_global_sessions_updated ON global_sessions(updated_at);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_global_sessions_status ON global_sessions(status);")
        conn.close()

    def register_or_update(self, session_data: Dict[str, Any]) -> None:
        """Registers or updates a session entry in the global registry."""
        sid = session_data["session_id"]
        now = time.time()
        conn = self._get_connection()
        with conn:
            conn.execute("""
            INSERT INTO global_sessions (
                session_id, cascade_id, title, status, workspace_dir, db_path, blobs_dir,
                pid, total_tokens, prompt_tokens, candidates_tokens, thoughts_tokens, cached_tokens,
                subagent_count, step_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                cascade_id = COALESCE(excluded.cascade_id, global_sessions.cascade_id),
                title = COALESCE(excluded.title, global_sessions.title),
                status = COALESCE(excluded.status, global_sessions.status),
                workspace_dir = COALESCE(excluded.workspace_dir, global_sessions.workspace_dir),
                db_path = COALESCE(excluded.db_path, global_sessions.db_path),
                blobs_dir = COALESCE(excluded.blobs_dir, global_sessions.blobs_dir),
                pid = COALESCE(excluded.pid, global_sessions.pid),
                total_tokens = CASE WHEN excluded.total_tokens > 0 THEN excluded.total_tokens ELSE global_sessions.total_tokens END,
                prompt_tokens = CASE WHEN excluded.prompt_tokens > 0 THEN excluded.prompt_tokens ELSE global_sessions.prompt_tokens END,
                candidates_tokens = CASE WHEN excluded.candidates_tokens > 0 THEN excluded.candidates_tokens ELSE global_sessions.candidates_tokens END,
                thoughts_tokens = CASE WHEN excluded.thoughts_tokens > 0 THEN excluded.thoughts_tokens ELSE global_sessions.thoughts_tokens END,
                cached_tokens = CASE WHEN excluded.cached_tokens > 0 THEN excluded.cached_tokens ELSE global_sessions.cached_tokens END,
                subagent_count = CASE WHEN excluded.subagent_count > 0 THEN excluded.subagent_count ELSE global_sessions.subagent_count END,
                step_count = CASE WHEN excluded.step_count > 0 THEN excluded.step_count ELSE global_sessions.step_count END,
                updated_at = excluded.updated_at;
            """, (
                sid,
                session_data.get("cascade_id", sid),
                session_data.get("title", f"Session ({sid[:8]})"),
                session_data.get("status", "STATE_ACTIVE"),
                session_data.get("workspace_dir", ""),
                session_data.get("db_path", ""),
                session_data.get("blobs_dir", ""),
                session_data.get("pid"),
                session_data.get("total_tokens", 0),
                session_data.get("prompt_tokens", 0),
                session_data.get("candidates_tokens", 0),
                session_data.get("thoughts_tokens", 0),
                session_data.get("cached_tokens", 0),
                session_data.get("subagent_count", 0),
                session_data.get("step_count", 0),
                session_data.get("created_at", now),
                now,
            ))
        conn.close()

    def delete_session(self, session_id: str) -> None:
        """Deletes a session entry by ID."""
        conn = self._get_connection()
        with conn:
            conn.execute("DELETE FROM global_sessions WHERE session_id = ?;", (session_id,))
        conn.close()

    def list_sessions(self, limit: int = 100, prune_missing: bool = False) -> List[Dict[str, Any]]:
        """Returns all registered sessions sorted by most recent activity, with live process status."""
        conn = self._get_connection()
        conn.row_factory = sqlite3.Row
        with conn:
            # Automatically clean up any legacy placeholder entries
            conn.execute("DELETE FROM global_sessions WHERE session_id = 'wire_tap';")
            rows = conn.execute("""
            SELECT * FROM global_sessions
            WHERE session_id != 'wire_tap'
            ORDER BY updated_at DESC
            LIMIT ?;
            """, (limit,)).fetchall()

        results = []
        stale_ids = []
        for r in rows:
            d = dict(r)
            db_p = d.get("db_path", "")

            # If pruning requested and database file does not exist, mark for removal
            if prune_missing and db_p and not os.path.exists(db_p):
                stale_ids.append(d["session_id"])
                continue

            pid = d.get("pid")
            status = d.get("status")

            # Determine live status: process must be alive and not cancelled
            pid_alive = is_pid_alive(pid) if pid else False
            d["is_live"] = pid_alive and (status != "STATE_CANCELLED")
            if not pid_alive and status in ("STATE_ACTIVE", "STATE_RUNNING"):
                d["status"] = "STATE_DONE"
            results.append(d)

        if stale_ids:
            with conn:
                conn.executemany("DELETE FROM global_sessions WHERE session_id = ?;", [(sid,) for sid in stale_ids])
        conn.close()

        return results

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single session by session_id or cascade_id prefix."""
        conn = self._get_connection()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
        SELECT * FROM global_sessions
        WHERE session_id = ? OR cascade_id = ? OR session_id LIKE ? OR cascade_id LIKE ?
        LIMIT 1
        """, (session_id, session_id, f"{session_id}%", f"{session_id}%"))
        row = cur.fetchone()
        conn.close()

        if not row:
            return None

        d = dict(row)
        pid = d.get("pid")
        status = d.get("status")
        pid_alive = is_pid_alive(pid) if pid else False
        d["is_live"] = pid_alive and (status != "STATE_CANCELLED")
        if not pid_alive and status in ("STATE_ACTIVE", "STATE_RUNNING"):
            d["status"] = "STATE_DONE"
        return d


_default_registry: Optional[GlobalRegistry] = None


def get_global_registry() -> GlobalRegistry:
    """Returns the singleton GlobalRegistry instance, honoring AGY_WATCH_REGISTRY_DB if set."""
    global _default_registry
    if _default_registry is not None:
        return _default_registry
    env_path = os.environ.get("AGY_WATCH_REGISTRY_DB")
    if env_path:
        _default_registry = GlobalRegistry(db_path=env_path)
    else:
        _default_registry = GlobalRegistry()
    return _default_registry
