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

"""Agent & Sub-agent Configuration & Model Observability Engine.

Extracts declarative model options (name, thinking level, service tier),
safety policies, tool definitions, MCP servers, and subagent profiles
for both SDK and non-SDK (Brain) sessions.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

from rich.console import RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def extract_sdk_agent_config(config_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Extracts a structured configuration summary from an SDK HarnessConfig dictionary."""
    if not isinstance(config_dict, dict):
        return {}

    # Handle wrapper keys like initializeConversationEvent or config
    if "initializeConversationEvent" in config_dict:
        config_dict = config_dict["initializeConversationEvent"].get("config", {})
    elif "initialize_conversation_event" in config_dict:
        config_dict = config_dict["initialize_conversation_event"].get("config", {})
    elif "config" in config_dict and isinstance(config_dict["config"], dict):
        config_dict = config_dict["config"]

    res: Dict[str, Any] = {
        "session_type": "sdk",
        "models": [],
        "policies": [],
        "builtin_tools": [],
        "custom_tools": [],
        "mcp_servers": [],
        "subagents": {},
        "retry_config": {},
        "workspaces": [],
        "system_identity": "",
    }

    # 1. Models & Endpoints
    models_raw = config_dict.get("models") or []
    for m in models_raw:
        if not isinstance(m, dict):
            continue
        m_name = m.get("name") or "gemini-3.6-flash"
        m_types = m.get("types") or ["TEXT"]

        endpoint = (
            m.get("geminiApiEndpoint")
            or m.get("gemini_api_endpoint")
            or m.get("vertexEndpoint")
            or m.get("vertex_endpoint")
            or m.get("gemmaEndpoint")
            or m.get("gemma_endpoint")
            or m.get("customEndpoint")
            or m.get("custom_endpoint")
            or {}
        )

        backend = "Gemini API"
        if "vertexEndpoint" in m or "vertex_endpoint" in m:
            backend = "Agent Platform"
        elif "gemmaEndpoint" in m or "gemma_endpoint" in m:
            backend = "Gemma"
        elif "customEndpoint" in m or "custom_endpoint" in m:
            backend = "Custom Backend"

        options = endpoint.get("options") or {}
        thinking_level = options.get("thinkingLevel") or options.get("thinking_level") or "default"
        service_tier = options.get("serviceTier") or options.get("service_tier") or "standard"

        res["models"].append({
            "name": m_name,
            "types": m_types,
            "backend": backend,
            "thinking_level": thinking_level,
            "service_tier": service_tier,
            "project": endpoint.get("project"),
            "location": endpoint.get("location"),
            "base_url": endpoint.get("baseUrl") or endpoint.get("base_url"),
        })

    if not res["models"]:
        res["models"].append({
            "name": "gemini-3.6-flash",
            "types": ["TEXT"],
            "backend": "Gemini API",
            "thinking_level": "default",
            "service_tier": "standard",
        })

    # 2. Policies
    policy_cfg = config_dict.get("policyConfig") or config_dict.get("policy_config") or {}
    rules_raw = policy_cfg.get("rules") or []
    for r in rules_raw:
        if not isinstance(r, dict):
            continue
        decision_raw = r.get("decision", "POLICY_DECISION_ALLOW")
        decision_str = str(decision_raw).replace("POLICY_DECISION_", "")
        res["policies"].append({
            "name": r.get("name") or "",
            "tool": r.get("tool") or "*",
            "server_name": r.get("serverName") or r.get("server_name") or "*",
            "decision": decision_str,
            "deny_reason": r.get("denyReason") or r.get("deny_reason") or "",
            "is_dynamic": bool(r.get("isDynamic") or r.get("is_dynamic")),
        })

    # 3. Built-in Harness Tools
    harness_tools = config_dict.get("harnessSideTools") or config_dict.get("harness_side_tools") or {}
    for tool_key, tool_cfg in harness_tools.items():
        if isinstance(tool_cfg, dict) and tool_cfg.get("enabled"):
            res["builtin_tools"].append(tool_key)
        elif tool_cfg is True:
            res["builtin_tools"].append(tool_key)

    # 4. Custom Tools
    tools_raw = config_dict.get("tools") or []
    for t in tools_raw:
        if not isinstance(t, dict):
            continue
        res["custom_tools"].append({
            "name": t.get("name") or "",
            "description": t.get("description") or "",
            "parameters_json_schema": t.get("parametersJsonSchema") or t.get("parameters_json_schema") or "{}",
            "defer_loading": bool(t.get("deferLoading") or t.get("defer_loading")),
        })

    # 5. MCP Servers
    mcp_raw = config_dict.get("mcpServers") or config_dict.get("mcp_servers") or []
    for s in mcp_raw:
        if not isinstance(s, dict):
            continue
        res["mcp_servers"].append({
            "name": s.get("name") or "",
            "command": s.get("command") or "",
            "args": s.get("args") or [],
            "url": s.get("url") or "",
            "transport": s.get("transport") or "stdio",
        })

    # 6. Custom Declared Subagents
    subs_raw = config_dict.get("customSubagents") or config_dict.get("custom_subagents") or []
    for sa in subs_raw:
        if not isinstance(sa, dict):
            continue
        s_name = sa.get("name") or "custom_agent"
        res["subagents"][s_name] = {
            "name": s_name,
            "description": sa.get("description") or "",
            "agent_mode": sa.get("agentMode") or sa.get("agent_mode") or "AUTONOMOUS",
            "tools_count": len(sa.get("tools") or []),
        }

    # 7. Workspaces
    ws_raw = config_dict.get("workspaces") or []
    for w in ws_raw:
        if isinstance(w, dict):
            fws = w.get("filesystemWorkspace") or w.get("filesystem_workspace") or {}
            d = fws.get("directory")
            if d:
                res["workspaces"].append(d)

    # 8. Retries & Compaction
    retry_cfg = config_dict.get("retryConfig") or config_dict.get("retry_config") or {}
    res["retry_config"] = retry_cfg
    res["compaction_threshold"] = config_dict.get("compactionThreshold") or config_dict.get("compaction_threshold")

    return res


def extract_brain_agent_config(
    session_dir: str,
    transcript_path: Optional[str] = None,
    conv_db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Extracts agent, subagent, tool, and model metadata for a Brain session."""
    res: Dict[str, Any] = {
        "session_type": "brain",
        "models": [
            {
                "name": "gemini-3.6-flash (Adaptive/Default)",
                "types": [],
                "backend": "Google Antigravity Brain",
                "thinking_level": "Adaptive",
                "service_tier": "Standard",
            }
        ],
        "policies": [],
        "builtin_tools": [],
        "custom_tools": [],
        "mcp_servers": [],
        "subagents": {},
        "subagent_role_map": {},  # subagent_uuid -> Role string
        "workspaces": [],
        "plugins": [],
        "skills": [],
        "system_identity": "",
    }

    if not os.path.isdir(session_dir):
        return res

    # 1. Inspect transcript for define_subagent and invoke_subagent calls & system prompt
    t_path = transcript_path or os.path.join(session_dir, ".system_generated", "logs", "transcript.jsonl")
    if os.path.exists(t_path):
        try:
            last_invoke_subagents: List[Dict[str, Any]] = []
            with open(t_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue

                    # Check for define_subagent tool calls
                    for tc in d.get("tool_calls", []):
                        name = tc.get("name")
                        args = tc.get("args") or {}
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except Exception:
                                pass

                        if name == "define_subagent" and isinstance(args, dict):
                            sa_name = args.get("name", "").strip('"\'')
                            if sa_name:
                                res["subagents"][sa_name] = {
                                    "name": sa_name,
                                    "description": args.get("description", "").strip('"\''),
                                    "system_prompt": args.get("system_prompt", ""),
                                    "enable_write_tools": bool(args.get("enable_write_tools") in (True, "true", "True")),
                                    "enable_mcp_tools": bool(args.get("enable_mcp_tools") in (True, "true", "True")),
                                    "enable_subagent_tools": bool(args.get("enable_subagent_tools") in (True, "true", "True")),
                                }

                        elif name == "invoke_subagent" and isinstance(args, dict):
                            subs_arg = args.get("Subagents") or []
                            if isinstance(subs_arg, str):
                                try:
                                    subs_arg = json.loads(subs_arg)
                                except Exception:
                                    subs_arg = []
                            if isinstance(subs_arg, list):
                                last_invoke_subagents = subs_arg
                                for item in subs_arg:
                                    if isinstance(item, dict):
                                        t_name = item.get("TypeName") or item.get("Role") or "Subagent"
                                        role = item.get("Role") or t_name
                                        prompt = item.get("Prompt") or ""
                                        ws_mode = item.get("Workspace") or "inherit"
                                        sub_key = role if (t_name in ("self", "Subagent") or t_name in res["subagents"]) else t_name
                                        if sub_key not in res["subagents"]:
                                            res["subagents"][sub_key] = {
                                                "name": t_name,
                                                "role": role,
                                                "description": prompt[:120],
                                                "workspace": ws_mode,
                                                "enable_write_tools": True,
                                                "enable_mcp_tools": True,
                                                "enable_subagent_tools": True,
                                            }

                    # Map invoke return values to subagent conversation IDs
                    step_content = d.get("content") or ""
                    if d.get("type") == "INVOKE_SUBAGENT" or "Created the following subagents:" in step_content:
                        uuids = [m.group(1) for m in re.finditer(r'conversationId[\\"]*:\s*[\\"]*([a-zA-Z0-9_\-]+)', step_content, re.IGNORECASE)]
                        for idx, sub_uuid in enumerate(uuids):
                            if idx < len(last_invoke_subagents) and isinstance(last_invoke_subagents[idx], dict):
                                res["subagent_role_map"][sub_uuid] = last_invoke_subagents[idx].get("Role") or last_invoke_subagents[idx].get("TypeName") or "Worker Subagent"
                            elif sub_uuid not in res["subagent_role_map"]:
                                res["subagent_role_map"][sub_uuid] = "Worker Subagent"

                    # Parse initial system identity and declared plugins/skills from step 0 or system prompts
                    if d.get("step_index") in (0, 1) and d.get("source") in ("SYSTEM", "USER_EXPLICIT"):
                        txt = d.get("content") or ""
                        if "<identity>" in txt:
                            match = re.search(r"<identity>(.*?)</identity>", txt, re.DOTALL)
                            if match:
                                res["system_identity"] = match.group(1).strip()
                        if "Available plugins:" in txt or "Available skills:" in txt:
                            for p_match in re.finditer(r'#\s+([a-zA-Z0-9_\-]+)\s+\(file:///', txt):
                                p_name = p_match.group(1)
                                if p_name not in res["plugins"]:
                                    res["plugins"].append(p_name)
                            for s_match in re.finditer(r'-\s+([a-zA-Z0-9_\-]+)\s+\(/.*?SKILL\.md\)', txt):
                                s_name = s_match.group(1)
                                if s_name not in res["skills"]:
                                    res["skills"].append(s_name)

        except Exception:
            pass

    # 2. Extract workspace paths and metadata from sibling conversations/<sid>.db
    conv_db = conv_db_path or os.path.join(os.path.dirname(os.path.dirname(session_dir)), "conversations", f"{os.path.basename(session_dir)}.db")
    if os.path.exists(conv_db):
        try:
            conn = sqlite3.connect(f"file:{conv_db}?mode=ro", uri=True)
            row = conn.execute("SELECT data FROM trajectory_metadata_blob LIMIT 1").fetchone()
            if row and row[0]:
                from agy_watch.brain_watcher import _decode_proto_fields
                decoded = _decode_proto_fields(row[0])
                for field_vals in decoded.values():
                    for v in field_vals:
                        if isinstance(v, bytes):
                            s = v.decode("utf-8", errors="ignore")
                            if s.startswith("file:///"):
                                clean_p = s.replace("file:///", "/")
                                if clean_p not in res["workspaces"]:
                                    res["workspaces"].append(clean_p)
            conn.close()
        except Exception:
            pass

    if not res["workspaces"]:
        res["workspaces"].append(session_dir)

    return res


def render_agent_config_panels(config: Dict[str, Any], width: int = 80) -> List[RenderableType]:
    """Renders structured Rich renderables for the Agent Config Inspector tab using vertical card layouts."""
    panels: List[RenderableType] = []

    # 1. Models & Inference Configuration
    models = config.get("models") or []
    m_text = Text()
    for i, m in enumerate(models):
        m_name = m.get("name", "gemini-3.6-flash")
        backend = m.get("backend", "Gemini API")
        if m.get("location"):
            backend += f" ({m['location']})"
        if m.get("project"):
            backend += f" [project: {m['project']}]"

        th = str(m.get("thinking_level", "default")).capitalize()
        st = str(m.get("service_tier", "standard")).capitalize()

        if i > 0:
            m_text.append("\n\n")
        m_text.append("• Model: ", style="bold green")
        m_text.append(f"{m_name}\n", style="bold cyan")
        m_text.append(f"  Target: ", style="bold")
        m_text.append(f"{backend}\n", style="yellow")
        m_text.append(f"  Thinking: ", style="bold")
        m_text.append(f"{th}", style="magenta")
        m_text.append("  |  Service Tier: ", style="bold")
        m_text.append(f"{st}", style="green")

        types_list = m.get("types") or []
        if types_list and config.get("session_type") != "brain":
            types_str = ", ".join(types_list)
            m_text.append("  |  Types: ", style="bold")
            m_text.append(f"{types_str}", style="dim")

    if not m_text.plain.strip():
        m_text.append("• Model: gemini-3.6-flash (Adaptive/Default)\n  Target: Google Antigravity Brain", style="dim")

    panels.append(
        Panel(
            m_text,
            title="[bold green]⚡ Model & Inference Specs[/bold green]",
            border_style="green",
            padding=(0, 1),
        )
    )

    # 2. Safety Policies & Rule Matrix
    policies = config.get("policies") or []
    if policies:
        p_text = Text()
        for i, r in enumerate(policies):
            dec = r.get("decision", "ALLOW")
            dec_badge = f"[{dec}]"
            dec_style = "bold green" if dec == "ALLOW" else ("bold red" if dec == "DENY" else "bold yellow")

            r_name = r.get("name") or "Policy Rule"
            tool_target = r.get("tool", "*")
            srv = r.get("server_name", "*")
            reason = r.get("deny_reason") or ("Dynamic Hook" if r.get("is_dynamic") else "Static Rule")

            if i > 0:
                p_text.append("\n\n")
            p_text.append("• Rule: ", style="bold red")
            p_text.append(f"{r_name} ", style="bold white")
            p_text.append(f"{dec_badge}\n", style=dec_style)
            p_text.append(f"  Tool Target: ", style="bold")
            p_text.append(f"{tool_target}", style="cyan")
            p_text.append("  |  Server: ", style="bold")
            p_text.append(f"{srv}\n", style="dim")
            p_text.append(f"  Mode / Reason: ", style="bold")
            p_text.append(f"{reason}", style="dim")

        panels.append(
            Panel(
                p_text,
                title="[bold red]🛡️ Safety Policies & Access Rules[/bold red]",
                border_style="red",
                padding=(0, 1),
            )
        )

    # 3. Subagents & Multi-Agent Archetypes
    subagents = config.get("subagents") or {}
    if subagents:
        sa_text = Text()
        for i, (name, sa) in enumerate(subagents.items()):
            role = sa.get("role") or sa.get("agent_mode") or "Autonomous"
            w_access = "Enabled" if sa.get("enable_write_tools", True) else "Disabled"
            mcp_access = "Enabled" if sa.get("enable_mcp_tools", True) else "Disabled"
            ws = sa.get("workspace") or "inherit"
            desc = sa.get("description") or ""

            if i > 0:
                sa_text.append("\n\n")
            sa_text.append("• Subagent: ", style="bold cyan")
            sa_text.append(f"{name}\n", style="bold white")
            sa_text.append(f"  Role / Mode: ", style="bold")
            sa_text.append(f"{role}", style="yellow")
            sa_text.append("  |  Workspace: ", style="bold")
            sa_text.append(f"{ws}\n", style="dim")
            sa_text.append(f"  Permissions: ", style="bold")
            sa_text.append(f"Write: {w_access}", style="green" if w_access == "Enabled" else "red")
            sa_text.append("  |  ")
            sa_text.append(f"MCP: {mcp_access}", style="green" if mcp_access == "Enabled" else "red")
            if desc:
                sa_text.append(f"\n  Description: ", style="bold")
                sa_text.append(f"{desc}", style="dim italic")

        panels.append(
            Panel(
                sa_text,
                title=f"[bold cyan]🤖 Multi-Agent Archetypes & Subagents ({len(subagents)})[/bold cyan]",
                border_style="cyan",
                padding=(0, 1),
            )
        )

    # 4. Tools & MCP Servers
    builtin_tools = config.get("builtin_tools") or []
    custom_tools = config.get("custom_tools") or []
    mcp_servers = config.get("mcp_servers") or []
    plugins = config.get("plugins") or []
    skills = config.get("skills") or []

    tools_text = Text()
    if builtin_tools:
        tools_text.append("• Built-in Tools: ", style="bold green")
        tools_text.append(", ".join(builtin_tools) + "\n", style="cyan")

    if custom_tools:
        tools_text.append(f"• Custom Tools ({len(custom_tools)}): ", style="bold magenta")
        tools_text.append(", ".join([t["name"] for t in custom_tools]) + "\n", style="magenta")

    if mcp_servers:
        tools_text.append(f"• MCP Servers ({len(mcp_servers)}): ", style="bold yellow")
        mcp_summary = [f"{s['name']} ({s.get('transport', 'stdio')})" for s in mcp_servers]
        tools_text.append(", ".join(mcp_summary) + "\n", style="yellow")

    if plugins:
        tools_text.append(f"• Plugins ({len(plugins)}): ", style="bold blue")
        tools_text.append(", ".join(plugins) + "\n", style="blue")

    if skills:
        tools_text.append(f"• Active Skills ({len(skills)}): ", style="bold dim")
        tools_text.append(", ".join(skills[:8]) + ("..." if len(skills) > 8 else "") + "\n", style="dim")

    if not tools_text.plain.strip():
        tools_text.append("• Standard Antigravity Agent Toolset Enabled (run_command, view_file, edit_file, etc.)", style="dim")

    panels.append(
        Panel(
            tools_text,
            title="[bold yellow]🔧 Tools, Plugins & MCP Integrations[/bold yellow]",
            border_style="yellow",
            padding=(0, 1),
        )
    )

    # 5. Workspaces & Identity
    workspaces = config.get("workspaces") or []
    identity = config.get("system_identity") or ""
    ws_text = Text()
    if workspaces:
        ws_text.append("• Workspace Roots:\n", style="bold")
        for w in workspaces:
            ws_text.append(f"    {w}\n", style="dim cyan")
    if identity:
        ws_text.append("\n• Identity & System Persona:\n", style="bold")
        ws_text.append(f"  {identity[:300]}...\n" if len(identity) > 300 else f"  {identity}\n", style="dim italic")

    if ws_text.plain.strip():
        panels.append(
            Panel(
                ws_text,
                title="[bold dim]📂 Workspace & Persona[/bold dim]",
                border_style="dim",
                padding=(0, 1),
            )
        )

    return panels
