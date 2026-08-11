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
        self.pending_hook_requests: Dict[str, Dict[str, Any]] = {}

    @property
    def session_id(self) -> Optional[str]:
        return self.session_info.get("session_id")

    @session_id.setter
    def session_id(self, val: str) -> None:
        self.session_info["session_id"] = val

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

            # 1. Update session metadata (select canonical root session)
            cur.execute("""
            SELECT session_id, cascade_id, title, status, total_tokens, prompt_tokens,
                   candidates_tokens, thoughts_tokens, cached_tokens, subagent_count, step_count
            FROM session_meta
            ORDER BY (session_id = cascade_id) DESC, updated_at DESC LIMIT 1;
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

            # Infer subagent status from trajectory_id
            su = payload.get("stepUpdate") or payload.get("step_update") or payload.get("trajectoryStateUpdate") or payload.get("trajectory_state_update") or {}
            row_traj = su.get("trajectoryId") or su.get("trajectory_id") or traj_id
            traj_id = row_traj

            if traj_id and self.session_id:
                is_main = bool(traj_id == self.session_id)
            else:
                is_main = bool(is_main)

            sub_id = traj_id if (not is_main and traj_id) else None
            if sub_id:
                self.session_info["subagents"].add(sub_id)

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
                "subagent_id": sub_id,
                "tokens": None,
                "artifacts": [],
                "payload": payload,
            }

            # Buffer PreTool hook arguments and classify Hook events
            if "callHookRequest" in payload or "call_hook_request" in payload:
                chr_obj = payload.get("callHookRequest") or payload.get("call_hook_request") or {}
                req_id = chr_obj.get("requestId") or chr_obj.get("request_id")
                h_name = chr_obj.get("name") or ""
                h_type = chr_obj.get("type") or ""

                if "OnSessionStart" in h_name or "ON_SESSION_START" in h_type:
                    event["step_type"] = "ON_SESSION_START_HOOK"
                    event["hook_args"] = {}
                elif "OnSessionEnd" in h_name or "ON_SESSION_END" in h_type:
                    event["step_type"] = "ON_SESSION_END_HOOK"
                    event["hook_args"] = {}
                elif "preToolArgs" in chr_obj or "pre_tool_args" in chr_obj:
                    pt_args = chr_obj.get("preToolArgs") or chr_obj.get("pre_tool_args") or {}
                    t_name = pt_args.get("toolName") or pt_args.get("tool_name")
                    args_json = pt_args.get("argumentsJson") or pt_args.get("arguments_json")
                    parsed_args = {}
                    if t_name and args_json:
                        try:
                            parsed_args = json.loads(args_json)
                        except Exception:
                            parsed_args = {"raw": args_json}
                        self.pending_pretool_args[t_name] = parsed_args
                    event["step_type"] = "PRE_TOOL_HOOK"
                    event["tool_name"] = t_name
                    event["tool_args"] = parsed_args
                elif "preTurnArgs" in chr_obj or "pre_turn_args" in chr_obj:
                    event["step_type"] = "PRE_TURN_HOOK"
                    event["hook_args"] = chr_obj.get("preTurnArgs") or chr_obj.get("pre_turn_args") or {}
                elif "postTurnArgs" in chr_obj or "post_turn_args" in chr_obj:
                    event["step_type"] = "POST_TURN_HOOK"
                    event["hook_args"] = chr_obj.get("postTurnArgs") or chr_obj.get("post_turn_args") or {}
                elif "postToolArgs" in chr_obj or "post_tool_args" in chr_obj:
                    event["step_type"] = "POST_TOOL_HOOK"
                    post_tool = chr_obj.get("postToolArgs") or chr_obj.get("post_tool_args") or {}
                    event["tool_name"] = post_tool.get("toolName") or post_tool.get("tool_name")
                    event["hook_args"] = post_tool
                elif "onToolErrorArgs" in chr_obj or "on_tool_error_args" in chr_obj:
                    event["step_type"] = "ON_TOOL_ERROR_HOOK"
                    ote_args = chr_obj.get("onToolErrorArgs") or chr_obj.get("on_tool_error_args") or {}
                    event["tool_name"] = ote_args.get("toolName") or ote_args.get("tool_name")
                    event["hook_args"] = ote_args
                elif "onCompactionArgs" in chr_obj or "on_compaction_args" in chr_obj:
                    event["step_type"] = "ON_COMPACTION_HOOK"
                    event["hook_args"] = chr_obj.get("onCompactionArgs") or chr_obj.get("on_compaction_args") or {}
                else:
                    event["step_type"] = f"HOOK_{h_name.upper()}" if h_name else "HOOK_REQUEST"

                if req_id:
                    self.pending_hook_requests[req_id] = {
                        "traj_id": traj_id,
                        "is_main": event["is_main"],
                        "subagent_id": sub_id,
                        "tool_name": event.get("tool_name"),
                        "tool_args": event.get("tool_args") or event.get("hook_args"),
                        "event": event,
                    }

                if event["step_type"] == "ON_SESSION_END_HOOK" and hasattr(self, "pending_session_end") and self.pending_session_end:
                    event["child_events"] = [self.pending_session_end, event]
                    self.pending_session_end = None

                new_events.append(event)
                self.all_events.append(event)
                continue

            if "sessionEndRequest" in payload or "session_end_request" in payload:
                event["step_type"] = "SESSION_END_REQUEST"
                event["text"] = "Session termination requested by client"
                self.pending_session_end = event
                continue

            if "sessionEndResponse" in payload or "session_end_response" in payload:
                event["step_type"] = "SESSION_END_RESPONSE"
                event["text"] = "Session termination acknowledged by harness"
                for prev_ev in reversed(self.all_events):
                    if prev_ev.get("step_type") == "ON_SESSION_END_HOOK":
                        if "child_events" not in prev_ev:
                            prev_ev["child_events"] = [prev_ev]
                        prev_ev["child_events"].append(event)
                        break
                continue

            if "config" in payload:
                event["step_type"] = "CLIENT_CONFIG"
                self.pending_client_config = event
                continue

            if ("initializeConversationResponse" in payload or "initialize_conversation_response" in payload) and ("stepUpdate" not in payload and "step_update" not in payload):
                event["step_type"] = "CLIENT_CONFIG"
                if hasattr(self, "pending_client_config") and self.pending_client_config:
                    event["child_events"] = [self.pending_client_config, event]
                    self.pending_client_config = None
                new_events.append(event)
                self.all_events.append(event)
                continue

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
                        event["tool_args"] = {"raw": args_json}
                else:
                    event["tool_args"] = tc_obj.get("arguments") or {}

                # Look for artifacts generated by tool calls
                if event["tool_name"] in ("create_file", "edit_file", "createFile", "editFile"):
                    p = (event["tool_args"] or {}).get("path") or (event["tool_args"] or {}).get("file_path") or (event["tool_args"] or {}).get("target_file")
                    if p:
                        event["artifacts"].append({
                            "type": "code" if not p.endswith((".md", ".txt")) else "markdown",
                            "path": p,
                            "identifier": p,
                            "source_step": event.get("step_index")
                        })
                elif event["tool_name"] in ("generate_image", "generateImage"):
                    p = (event["tool_args"] or {}).get("output_path") or (event["tool_args"] or {}).get("image_path")
                    if p:
                        event["artifacts"].append({
                            "type": "image",
                            "path": p,
                            "identifier": p,
                            "source_step": event.get("step_index")
                        })
                
                new_events.append(event)
                self.all_events.append(event)
                continue

            if "toolResponse" in payload or "tool_response" in payload:
                tr_obj = payload.get("toolResponse") or payload.get("tool_response") or {}
                resp_json = tr_obj.get("responseJson") or tr_obj.get("response_json")
                err_msg = tr_obj.get("error") or tr_obj.get("errorMessage") or tr_obj.get("error_message")

                # Correlate tool response onto matching tool call in this session
                matching_tc = None
                for prev_ev in reversed(self.all_events):
                    if prev_ev.get("step_type") == "TOOL_CALL" and (prev_ev.get("tool_name") == tr_obj.get("name") or not tr_obj.get("name")):
                        matching_tc = prev_ev
                        if "payload" in prev_ev and isinstance(prev_ev["payload"], dict):
                            if "stepUpdate" not in prev_ev["payload"] or not isinstance(prev_ev["payload"]["stepUpdate"], dict):
                                prev_ev["payload"]["stepUpdate"] = {}
                            if resp_json:
                                prev_ev["payload"]["stepUpdate"]["responseJson"] = resp_json
                            if err_msg:
                                prev_ev["payload"]["stepUpdate"]["error"] = {"errorMessage": err_msg}
                        break

                if err_msg:
                    event["step_type"] = "TOOL_ERROR"
                    event["error_message"] = err_msg
                    event["state"] = "STATE_ERROR"
                else:
                    event["step_type"] = "TOOL_RESPONSE"
                    event["text"] = resp_json or tr_obj.get("response") or ""

                if matching_tc:
                    if "child_events" not in matching_tc:
                        matching_tc["child_events"] = [
                            {
                                "id": matching_tc.get("id"),
                                "seq_num": matching_tc.get("seq_num"),
                                "direction": matching_tc.get("direction", "FROM_HARNESS"),
                                "timestamp": matching_tc.get("timestamp"),
                                "message_type": matching_tc.get("message_type", "TOOL_CALL"),
                                "step_index": matching_tc.get("step_index"),
                                "payload": matching_tc.get("payload"),
                                "tool_name": matching_tc.get("tool_name"),
                                "tool_args": matching_tc.get("tool_args"),
                                "session_id": matching_tc.get("session_id"),
                                "trajectory_id": matching_tc.get("trajectory_id"),
                            }
                        ]
                    matching_tc["child_events"].append({
                        "id": event.get("id"),
                        "seq_num": event.get("seq_num"),
                        "direction": event.get("direction", "TO_HARNESS"),
                        "timestamp": event.get("timestamp"),
                        "message_type": event.get("message_type", "TOOL_RESPONSE"),
                        "payload": payload,
                        "session_id": event.get("session_id"),
                        "trajectory_id": event.get("trajectory_id"),
                    })
                    if not err_msg:
                        continue

                new_events.append(event)
                self.all_events.append(event)
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
                        for p in parts:
                            sc = p.get("slashCommand") or p.get("slash_command")
                            if sc and isinstance(sc, dict) and sc.get("name"):
                                event["slash_command"] = f"/{sc.get('name')}"
                    else:
                        event["prompt"] = payload.get("prompt") or payload.get("content")
                elif msg_type == "TRIGGER_NOTIFICATION" or "automatedTrigger" in payload or "automated_trigger" in payload:
                    event["step_type"] = "TRIGGER_NOTIFICATION"
                    trig_val = payload.get("automatedTrigger") or payload.get("automated_trigger") or ""
                    event["trigger_content"] = trig_val
                    event["text"] = str(trig_val)
                    event["prompt"] = str(trig_val)
                elif msg_type == "HALT_REQUEST" or "haltRequest" in payload or "halt_request" in payload:
                    event["step_type"] = "CANCELLATION_REQUEST"
                    event["text"] = "Turn cancellation requested by client"
                elif msg_type in ("POLICY_DECISION", "HOOK_RESPONSE", "ON_TOOL_ERROR_RESPONSE") or "callHookResponse" in payload or "call_hook_response" in payload:
                    chr_resp = payload.get("callHookResponse") or payload.get("call_hook_response") or {}
                    req_id = chr_resp.get("requestId") or chr_resp.get("request_id")
                    
                    has_pre_turn = "preTurnResult" in chr_resp or "pre_turn_result" in chr_resp
                    has_pre_tool = "preToolResult" in chr_resp or "pre_tool_result" in chr_resp
                    has_error_result = "onToolErrorResult" in chr_resp or "on_tool_error_result" in chr_resp

                    if has_pre_turn:
                        event["step_type"] = "PRE_TURN_DECISION"
                        pre_turn = chr_resp.get("preTurnResult") or chr_resp.get("pre_turn_result") or {}
                        decision = pre_turn.get("decision", "ALLOW")
                        reason = pre_turn.get("reason", "")
                        event["decision"] = decision
                        event["reason"] = reason
                        if decision == "DENY":
                            event["state"] = "STATE_ERROR"
                    elif has_pre_tool:
                        event["step_type"] = "POLICY_DECISION"
                        pre_res = chr_resp.get("preToolResult") or chr_resp.get("pre_tool_result") or {}
                        decision = pre_res.get("decision", "ALLOW")
                        reason = pre_res.get("reason", "")
                        event["decision"] = decision
                        event["reason"] = reason
                        if decision == "DENY":
                            event["state"] = "STATE_ERROR"
                    elif has_error_result:
                        event["step_type"] = "ON_TOOL_ERROR_RESULT"
                        ote_res = chr_resp.get("onToolErrorResult") or chr_resp.get("on_tool_error_result") or {}
                        event["custom_error_message"] = ote_res.get("customErrorMessage") or ote_res.get("custom_error_message")
                    else:
                        event["step_type"] = "HOOK_RESPONSE"

                    matching_hook = None
                    if req_id and req_id in self.pending_hook_requests:
                        h_info = self.pending_hook_requests[req_id]
                        event["trajectory_id"] = h_info["traj_id"]
                        event["is_main"] = h_info["is_main"]
                        event["subagent_id"] = h_info["subagent_id"]
                        event["tool_name"] = h_info["tool_name"]
                        event["tool_args"] = h_info["tool_args"]
                        matching_hook = h_info.get("event")
                    else:
                        for prev_ev in reversed(self.all_events):
                            if prev_ev.get("step_type") in ("PRE_TOOL_HOOK", "PRE_TURN_HOOK", "POST_TOOL_HOOK", "POST_TURN_HOOK", "ON_COMPACTION_HOOK") or prev_ev.get("message_type", "").startswith("CALL_HOOK"):
                                event["tool_name"] = prev_ev.get("tool_name")
                                event["tool_args"] = prev_ev.get("tool_args")
                                event["trajectory_id"] = prev_ev.get("trajectory_id")
                                event["is_main"] = prev_ev.get("is_main", True)
                                event["subagent_id"] = prev_ev.get("subagent_id")
                                matching_hook = prev_ev
                                if "decision" in event:
                                    prev_ev["decision"] = event["decision"]
                                if "reason" in event:
                                    prev_ev["reason"] = event["reason"]
                                if event.get("decision") == "DENY":
                                    prev_ev["state"] = "STATE_ERROR"
                                break

                    # If this was an empty acknowledgement for an inspect hook (POST_TOOL, POST_TURN, etc.)
                    # Correlate it into matching_hook's child_events and skip emitting a duplicate timeline node!
                    if event["step_type"] == "HOOK_RESPONSE" and matching_hook:
                        if "child_events" not in matching_hook:
                            matching_hook["child_events"] = [
                                {
                                    "id": matching_hook.get("id"),
                                    "seq_num": matching_hook.get("seq_num"),
                                    "direction": matching_hook.get("direction", "FROM_HARNESS"),
                                    "timestamp": matching_hook.get("timestamp"),
                                    "message_type": matching_hook.get("message_type", "CALL_HOOK"),
                                    "payload": matching_hook.get("payload"),
                                    "tool_name": matching_hook.get("tool_name"),
                                    "tool_args": matching_hook.get("tool_args"),
                                    "session_id": matching_hook.get("session_id"),
                                    "trajectory_id": matching_hook.get("trajectory_id"),
                                }
                            ]
                        matching_hook["child_events"].append({
                            "id": event.get("id"),
                            "seq_num": event.get("seq_num"),
                            "direction": event.get("direction", "TO_HARNESS"),
                            "timestamp": event.get("timestamp"),
                            "message_type": event.get("message_type", "CALL_HOOK_RESPONSE"),
                            "payload": payload,
                            "session_id": event.get("session_id"),
                            "trajectory_id": event.get("trajectory_id"),
                        })
                        continue

                    if matching_hook:
                        event["child_events"] = [
                            {
                                "id": matching_hook.get("id"),
                                "seq_num": matching_hook.get("seq_num"),
                                "direction": matching_hook.get("direction", "FROM_HARNESS"),
                                "timestamp": matching_hook.get("timestamp"),
                                "message_type": matching_hook.get("message_type", "CALL_HOOK"),
                                "payload": matching_hook.get("payload"),
                                "tool_name": matching_hook.get("tool_name"),
                                "tool_args": matching_hook.get("tool_args"),
                                "session_id": matching_hook.get("session_id"),
                                "trajectory_id": matching_hook.get("trajectory_id"),
                            },
                            {
                                "id": event.get("id"),
                                "seq_num": event.get("seq_num"),
                                "direction": event.get("direction", "TO_HARNESS"),
                                "timestamp": event.get("timestamp"),
                                "message_type": event.get("message_type", "CALL_HOOK_RESPONSE"),
                                "payload": payload,
                                "decision": event.get("decision"),
                                "reason": event.get("reason"),
                                "session_id": event.get("session_id"),
                                "trajectory_id": event.get("trajectory_id"),
                            }
                        ]

                elif msg_type == "USER_ANSWER" or "questionResponse" in payload or "question_response" in payload:
                    event["step_type"] = "USER_ANSWER"
                    qr = payload.get("questionResponse") or payload.get("question_response") or {}
                    resp_obj = qr.get("response") or {}
                    answers = resp_obj.get("answers") or []

                    target_step_idx = qr.get("stepIndex") if "stepIndex" in qr else qr.get("step_index")
                    target_traj_id = qr.get("trajectoryId") or qr.get("trajectory_id")

                    matching_q_event = None
                    if target_step_idx is not None:
                        for prev_ev in reversed(self.all_events):
                            if prev_ev.get("tool_name") in ("ask_question", "askQuestion", "questionsRequest") and prev_ev.get("step_index") == target_step_idx:
                                if prev_ev.get("trajectory_id") == target_traj_id or not target_traj_id:
                                    matching_q_event = prev_ev
                                    break

                    answer_summaries = []
                    for ans in answers:
                        if isinstance(ans, dict):
                            if "multipleChoiceAnswer" in ans or "multiple_choice_answer" in ans:
                                mca = ans.get("multipleChoiceAnswer") or ans.get("multiple_choice_answer") or {}
                                indices = mca.get("selectedChoiceIndices") or mca.get("selected_choice_indices") or []
                                choice_labels = []
                                if matching_q_event:
                                    from agy_watch.tool_renderers import _normalize_questions
                                    p_args = matching_q_event.get("tool_args") or {}
                                    p_su = matching_q_event.get("payload", {}).get("stepUpdate", {})
                                    norm_qs = _normalize_questions(p_args, p_su)
                                    if norm_qs and norm_qs[0].get("options"):
                                        opts = norm_qs[0]["options"]
                                        for idx in indices:
                                            if 0 <= idx < len(opts):
                                                choice_labels.append(opts[idx])
                                if choice_labels:
                                    answer_summaries.append(", ".join(choice_labels))
                                else:
                                    answer_summaries.append(f"Option indices {indices}")
                            elif "textAnswer" in ans or "text_answer" in ans:
                                ta = ans.get("textAnswer") or ans.get("text_answer")
                                answer_summaries.append(str(ta))
                            elif "openEndedAnswer" in ans or "open_ended_answer" in ans:
                                oea = ans.get("openEndedAnswer") or ans.get("open_ended_answer")
                                answer_summaries.append(str(oea))

                    answer_text = " | ".join(answer_summaries) if answer_summaries else "User response submitted"
                    event["text"] = answer_text
                    event["prompt"] = answer_text
                    event["response"] = resp_obj

                    # Correlate response back onto matching ask_question event
                    if matching_q_event:
                        if not isinstance(matching_q_event.get("tool_args"), dict):
                            matching_q_event["tool_args"] = {}
                        matching_q_event["tool_args"]["response"] = resp_obj
                        matching_q_event["user_answer"] = resp_obj
                        event["child_events"] = [
                            {
                                "id": matching_q_event.get("id"),
                                "seq_num": matching_q_event.get("seq_num"),
                                "direction": matching_q_event.get("direction", "FROM_HARNESS"),
                                "timestamp": matching_q_event.get("timestamp"),
                                "message_type": matching_q_event.get("message_type", "STEP_UPDATE"),
                                "step_index": matching_q_event.get("step_index"),
                                "payload": matching_q_event.get("payload"),
                                "tool_name": matching_q_event.get("tool_name"),
                                "tool_args": matching_q_event.get("tool_args"),
                                "session_id": matching_q_event.get("session_id"),
                                "trajectory_id": matching_q_event.get("trajectory_id"),
                            },
                            {
                                "id": event.get("id"),
                                "seq_num": event.get("seq_num"),
                                "direction": event.get("direction", "TO_HARNESS"),
                                "timestamp": event.get("timestamp"),
                                "message_type": event.get("message_type", "USER_ANSWER"),
                                "payload": payload,
                                "text": answer_text,
                                "session_id": event.get("session_id"),
                                "trajectory_id": event.get("trajectory_id"),
                            }
                        ]

            elif "trajectoryStateUpdate" in payload or "trajectory_state_update" in payload:
                tsu = payload.get("trajectoryStateUpdate") or payload.get("trajectory_state_update") or {}
                st = tsu.get("state")
                if st in ("STATE_CANCELLED", "CANCELLED", 3):
                    event["step_type"] = "CANCELLATION"
                    event["state"] = "STATE_CANCELLED"
                    event["text"] = "Turn cancelled by client"
            else:
                su = payload.get("stepUpdate") or payload.get("step_update") or {}
                if su:
                    event["state"] = su.get("state") or "STATE_ACTIVE"
                    source = su.get("source")
                    target = su.get("target")

                    # Classify user instructions to subagents vs model responses
                    if source == "SOURCE_USER" and target == "TARGET_MODEL":
                        if is_main:
                            prompt_text = su.get("text") or ""
                            matching_prompt = None
                            for prev_ev in reversed(self.all_events):
                                if prev_ev.get("is_main", True) and prev_ev.get("step_type") == "USER_INPUT":
                                    matching_prompt = prev_ev
                                    break
                            if matching_prompt and (not matching_prompt.get("prompt") or matching_prompt.get("prompt") == prompt_text or not prompt_text):
                                if event.get("step_index") is not None:
                                    matching_prompt["step_index"] = event.get("step_index")
                                matching_prompt["state"] = su.get("state") or "STATE_ACTIVE"
                                orig_payload = matching_prompt.get("payload") or {}
                                if isinstance(orig_payload, dict) and "client_request" not in orig_payload:
                                    matching_prompt["payload"] = {
                                        "client_request": orig_payload,
                                        "harness_step_update": payload.get("stepUpdate") or su,
                                    }
                                elif isinstance(orig_payload, dict):
                                    matching_prompt["payload"]["harness_step_update"] = payload.get("stepUpdate") or su

                                if "child_events" not in matching_prompt:
                                    matching_prompt["child_events"] = [
                                        {
                                            "id": matching_prompt.get("id"),
                                            "seq_num": matching_prompt.get("seq_num"),
                                            "direction": matching_prompt.get("direction", "TO_HARNESS"),
                                            "timestamp": matching_prompt.get("timestamp"),
                                            "message_type": matching_prompt.get("message_type", "USER_INPUT"),
                                            "step_index": matching_prompt.get("step_index"),
                                            "payload": orig_payload.get("client_request") if isinstance(orig_payload, dict) and "client_request" in orig_payload else orig_payload,
                                            "prompt": matching_prompt.get("prompt"),
                                            "session_id": matching_prompt.get("session_id"),
                                            "trajectory_id": matching_prompt.get("trajectory_id"),
                                        }
                                    ]
                                matching_prompt["child_events"].append({
                                    "id": event.get("id"),
                                    "seq_num": event.get("seq_num"),
                                    "direction": event.get("direction", "FROM_HARNESS"),
                                    "timestamp": event.get("timestamp"),
                                    "message_type": event.get("message_type", "STEP_UPDATE"),
                                    "step_index": event.get("step_index"),
                                    "payload": payload,
                                    "session_id": event.get("session_id"),
                                    "trajectory_id": event.get("trajectory_id"),
                                })
                                continue
                            event["step_type"] = "USER_INPUT"
                            event["prompt"] = prompt_text
                        else:
                            event["step_type"] = "SUBAGENT_PROMPT"
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

                    # Check action fields across all 17 built-in and harness tools
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
                        ("compaction", "compaction"),
                        ("ActionCompaction", "compaction"),
                        ("askPermission", "ask_permission"),
                        ("ask_permission", "ask_permission"),
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

                            # Correlate arguments: merge pending PreTool hook args with action payload
                            raw_args = dict(su[action_key]) if isinstance(su[action_key], dict) else {}
                            if action_name in self.pending_pretool_args:
                                pt_data = self.pending_pretool_args[action_name]
                                if isinstance(pt_data, dict):
                                    merged = dict(pt_data)
                                    merged.update(raw_args)
                                    raw_args = merged
                            tc_ev["tool_args"] = raw_args

                            # Check for agent skills activation
                            if action_name == "view_file":
                                target_f = raw_args.get("filePath") or raw_args.get("file_path") or raw_args.get("AbsolutePath") or raw_args.get("path") or ""
                                if target_f and (target_f.endswith("SKILL.md") or "/skills/" in target_f):
                                    tc_ev["is_skill"] = True
                                    if "/skills/" in target_f:
                                        tc_ev["skill_name"] = target_f.split("/skills/")[1].split("/")[0]
                                    else:
                                        tc_ev["skill_name"] = os.path.basename(os.path.dirname(target_f)) or "Skill"

                            if not is_main and traj_id:
                                tc_ev["subagent_id"] = traj_id
                                self.session_info["subagents"].add(traj_id)

                            # Correlate back to preceding unmatched hook events
                            curr_target = raw_args.get("TargetFile") or raw_args.get("filePath") or raw_args.get("target_file") or raw_args.get("targetFile") or raw_args.get("CommandLine") or raw_args.get("command") or raw_args.get("ImageName") or ""
                            curr_norm_target = os.path.basename(str(curr_target).replace("file://", ""))

                            FILE_TOOLS = {"create_file", "edit_file", "write_to_file", "view_file"}
                            for prev in reversed(self.all_events):
                                if prev.get("step_type") in ("PRE_TOOL_HOOK", "POLICY_DECISION", "PRE_TURN_HOOK") and not prev.get("_matched_tool"):
                                    p_args = prev.get("tool_args") or {}
                                    p_target = p_args.get("TargetFile") or p_args.get("filePath") or p_args.get("target_file") or p_args.get("CommandLine") or p_args.get("ImageName") or ""
                                    p_norm_target = os.path.basename(str(p_target).replace("file://", ""))

                                    target_match = bool(curr_norm_target and p_norm_target and (curr_norm_target == p_norm_target))
                                    p_tool = prev.get("tool_name") or ""
                                    c_tool = tc_ev.get("tool_name") or ""
                                    tool_match = (p_tool == c_tool) or (p_tool in FILE_TOOLS and c_tool in FILE_TOOLS)

                                    if target_match or (tool_match and not p_norm_target and not curr_norm_target) or tool_match:
                                        prev["is_main"] = is_main
                                        prev["trajectory_id"] = traj_id
                                        prev["subagent_id"] = traj_id if not is_main else None
                                        prev["_matched_tool"] = True
                                        break

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
        from agy_watch.wire_tap import extract_event_artifacts
        return extract_event_artifacts(
            ev,
            workspace_dir=self.workspace_dir,
            session_id=self.session_info.get("session_id"),
        )

    def get_agent_config(self) -> Dict[str, Any]:
        """Returns structured agent configuration metadata with fast caching."""
        if hasattr(self, "_cached_agent_config") and self._cached_agent_config is not None:
            return self._cached_agent_config
        from agy_watch.agent_config import extract_sdk_agent_config
        if not os.path.exists(self.db_path):
            self._cached_agent_config = extract_sdk_agent_config({})
            return self._cached_agent_config
        try:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT payload_json FROM wire_events WHERE message_type LIKE '%Initialize%' OR payload_json LIKE '%harnessConfig%' OR payload_json LIKE '%models%' ORDER BY seq_num ASC LIMIT 1"
            ).fetchone()
            conn.close()
            if row and row[0]:
                data = json.loads(row[0])
                self._cached_agent_config = extract_sdk_agent_config(data)
                return self._cached_agent_config
        except Exception:
            pass
        self._cached_agent_config = extract_sdk_agent_config({})
        return self._cached_agent_config
