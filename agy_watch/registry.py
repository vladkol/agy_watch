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
                pid, total_tokens, prompt_tokens, candidates_tokens, thoughts_tokens,
                cached_tokens, subagent_count, step_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                cascade_id=coalesce(excluded.cascade_id, global_sessions.cascade_id),
                title=coalesce(excluded.title, global_sessions.title),
                status=excluded.status,
                workspace_dir=coalesce(excluded.workspace_dir, global_sessions.workspace_dir),
                db_path=excluded.db_path,
                blobs_dir=excluded.blobs_dir,
                pid=excluded.pid,
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
                session_data.get("cascade_id"),
                session_data.get("title", f"Session {sid[:8]}"),
                session_data.get("status", "STATE_ACTIVE"),
                session_data.get("workspace_dir"),
                session_data.get("db_path"),
                session_data.get("blobs_dir"),
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

    def list_sessions(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Returns all registered sessions sorted by most recent activity, with live process status."""
        conn = self._get_connection()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM global_sessions ORDER BY updated_at DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        conn.close()

        results = []
        for r in rows:
            d = dict(r)
            pid = d.get("pid")
            status = d.get("status")

            # Determine live status: process must be alive and status not terminal
            is_active_state = status not in ("STATE_DONE", "STATE_ERROR", "STATE_CANCELLED")
            pid_alive = is_pid_alive(pid) if pid else False
            d["is_live"] = is_active_state and pid_alive
            results.append(d)

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
        is_active_state = status not in ("STATE_DONE", "STATE_ERROR", "STATE_CANCELLED")
        d["is_live"] = is_active_state and (is_pid_alive(pid) if pid else False)
        return d


_default_registry: Optional[GlobalRegistry] = None


def get_global_registry() -> GlobalRegistry:
    """Returns the singleton GlobalRegistry instance."""
    global _default_registry
    if _default_registry is None:
        _default_registry = GlobalRegistry()
    return _default_registry
