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

"""Dedicated visualizers and rich renderers for Antigravity SDK tools, custom tools, MCP servers, and execution states."""

import difflib
import json
import os
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple, Union

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text


def _normalize_path(path: Any) -> str:
    """Translates file://, cns:// URIs or relative strings to clean filesystem paths."""
    if not path or not isinstance(path, str):
        return ""
    if path.startswith("file://"):
        parsed = urllib.parse.urlparse(path)
        path = urllib.parse.unquote(parsed.path)
    elif path.startswith("cns://"):
        parsed = urllib.parse.urlparse(path)
        path = "/cns/" + parsed.netloc + urllib.parse.unquote(parsed.path)
    return path


def _guess_syntax_lexer(path_or_name: str) -> str:
    """Infers pygments lexer name from file extension."""
    ext = os.path.splitext(path_or_name)[1].lower().lstrip(".")
    lexer_map = {
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
        "json": "json",
        "toml": "toml",
        "yaml": "yaml",
        "yml": "yaml",
        "sh": "bash",
        "zsh": "bash",
        "bash": "bash",
        "md": "markdown",
        "html": "html",
        "css": "css",
        "sql": "sql",
        "go": "go",
        "rs": "rust",
        "cpp": "cpp",
        "c": "c",
        "h": "c",
        "proto": "protobuf",
        "txt": "text",
    }
    return lexer_map.get(ext, "text")


def _format_bytes(size: int) -> str:
    """Formats raw bytes into human-readable size string."""
    try:
        size = int(size)
    except (ValueError, TypeError):
        return "0 B"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _extract_merged_args_and_result(ev: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Combines tool_args, stepUpdate tool payload, and action outputs into a single resolved dictionary."""
    tool_name = ev.get("tool_name") or ""
    raw_tool_args = ev.get("tool_args") or {}

    if isinstance(raw_tool_args, str):
        try:
            tool_args = json.loads(raw_tool_args)
        except Exception:
            tool_args = {"raw": raw_tool_args}
    elif isinstance(raw_tool_args, dict):
        tool_args = dict(raw_tool_args)
    else:
        tool_args = {}

    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    su = payload.get("stepUpdate") or payload.get("step_update") or {}

    # Check for direct action message in stepUpdate (e.g. listDirectory, editFile, runCommand, mcpTool)
    for action_key in (
        tool_name,
        "listDirectory", "list_directory", "listDir",
        "editFile", "edit_file", "replace_file_content",
        "createFile", "create_file", "write_to_file",
        "viewFile", "view_file",
        "runCommand", "run_command",
        "searchDirectory", "search_directory", "grep_search",
        "findFile", "find_file", "find_by_name",
        "generateImage", "generate_image",
        "invokeSubagent", "invoke_subagent",
        "searchWeb", "search_web",
        "readUrlContent", "read_url_content",
        "questionsRequest", "questions_request", "ask_question",
        "mcpTool", "mcp_tool",
        "customTool", "custom_tool",
        "finish",
    ):
        if action_key in su and isinstance(su[action_key], dict):
            for k, v in su[action_key].items():
                if k not in tool_args or not tool_args[k]:
                    tool_args[k] = v

    # Expand argumentsJson if present
    if "argumentsJson" in tool_args and isinstance(tool_args["argumentsJson"], str):
        try:
            parsed_json = json.loads(tool_args["argumentsJson"])
            if isinstance(parsed_json, dict):
                for pk, pv in parsed_json.items():
                    if pk not in tool_args:
                        tool_args[pk] = pv
        except Exception:
            pass

    return tool_args, su


def _normalize_questions(tool_args: Dict[str, Any], su: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalizes both flat Python args and protobuf questionsRequest wire structures into a standard list."""
    raw_q = tool_args.get("questions") or su.get("questions")
    if not raw_q and "questionsRequest" in su:
        raw_q = su["questionsRequest"].get("questions")
    if not raw_q and "questions_request" in su:
        raw_q = su["questions_request"].get("questions")
    if not raw_q and ("question" in tool_args or "multipleChoice" in tool_args or "multiple_choice" in tool_args):
        raw_q = [tool_args]

    normalized = []
    if isinstance(raw_q, list):
        for item in raw_q:
            if not isinstance(item, dict):
                continue
            if "multipleChoice" in item or "multiple_choice" in item:
                mc = item.get("multipleChoice") or item.get("multiple_choice") or {}
                normalized.append({
                    "question": mc.get("question", ""),
                    "options": mc.get("choices") or mc.get("options") or [],
                    "is_multi_select": mc.get("isMultiSelect", mc.get("is_multi_select", False)),
                    "type": "multiple_choice",
                })
            elif "openEnded" in item or "open_ended" in item:
                oe = item.get("openEnded") or item.get("open_ended") or {}
                normalized.append({
                    "question": oe.get("question", ""),
                    "placeholder": oe.get("placeholder", ""),
                    "options": [],
                    "is_multi_select": False,
                    "type": "open_ended",
                })
            elif "question" in item:
                normalized.append({
                    "question": item.get("question", ""),
                    "options": item.get("options") or item.get("choices") or [],
                    "is_multi_select": item.get("is_multi_select", item.get("isMultiSelect", False)),
                    "type": "multiple_choice" if (item.get("options") or item.get("choices")) else "open_ended",
                })
    return normalized


def _extract_user_answers(tool_args: Dict[str, Any], su: Dict[str, Any], ev: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extracts answered choices and strings from outbound/correlated question response payloads."""
    answers = []
    resp = (
        tool_args.get("response")
        or su.get("response")
        or ev.get("user_answer")
        or ev.get("response")
        or {}
    )
    if isinstance(resp, dict):
        raw_answers = resp.get("answers") or []
        for ans in raw_answers:
            if isinstance(ans, dict):
                if "multipleChoiceAnswer" in ans or "multiple_choice_answer" in ans:
                    mca = ans.get("multipleChoiceAnswer") or ans.get("multiple_choice_answer") or {}
                    indices = mca.get("selectedChoiceIndices") or mca.get("selected_choice_indices") or []
                    answers.append({"type": "indices", "indices": indices})
                elif "textAnswer" in ans or "text_answer" in ans:
                    ta = ans.get("textAnswer") or ans.get("text_answer")
                    answers.append({"type": "text", "text": ta})
                elif "openEndedAnswer" in ans or "open_ended_answer" in ans:
                    oea = ans.get("openEndedAnswer") or ans.get("open_ended_answer")
                    answers.append({"type": "text", "text": oea})

    # Also check flat answers
    flat_ans = su.get("answer") or su.get("selected_options") or su.get("selectedOptions") or tool_args.get("answer")
    if flat_ans:
        if isinstance(flat_ans, list):
            answers.append({"type": "strings", "strings": [str(x) for x in flat_ans]})
        else:
            answers.append({"type": "strings", "strings": [str(flat_ans)], "text": str(flat_ans)})
    return answers


def build_tool_tree_label(ev: Dict[str, Any]) -> Text:
    """Builds a concise, informative label for tree and flat timeline nodes."""
    tool_name = ev.get("tool_name") or "tool"
    tool_args, su = _extract_merged_args_and_result(ev)

    state = ev.get("state") or su.get("state") or "STATE_ACTIVE"
    error = ev.get("error") or su.get("error") or {}
    has_error = bool(state == "STATE_ERROR" or (isinstance(error, dict) and error.get("error_message")))

    t = Text()

    # Status icon prefix
    if has_error:
        t.append("❌ ", style="bold red")
    elif state == "STATE_WAITING_FOR_USER":
        t.append("⏳ ", style="bold yellow")
    else:
        t.append("✓ ", style="bold green")

    # Domain-specific compact argument summary
    if tool_name in ("mcp_tool", "mcpTool", "call_mcp_tool"):
        server = tool_args.get("serverName") or tool_args.get("server_name") or tool_args.get("ServerName") or "mcp"
        sub_tool = tool_args.get("toolName") or tool_args.get("tool_name") or tool_args.get("ToolName") or "tool"
        t.append(f"MCP [{server}:{sub_tool}]", style="bold blue")
        msg = tool_args.get("message") or tool_args.get("query") or ""
        if msg:
            t.append(f' ("{str(msg)[:25]}")', style="italic bright_cyan")
    elif tool_name in ("run_command", "runCommand"):
        t.append("TOOL: run_command", style="bold yellow")
        cmd = tool_args.get("commandLine") or tool_args.get("command_line") or tool_args.get("CommandLine") or tool_args.get("command") or tool_args.get("cmd") or ""
        if cmd:
            first_line = cmd.strip().splitlines()[0][:35]
            t.append(f" ($ {first_line})", style="italic bright_black")
    elif tool_name in ("edit_file", "replace_file_content", "editFile"):
        t.append("TOOL: edit_file", style="bold yellow")
        target = _normalize_path(tool_args.get("filePath") or tool_args.get("file_path") or tool_args.get("TargetFile") or tool_args.get("path") or "")
        if target:
            t.append(f" ({os.path.basename(target)})", style="italic bright_cyan")
    elif tool_name in ("create_file", "write_to_file", "createFile"):
        t.append("TOOL: create_file", style="bold yellow")
        target = _normalize_path(tool_args.get("filePath") or tool_args.get("file_path") or tool_args.get("TargetFile") or tool_args.get("path") or "")
        if target:
            t.append(f" (+ {os.path.basename(target)})", style="italic bright_green")
    elif tool_name in ("view_file", "viewFile"):
        t.append("TOOL: view_file", style="bold yellow")
        target = _normalize_path(tool_args.get("filePath") or tool_args.get("file_path") or tool_args.get("AbsolutePath") or tool_args.get("path") or "")
        if target:
            t.append(f" ({os.path.basename(target)})", style="italic bright_blue")
    elif tool_name in ("list_dir", "list_directory", "listDirectory"):
        t.append("TOOL: list_directory", style="bold yellow")
        path = _normalize_path(tool_args.get("directoryPath") or tool_args.get("directory_path") or tool_args.get("DirectoryPath") or tool_args.get("path") or ".")
        t.append(f" ({os.path.basename(path) or '.'}/)", style="italic bright_yellow")
    elif tool_name in ("search_dir", "search_directory", "grep_search"):
        t.append("TOOL: search_directory", style="bold yellow")
        q = tool_args.get("query") or tool_args.get("Query") or tool_args.get("pattern") or ""
        if q:
            t.append(f' ("{q[:25]}")', style="italic bright_magenta")
    elif tool_name in ("find_file", "find_by_name"):
        t.append("TOOL: find_file", style="bold yellow")
        pat = tool_args.get("query") or tool_args.get("Pattern") or tool_args.get("name") or tool_args.get("pattern") or ""
        if pat:
            t.append(f" ({pat})", style="italic bright_cyan")
    elif tool_name in ("invoke_subagent", "start_subagent", "invokeSubagent"):
        t.append("TOOL: invoke_subagent", style="bold yellow")
        subs = tool_args.get("Subagents") or tool_args.get("subagents") or []
        count = len(subs) if isinstance(subs, list) else 0
        if count > 0:
            t.append(f" ({count} workers)", style="italic bright_yellow")
    elif tool_name in ("generate_image", "generateImage"):
        t.append("TOOL: generate_image", style="bold yellow")
        img = tool_args.get("imageName") or tool_args.get("image_name") or tool_args.get("ImageName") or ""
        if img:
            t.append(f" ({img})", style="italic bright_magenta")
    elif tool_name in ("search_web", "searchWeb"):
        t.append("TOOL: search_web", style="bold yellow")
        q = tool_args.get("query") or tool_args.get("Query") or ""
        if q:
            t.append(f' ("{q[:30]}")', style="italic bright_blue")
    elif tool_name in ("read_url_content", "readUrlContent"):
        t.append("TOOL: read_url_content", style="bold yellow")
        url = tool_args.get("url") or tool_args.get("Url") or ""
        if url:
            t.append(f" ({url[:30]})", style="italic bright_blue")
    elif tool_name in ("ask_question", "askQuestion", "questionsRequest", "questions_request"):
        questions = _normalize_questions(tool_args, su)
        q = questions[0].get("question", "") if questions else ""
        t.append("TOOL: ask_question", style="bold yellow")
        if q:
            t.append(f' ("{q[:30]}...")', style="italic bright_green")
    elif tool_name in ("finish",):
        t.append("TOOL: finish", style="bold green")
        fin_dict = su.get("finish") or tool_args
        summary = fin_dict.get("final_message") or fin_dict.get("output_string") or ""
        if summary:
            t.append(f' ("{str(summary)[:25]}...")', style="italic bright_white")
    elif tool_name in ("compaction", "ActionCompaction"):
        t.append("TOOL: compaction", style="bold magenta")
        t.append(" (Pruned Context)", style="italic bright_magenta")
    elif tool_name in ("ask_permission", "askPermission"):
        t.append("TOOL: ask_permission", style="bold yellow")
        act = tool_args.get("Action") or tool_args.get("action") or ""
        tgt = tool_args.get("Target") or tool_args.get("target") or ""
        if act or tgt:
            t.append(f" ({act} {tgt[:15]})", style="italic bright_yellow")
    else:
        # Custom Python Tool
        t.append(f"PYTHON: {tool_name}", style="bold cyan")
        first_arg = next((f"{k}={v}" for k, v in tool_args.items() if not str(k).startswith("_")), "")
        if first_arg:
            t.append(f" ({first_arg[:30]})", style="italic bright_yellow")

    return t


def _render_state_banner(ev: Dict[str, Any]) -> Optional[RenderableType]:
    """Renders failure, error, policy violation, or waiting state banners."""
    state = ev.get("state") or "STATE_ACTIVE"
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    su = payload.get("stepUpdate") or payload.get("step_update") or {}
    err_field = su.get("error") or ev.get("error") or {}

    error_msg = ""
    http_code = 0
    if isinstance(err_field, dict):
        error_msg = err_field.get("error_message") or err_field.get("message") or ""
        http_code = err_field.get("http_code", 0)
    elif isinstance(err_field, str):
        error_msg = err_field

    # 1. Blocked by Security Policy / Sandbox
    if "policy" in error_msg.lower() or "blocked" in error_msg.lower() or "denied" in error_msg.lower():
        msg = Text()
        msg.append("⚠️  EXECUTION BLOCKED BY POLICY / SANDBOX\n", style="bold yellow")
        msg.append(f"Details: {error_msg}\n", style="yellow")
        return Panel(msg, title="[bold yellow]SECURITY / POLICY CONSTRAINT[/bold yellow]", border_style="yellow")

    # 2. General Error / Exception
    if state == "STATE_ERROR" or error_msg:
        msg = Text()
        http_str = f" [HTTP {http_code}]" if http_code else ""
        msg.append(f"❌ EXECUTION FAILED{http_str}\n", style="bold red")
        if error_msg:
            msg.append(f"Error Message:\n{error_msg}\n", style="bright_red")
        return Panel(msg, title="[bold red]ERROR / EXCEPTION[/bold red]", border_style="red")

    # 3. Waiting for User Input / Approval
    if state == "STATE_WAITING_FOR_USER":
        msg = Text()
        msg.append("⏳ AGENT WAITING FOR USER INPUT / APPROVAL\n", style="bold cyan")
        msg.append("Action is paused awaiting user confirmation or interactive response.\n", style="dim")
        return Panel(msg, title="[bold cyan]AWAITING INTERACTIVE INPUT[/bold cyan]", border_style="cyan")

    return None


# =============================================================================
# Dedicated Tool Visualizers
# =============================================================================

def render_run_command(ev: Dict[str, Any], syntax_theme: str = "dracula") -> RenderableType:
    """Renders terminal command execution, cwd, exit code, stdout and stderr."""
    args, su = _extract_merged_args_and_result(ev)

    cmd = (
        args.get("commandLine")
        or args.get("command_line")
        or args.get("CommandLine")
        or args.get("command")
        or args.get("cmd")
        or ""
    )
    cwd = _normalize_path(
        args.get("workingDir")
        or args.get("working_dir")
        or args.get("cwd")
        or args.get("Cwd")
        or "."
    )
    exit_code = args.get("exitCode") if "exitCode" in args else args.get("exit_code")
    timeout = args.get("NotificationTimeoutSeconds") or args.get("timeout")

    items: List[RenderableType] = []

    meta_table = Table.grid(padding=(0, 2))
    meta_table.add_column(style="bold cyan")
    meta_table.add_column()
    meta_table.add_row("Working Directory:", str(cwd))
    if exit_code is not None:
        exit_style = "bold green" if exit_code == 0 else "bold red"
        meta_table.add_row("Exit Code:", f"[{exit_style}][EXIT {exit_code}][/{exit_style}]")
    if timeout:
        meta_table.add_row("Timeout:", f"{timeout}s")

    items.append(meta_table)
    items.append(Text(""))

    cmd_display = cmd.strip() if cmd else "(empty command)"
    items.append(Syntax(f"$ {cmd_display}", "bash", theme=syntax_theme, line_numbers=False, word_wrap=True))

    output = (
        args.get("combinedOutput")
        or args.get("combined_output")
        or su.get("combinedOutput")
        or su.get("combined_output")
        or su.get("output")
        or su.get("text")
        or ev.get("text")
        or ""
    )

    if output:
        items.append(Text("\nOutput:", style="bold green"))
        items.append(Syntax(output, "text", theme=syntax_theme, line_numbers=True, word_wrap=True))

    return Panel(Group(*items), title="[bold yellow]RUN COMMAND[/bold yellow]", border_style="yellow")


def render_edit_file(ev: Dict[str, Any], syntax_theme: str = "dracula") -> RenderableType:
    """Renders file edits with instruction banner and unified colorized diff."""
    args, su = _extract_merged_args_and_result(ev)

    target_file = _normalize_path(
        args.get("filePath")
        or args.get("file_path")
        or args.get("TargetFile")
        or args.get("path")
        or ""
    )
    instruction = (
        args.get("Instruction")
        or args.get("instruction")
        or args.get("Description")
        or su.get("text")
        or ev.get("text")
        or ""
    )
    target_content = args.get("TargetContent") or args.get("old_content") or ""
    replacement_content = args.get("ReplacementContent") or args.get("new_content") or ""
    start_line = args.get("startLine") or args.get("StartLine")
    end_line = args.get("endLine") or args.get("EndLine")

    items: List[RenderableType] = []

    header = Table.grid(padding=(0, 2))
    header.add_column(style="bold cyan")
    header.add_column()
    header.add_row("Target File:", str(target_file or "(unspecified)"))
    if start_line is not None and end_line is not None:
        header.add_row("Line Range:", f"Lines {start_line} - {end_line}")
    if instruction:
        header.add_row("Instruction:", str(instruction))

    items.append(header)
    items.append(Text(""))

    diff_blocks = args.get("diffBlock") or args.get("diff_block") or []
    if isinstance(diff_blocks, list) and diff_blocks:
        diff_lines = []
        base_name = os.path.basename(target_file) or "file"
        diff_lines.append(f"--- a/{base_name}")
        diff_lines.append(f"+++ b/{base_name}")
        for block in diff_blocks:
            sl = block.get("startLine", 0)
            el = block.get("endLine", 0)
            diff_lines.append(f"@@ -{sl} +{el} @@")
            for line_obj in block.get("lines", []):
                action = line_obj.get("action", "")
                text = line_obj.get("text", "")
                if action == "LINE_ACTION_INSERT":
                    diff_lines.append(f"+{text}")
                elif action == "LINE_ACTION_DELETE":
                    diff_lines.append(f"-{text}")
                else:
                    diff_lines.append(f" {text}")
        diff_text = "\n".join(diff_lines)
        items.append(Syntax(diff_text, "diff", theme=syntax_theme, line_numbers=True, word_wrap=True))
    elif target_content or replacement_content:
        old_lines = target_content.splitlines(keepends=True)
        new_lines = replacement_content.splitlines(keepends=True)
        diff_lines = list(difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{os.path.basename(target_file)}",
            tofile=f"b/{os.path.basename(target_file)}",
            lineterm=""
        ))
        diff_text = "\n".join(diff_lines) if diff_lines else f"-{target_content}\n+{replacement_content}"
        items.append(Syntax(diff_text, "diff", theme=syntax_theme, line_numbers=True, word_wrap=True))

    return Panel(Group(*items), title="[bold cyan]EDIT FILE (UNIFIED DIFF)[/bold cyan]", border_style="cyan")


def render_create_file(ev: Dict[str, Any], syntax_theme: str = "dracula") -> RenderableType:
    """Renders file creation with file size, overwrite flag, and syntax highlighting."""
    args, su = _extract_merged_args_and_result(ev)

    target_file = _normalize_path(
        args.get("filePath")
        or args.get("file_path")
        or args.get("TargetFile")
        or args.get("path")
        or ""
    )
    code = args.get("contents") or args.get("CodeContent") or args.get("content") or ""
    overwrite = args.get("Overwrite") or args.get("overwrite", False)
    desc = args.get("Description") or su.get("text") or ""

    items: List[RenderableType] = []

    header = Table.grid(padding=(0, 2))
    header.add_column(style="bold cyan")
    header.add_column()
    header.add_row("Target File:", str(target_file or "(unspecified)"))
    header.add_row("File Size:", _format_bytes(len(code.encode("utf-8"))))
    header.add_row("Overwrite Existing:", str(overwrite))
    if desc:
        header.add_row("Description:", str(desc))

    items.append(header)
    items.append(Text(""))

    lexer = _guess_syntax_lexer(target_file)
    items.append(Syntax(code, lexer, theme=syntax_theme, line_numbers=True, word_wrap=True))

    return Panel(Group(*items), title="[bold green]CREATE FILE[/bold green]", border_style="green")


def render_view_file(ev: Dict[str, Any], syntax_theme: str = "dracula") -> RenderableType:
    """Renders file inspection snippet with line ranges and syntax highlighting."""
    args, su = _extract_merged_args_and_result(ev)

    target_file = _normalize_path(
        args.get("filePath")
        or args.get("file_path")
        or args.get("AbsolutePath")
        or args.get("path")
        or ""
    )
    start_line = args.get("startLine") or args.get("StartLine")
    end_line = args.get("endLine") or args.get("EndLine")
    is_skill = ev.get("is_skill") or target_file.endswith("SKILL.md") or "/skills/" in target_file
    skill_name = ev.get("skill_name") or (target_file.split("/skills/")[1].split("/")[0] if "/skills/" in target_file else (os.path.basename(os.path.dirname(target_file)) if target_file.endswith("SKILL.md") else ""))

    items: List[RenderableType] = []

    header = Table.grid(padding=(0, 2))
    header.add_column(style="bold cyan")
    header.add_column()
    if is_skill and skill_name:
        header.add_row("Agent Skill:", f"[bold green]🎯 {skill_name}[/bold green]")
    header.add_row("File Path:", str(target_file or "(unspecified)"))
    if start_line is not None and end_line is not None:
        header.add_row("Viewing Lines:", f"{start_line} to {end_line}")

    items.append(header)
    items.append(Text(""))

    content = args.get("content") or args.get("contents") or su.get("content") or su.get("text") or ev.get("text") or ""

    if content:
        lexer = _guess_syntax_lexer(target_file)
        items.append(Syntax(content, lexer, theme=syntax_theme, line_numbers=True, word_wrap=True))

    title_text = f"[bold cyan]🎯 AGENT SKILL: {skill_name}[/bold cyan]" if (is_skill and skill_name) else "[bold blue]VIEW FILE[/bold blue]"
    border_color = "cyan" if is_skill else "blue"
    return Panel(Group(*items), title=title_text, border_style=border_color)


def render_list_dir(ev: Dict[str, Any]) -> RenderableType:
    """Renders directory structure with directory icons, file sizes, and item counts."""
    args, su = _extract_merged_args_and_result(ev)

    path = _normalize_path(
        args.get("directoryPath")
        or args.get("directory_path")
        or args.get("DirectoryPath")
        or args.get("path")
        or "."
    )

    items: List[RenderableType] = []
    items.append(Text(f"Directory: {path}\n", style="bold cyan"))

    table = Table(title="Directory Entries", show_header=True, header_style="bold magenta", expand=True)
    table.add_column("Type", width=8)
    table.add_column("Name", style="bold white")
    table.add_column("Size", justify="right", style="yellow")

    entries = args.get("results") or su.get("results") or su.get("entries") or args.get("entries") or []

    if isinstance(entries, list):
        for e in entries:
            if isinstance(e, dict):
                is_dir = e.get("isDirectory", e.get("is_directory", e.get("isDir", False)))
                name = e.get("name") or e.get("path") or ""
                size = e.get("fileSize", e.get("file_size", e.get("sizeBytes", 0)))
                icon = "📁 DIR" if is_dir else "📄 FILE"
                size_str = "-" if is_dir else _format_bytes(size)
                table.add_row(icon, name, size_str)

    items.append(table)
    return Panel(Group(*items), title="[bold yellow]DIRECTORY LISTING[/bold yellow]", border_style="yellow")


def render_search_dir(ev: Dict[str, Any]) -> RenderableType:
    """Renders grep pattern matches with line numbers and matched content."""
    args, su = _extract_merged_args_and_result(ev)

    q = args.get("query") or args.get("Query") or args.get("pattern") or ""
    path = _normalize_path(
        args.get("directoryPath")
        or args.get("directory_path")
        or args.get("SearchPath")
        or args.get("path")
        or "."
    )

    items: List[RenderableType] = []
    items.append(Text(f'Search Query: "{q}" in {path}\n', style="bold magenta"))

    table = Table(title="Search Matches", show_header=True, header_style="bold cyan", expand=True)
    table.add_column("File", style="bold white")
    table.add_column("Line", justify="right", style="yellow", width=8)
    table.add_column("Match Content", style="white")

    matches = args.get("matches") or su.get("matches") or su.get("results") or args.get("results") or []

    if isinstance(matches, list):
        for m in matches:
            if isinstance(m, dict):
                fn = m.get("Filename") or m.get("filename") or m.get("file") or ""
                ln = str(m.get("LineNumber") or m.get("line_number") or m.get("line") or "")
                content = m.get("LineContent") or m.get("content") or ""
                table.add_row(os.path.basename(fn), ln, content)

    items.append(table)
    return Panel(Group(*items), title="[bold magenta]SEARCH DIRECTORY (GREP)[/bold magenta]", border_style="magenta")


def render_find_file(ev: Dict[str, Any]) -> RenderableType:
    """Renders find file results."""
    args, su = _extract_merged_args_and_result(ev)

    pattern = args.get("query") or args.get("Pattern") or args.get("name") or args.get("pattern") or "*"
    dir_path = _normalize_path(
        args.get("directoryPath")
        or args.get("directory_path")
        or args.get("SearchDirectory")
        or args.get("directory")
        or "."
    )

    items: List[RenderableType] = []
    items.append(Text(f'Pattern: "{pattern}" in {dir_path}\n', style="bold cyan"))

    files = args.get("output") or su.get("output") or su.get("files") or args.get("files") or []

    if isinstance(files, list):
        for f in files:
            items.append(Text(f" • {f}", style="bright_white"))
    elif isinstance(files, str):
        for line in files.splitlines():
            if line.strip():
                items.append(Text(f" • {line.strip()}", style="bright_white"))

    return Panel(Group(*items), title="[bold cyan]FIND FILES[/bold cyan]", border_style="cyan")


def render_invoke_subagent(ev: Dict[str, Any]) -> RenderableType:
    """Renders subagent delegation cards with roles, types, and prompts."""
    args, su = _extract_merged_args_and_result(ev)
    subagents = args.get("Subagents") or args.get("subagents") or []
    count = len(subagents) if isinstance(subagents, list) else 0

    items: List[RenderableType] = []
    items.append(Text(f"Delegating tasks to {count} subagent worker(s):\n", style="bold bright_yellow"))

    for i, sub in enumerate(subagents, 1):
        role = sub.get("Role") or sub.get("role") or "Specialist Worker"
        type_name = sub.get("TypeName") or sub.get("typeName") or sub.get("type") or "self"
        prompt = sub.get("Prompt") or sub.get("prompt") or ""
        ws = sub.get("Workspace") or sub.get("workspace") or "inherit"

        card_text = Text()
        card_text.append(f"Worker {i}: {role}\n", style="bold cyan")
        card_text.append(f"Type: {type_name} | Workspace: {ws}\n", style="dim")
        card_text.append("Prompt:\n", style="bold green")
        card_text.append(f"{prompt}\n", style="white")

        items.append(Panel(card_text, border_style="bright_blue"))

    return Panel(Group(*items), title="[bold yellow]INVOKE SUBAGENTS[/bold yellow]", border_style="yellow")


def render_ask_question(ev: Dict[str, Any]) -> RenderableType:
    """Renders interactive Q&A cards with option lists, answer badges, and active state indicators."""
    args, su = _extract_merged_args_and_result(ev)
    questions = _normalize_questions(args, su)
    user_answers = _extract_user_answers(args, su, ev)
    state = ev.get("state") or su.get("state") or "STATE_ACTIVE"

    items: List[RenderableType] = []

    # 1. Active Waiting Status
    if state == "STATE_WAITING_FOR_USER" and not user_answers:
        waiting_banner = Text()
        waiting_banner.append("⏳ WAITING FOR USER INPUT / SELECTION\n", style="bold yellow")
        waiting_banner.append("Execution is paused awaiting your response in the console/client.\n", style="italic bright_yellow")
        items.append(Panel(waiting_banner, border_style="yellow"))
        items.append(Text(""))

    if not questions:
        req_text = su.get("requestText") or args.get("requestText") or "Interactive Question"
        items.append(Text(f"❓ {req_text}\n", style="bold bright_green"))
    else:
        for idx, q_obj in enumerate(questions):
            q_text = q_obj.get("question", "")
            options = q_obj.get("options", [])
            multi = q_obj.get("is_multi_select", False)

            selected_indices = set()
            selected_strings = set()
            write_in_text = None

            if idx < len(user_answers):
                ans = user_answers[idx]
                if ans["type"] == "indices":
                    selected_indices = set(ans.get("indices", []))
                elif ans["type"] == "strings":
                    selected_strings = set(ans.get("strings", []))
                elif ans["type"] == "text":
                    write_in_text = ans.get("text")

            items.append(Text(f"❓ {q_text}\n", style="bold bright_green"))
            mode_str = "Multiple Choice (Checkboxes)" if multi else "Single Selection"
            items.append(Text(f"Mode: {mode_str}\n\n", style="dim"))

            if options:
                for opt_idx, opt in enumerate(options):
                    is_selected = (opt_idx in selected_indices) or (opt in selected_strings)
                    badge = "[✓]" if is_selected else "[ ]"
                    style = "bold green" if is_selected else "white"
                    suffix = "  ◄── SELECTED CHOICE" if is_selected else ""
                    items.append(Text(f"  {badge} {opt}{suffix}\n", style=style))

            if write_in_text and (not options or write_in_text not in options):
                items.append(Text(f"\n  ✍️ User Answer: \"{write_in_text}\"\n", style="bold bright_cyan"))

    return Panel(Group(*items), title="[bold green]USER QUESTION & INTERACTION[/bold green]", border_style="green")


def render_generate_image(ev: Dict[str, Any]) -> RenderableType:
    """Renders image metadata card with aspect ratio, resolution, file path, and OS viewer shortcut."""
    args, su = _extract_merged_args_and_result(ev)

    image_name = args.get("imageName") or args.get("image_name") or args.get("ImageName") or "generated_image.png"
    prompt = args.get("prompt") or args.get("Prompt") or ""
    aspect_ratio = args.get("aspectRatio") or args.get("aspect_ratio") or args.get("AspectRatio") or "1:1"

    artifacts = ev.get("artifacts", [])
    path = artifacts[0]["path"] if artifacts else "Generating in background / brain..."

    items: List[RenderableType] = []

    header = Table.grid(padding=(0, 2))
    header.add_column(style="bold magenta")
    header.add_column()
    header.add_row("Image Name:", str(image_name))
    header.add_row("Aspect Ratio:", str(aspect_ratio))
    header.add_row("Artifact Path:", str(path))

    items.append(header)
    items.append(Text("\nPrompt:", style="bold cyan"))
    items.append(Text(f'"{prompt}"\n', style="italic bright_white"))

    hint = Text("▶ Press 'o' or open the file in your OS default viewer (Preview, Photoshop, etc.)", style="bold yellow")
    items.append(hint)

    return Panel(Group(*items), title="[bold magenta]GENERATED IMAGE ARTIFACT[/bold magenta]", border_style="magenta")


def render_search_web(ev: Dict[str, Any]) -> RenderableType:
    """Renders web search queries and formatted search result cards."""
    args, su = _extract_merged_args_and_result(ev)
    query = args.get("query") or args.get("Query") or ""

    items: List[RenderableType] = []
    items.append(Text(f'Search Query: "{query}"\n', style="bold blue"))

    results = su.get("results") or su.get("summary") or args.get("summary") or ""

    if isinstance(results, str) and results:
        items.append(Markdown(results))
    elif isinstance(results, list):
        for r in results:
            if isinstance(r, dict):
                t = r.get("title", "Web Page")
                u = r.get("url", "")
                s = r.get("snippet", "")
                items.append(Text(f"🌐 {t} ({u})\n", style="bold cyan"))
                if s:
                    items.append(Text(f"   {s}\n", style="dim"))

    return Panel(Group(*items), title="[bold blue]WEB SEARCH[/bold blue]", border_style="blue")


def render_read_url_content(ev: Dict[str, Any]) -> RenderableType:
    """Renders fetched web content and markdown reader view."""
    args, su = _extract_merged_args_and_result(ev)
    url = args.get("url") or args.get("Url") or ""

    items: List[RenderableType] = []
    items.append(Text(f"URL: {url}\n", style="bold cyan"))

    content = su.get("content") or su.get("summary") or su.get("text") or args.get("summary") or ""

    if content:
        items.append(Markdown(content))

    return Panel(Group(*items), title="[bold cyan]READ URL CONTENT[/bold cyan]", border_style="cyan")


def render_mcp_tool(ev: Dict[str, Any], syntax_theme: str = "dracula") -> RenderableType:
    """Renders Model Context Protocol (MCP) tool calls, server metadata, and parameters."""
    args, su = _extract_merged_args_and_result(ev)

    server_name = args.get("serverName") or args.get("server_name") or args.get("ServerName") or "mcp"
    tool_name = args.get("toolName") or args.get("tool_name") or args.get("ToolName") or ev.get("tool_name") or "tool"

    raw_args = args.get("Arguments") or args.get("arguments") or args.get("argumentsJson") or args.get("arguments_json") or args
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args)
        except Exception:
            pass

    items: List[RenderableType] = []

    header = Table.grid(padding=(0, 2))
    header.add_column(style="bold blue")
    header.add_column()
    header.add_row("MCP Server:", f"[bold cyan]{server_name}[/bold cyan]")
    header.add_row("MCP Tool:", f"[bold yellow]{tool_name}[/bold yellow]")
    header.add_row("Protocol Transport:", "Model Context Protocol (Stdio / Streamable HTTP)")
    items.append(header)
    items.append(Text(""))

    # Clean parameters for display
    clean_params = {}
    if isinstance(raw_args, dict):
        clean_params = {
            k: v for k, v in raw_args.items()
            if k not in ("serverName", "server_name", "ServerName", "toolName", "tool_name", "ToolName", "argumentsJson", "Arguments")
        }

    if clean_params:
        table = Table(title="Tool Parameters", show_header=True, header_style="bold magenta", expand=True)
        table.add_column("Parameter", style="bold cyan", width=22)
        table.add_column("Value", style="bright_white")
        for k, v in clean_params.items():
            val_str = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
            table.add_row(str(k), val_str)
        items.append(table)
    elif isinstance(raw_args, dict) and raw_args:
        formatted_json = json.dumps(raw_args, indent=2, default=str)
        items.append(Syntax(formatted_json, "json", theme=syntax_theme, line_numbers=True, word_wrap=True))

    output = (
        args.get("output")
        or su.get("output")
        or su.get("text")
        or ev.get("text")
        or ""
    )
    if output:
        items.append(Text("\nResponse Output:", style="bold green"))
        items.append(Syntax(str(output), "text", theme=syntax_theme, line_numbers=False, word_wrap=True))

    return Panel(Group(*items), title=f"[bold blue]MCP TOOL: {server_name} / {tool_name}[/bold blue]", border_style="blue")


def render_custom_tool(ev: Dict[str, Any], syntax_theme: str = "dracula") -> RenderableType:
    """Renders custom Python-based function invocations, argument tables, and return values."""
    args, su = _extract_merged_args_and_result(ev)
    tool_name = ev.get("tool_name") or "custom_tool"

    items: List[RenderableType] = []

    header = Table.grid(padding=(0, 2))
    header.add_column(style="bold cyan")
    header.add_column()
    header.add_row("Python Function:", f"[bold yellow]{tool_name}()[/bold yellow]")
    header.add_row("Execution Target:", "In-Process Python Tool Runner")
    items.append(header)
    items.append(Text(""))

    clean_params = {
        k: v for k, v in args.items()
        if k not in ("name", "id", "tool_name", "argumentsJson", "raw") and not str(k).startswith("_")
    }

    if clean_params:
        table = Table(title="Function Arguments", show_header=True, header_style="bold magenta", expand=True)
        table.add_column("Argument", style="bold cyan", width=22)
        table.add_column("Value", style="bright_white")
        for k, v in clean_params.items():
            val_str = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
            table.add_row(str(k), val_str)
        items.append(table)
    else:
        formatted_json = json.dumps(args, indent=2, default=str)
        items.append(Syntax(formatted_json, "json", theme=syntax_theme, line_numbers=True, word_wrap=True))

    output = (
        args.get("responseJson")
        or args.get("response")
        or su.get("responseJson")
        or su.get("response")
        or su.get("output")
        or su.get("text")
        or ev.get("text")
        or ""
    )
    if output:
        items.append(Text("\nReturn Value:", style="bold green"))
        if isinstance(output, str):
            try:
                parsed_out = json.loads(output)
                items.append(Syntax(json.dumps(parsed_out, indent=2), "json", theme=syntax_theme, line_numbers=True, word_wrap=True))
            except Exception:
                items.append(Syntax(output, "text", theme=syntax_theme, line_numbers=False, word_wrap=True))
        else:
            items.append(Syntax(json.dumps(output, indent=2), "json", theme=syntax_theme, line_numbers=True, word_wrap=True))

    return Panel(Group(*items), title=f"[bold cyan]PYTHON TOOL: {tool_name}[/bold cyan]", border_style="cyan")


def render_finish(ev: Dict[str, Any], syntax_theme: str = "dracula") -> RenderableType:
    """Renders session completion banner and structured JSON output."""
    args, su = _extract_merged_args_and_result(ev)

    finish_dict = su.get("finish") or args
    summary = finish_dict.get("final_message") or finish_dict.get("output_string") or ev.get("output_string") or ev.get("text") or "Task Complete"

    items: List[RenderableType] = []
    items.append(Text("🏁 SESSION EXECUTION FINISHED\n", style="bold green"))

    # Check if summary is structured JSON output
    is_structured = False
    if isinstance(summary, str) and (summary.strip().startswith("{") or summary.strip().startswith("[")):
        try:
            parsed = json.loads(summary)
            items.append(Text("📋 STRUCTURED OUTPUT RESULT:\n", style="bold cyan"))
            items.append(Syntax(json.dumps(parsed, indent=2, default=str), "json", theme=syntax_theme, line_numbers=True, word_wrap=True))
            is_structured = True
        except Exception:
            pass
    elif isinstance(summary, (dict, list)):
        items.append(Text("📋 STRUCTURED OUTPUT RESULT:\n", style="bold cyan"))
        items.append(Syntax(json.dumps(summary, indent=2, default=str), "json", theme=syntax_theme, line_numbers=True, word_wrap=True))
        is_structured = True

    if not is_structured:
        items.append(Text(f"Summary: {summary}\n", style="bright_white"))

    return Panel(Group(*items), title="[bold green]FINISH (COMPLETE)[/bold green]", border_style="green", expand=True, padding=(0, 1))


def render_compaction(ev: Dict[str, Any]) -> RenderableType:
    """Renders context window compaction and token pruning event."""
    items: List[RenderableType] = []
    items.append(Text("🧹 CONTEXT WINDOW COMPACTION TRIGGERED\n", style="bold magenta"))
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", width=18)
    grid.add_column()
    grid.add_row("Operation:", "[bold white]Context Compaction[/bold white]")
    grid.add_row("Status:", "[bold green]COMPLETED[/bold green]")
    grid.add_row("Description:", "Conversation history pruned to maintain active context within token threshold limits.")
    items.append(grid)
    return Panel(Group(*items), title="[bold magenta]CONTEXT COMPACTION[/bold magenta]", border_style="magenta", expand=True, padding=(0, 1))


def render_ask_permission(ev: Dict[str, Any], syntax_theme: str = "dracula") -> RenderableType:
    """Renders runtime permission elevation request with target scope and justification."""
    args, su = _extract_merged_args_and_result(ev)
    action = args.get("Action") or args.get("action") or ""
    target = args.get("Target") or args.get("target") or ""
    reason = args.get("Reason") or args.get("reason") or ""

    items: List[RenderableType] = []
    items.append(Text("🔐 RUNTIME PERMISSION ELEVATION REQUEST\n", style="bold yellow"))
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", width=18)
    grid.add_column()
    grid.add_row("Action Type:", str(action or "Unknown"))
    grid.add_row("Target Scope:", f"[bold white]{target}[/bold white]" if target else "Unknown Scope")
    if reason:
        grid.add_row("Justification:", f"[italic yellow]{reason}[/italic yellow]")
    items.append(grid)
    return Panel(Group(*items), title="[bold yellow]PERMISSION REQUEST[/bold yellow]", border_style="yellow", expand=True, padding=(0, 1))


def render_trigger_notification(ev: Dict[str, Any], syntax_theme: str = "dracula") -> RenderableType:
    """Renders autonomous background triggers (interval timers, file watchers, webhooks)."""
    trigger_content = ev.get("trigger_content") or ev.get("text") or ev.get("prompt") or ""
    t_str = str(trigger_content)

    trigger_kind = "Timer / Interval Trigger"
    icon = "⏰"
    if "ADDED:" in t_str or "MODIFIED:" in t_str or "DELETED:" in t_str or ("/" in t_str and "." in t_str):
        trigger_kind = "Filesystem Change Trigger"
        icon = "📁"
    elif "webhook" in t_str.lower():
        trigger_kind = "Webhook Event Trigger"
        icon = "🪝"

    items: List[RenderableType] = []
    items.append(Text(f"{icon} AUTONOMOUS TRIGGER NOTIFICATION\n", style="bold green"))
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", width=18)
    grid.add_column()
    grid.add_row("Trigger Type:", trigger_kind)
    grid.add_row("Event Payload:", f"[bright_white]{t_str}[/bright_white]")
    items.append(grid)
    return Panel(Group(*items), title=f"[bold green]{icon} TRIGGER NOTIFICATION[/bold green]", border_style="green", expand=True, padding=(0, 1))


def render_cancellation(ev: Dict[str, Any]) -> RenderableType:
    """Renders turn cancellation and client halt request."""
    items: List[RenderableType] = []
    items.append(Text("🛑 TURN EXECUTION CANCELLED\n", style="bold red"))
    items.append(Text("The agent turn was aborted programmatically or by client task cancellation.\n", style="bright_white"))
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", width=18)
    grid.add_column()
    grid.add_row("Status:", "[bold red]CANCELLED[/bold red]")
    grid.add_row("Reason:", "Halt signal received from client runtime.")
    items.append(grid)
    return Panel(Group(*items), title="[bold red]TURN CANCELLED[/bold red]", border_style="red", expand=True, padding=(0, 1))


def render_generic_tool(ev: Dict[str, Any], syntax_theme: str = "dracula") -> RenderableType:
    """Fallback visualizer for custom or MCP tools."""
    tool_name = ev.get("tool_name") or "custom_tool"
    tool_args, su = _extract_merged_args_and_result(ev)

    items: List[RenderableType] = []
    formatted_json = json.dumps(tool_args, indent=2, default=str)
    items.append(Syntax(formatted_json, "json", theme=syntax_theme, line_numbers=True, word_wrap=True))

    return Panel(Group(*items), title=f"[bold yellow]TOOL: {tool_name}[/bold yellow]", border_style="yellow")


def render_policy_event(ev: Dict[str, Any], syntax_theme: str = "dracula") -> RenderableType:
    """Renders lifecycle hook decisions, pre-tool evaluations, error transforms, and security policy block banners."""
    step_type = ev.get("step_type") or ""
    msg_type = ev.get("message_type") or ""
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    chr_req = payload.get("callHookRequest") or payload.get("call_hook_request") or {}
    chr_resp = payload.get("callHookResponse") or payload.get("call_hook_response") or {}

    # 1. Error Transform Hook (OnToolError)
    if step_type == "ON_TOOL_ERROR_HOOK" or msg_type == "CALL_HOOK_ONTOOLERROR":
        tool_name = ev.get("tool_name") or "tool"
        ote = chr_req.get("onToolErrorArgs") or chr_req.get("on_tool_error_args") or {}
        orig_err = ote.get("errorMessage") or ote.get("error_message") or ""
        items: List[RenderableType] = []
        items.append(Text("🔄 LIFECYCLE HOOK: ON_TOOL_ERROR (TRANSFORM HOOK)\n", style="bold magenta"))
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", width=18)
        grid.add_column()
        grid.add_row("Target Tool:", f"[bold white]{tool_name}[/bold white]")
        grid.add_row("Hook Type:", "[bold magenta]Transform Hook (Error Shaping)[/bold magenta]")
        if orig_err:
            grid.add_row("Original Error:", f"[bright_red]{orig_err}[/bright_red]")
        items.append(grid)
        return Panel(Group(*items), title="[bold magenta]HOOK: ON_TOOL_ERROR[/bold magenta]", border_style="magenta", expand=True, padding=(0, 1))

    if step_type == "ON_TOOL_ERROR_RESULT":
        custom_err = ev.get("custom_error_message") or ""
        items = []
        items.append(Text("🔄 TRANSFORM HOOK: CUSTOM ERROR SHAPED\n", style="bold magenta"))
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", width=18)
        grid.add_column()
        grid.add_row("Shaped Message:", f"[italic yellow]{custom_err}[/italic yellow]")
        items.append(grid)
        return Panel(Group(*items), title="[bold magenta]TRANSFORM HOOK RESULT[/bold magenta]", border_style="magenta", expand=True, padding=(0, 1))

    # 2. PreTurn Decide Hook
    if step_type in ("PRE_TURN_HOOK", "PRE_TURN_DECISION") or msg_type == "CALL_HOOK_PRETURN":
        decision = ev.get("decision", "ALLOW")
        reason = ev.get("reason", "")
        items = []
        if decision == "DENY":
            items.append(Text("🔒 PRE-TURN DECIDE HOOK: TURN REJECTED\n", style="bold red"))
            grid = Table.grid(padding=(0, 2))
            grid.add_column(style="bold cyan", width=18)
            grid.add_column()
            grid.add_row("Hook Decision:", "[bold red]DENY (Turn Execution Prohibited)[/bold red]")
            if reason:
                grid.add_row("Reason:", f"[bold yellow]{reason}[/bold yellow]")
            items.append(grid)
            return Panel(Group(*items), title="[bold red]PRE-TURN HOOK REJECTION[/bold red]", border_style="red", expand=True, padding=(0, 1))
        else:
            items.append(Text("✅ PRE-TURN DECIDE HOOK: TURN APPROVED\n", style="bold green"))
            return Panel(Group(*items), title="[bold green]PRE-TURN HOOK[/bold green]", border_style="green", expand=True, padding=(0, 1))

    # 3. PostTurn & PostTool Inspect Hooks
    if step_type == "POST_TURN_HOOK" or msg_type == "CALL_HOOK_POSTTURN":
        items = [Text("ℹ️ LIFECYCLE HOOK: POST_TURN (INSPECT HOOK)\n", style="bold cyan")]
        return Panel(Group(*items), title="[bold cyan]INSPECT HOOK: POST_TURN[/bold cyan]", border_style="cyan", expand=True, padding=(0, 1))

    if step_type == "POST_TOOL_HOOK" or msg_type == "CALL_HOOK_POSTTOOL":
        tool_name = ev.get("tool_name") or "tool"
        items = [Text(f"ℹ️ LIFECYCLE HOOK: POST_TOOL ({tool_name}) (INSPECT HOOK)\n", style="bold cyan")]
        return Panel(Group(*items), title=f"[bold cyan]INSPECT HOOK: POST_TOOL ({tool_name})[/bold cyan]", border_style="cyan", expand=True, padding=(0, 1))

    if step_type == "ON_COMPACTION_HOOK" or msg_type == "CALL_HOOK_ONCOMPACTION":
        items = [Text("ℹ️ LIFECYCLE HOOK: ON_COMPACTION (INSPECT HOOK)\n", style="bold magenta")]
        return Panel(Group(*items), title="[bold magenta]INSPECT HOOK: ON_COMPACTION[/bold magenta]", border_style="magenta", expand=True, padding=(0, 1))

    # 4. Standard PreTool Decide Hook
    pre_result = chr_resp.get("preToolResult") or chr_resp.get("pre_tool_result") or {}
    decision = ev.get("decision") or pre_result.get("decision")
    reason = ev.get("reason") or pre_result.get("reason") or ""

    tool_name = ev.get("tool_name") or ""
    if not tool_name and chr_req:
        pt_args = chr_req.get("preToolArgs") or chr_req.get("pre_tool_args") or {}
        tool_name = pt_args.get("toolName") or pt_args.get("tool_name") or ""

    args_data = ev.get("tool_args") or {}
    if not args_data and chr_req:
        pt_args = chr_req.get("preToolArgs") or chr_req.get("pre_tool_args") or {}
        args_json = pt_args.get("argumentsJson") or pt_args.get("arguments_json")
        if args_json:
            try:
                args_data = json.loads(args_json)
            except Exception:
                args_data = {"raw": args_json}

    items: List[RenderableType] = []

    if decision == "DENY":
        items.append(Text("🔒 PRE-TOOL SECURITY POLICY ENFORCEMENT\n", style="bold yellow"))
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", width=18)
        grid.add_column()
        grid.add_row("Target Tool:", f"[bold white]{tool_name}[/bold white]" if tool_name else "Unknown Tool")
        grid.add_row("Policy Decision:", "[bold red]DENY (Execution Prohibited)[/bold red]")
        if reason:
            grid.add_row("Denial Reason:", f"[bold yellow]{reason}[/bold yellow]")
        items.append(grid)

        if args_data:
            items.append(Text("\nEvaluated Tool Arguments:", style="bold magenta"))
            items.append(Syntax(json.dumps(args_data, indent=2, default=str), "json", theme=syntax_theme, line_numbers=True, word_wrap=True))

        return Panel(Group(*items), title="[bold yellow]SECURITY / POLICY INTERCEPTION[/bold yellow]", border_style="yellow", expand=True, padding=(0, 1))

    elif decision == "ALLOW":
        items.append(Text("✅ LIFECYCLE HOOK: PRE_TOOL APPROVED\n", style="bold green"))
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", width=18)
        grid.add_column()
        grid.add_row("Target Tool:", f"[bold white]{tool_name}[/bold white]" if tool_name else "All Tools")
        grid.add_row("Hook Decision:", "[bold green]ALLOW (Approved by Policy)[/bold green]")
        items.append(grid)

        if args_data:
            items.append(Text("\nApproved Tool Arguments:", style="bold green"))
            items.append(Syntax(json.dumps(args_data, indent=2, default=str), "json", theme=syntax_theme, line_numbers=True, word_wrap=True))

        return Panel(Group(*items), title="[bold green]LIFECYCLE HOOK: PRE_TOOL[/bold green]", border_style="green", expand=True, padding=(0, 1))

    else:
        # Pending / In-flight evaluation
        items.append(Text("⏳ LIFECYCLE HOOK: PRE_TOOL EVALUATING\n", style="bold cyan"))
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", width=18)
        grid.add_column()
        grid.add_row("Target Tool:", f"[bold white]{tool_name}[/bold white]" if tool_name else "Unknown Tool")
        grid.add_row("Status:", "[italic bright_black]Evaluating Security Policies & Pre-Tool Hooks...[/italic bright_black]")
        items.append(grid)

        if args_data:
            items.append(Text("\nTarget Tool Arguments:", style="bold cyan"))
            items.append(Syntax(json.dumps(args_data, indent=2, default=str), "json", theme=syntax_theme, line_numbers=True, word_wrap=True))

        return Panel(Group(*items), title="[bold cyan]LIFECYCLE HOOK: PRE_TOOL (EVALUATING)[/bold cyan]", border_style="cyan", expand=True, padding=(0, 1))


def render_tool_error(ev: Dict[str, Any], syntax_theme: str = "dracula") -> RenderableType:
    """Renders tool call exception details, traceback/error message, and parameters."""
    tool_name = ev.get("tool_name") or "tool"
    err_msg = ev.get("error_message") or ev.get("text") or "Tool execution exception occurred."
    args = ev.get("tool_args") or {}

    items: List[RenderableType] = []
    items.append(Text("❌ TOOL EXECUTION EXCEPTION RAISED\n", style="bold red"))

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", width=18)
    grid.add_column()
    grid.add_row("Target Tool:", f"[bold yellow]{tool_name}[/bold yellow]")
    grid.add_row("Status:", "[bold red]FAILED (Exception Raised in In-Process Tool Runner)[/bold red]")
    items.append(grid)

    items.append(Text("\nException Message / Traceback:", style="bold bright_red"))
    items.append(Panel(Text(str(err_msg), style="bright_red"), border_style="red"))

    if args:
        items.append(Text("\nTool Call Arguments:", style="bold cyan"))
        items.append(Syntax(json.dumps(args, indent=2, default=str), "json", theme=syntax_theme, line_numbers=True, word_wrap=True))

    return Panel(Group(*items), title="[bold red]TOOL EXECUTION ERROR[/bold red]", border_style="red", expand=True, padding=(0, 1))


# Registry of dedicated tool visualizers across all 17 Antigravity tool types
_TOOL_DISPATCH_TABLE = {
    "run_command": render_run_command,
    "runCommand": render_run_command,
    "edit_file": render_edit_file,
    "replace_file_content": render_edit_file,
    "editFile": render_edit_file,
    "create_file": render_create_file,
    "write_to_file": render_create_file,
    "createFile": render_create_file,
    "view_file": render_view_file,
    "viewFile": render_view_file,
    "list_dir": render_list_dir,
    "list_directory": render_list_dir,
    "listDirectory": render_list_dir,
    "search_dir": render_search_dir,
    "search_directory": render_search_dir,
    "searchDirectory": render_search_dir,
    "grep_search": render_search_dir,
    "find_file": render_find_file,
    "findFile": render_find_file,
    "find_by_name": render_find_file,
    "invoke_subagent": render_invoke_subagent,
    "start_subagent": render_invoke_subagent,
    "invokeSubagent": render_invoke_subagent,
    "ask_question": render_ask_question,
    "askQuestion": render_ask_question,
    "questionsRequest": render_ask_question,
    "questions_request": render_ask_question,
    "generate_image": render_generate_image,
    "generateImage": render_generate_image,
    "search_web": render_search_web,
    "searchWeb": render_search_web,
    "read_url_content": render_read_url_content,
    "readUrlContent": render_read_url_content,
    "compaction": render_compaction,
    "ActionCompaction": render_compaction,
    "ask_permission": render_ask_permission,
    "askPermission": render_ask_permission,
    "trigger_notification": render_trigger_notification,
    "cancellation": render_cancellation,
    "cancellation_request": render_cancellation,
    "mcp_tool": render_mcp_tool,
    "mcpTool": render_mcp_tool,
    "call_mcp_tool": render_mcp_tool,
    "custom_tool": render_custom_tool,
    "customTool": render_custom_tool,
    "policy_decision": render_policy_event,
    "pre_tool_hook": render_policy_event,
    "tool_error": render_tool_error,
    "finish": render_finish,
}


def render_tool_event(ev: Dict[str, Any], syntax_theme: str = "dracula") -> RenderableType:
    """Master entry point for rendering tool calls, failure banners, policy events, triggers, and cancellation."""
    step_type = ev.get("step_type")
    msg_type = ev.get("message_type") or ""

    if (
        step_type in (
            "POLICY_DECISION", "PRE_TOOL_HOOK", "PRE_TURN_HOOK", "PRE_TURN_DECISION",
            "POST_TURN_HOOK", "POST_TOOL_HOOK", "ON_TOOL_ERROR_HOOK", "ON_TOOL_ERROR_RESULT",
            "ON_COMPACTION_HOOK", "HOOK_REQUEST", "HOOK_RESPONSE",
        )
        or msg_type.startswith("CALL_HOOK_")
        or msg_type in ("POLICY_DECISION", "HOOK_RESPONSE")
    ):
        return render_policy_event(ev, syntax_theme=syntax_theme)

    if step_type == "TRIGGER_NOTIFICATION" or msg_type == "TRIGGER_NOTIFICATION":
        return render_trigger_notification(ev, syntax_theme=syntax_theme)

    if step_type in ("CANCELLATION", "CANCELLATION_REQUEST") or msg_type == "HALT_REQUEST":
        return render_cancellation(ev)

    if step_type == "COMPACTION":
        return render_compaction(ev)

    if step_type == "TOOL_ERROR":
        return render_tool_error(ev, syntax_theme=syntax_theme)

    elements: List[RenderableType] = []

    # 1. State / Failure / Policy Banner (if active)
    state_banner = _render_state_banner(ev)
    if state_banner:
        elements.append(state_banner)
        elements.append(Text(""))

    # 2. Domain-Specific Tool Card
    tool_name = ev.get("tool_name", "")
    renderer = _TOOL_DISPATCH_TABLE.get(tool_name)

    if renderer:
        if renderer in (
            render_run_command,
            render_edit_file,
            render_create_file,
            render_view_file,
            render_ask_permission,
            render_trigger_notification,
            render_mcp_tool,
            render_custom_tool,
            render_generic_tool,
            render_finish,
        ):
            elements.append(renderer(ev, syntax_theme=syntax_theme))
        else:
            elements.append(renderer(ev))
    else:
        # Default to Custom Python Tool visualizer for any unknown/user-defined tool
        elements.append(render_custom_tool(ev, syntax_theme=syntax_theme))

    return Group(*elements) if len(elements) > 1 else elements[0]
