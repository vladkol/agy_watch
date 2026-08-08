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

"""Tests for dedicated Antigravity SDK tool visualizers, error states, and tree labels."""

import pytest
from rich.console import Console

from agy_watch.tool_renderers import (
    build_tool_tree_label,
    render_tool_event,
    render_run_command,
    render_edit_file,
    render_create_file,
    render_view_file,
    render_list_dir,
    render_search_dir,
    render_find_file,
    render_invoke_subagent,
    render_ask_question,
    render_generate_image,
    render_search_web,
    render_read_url_content,
    render_finish,
    render_generic_tool,
)


def _render_to_string(renderable) -> str:
    """Helper to render Rich renderable to plain string."""
    console = Console(width=100, record=True, color_system=None)
    console.print(renderable)
    return console.export_text()


def test_run_command_rendering_and_exit_code():
    """Verifies run_command rendering with command, cwd, and output."""
    ev = {
        "tool_name": "run_command",
        "tool_args": {
            "CommandLine": "git status --short",
            "Cwd": "/Users/test/repo",
            "NotificationTimeoutSeconds": 30,
        },
        "payload": {
            "stepUpdate": {
                "output": "M agy_watch/tui.py\n?? tests/test_tools.py",
            }
        },
        "state": "STATE_DONE",
    }
    rendered = _render_to_string(render_tool_event(ev))
    assert "RUN COMMAND" in rendered
    assert "git status --short" in rendered
    assert "/Users/test/repo" in rendered
    assert "M agy_watch/tui.py" in rendered

    # Verify tree label
    label = build_tool_tree_label(ev).plain
    assert "TOOL: run_command" in label
    assert "git status" in label


def test_edit_file_unified_diff_rendering():
    """Verifies edit_file generates and renders colorized unified diffs."""
    ev = {
        "tool_name": "edit_file",
        "tool_args": {
            "TargetFile": "/workspace/src/agent.py",
            "Instruction": "Add timeout parameter to run_command",
            "TargetContent": "def run():\n    pass",
            "ReplacementContent": "def run(timeout=30):\n    pass",
            "StartLine": 10,
            "EndLine": 12,
        },
        "state": "STATE_DONE",
    }
    rendered = _render_to_string(render_tool_event(ev))
    assert "EDIT FILE (UNIFIED DIFF)" in rendered
    assert "agent.py" in rendered
    assert "Add timeout parameter" in rendered
    assert "-def run():" in rendered
    assert "+def run(timeout=30):" in rendered

    label = build_tool_tree_label(ev).plain
    assert "TOOL: edit_file" in label
    assert "agent.py" in label


def test_create_file_rendering():
    """Verifies create_file rendering with file size and syntax highlighting."""
    ev = {
        "tool_name": "create_file",
        "tool_args": {
            "TargetFile": "/workspace/src/utils.py",
            "CodeContent": "def add(a, b):\n    return a + b\n",
            "Overwrite": True,
            "Description": "Math utility functions",
        },
        "state": "STATE_DONE",
    }
    rendered = _render_to_string(render_tool_event(ev))
    assert "CREATE FILE" in rendered
    assert "utils.py" in rendered
    assert "Math utility functions" in rendered
    assert "def add(a, b):" in rendered

    label = build_tool_tree_label(ev).plain
    assert "TOOL: create_file" in label
    assert "+ utils.py" in label


def test_view_file_rendering():
    """Verifies view_file snippet rendering."""
    ev = {
        "tool_name": "view_file",
        "tool_args": {
            "AbsolutePath": "/workspace/pyproject.toml",
            "StartLine": 1,
            "EndLine": 10,
        },
        "payload": {
            "stepUpdate": {
                "content": '[project]\nname = "agy_watch"\nversion = "0.1.0"\n',
            }
        },
        "state": "STATE_DONE",
    }
    rendered = _render_to_string(render_tool_event(ev))
    assert "VIEW FILE" in rendered
    assert "pyproject.toml" in rendered
    assert "1 to 10" in rendered
    assert "agy_watch" in rendered

    label = build_tool_tree_label(ev).plain
    assert "TOOL: view_file" in label
    assert "pyproject.toml" in label


def test_list_dir_and_search_dir_rendering():
    """Verifies list_dir and search_dir formatted tables."""
    ev_list = {
        "tool_name": "list_dir",
        "tool_args": {"DirectoryPath": "/workspace"},
        "payload": {
            "stepUpdate": {
                "entries": [
                    {"name": "src", "is_directory": True, "file_size": 0},
                    {"name": "README.md", "is_directory": False, "file_size": 2048},
                ]
            }
        },
        "state": "STATE_DONE",
    }
    rendered_list = _render_to_string(render_tool_event(ev_list))
    assert "DIRECTORY LISTING" in rendered_list
    assert "src" in rendered_list
    assert "README.md" in rendered_list
    assert "2.0 KB" in rendered_list

    ev_search = {
        "tool_name": "search_dir",
        "tool_args": {"Query": "BuiltinTools", "SearchPath": "/workspace"},
        "payload": {
            "stepUpdate": {
                "matches": [
                    {"Filename": "main.py", "LineNumber": 42, "LineContent": "from google.antigravity.types import BuiltinTools"},
                ]
            }
        },
        "state": "STATE_DONE",
    }
    rendered_search = _render_to_string(render_tool_event(ev_search))
    assert "SEARCH DIRECTORY (GREP)" in rendered_search
    assert "BuiltinTools" in rendered_search
    assert "main.py" in rendered_search
    assert "42" in rendered_search


def test_jsonl_grep_search_and_list_dir_rendering():
    """Verifies that grep_search and list_dir output formatted as newline-delimited JSON or raw text is correctly parsed into tables."""
    # 1. JSONL Grep Search Result
    grep_raw_text = """Created At: 2026-08-07T16:26:47-07:00
Completed At: 2026-08-07T16:26:47-07:00
{"File":"/Users/vladkol/antigravity-sdk-agent/agy_watch/tui.py","LineNumber":1335,"LineContent":" def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:"}
{"File":"/Users/vladkol/antigravity-sdk-agent/agy_watch/tui.py","LineNumber":1371,"LineContent":" def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:"}
"""
    ev_grep_jsonl = {
        "tool_name": "grep_search",
        "action_type": "grep_search",
        "tool_args": {"Query": "on_tree", "SearchPath": "/Users/vladkol/antigravity-sdk-agent/agy_watch/tui.py"},
        "text": grep_raw_text,
        "state": "STATE_DONE",
    }
    rendered_grep = _render_to_string(render_tool_event(ev_grep_jsonl))
    assert "SEARCH DIRECTORY (GREP)" in rendered_grep
    assert 'Search Query: "on_tree"' in rendered_grep
    assert "tui.py" in rendered_grep
    assert "1335" in rendered_grep
    assert "on_tree_node_selected" in rendered_grep
    assert "1371" in rendered_grep
    assert "on_tree_node_highlighted" in rendered_grep

    # 2. JSONL Directory Listing Result
    list_raw_text = """Created At: 2026-08-03T15:34:42-07:00
Completed At: 2026-08-03T15:34:42-07:00
{"name":".DS_Store", "sizeBytes":"8196"}
{"name":".cache", "isDir":true}
{"name":".git", "isDir":true}
{"name":"README.md", "sizeBytes":"1024"}
"""
    ev_list_jsonl = {
        "tool_name": "list_dir",
        "action_type": "list_dir",
        "tool_args": {"DirectoryPath": "/workspace"},
        "text": list_raw_text,
        "state": "STATE_DONE",
    }
    rendered_list = _render_to_string(render_tool_event(ev_list_jsonl))
    assert "DIRECTORY LISTING" in rendered_list
    assert "README.md" in rendered_list
    assert "1.0 KB" in rendered_list
    assert ".cache" in rendered_list
    assert "DIR" in rendered_list

    # 3. Raw Text Find Files
    find_raw_text = """Created At: 2026-08-07T12:00:00Z
Completed At: 2026-08-07T12:00:01Z
/workspace/src/agent.py
/workspace/src/utils.py
"""
    ev_find = {
        "tool_name": "find_by_name",
        "action_type": "find_by_name",
        "tool_args": {"Pattern": "*.py", "SearchDirectory": "/workspace"},
        "text": find_raw_text,
        "state": "STATE_DONE",
    }
    rendered_find = _render_to_string(render_tool_event(ev_find))
    assert "FIND FILES" in rendered_find
    assert "/workspace/src/agent.py" in rendered_find
    assert "/workspace/src/utils.py" in rendered_find

    # 4. SDK Search Directory Event with plain text description (regression for NameError 're')
    ev_sdk_search = {
        "tool_name": "search_directory",
        "action_type": "search_directory",
        "tool_args": {
            "Query": "fetch_unstructured_meeting_notes",
            "SearchPath": "/workspace",
            "query": "fetch_unstructured_meeting_notes",
            "numResults": 0,
        },
        "payload": {
            "stepUpdate": {
                "text": "Search repo for meeting notes references",
                "state": "STATE_DONE",
            }
        },
        "state": "STATE_DONE",
    }
    rendered_sdk = _render_to_string(render_tool_event(ev_sdk_search))
    assert "SEARCH DIRECTORY (GREP)" in rendered_sdk
    assert "No matches found." in rendered_sdk


def test_invoke_subagent_rendering():
    """Verifies invoke_subagent worker delegation cards."""
    ev = {
        "tool_name": "invoke_subagent",
        "tool_args": {
            "Subagents": [
                {"Role": "Mathematician", "TypeName": "self", "Prompt": "Calculate sum of primes"},
                {"Role": "Reviewer", "TypeName": "self", "Prompt": "Review test coverage"},
            ]
        },
        "state": "STATE_DONE",
    }
    rendered = _render_to_string(render_tool_event(ev))
    assert "INVOKE SUBAGENTS" in rendered
    assert "Mathematician" in rendered
    assert "Calculate sum of primes" in rendered
    assert "Reviewer" in rendered

    label = build_tool_tree_label(ev).plain
    assert "2 workers" in label


def test_ask_question_rendering():
    """Verifies ask_question option list with selected answer badge in both flat and protobuf wire format."""
    # 1. Flat schema with direct answer
    ev = {
        "tool_name": "ask_question",
        "tool_args": {
            "question": "Which backend to use?",
            "options": ["SQLite", "PostgreSQL", "Firestore"],
            "is_multi_select": False,
        },
        "payload": {
            "stepUpdate": {
                "answer": "SQLite",
            }
        },
        "state": "STATE_DONE",
    }
    rendered = _render_to_string(render_tool_event(ev))
    assert "USER QUESTION" in rendered
    assert "Which backend to use?" in rendered
    assert "[✓] SQLite  ◄── SELECTED" in rendered
    assert "[ ] PostgreSQL" in rendered

    # 2. Real protobuf wire format with multipleChoice and selectedChoiceIndices
    ev_wire = {
        "tool_name": "ask_question",
        "step_index": 2,
        "tool_args": {},
        "payload": {
            "stepUpdate": {
                "state": "STATE_DONE",
                "questionsRequest": {
                    "questions": [
                        {
                            "multipleChoice": {
                                "question": "What file or pattern are you searching for?",
                                "choices": [
                                    "Search in current workspace",
                                    "Search for specific extension",
                                    "Search in directory path",
                                ],
                                "isMultiSelect": False,
                            }
                        }
                    ]
                },
                "response": {
                    "answers": [
                        {"multipleChoiceAnswer": {"selectedChoiceIndices": [1]}}
                    ]
                },
            }
        },
        "state": "STATE_DONE",
    }
    rendered_wire = _render_to_string(render_tool_event(ev_wire))
    assert "What file or pattern are you searching for?" in rendered_wire
    assert "[ ] Search in current workspace" in rendered_wire
    assert "[✓] Search for specific extension  ◄── SELECTED" in rendered_wire
    assert "[ ] Search in directory path" in rendered_wire

    # 3. Active waiting state banner
    ev_waiting = {
        "tool_name": "ask_question",
        "state": "STATE_WAITING_FOR_USER",
        "payload": {
            "stepUpdate": {
                "state": "STATE_WAITING_FOR_USER",
                "questionsRequest": {
                    "questions": [
                        {
                            "multipleChoice": {
                                "question": "Confirm action?",
                                "choices": ["Yes", "No"],
                            }
                        }
                    ]
                },
            }
        },
    }
    rendered_waiting = _render_to_string(render_tool_event(ev_waiting))
    assert "WAITING FOR USER INPUT" in rendered_waiting


def test_generate_image_metadata_card():
    """Verifies generate_image renders clean metadata card with OS viewer shortcut (no ANSI art)."""
    ev = {
        "tool_name": "generate_image",
        "tool_args": {
            "ImageName": "golden_retriever.png",
            "Prompt": "Cute golden retriever puppy in field",
            "AspectRatio": "16:9",
        },
        "artifacts": [
            {"path": "/workspace/brain/cas_123/golden_retriever.png", "filename": "golden_retriever.png"}
        ],
        "state": "STATE_DONE",
    }
    rendered = _render_to_string(render_tool_event(ev))
    assert "GENERATED IMAGE ARTIFACT" in rendered
    assert "golden_retriever.png" in rendered
    assert "16:9" in rendered
    assert "Cute golden retriever puppy" in rendered
    assert "Press 'o' or open the file in your OS default viewer" in rendered


def test_search_web_and_read_url_content():
    """Verifies search_web and read_url_content markdown renderers."""
    ev_web = {
        "tool_name": "search_web",
        "tool_args": {"query": "Antigravity SDK documentation"},
        "payload": {
            "stepUpdate": {
                "results": "### Search Results\n- [Google Cloud](https://cloud.google.com): SDK reference guide.",
            }
        },
        "state": "STATE_DONE",
    }
    rendered_web = _render_to_string(render_tool_event(ev_web))
    assert "WEB SEARCH" in rendered_web
    assert "Antigravity SDK documentation" in rendered_web

    ev_url = {
        "tool_name": "read_url_content",
        "tool_args": {"url": "https://example.com/api"},
        "payload": {
            "stepUpdate": {
                "content": "# Example API\nEndpoint documentation for v1.",
            }
        },
        "state": "STATE_DONE",
    }
    rendered_url = _render_to_string(render_tool_event(ev_url))
    assert "READ URL CONTENT" in rendered_url
    assert "https://example.com/api" in rendered_url


def test_error_and_policy_block_banners():
    """Verifies error banners and policy rejection cards."""
    # Policy Block
    ev_policy = {
        "tool_name": "run_command",
        "tool_args": {"CommandLine": "rm -rf /"},
        "error": {"error_message": "Policy violation: Command denied by workspace_only policy"},
        "state": "STATE_ERROR",
    }
    rendered_policy = _render_to_string(render_tool_event(ev_policy))
    assert "SECURITY / POLICY CONSTRAINT" in rendered_policy
    assert "EXECUTION BLOCKED BY POLICY" in rendered_policy
    assert "workspace_only" in rendered_policy

    # General Exception
    ev_err = {
        "tool_name": "edit_file",
        "tool_args": {"TargetFile": "/invalid/path.py"},
        "error": {"error_message": "FileNotFoundError: [Errno 2] No such file", "http_code": 404},
        "state": "STATE_ERROR",
    }
    rendered_err = _render_to_string(render_tool_event(ev_err))
    assert "ERROR / EXCEPTION" in rendered_err
    assert "EXECUTION FAILED [HTTP 404]" in rendered_err
    assert "FileNotFoundError" in rendered_err

    # Tree label error icon
    label = build_tool_tree_label(ev_err).plain
    assert "❌" in label


def test_real_wire_format_list_directory():
    """Verifies listDirectory with camelCase results and file:// URI decoding."""
    ev = {
        "tool_name": "list_directory",
        "payload": {
            "stepUpdate": {
                "listDirectory": {
                    "directoryPath": "file:///Users/vladkol/antigravity-sdk-agent/.local/tasks/yolo_subagents_run",
                    "results": [
                        {"name": ".trajectories", "isDirectory": True, "fileSize": 0},
                        {"name": "worker1.txt", "isDirectory": False, "fileSize": 1024},
                    ],
                }
            }
        },
        "state": "STATE_DONE",
    }
    rendered = _render_to_string(render_tool_event(ev))
    assert "DIRECTORY LISTING" in rendered
    assert "/Users/vladkol/antigravity-sdk-agent/.local/tasks/yolo_subagents_run" in rendered
    assert ".trajectories" in rendered
    assert "worker1.txt" in rendered
    assert "📁 DIR" in rendered
    assert "📄 FILE" in rendered

    label = build_tool_tree_label(ev).plain
    assert "TOOL: list_directory" in label
    assert "yolo_subagents_run" in label


def test_real_wire_format_edit_file_diff_block():
    """Verifies editFile with diffBlock line reconstruction and file:// URI path."""
    ev = {
        "tool_name": "edit_file",
        "payload": {
            "stepUpdate": {
                "editFile": {
                    "filePath": "file:///Users/vladkol/antigravity-sdk-agent/.local/tasks/yolo_subagents_run/worker2.txt",
                    "diffBlock": [
                        {
                            "startLine": 0,
                            "endLine": 0,
                            "lines": [
                                {"text": "YOLO Subagent 2 Done", "action": "LINE_ACTION_INSERT"},
                                {"text": "Old line deleted", "action": "LINE_ACTION_DELETE"},
                                {"text": "Unchanged line", "action": "LINE_ACTION_KEEP"},
                            ],
                        }
                    ],
                }
            }
        },
        "state": "STATE_DONE",
    }
    rendered = _render_to_string(render_tool_event(ev))
    assert "EDIT FILE (UNIFIED DIFF)" in rendered
    assert "/Users/vladkol/antigravity-sdk-agent/.local/tasks/yolo_subagents_run/worker2.txt" in rendered
    assert "+YOLO Subagent 2 Done" in rendered
    assert "-Old line deleted" in rendered
    assert " Unchanged line" in rendered

    label = build_tool_tree_label(ev).plain
    assert "TOOL: edit_file" in label
    assert "worker2.txt" in label


def test_real_wire_format_run_command():
    """Verifies runCommand with camelCase fields (commandLine, workingDir, exitCode, combinedOutput)."""
    ev = {
        "tool_name": "run_command",
        "payload": {
            "stepUpdate": {
                "runCommand": {
                    "commandLine": "pytest tests/ -v",
                    "workingDir": "/Users/vladkol/antigravity-sdk-agent",
                    "exitCode": 0,
                    "combinedOutput": "================ 26 passed in 1.2s ================",
                }
            }
        },
        "state": "STATE_DONE",
    }
    rendered = _render_to_string(render_tool_event(ev))
    assert "RUN COMMAND" in rendered
    assert "pytest tests/ -v" in rendered
    assert "/Users/vladkol/antigravity-sdk-agent" in rendered
    assert "[EXIT 0]" in rendered
    assert "26 passed in 1.2s" in rendered

    label = build_tool_tree_label(ev).plain
    assert "TOOL: run_command" in label
    assert "pytest tests/ -v" in label


def test_render_mcp_tool():
    """Verifies render_mcp_tool with server metadata, parameters table, and tree label."""
    ev = {
        "tool_name": "mcp_tool",
        "tool_args": {
            "serverName": "everything",
            "toolName": "echo",
            "argumentsJson": '{"message": "Trip planning verified"}',
        },
        "payload": {
            "stepUpdate": {
                "output": "Echo: Trip planning verified",
            }
        },
        "state": "STATE_DONE",
    }
    rendered = _render_to_string(render_tool_event(ev))
    assert "MCP TOOL: everything / echo" in rendered
    assert "everything" in rendered
    assert "Trip planning verified" in rendered
    assert "Model Context Protocol" in rendered

    label = build_tool_tree_label(ev).plain
    assert "MCP [everything:echo]" in label
    assert "Trip planning verified" in label


def test_render_custom_python_tool():
    """Verifies render_custom_tool with function name, arguments grid, return JSON, and tree label."""
    ev = {
        "tool_name": "calculate_travel_carbon_footprint",
        "tool_args": {
            "distance_km": 650.0,
            "transport_mode": "train",
        },
        "payload": {
            "stepUpdate": {
                "responseJson": '{"emissions_kg_co2": 26.65, "status": "success"}',
            }
        },
        "state": "STATE_DONE",
    }
    rendered = _render_to_string(render_tool_event(ev))
    assert "PYTHON TOOL: calculate_travel_carbon_footprint" in rendered
    assert "calculate_travel_carbon_footprint()" in rendered
    assert "distance_km" in rendered
    assert "650" in rendered
    assert "train" in rendered
    assert "26.65" in rendered

    label = build_tool_tree_label(ev).plain
    assert "PYTHON: calculate_travel_carbon_footprint" in label
    assert "distance_km=650.0" in label


def test_render_policy_event_and_tool_error():
    """Verifies render_policy_event for security denials and render_tool_error for exceptions."""
    # 1. Policy Denial Event
    ev_policy = {
        "step_type": "POLICY_DECISION",
        "tool_name": "purge_cache_files",
        "decision": "DENY",
        "reason": "Denied by policy 'block-destructive-purge'.",
        "tool_args": {"target_dir": "/var/log/system"},
    }
    rendered_policy = _render_to_string(render_tool_event(ev_policy))
    assert "SECURITY / POLICY INTERCEPTION" in rendered_policy
    assert "purge_cache_files" in rendered_policy
    assert "DENY (Execution Prohibited)" in rendered_policy
    assert "block-destructive-purge" in rendered_policy
    assert "/var/log/system" in rendered_policy

    # 2. Tool Execution Error / Exception Event
    ev_error = {
        "step_type": "TOOL_ERROR",
        "tool_name": "failing_database_query",
        "error_message": "FATAL: database 'prod_analytics' at 10.0.4.12:5432 connection refused (OOMKilled)",
        "tool_args": {"query": "SELECT * FROM users;"},
    }
    rendered_err = _render_to_string(render_tool_event(ev_error))
    assert "TOOL EXECUTION ERROR" in rendered_err
    assert "failing_database_query" in rendered_err
    assert "FATAL: database 'prod_analytics'" in rendered_err
    assert "SELECT * FROM users;" in rendered_err

    # 3. Pending Pre-Tool Hook Evaluation (no decision yet)
    ev_pending = {
        "step_type": "PRE_TOOL_HOOK",
        "tool_name": "purge_cache_files",
        "tool_args": {"target_dir": "/var/log/system"},
    }
    rendered_pending = _render_to_string(render_tool_event(ev_pending))
    assert "LIFECYCLE HOOK: PRE_TOOL (EVALUATING)" in rendered_pending
    assert "Evaluating Security Policies" in rendered_pending
    assert "purge_cache_files" in rendered_pending

    # 4. Approved Pre-Tool Hook (decision == ALLOW)
    ev_allow = {
        "step_type": "POLICY_DECISION",
        "tool_name": "failing_database_query",
        "decision": "ALLOW",
        "tool_args": {"query": "SELECT 1;"},
    }
    rendered_allow = _render_to_string(render_tool_event(ev_allow))
    assert "LIFECYCLE HOOK: PRE_TOOL APPROVED" in rendered_allow
    assert "ALLOW (Approved by Policy)" in rendered_allow


