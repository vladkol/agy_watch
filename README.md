# 👀 agy_watch

**Antigravity SDK Observability Console**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![Built with Textual](https://img.shields.io/badge/TUI-Textual-green.svg)](https://textual.textualize.io/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

## Welcome to agy_watch

The [Google Antigravity SDK](https://github.com/google-antigravity/antigravity-sdk-python) makes it fast and intuitive to build autonomous AI agents, multi-agent swarms, and tool-augmented workflows. Antigravity SDK exposes a Python interface paired under the hood with a high-performance Go runtime (`localharness`) that executes model reasoning, tool invocations, and subagent lifecycles at high speed.

**`agy_watch` provides deep, real-time observability into your Antigravity agents' inner execution loops.**

Whether you are debugging multi-agent coordination, inspecting subagent prompts, viewing Model Context Protocol (MCP) tools, or previewing generated code and media artifacts live, `agy_watch` lets you monitor the action directly from your terminal.

![TUI Screenshot](docs/images/screenshot.jpg)

### Core Capabilities
1. **Zero-Code Wire-Tap**: Non-intrusively captures all inbound and outbound IPC frames between the Python SDK and the `localharness` runtime without requiring any changes to your agent code.
2. **Interactive TUI Dashboard**: A 3-pane terminal console built with [Textual](https://textual.textualize.io/) and [Rich](https://rich.readthedocs.io/) to monitor live sessions, explore recursive sub-agent trees, and inspect code diffs and media files with syntax highlighting.
3. **First-Class Tool Support**: Dedicated rich renderers for in-process Python callable functions, Model Context Protocol (MCP) stdio servers, standard SDK tools, and subagent spawning.
4. **Machine-Wide Session Registry**: Automatically tracks, indexes, and monitors all agent runs across your machine with zero-configuration discovery and process liveness checks.
5. **Scriptable CLI**: Non-interactive subcommands (`list`, `tail -f`, `inspect`) with JSON, YAML, and formatted table outputs for scripting, terminal piping, and automated evaluations.

---

## Key Features

* **Zero-Lock Real-Time Streaming**: Powered by SQLite Write-Ahead Logging (`PRAGMA journal_mode=WAL;`) and incremental sequence cursors, delivering instant updates with zero lock contention against running agents.
* **Hierarchical Recursive Sub-Agent Tree**: Automatically maps concurrent subagents (`invoke_subagent`), tool dispatches, and model reasoning into clean, expandable and collapsible tree branches.
* **Correlated Tool Arguments**: Pairs pre-tool hooks (`CALL_HOOK_PRETOOL`) with active tool calls, providing complete visibility into worker prompts, subagent roles, and tool parameters.
* **Dedicated Custom & MCP Tool Renderers**: Color-coded, structured visualization for Python function calls and Model Context Protocol (MCP) tools with transport badges and parameter tables.
* **Master-Detail In-Terminal Artifact Preview**: Live syntax-highlighted previewer for Python, HTML, TypeScript, JSON, YAML, Shell, SQL, Markdown, and media assets (`.png`, `.jpg`, `.mp4`).
* **Multimodal Brain Storage Discovery**: Automatically locates and surfaces generated images, diagrams, and files from local workspaces and shared Antigravity brain storage.
* **Full-Screen Syntax Reader**: Press `f` or `Enter` to read prompts, reasoning traces, and code diffs in a dedicated full-screen viewer with paired syntax highlighting and toggleable word wrapping (`w`).
* **OS-Native Locale & Clock Formatting**: Dynamically detects system locale conventions (such as macOS `AppleLocale` and 12-hour/24-hour preferences) with 2-digit year representations.
* **Persistent User Preferences**: Automatically remembers your preferred app theme, syntax palette, view mode (`tree` vs `flat`), active inspector tab, and text wrapping across restarts.

---

## Installation

Install `agy_watch` into your Python virtual environment (Python 3.11 to 3.13):

```bash
# Using uv (Recommended)
uv add git+https://github.com/vladkol/agy_watch.git

# Or via standard pip
pip install git+https://github.com/vladkol/agy_watch.git
```

### Local Development Setup

```bash
# Clone repository
git clone https://github.com/vladkol/agy_watch.git
cd agy_watch

# Install dependencies and build editable environment
uv sync
```

---

## Global CLI Installation (Optional)

You can install `agy_watch` globally using `uv tool` or `pipx`:

```bash
# Install globally via uv tool
uv tool install git+https://github.com/vladkol/agy_watch.git

# Or into an active virtual environment
uv add git+https://github.com/vladkol/agy_watch.git
```

---

## Zero-Code Wire-Tapping

`agy_watch` provides multiple ways to observe your agents without changing a single line of your agent code:

### 1. In-Venv Auto-Observability (`agy_watch watch`) *(Recommended for Python)*

Enable auto-observability on your active virtualenv with one command:

```bash
# Watch the current virtual environment
agy_watch watch

# Or watch a specific project directory
agy_watch watch ./my_agent_project
```

Run your agents normally with `python`, `uv run`, `pytest`, or your IDE's Run button:
```bash
python my_agent.py
```
Every execution is automatically wire-tapped and indexed. To disable:
```bash
agy_watch unwatch
```

---

### 2. Universal Non-Python & Standalone Agents (`proxy-path`)

For agents written in **Node.js/TypeScript, Go, Rust, Java**, or standalone binary scripts:

```bash
# Run any agent through the universal harness proxy
ANTIGRAVITY_HARNESS_PATH="$(agy_watch proxy-path)" ./my-agent
```

---

### 3. On-Demand CLI Runner (`agy_watch run`)

Run any Python agent script on-demand with automatic proxy wire-tapping:

```bash
agy_watch run my_agent.py --arg1 value
```

---

## Interactive TUI Dashboard

Launch the interactive 3-pane dashboard:

```bash
agy_watch
```

### Layout Overview

```text
┌─────────────────────────┬───────────────────────────────┬───────────────────────────────┐
│ SESSIONS (Machine-Wide) │ EXECUTION TREE (Hierarchical) │ EVENT & ARTIFACT INSPECTOR    │
├─────────────────────────┼───────────────────────────────┼───────────────────────────────┤
│ 🟢 03c8590a 08/05/26    │ Root Agent Execution          │ [Event Details] [Artifacts]   │
│    Carbon Trip Planner  │  ├─ [12:14:44 PM] USER_PROMPT │ ───────────────────────────── │
│    41.7k tok            │  ├─ ▼ TOOL: calculate_carbon  │ ─── CUSTOM PYTHON TOOL ───────│
│ ─────────────────────── │  │   └─ [Done] 1.8 kg CO2     │ Function: calculate_carbon    │
│ ⚪ 6a52a571 08/05/26    │  ├─ ▼ MCP [everything:echo]   │ Arguments:                    │
│    Multimodal Image Run │  │   └─ [Done] Trip confirmed │   distance_km: 650            │
│    25.1k tok • 1 worker │  └─ [12:15:10 PM] MODEL_RESP  │   transport_mode: "train"     │
└─────────────────────────┴───────────────────────────────┴───────────────────────────────┘
```

### Keyboard Shortcuts

| Key | Action | Description |
| :--- | :--- | :--- |
| **`q`** | Quit | Exit the `agy_watch` console. |
| **`Space`** | Follow / Pause | Toggle auto-scrolling to follow the live event stream. |
| **`f`** or **`Enter`** | Fullscreen | Open the full-screen reader modal for the selected event, code, or markdown file. |
| **`c`** / **`Cmd+C`** / **`Alt+C`** / **`Ctrl+C`** | Copy Text | Copy highlighted text from active control or full event payload directly to OS clipboard. |
| **`a`** | Toggle Tab | Switch inspector pane between `Event Details` and `Artifacts & Files`. |
| **`t`** | Tree / Flat View | Toggle between Hierarchical Subagent Tree and Flat Chronological Stream. |
| **`s`** / **`p`** | Cycle Theme | Cycle through paired TUI and syntax highlighter color schemes (Dracula, Nord, Monokai, Tokyo Night, Gruvbox, Catppuccin). |
| **`o`** | Open External | Open selected media file in your OS default viewer (Preview, QuickLook, `xdg-open`). |
| **`w`** | Toggle Wrap | Toggle word wrapping inside the full-screen reader. |
| **`r`** | Refresh | Force immediate refresh of host sessions. |
| **`0`** | Filter All | Reset subagent filters and display all execution lanes. |

---

## CLI Commands (Scripting & Automation)

`agy_watch` provides non-interactive commands for scripting, terminal piping, and automated evaluation workflows:

### 1. List Sessions (`agy_watch list`)

```bash
# Formatted table with status icons and locale timestamps
agy_watch list

# Filter by live processes only
agy_watch list --status live

# JSON output for jq processing
agy_watch list --json

# YAML output
agy_watch list --yaml
```

### 2. Stream Live Events (`agy_watch tail`)

```bash
# Follow events in real time
agy_watch tail <session_id> --follow

# Stream events as structured JSON Lines (NDJSON)
agy_watch tail <session_id> --follow --json
```

### 3. Inspect Steps & Payloads (`agy_watch inspect`)

```bash
# Full summary and event list
agy_watch inspect <session_id>

# Inspect a specific step index in JSON format
agy_watch inspect <session_id> --step 3 --json
```

### 4. Direct TUI Attachment (`agy_watch attach`)

```bash
agy_watch attach <session_id>
```

---

## Examples

The [`examples/`](examples/) directory contains complete, standalone Antigravity SDK scripts showcasing various agent configurations:

* **[`01_quickstart_chat_streaming.py`](examples/01_quickstart_chat_streaming.py)**: Basic agent reasoning, system prompts, and streaming chat.
* **[`02_multimodal_image_artifacts.py`](examples/02_multimodal_image_artifacts.py)**: Multimodal image generation and binary artifact extraction.
* **[`03_custom_and_mcp_tools.py`](examples/03_custom_and_mcp_tools.py)**: In-process custom Python tools and Model Context Protocol (MCP) stdio server integration.
* **[`04_multi_subagent_hierarchy.py`](examples/04_multi_subagent_hierarchy.py)**: Hierarchical agent swarms with concurrent subagent workers.

For instructions on running the examples, see [`examples/README.md`](examples/README.md).

---

> [!IMPORTANT]
> **Disclaimer**: `agy_watch` is a personal developer debugging and observability tool and is **NOT an official Google product or framework**. It is designed for testing, inspecting, and profiling autonomous agents built with [`google-antigravity==0.1.9`](https://pypi.org/project/google-antigravity/) and its bundled `localharness` binary.
> The tool is built by Antigravity itself by observing Antigravity SDK and localharness protocol.
> There is no guarantee for this tool to work prior or beyond this version.
> If any changes are required, you are welcome to file an issue and/or make a Pull Request.

---

## Architecture & Internals

For in-depth technical details on the WebSocket hook implementation, SQLite WAL synchronization, CAS blob offloading, MCP wire formats, and stream deduplication, see **[docs/INTERNALS.md](docs/INTERNALS.md)**.
