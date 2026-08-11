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

import json
import os
import tempfile
import pytest

from agy_watch.agent_config import (
    extract_sdk_agent_config,
    extract_brain_agent_config,
    render_agent_config_panels,
)


def test_extract_sdk_agent_config():
    sample_sdk_config = {
        "initializeConversationEvent": {
            "config": {
                "cascadeId": "test-cascade-123",
                "models": [
                    {
                        "name": "gemini-3.6-flash",
                        "types": ["TEXT", "IMAGE"],
                        "geminiApiEndpoint": {
                            "options": {
                                "thinkingLevel": "high",
                                "serviceTier": "priority",
                            }
                        }
                    },
                    {
                        "name": "gemini-3.1-pro",
                        "types": ["TEXT"],
                        "vertexEndpoint": {
                            "project": "my-gcp-project",
                            "location": "us-central1",
                            "options": {
                                "thinkingLevel": "medium",
                                "serviceTier": "standard",
                            }
                        }
                    }
                ],
                "policyConfig": {
                    "rules": [
                        {
                            "name": "deny_shell",
                            "tool": "run_command",
                            "decision": "POLICY_DECISION_DENY",
                            "denyReason": "Dangerous command execution disabled",
                        },
                        {
                            "name": "allow_read",
                            "tool": "view_file",
                            "decision": "POLICY_DECISION_ALLOW",
                        }
                    ]
                },
                "harnessSideTools": {
                    "runCommand": {"enabled": True},
                    "viewFile": {"enabled": True},
                    "searchWeb": {"enabled": True},
                },
                "tools": [
                    {
                        "name": "custom_calc",
                        "description": "Calculates expressions",
                        "parametersJsonSchema": '{"type": "object"}',
                    }
                ],
                "mcpServers": [
                    {
                        "name": "chrome_devtools",
                        "command": "npx",
                        "args": ["-y", "chrome-devtools-mcp"],
                        "transport": "stdio",
                    }
                ],
                "customSubagents": [
                    {
                        "name": "reviewer",
                        "description": "Code reviewer subagent",
                        "agentMode": "AUTONOMOUS",
                    }
                ]
            }
        }
    }

    res = extract_sdk_agent_config(sample_sdk_config)
    assert res["session_type"] == "sdk"
    assert len(res["models"]) == 2
    assert res["models"][0]["name"] == "gemini-3.6-flash"
    assert res["models"][0]["thinking_level"] == "high"
    assert res["models"][0]["service_tier"] == "priority"
    assert res["models"][1]["backend"] == "Agent Platform"
    assert res["models"][1]["location"] == "us-central1"

    assert len(res["policies"]) == 2
    assert res["policies"][0]["tool"] == "run_command"
    assert res["policies"][0]["decision"] == "DENY"

    assert "runCommand" in res["builtin_tools"]
    assert len(res["custom_tools"]) == 1
    assert res["custom_tools"][0]["name"] == "custom_calc"

    assert len(res["mcp_servers"]) == 1
    assert res["mcp_servers"][0]["name"] == "chrome_devtools"

    assert "reviewer" in res["subagents"]
    assert res["subagents"]["reviewer"]["description"] == "Code reviewer subagent"


def test_extract_brain_agent_config(tmp_path):
    sdir = tmp_path / "test_session_brain"
    logs_dir = sdir / ".system_generated" / "logs"
    logs_dir.mkdir(parents=True)
    tpath = logs_dir / "transcript.jsonl"

    steps = [
        {
            "step_index": 0,
            "source": "SYSTEM",
            "type": "SYSTEM_PROMPT",
            "content": "<identity>You are a Principal Software Engineer.</identity>\nAvailable plugins:\n# chrome-devtools-plugin (file:///plugins/chrome/)\nAvailable skills:\n- uv (/plugins/science/skills/uv/SKILL.md)\n"
        },
        {
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "tool_calls": [
                {
                    "name": "define_subagent",
                    "args": {
                        "name": "code_refactorer",
                        "description": "Refactors Python code",
                        "system_prompt": "You are a code refactoring specialist.",
                        "enable_write_tools": "true",
                        "enable_mcp_tools": "false",
                    }
                }
            ]
        },
        {
            "step_index": 2,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "tool_calls": [
                {
                    "name": "invoke_subagent",
                    "args": {
                        "Subagents": [
                            {
                                "TypeName": "code_refactorer",
                                "Role": "Lead Refactoring Engineer",
                                "Prompt": "Refactor the module.",
                                "Workspace": "branch"
                            }
                        ]
                    }
                }
            ]
        }
    ]

    with open(tpath, "w", encoding="utf-8") as f:
        for s in steps:
            f.write(json.dumps(s) + "\n")

    res = extract_brain_agent_config(str(sdir))
    assert res["session_type"] == "brain"
    assert "Principal Software Engineer" in res["system_identity"]
    assert "chrome-devtools-plugin" in res["plugins"]
    assert "uv" in res["skills"]

    assert "code_refactorer" in res["subagents"]
    sub = res["subagents"]["code_refactorer"]
    assert sub["description"] == "Refactors Python code"
    assert sub["enable_write_tools"] is True
    assert sub["enable_mcp_tools"] is False


def test_render_agent_config_panels():
    config = {
        "session_type": "sdk",
        "models": [
            {
                "name": "gemini-3.6-flash",
                "types": ["TEXT", "IMAGE"],
                "backend": "Gemini API",
                "thinking_level": "high",
                "service_tier": "priority",
            }
        ],
        "policies": [
            {
                "name": "rule1",
                "tool": "run_command",
                "server_name": "*",
                "decision": "DENY",
                "deny_reason": "Security policy",
            }
        ],
        "builtin_tools": ["run_command", "view_file"],
        "custom_tools": [{"name": "tool1", "description": "desc"}],
        "mcp_servers": [{"name": "server1", "transport": "stdio"}],
        "subagents": {
            "worker": {
                "name": "worker",
                "role": "Researcher",
                "enable_write_tools": True,
                "enable_mcp_tools": True,
                "workspace": "inherit",
            }
        },
        "workspaces": ["/workspace/test"],
        "system_identity": "Agent persona description",
    }

    panels = render_agent_config_panels(config, width=80)
    assert len(panels) >= 4
