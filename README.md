# 👀 agy_watch

**Antigravity SDK Observability Console & Wire-Tap Module**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![Built with Textual](https://img.shields.io/badge/TUI-Textual-green.svg)](https://textual.textualize.io/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

## Welcome to agy_watch!

The [Google Antigravity Python SDK](https://github.com/google/antigravity) makes it fast and intuitive to build autonomous AI agents, multi-agent swarms, and tool-augmented workflows. Antigravity SDK exposes a Python interface paired under the hood with a high-performance Go runtime (`localharness`) that executes model reasoning, tool invocations, and subagent lifecycles at blistering speed.

**`agy_watch` gives you front-row seats to your Antigravity agents' inner worlds.**

Whether you're debugging multi-agent coordination, inspecting subagent prompts, or viewing generated artifacts live, `agy_watch` lets you watch the action unfold in real time right from your terminal.

![TUI Screenshot](docs/images/screenshot.jpg)

### What `agy_watch` Brings to Your Workflow:
1. **One-Line Python Wire-Tap**: Non-intrusively captures all inbound and outbound IPC frames between the Python SDK and the `localharness` runtime without requiring any changes to your agent logic.
2. **Interactive 3-Pane TUI Dashboard**: An interactive terminal console built with [Textual](https://textual.textualize.io/) and [Rich](https://rich.readthedocs.io/) to monitor live sessions, explore recursive sub-agent trees, and preview code and media files with syntax highlighting.
3. **Automated Host-Wide Session Registry**: Tracks and indexes all your agent runs across your machine with zero-config discovery and live process liveness checks.
4. **Scriptable CLI for Power Users**: Non-interactive subcommands (`list`, `tail -f`, `inspect`) with JSON and YAML formatting, perfect for scripting, terminal piping, and automated evaluations.

---

## Key Features

* **Zero-Lock Real-Time Streaming**: Powered by SQLite Write-Ahead Logging (`PRAGMA journal_mode=WAL;`) and incremental sequence cursors, delivering real-time updates with zero lock contention against running agents.
* **Hierarchical Recursive Sub-Agent Tree**: Automatically maps concurrent subagents (`invoke_subagent`), tool dispatches, and model reasoning into clean, expandable/collapsible tree branches.
* **Correlated Tool Arguments**: Pairs pre-tool hooks (`CALL_HOOK_PRETOOL`) with active tool calls, giving you full visibility into worker prompts, subagent roles, and tool parameters.
* **Master-Detail In-Terminal File Preview**: Live syntax-highlighted previewer for Python, HTML, TypeScript, JSON, YAML, Shell, SQL, Markdown, and media assets (`.png`, `.jpg`, `.mp4`).
* **Full-Screen Syntax Reader**: Press `f` or `Enter` to read prompts, reasoning traces, and code diffs in a dedicated full-screen viewer with Dracula syntax highlighting and toggleable word wrapping (`w`).
* **Machine-Wide Discovery**: Centralized registry at `~/.antigravity/samples/agy_watch/registry.db` that tracks all active and historical agent runs across your local machine with automatic PID liveness detection.

---

## Installation

Install `agy_watch` directly from GitHub into your Python virtual environment (Python 3.11 to 3.13):

```bash
# Using uv (Recommended)
uv add git+https://github.com/vladkol/agy_watch.git

# Or via standard pip
pip install git+https://github.com/vladkol/agy_watch.git
```

### For Local Development:

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

`agy_watch` offers flexible options to observe your agents without changing a single line of your agent code:

### 1. In-Venv Auto-Observability (`agy_watch watch`) *(Recommended for Python)*

Enable auto-observability on your active virtualenv with one command:

```bash
# Watch the current virtual environment
agy_watch watch

# Or watch a specific project directory
agy_watch watch ./my_agent_project
```

From this moment forward, run your agents normally with plain `python`, `uv run`, `pytest`, or your IDE's Run/Debug button:
```bash
python my_agent.py
```
Every execution is automatically wire-tapped and indexed. To disable:
```bash
agy_watch unwatch
```

---

### 2. Universal Non-Python & Standalone Agents (`proxy-path`)

For agents written in **Node.js/TypeScript, Go, Rust, Java**, or standalone Python scripts:

```bash
# Run any agent through the universal harness proxy
ANTIGRAVITY_HARNESS_PATH="$(agy_watch proxy-path)" ./my-non-python-agent
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
│ ● LIVE 3e3162c9 13:38   │ Root Agent Execution          │ [Event Details] [Artifacts]   │
│   YOLO Subagents Demo   │  ├─ [13:38:01] USER_PROMPT    │ ───────────────────────────── │
│   (2 workers) 17.9k tok │  ├─ ▼ TOOL: invoke_subagent   │ ─── TOOL CALL: invoke_subagent│
│                         │  │   ├─ Subagent 1 [Done]     │ Spawning 2 Subagent(s):       │
│ ○ IDLE bdb7db1e 12:10   │  │   │   └─ TOOL: write_to... │   1. Role: Worker 1           │
│   Image Generation Run  │  │   └─ Subagent 2 [Done]     │      Prompt: Write file...    │
│   15.4k tok             │  │       └─ TOOL: write_to... │   2. Role: Worker 2           │
│                         │  └─ [13:38:44] MODEL_RESPONSE │      Prompt: Write file...    │
└─────────────────────────┴───────────────────────────────┴───────────────────────────────┘
```

### Keyboard Shortcuts

| Key | Action | Description |
| :--- | :--- | :--- |
| **`q`** | Quit | Exit the `agy_watch` console. |
| **`Space`** | Follow / Pause | Toggle auto-scrolling to follow the live event stream. |
| **`f`** or **`Enter`** | Fullscreen | Open the full-screen reader modal for the selected event, code, or markdown file. |
| **`a`** | Toggle Tab | Switch inspector pane between `Event Details` and `Artifacts & Files`. |
| **`t`** | Tree / Flat View | Toggle between Hierarchical Subagent Tree and Flat Chronological Stream. |
| **`o`** | Open External | Open selected media file in your OS default viewer (Preview, QuickLook, `xdg-open`). |
| **`w`** | Toggle Wrap | Toggle word wrapping inside the full-screen reader. |
| **`c`** | Copy Payload | Copy the active step payload JSON to your system clipboard. |
| **`r`** | Refresh | Force immediate refresh of host sessions. |
| **`0`** | Filter All | Reset subagent filters and display all execution lanes. |

---

## CLI Commands (Scripting & Automation)

`agy_watch` provides clean non-interactive commands for scripting, terminal piping, and CI/CD pipelines:

### 1. List Sessions (`agy_watch list`)

```bash
# Human-readable table
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
# Follow events in real-time
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

## Examples

For pure Antigravity SDK demonstration scripts showcasing chat streaming, multimodal image artifacts, and recursive multi-subagent hierarchies, see the [`examples/`](examples/) directory and [`examples/README.md`](examples/README.md).

---

> [!IMPORTANT]
> **Disclaimer**: `agy_watch` is a personal developer debugging and observability tool and is **NOT an official Google product or framework**. It is designed for testing, inspecting, and profiling autonomous agents built with [`google-antigravity==0.1.9`](https://pypi.org/project/google-antigravity/) and its bundled `localharness` binary.
The tool is built by Antigravity itself by observing Antigravity SDK and localharness protocol.
There is no guaranty for this tool to work prior or beyond this version.
If any changes are required, you are welcome to file an issue and/or make a Pull Request.

---

## Architecture & Internals

For in-depth details on the WebSocket hook implementation, SQLite WAL synchronization, CAS blob offloading, and streaming deduplication, see **[docs/INTERNALS.md](docs/INTERNALS.md)**.
