"""Real-time Incremental Session Watcher for Antigravity Agents.

Reads wire_tap.db in SQLite WAL mode using incremental sequence cursors, providing zero-lock
real-time streaming of execution steps, sub-agent hierarchies, correlated tool arguments, tokens,
and generated multimodal artifacts (images, videos, markdown documents, diffs).
"""

import os
import re
import json
import glob
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple


class SessionWatcher:
    """Watches a specific agent session's wire_tap.db incrementally in real time."""

    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        self.workspace_dir = os.path.dirname(os.path.dirname(self.db_path))
        self.last_event_id = 0
        self.pending_pretool_args: Dict[str, Any] = {}
        self.session_info: Dict[str, Any] = {
            "session_id": os.path.splitext(os.path.basename(db_path))[0],
            "cascade_id": None,
            "title": "Agent Session",
            "status": "STATE_ACTIVE",
            "workspace_dir": self.workspace_dir,
            "total_tokens": 0,
            "prompt_tokens": 0,
            "candidates_tokens": 0,
            "thoughts_tokens": 0,
            "cached_tokens": 0,
            "subagents": set(),
            "subagent_count": 0,
            "step_count": 0,
        }
        self.all_events: List[Dict[str, Any]] = []

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        return conn

    def poll(self) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Polls for new events since last poll.

        Returns (updated_session_info, new_events_list).
        """
        if not os.path.exists(self.db_path):
            return self.session_info, []

        try:
            conn = self._get_connection()
            cur = conn.cursor()

            # 1. Update session metadata
            cur.execute("""
            SELECT session_id, cascade_id, title, status, total_tokens, prompt_tokens,
                   candidates_tokens, thoughts_tokens, cached_tokens, subagent_count, step_count
            FROM session_meta ORDER BY updated_at DESC LIMIT 1;
            """)
            meta_row = cur.fetchone()
            if meta_row:
                self.session_info["session_id"] = meta_row[0]
                self.session_info["cascade_id"] = meta_row[1]
                self.session_info["title"] = meta_row[2] or self.session_info["title"]
                self.session_info["status"] = meta_row[3]
                self.session_info["total_tokens"] = meta_row[4]
                self.session_info["prompt_tokens"] = meta_row[5]
                self.session_info["candidates_tokens"] = meta_row[6]
                self.session_info["thoughts_tokens"] = meta_row[7]
                self.session_info["cached_tokens"] = meta_row[8]
                self.session_info["subagent_count"] = meta_row[9]
                self.session_info["step_count"] = meta_row[10]

            # 2. Fetch new wire events
            cur.execute("""
            SELECT id, seq_num, timestamp, direction, message_type, trajectory_id, step_index, is_main, payload_json
            FROM wire_events WHERE id > ? ORDER BY id ASC;
            """, (self.last_event_id,))

            new_raw_rows = cur.fetchall()
            conn.close()
        except Exception:
            return self.session_info, []

        new_events: List[Dict[str, Any]] = []
        for row in new_raw_rows:
            e_id, seq, ts, direction, msg_type, traj_id, step_idx, is_main, payload_json = row
            self.last_event_id = max(self.last_event_id, e_id)

            try:
                payload = json.loads(payload_json)
            except Exception:
                payload = {}

            # Buffer PreTool hook arguments to attach to subsequent tool calls
            if "callHookRequest" in payload or "call_hook_request" in payload:
                chr_obj = payload.get("callHookRequest") or payload.get("call_hook_request") or {}
                if chr_obj.get("name") == "PreTool":
                    pt_args = chr_obj.get("preToolArgs") or chr_obj.get("pre_tool_args") or {}
                    tool_name = pt_args.get("toolName") or pt_args.get("tool_name")
                    args_json = pt_args.get("argumentsJson") or pt_args.get("arguments_json")
                    if tool_name and args_json:
                        try:
                            self.pending_pretool_args[tool_name] = json.loads(args_json)
                        except Exception:
                            self.pending_pretool_args[tool_name] = args_json

            event: Dict[str, Any] = {
                "id": e_id,
                "seq_num": seq,
                "timestamp": ts,
                "direction": direction,
                "message_type": msg_type,
                "trajectory_id": traj_id,
                "step_index": step_idx,
                "is_main": bool(is_main),
                "step_type": "UNKNOWN",
                "state": "STATE_ACTIVE",
                "prompt": None,
                "text": None,
                "thinking": None,
                "tool_name": None,
                "tool_id": None,
                "tool_args": None,
                "subagent_report": None,
                "subagent_id": None,
                "tokens": None,
                "artifacts": [],
                "payload": payload,
            }

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
                    event["state"] = su.get("state") or "STATE_ACTIVE"
                    source = su.get("source")
                    target = su.get("target")

                    # Classify user instructions to subagents vs model responses
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
                    ]:
                        if action_key in su:
                            action_detected = True
                            tc_ev = dict(event)
                            tc_ev["step_type"] = "TOOL_CALL"
                            tc_ev["tool_name"] = action_name

                            # Correlate arguments: prefer pending PreTool hook args if available
                            raw_args = su[action_key] or {}
                            if not raw_args and action_name in self.pending_pretool_args:
                                raw_args = self.pending_pretool_args[action_name]
                            tc_ev["tool_args"] = raw_args

                            if not is_main and traj_id:
                                tc_ev["subagent_id"] = traj_id
                                self.session_info["subagents"].add(traj_id)

                            tc_ev["artifacts"] = self._extract_event_artifacts(tc_ev)
                            new_events.append(tc_ev)
                            self.all_events.append(tc_ev)
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
                            if not t_args and tc_ev["tool_name"] in self.pending_pretool_args:
                                t_args = self.pending_pretool_args[tc_ev["tool_name"]]
                            tc_ev["tool_args"] = t_args
                            if not is_main and traj_id:
                                tc_ev["subagent_id"] = traj_id
                                self.session_info["subagents"].add(traj_id)
                            tc_ev["artifacts"] = self._extract_event_artifacts(tc_ev)
                            new_events.append(tc_ev)
                            self.all_events.append(tc_ev)
                        continue

            if not is_main and traj_id:
                self.session_info["subagents"].add(traj_id)

            event["artifacts"] = self._extract_event_artifacts(event)
            new_events.append(event)
            self.all_events.append(event)

        self.session_info["subagent_count"] = max(self.session_info["subagent_count"], len(self.session_info["subagents"]))
        return self.session_info, new_events

    def _extract_event_artifacts(self, ev: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Resolves generated images, markdown files, and media files associated with an event."""
        artifacts: List[Dict[str, Any]] = []
        seen_paths: Set[str] = set()

        def add_file_if_valid(p: str, kind: str = "file") -> None:
            if not p:
                return
            clean_path = p.replace("file://", "").strip()
            if not os.path.isabs(clean_path):
                clean_path = os.path.abspath(os.path.join(self.workspace_dir, clean_path))

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

        # 1. Check tool arguments for generateImage
        tool_args = ev.get("tool_args") or {}
        if ev.get("tool_name") in ("generate_image", "generateImage"):
            img_name = tool_args.get("ImageName") or tool_args.get("imageName") or ""
            search_patterns = [
                os.path.join(self.workspace_dir, "brain", "**", f"*{img_name}*"),
                os.path.join(self.workspace_dir, "**", f"*{img_name}*.jpg"),
                os.path.join(self.workspace_dir, "**", f"*{img_name}*.png"),
            ]
            for pattern in search_patterns:
                for match in glob.glob(pattern, recursive=True):
                    if os.path.isfile(match):
                        add_file_if_valid(match, "image")

        # 2. Check editFile, createFile, viewFile arguments
        for k in ("filePath", "filePath", "TargetFile", "targetFile", "AbsolutePath", "absolutePath"):
            if k in tool_args:
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
