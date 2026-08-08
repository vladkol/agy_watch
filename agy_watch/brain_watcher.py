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

        self.file_offset = 0
        self.last_mtime = 0.0
        self.all_events: List[Dict[str, Any]] = []
        self.child_watchers: Dict[str, "BrainTranscriptWatcher"] = {}
        self.known_subagent_ids: Set[str] = set()
        self.pending_tool_call: Optional[Dict[str, Any]] = None

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
        """Refreshes summary title, modification timestamps, and live status."""
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

                # If recently modified and not explicitly errored or cancelled
                if is_recent:
                    self.session_info["is_live"] = True
                    self.session_info["status"] = "STATE_RUNNING"
                else:
                    self.session_info["is_live"] = False
                    if self.session_info["status"] in ("STATE_ACTIVE", "STATE_RUNNING"):
                        self.session_info["status"] = "STATE_DONE"
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
        step_idx = result_dict.get("step_index", tool_dict.get("step_index"))
        result_type = result_dict.get("type", "TOOL_CALL")
        content = result_dict.get("content") or ""
        created_at = result_dict.get("created_at") or tool_dict.get("created_at")
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
            "state": "STATE_DONE" if status_str == "DONE" else "STATE_ERROR",
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
            "tokens": None,
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
            "state": "STATE_DONE" if d.get("status") != "ERROR" else "STATE_ERROR",
            "prompt": content if step_type_raw == "USER_INPUT" else "",
            "text": content,
            "thinking": thinking,
            "tool_name": tool_calls[0].get("name") if tool_calls else None,
            "tool_id": None,
            "tool_args": tool_calls[0].get("args") if tool_calls else None,
            "subagent_report": None,
            "tokens": None,
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
            else:
                event["step_type"] = "TEXT_RESPONSE" if not thinking else "MODEL_REASONING"
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
        if "conversationId" in content or "Created the following subagents:" in content:
            for sub_id in re.findall(r'conversationId[\\"]*:\s*[\\"]*([a-zA-Z0-9_\-]+)', content):
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
            for idx, ev in enumerate(self.all_events):
                ev["id"] = idx + 1

        self._refresh_session_meta()
        return self.session_info, new_events


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

            # Fast Cache Check: If file hasn't changed, reuse parsed metadata instantly
            if s_dir in _brain_discovery_cache and _brain_discovery_cache[s_dir][0] == mtime:
                _, cached_s, child_subs = _brain_discovery_cache[s_dir]
                is_live = (now - mtime) < 30.0
                s_copy = dict(cached_s)
                s_copy["is_live"] = is_live
                s_copy["status"] = "STATE_RUNNING" if is_live else "STATE_DONE"
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
            tag = normalize_source_tag(entry)
            s_dict = {
                "session_id": sid,
                "cascade_id": sid,
                "title": f"[{tag}] {title}",
                "status": "STATE_RUNNING" if is_live else "STATE_DONE",
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
