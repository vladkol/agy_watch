# 👀 agy_watch

**Antigravity Observability Console**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![Built with Textual](https://img.shields.io/badge/TUI-Textual-green.svg)](https://textual.textualize.io/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

## Welcome to agy_watch

`agy_watch` is a real-time observability console designed for [Google Antigravity](https://antigravity.google/) and the [Google Antigravity SDK](https://github.com/google-antigravity/antigravity-sdk-python).

Whether you are debugging multi-agent coordination, inspecting subagent swarms, auditing agent policies, checking tool calls, or previewing generated code and media artifacts live, `agy_watch` provides deep, interactive visibility into your agents' inner execution loops directly from your terminal.

![TUI Screenshot](docs/images/screenshot.jpg)

### Core Capabilities
1. **Unified Multi-Engine Observability**: Monitor both custom **SDK Agent Sessions** (Python/multi-language agents via loopback wire-tap) and native **Antigravity App Sessions** (Antigravity Agent and CLI harnesses via brain transcript streaming) in a single dashboard.
2. **Multi-Agent Swarm Tracking**: Visualize concurrent subagent workflows in a structured, hierarchical tree—tracking delegated instructions, worker reasoning, and nested tool calls in context.
3. **End-to-End Tool & Policy Inspection**: Inspect complete input arguments, execution outputs, runtime exceptions, and security policy interceptions with rich dedicated visualizers for shell commands, code diffs, and MCP servers.
4. **Live Workspace & Artifact Preview**: Review generated files, unified code diffs, markdown reports, and media assets in real time directly inside the terminal.
5. **High-Performance Lazy Pagination**: Smooth 60 FPS rendering even for massive sessions with thousands of turns, with interactive on-demand paging (`u` / click) and persistent per-session selection memory.
6. **Machine-Wide Discovery & Scriptable CLI**: Automatically track all local agent sessions from a unified dashboard, or stream structured JSON events into terminal pipelines and evaluation scripts.

---

## Two Kinds of Observed Sessions

`agy_watch` unifies observability across the entire Antigravity ecosystem:

| Session Kind | Badge | Source & Capture Mechanism | Key Benefits |
| :--- | :---: | :--- | :--- |
| **Antigravity App Sessions** | `[antigravity]`<br>`[jetski]`<br>`[cli]` | Native **Antigravity Agent** (IDE extension), **Antigravity CLI**, and standalone harnesses. Automatically discovered from `~/.gemini/*/brain` or loaded from custom paths via live transcript streaming (`transcript.jsonl`). | Instant insight into agent reasoning, thoughts, user prompts, 2-step tool transactions (`PLANNER_RESPONSE` + tool outputs), generated files, and code diffs. |
| **SDK Agent Sessions** | `[sdk]` | Custom agents and multi-agent swarms built with the `google-antigravity` Python SDK (or universal proxy). Wire-tapped via loopback WebSocket IPC (`wire_tap.db`). | Zero-code monitoring of custom agent workflows, MCP tools, subagent swarms, lifecycle policy hooks, and runtime approvals. |

---

## Quick Start

### 1. Launch Interactive Dashboard (Observes All Sessions)
```bash
# Open interactive 3-pane TUI (discovers all local SDK & Antigravity App sessions)
agy_watch
```

### 2. Watch Antigravity App Sessions (Agent, IDE & CLI)
Antigravity App sessions stored under `~/.gemini/*/brain` are **automatically discovered** upon launching `agy_watch`. You can also target specific sessions or custom brain directories directly:

```bash
# Open a specific Antigravity app session directly in TUI
agy_watch ~/.gemini/antigravity/brain/<session_id>

# Open a custom brain or app directory
agy_watch ~/my-project/.gemini/jetski/brain

# List all discovered Antigravity app and SDK sessions
agy_watch list

# Tail live events from an active Antigravity IDE or CLI session
agy_watch tail <session_id> --follow
```

### 3. Observe Antigravity SDK Agents
Enable zero-code auto-observability on your Python virtual environment with one command:

```bash
# Enable auto-hook in your active virtual environment
agy_watch watch

# Run your SDK agent script normally
python my_agent.py
```

---

## Key Features

* **Machine-Wide Unified Dashboard**: Automatically discovers active and historical SDK agents and Antigravity App sessions (IDE, Agent, and CLI) without manual configuration.
* **Hierarchical Execution Tree**: Groups root agent and subagent swarms into collapsible branches, giving you immediate clarity on delegated tasks and tool flows.
* **Dynamic Lazy Pagination & Selection Memory**: Opens instantly focused on the **most recent event**, maintaining smooth 60 FPS scrolling on 5,000+ turn sessions with on-demand expansion (`u` or click) and persistent per-session selection memory.
* **First-Class Visualizers for Common Tools**: Dedicated, human-readable visualizers for shell commands (with exit codes and terminal output), file edits (with syntax-colored unified diffs), search directories, image generation, interactive user questions, and MCP tools.
* **Security Policy & Error Interception**: Clearly identifies blocked actions, policy denial reasons, and tool runtime exceptions with prominent diagnostic cards.
* **In-Terminal Artifact & Diff Viewer**: Instant syntax-highlighted previews for code, markdown documents, and images as soon as agents create or update them in their workspace.
* **Real-Time Live Streaming & Follow Mode**: Watch thoughts, streaming tokens, and execution steps render live as the model generates them, with auto-scroll and pause controls.
* **Non-Interactive CLI for Scripting & CI/CD**: Query past runs, tail active sessions, and inspect step payloads in JSON or YAML format for automated evaluation pipelines.

---

## Installation

### Global CLI Installation (Recommended)

Install `agy_watch` globally using `uv tool` or `pipx`:

```bash
# Install globally via uv tool
uv tool install git+https://github.com/vladkol/agy_watch.git

# Or install globally via pipx
pipx install git+https://github.com/vladkol/agy_watch.git
```

---

### Local Virtual Environment Installation

To observe an Antigravity SDK agent from within its virtual environment:

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

## Observing Antigravity App Sessions (Agent, IDE & CLI)

`agy_watch` natively understands the Antigravity App session format (`transcript.jsonl` / `transcript_full.jsonl`) generated by Antigravity Agent, IDE extensions, and the Antigravity CLI.

### Automatic Discovery
By default, `agy_watch` automatically scans all subdirectories in `~/.gemini/*/brain` (such as `~/.gemini/antigravity`, `~/.gemini/jetski`, and any custom app workspace), displaying live and completed sessions with their source tags in the session switcher.

### Targeted Inspection
You can target specific sessions or custom workspace locations directly from the CLI:

```bash
# Open a specific session directly by folder path
agy_watch ~/.gemini/antigravity/brain/8c1c6ab6-59e3-4cbb-2d43-19c8a7c24989

# Scan a custom project directory containing a .gemini or brain folder
agy_watch /path/to/custom/workspace

# List sessions filtered from a specific brain root
agy_watch list /path/to/custom/workspace

# Inspect a specific step index from an App session
agy_watch inspect <session_id> --step 4 --json
```

---

## Observing Antigravity SDK Agents (Zero-Code Wire-Tapping)

`agy_watch` provides multiple ways to observe custom SDK agents without modifying your agent code:

### 1. In-Venv Auto-Observability (`agy_watch watch`) *(Recommended)*

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

For standalone or non-Python SDK agents (assuming they check `ANTIGRAVITY_HARNESS_PATH` as the Python SDK does):

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
# Open dashboard with machine-wide discovery
agy_watch

# Or open focused on a specific session ID or directory
agy_watch <session_id_or_path>
```

### Layout Overview

```text
┌─────────────────────────┬───────────────────────────────┬───────────────────────────────┐
│ SESSIONS (Machine-Wide) │ EXECUTION TREE (Hierarchical) │ EVENT & ARTIFACT INSPECTOR    │
├─────────────────────────┼───────────────────────────────┼───────────────────────────────┤
│ 🟢 03c8590a [sdk]       │ Root Agent Execution          │ [Event Details] [Artifacts]   │
│    Carbon Trip Planner  │  ├─ [12:14:44 PM] USER_PROMPT │ ───────────────────────────── │
│    41.7k tok            │  ├─ ▼ TOOL: calculate_carbon  │ ─── CUSTOM PYTHON TOOL ───────│
│ ─────────────────────── │  │   └─ [Done] 1.8 kg CO2     │ Function: calculate_carbon    │
│ ⚪ e7b21778 [antigravity│  ├─ ▼ MCP [everything:echo]   │ Arguments:                    │
│    Code Refactor Task   │  │   └─ [Done] Trip confirmed │   distance_km: 650            │
│    25.1k tok • 2 workers│  └─ [12:15:10 PM] MODEL_RESP  │   transport_mode: "train"     │
└─────────────────────────┴───────────────────────────────┴───────────────────────────────┘
```

### Keyboard Shortcuts

| Key | Action | Description |
| :--- | :--- | :--- |
| **`q`** | Quit | Exit the `agy_watch` console. |
| **`Space`** | Follow / Pause | Toggle auto-scrolling to follow the live event stream. |
| **`f`** or **`Enter`** | Fullscreen | Open the full-screen reader modal for the selected event, code, diff, or markdown file. |
| **`c`** / **`Cmd+C`** / **`Alt+C`** / **`Ctrl+C`** | Copy Text | Copy highlighted text from active control or full event payload directly to OS clipboard. |
| **`a`** | Toggle Tab | Switch inspector pane between `Event Details` and `Artifacts & Files`. |
| **`t`** | Tree / Flat View | Toggle between Hierarchical Subagent Tree and Flat Chronological Stream. |
| **`s`** / **`p`** | Cycle Theme | Cycle through paired TUI and syntax highlighter color schemes (Dracula, Nord, Monokai, Tokyo Night, Gruvbox, Catppuccin). |
| **`o`** | Open External | Open selected media file in your OS default viewer (Preview, QuickLook, `xdg-open`). |
| **`w`** | Toggle Wrap | Toggle word wrapping inside the full-screen reader. |
| **`u`** | Earlier Steps | Load the previous 150 historical steps on demand. |
| **`U`** | Load All Steps | Expand the execution tree to load all historical steps in the session. |
| **`r`** | Refresh | Force immediate refresh of host sessions. |
| **`0`** | Filter All | Reset subagent filters and display all execution lanes. |

---

## CLI Command Reference (Scripting & Automation)

`agy_watch` provides non-interactive subcommands for scripting, terminal piping, and automated evaluation workflows:

### 1. Main Entrypoint & TUI Launch (`agy_watch`)

```bash
# Launch TUI dashboard (auto-discovers SDK + App sessions)
agy_watch

# Open TUI attached directly to a session by ID or directory path
agy_watch 8c1c6ab6
agy_watch ~/.gemini/antigravity/brain/8c1c6ab659e34cbb2d4319c8a7c24989

# Direct attachment flag
agy_watch --attach <session_id>
```

### 2. List Sessions (`agy_watch list`)

```bash
# List all registered SDK and Antigravity App sessions
agy_watch list

# Filter by live processes only
agy_watch list --status live

# Filter by idle / completed sessions
agy_watch list --status idle

# Scan a custom workspace or brain directory
agy_watch list /path/to/workspace

# Output as JSON for jq pipelines
agy_watch list --json

# Output as YAML
agy_watch list --yaml
```

### 3. Stream Live Events (`agy_watch tail`)

```bash
# Print existing events from an SDK or Antigravity App session
agy_watch tail <session_id_or_path>

# Follow events in real time as they arrive
agy_watch tail <session_id_or_path> --follow

# Stream events as structured JSON Lines (NDJSON)
agy_watch tail <session_id_or_path> --follow --json

# Stream events as YAML
agy_watch tail <session_id_or_path> --yaml
```

### 4. Inspect Steps & Payloads (`agy_watch inspect`)

```bash
# Full session summary, event stream, and generated artifacts
agy_watch inspect <session_id_or_path>

# Inspect a specific step index
agy_watch inspect <session_id_or_path> --step 3

# Output full inspection payload as JSON
agy_watch inspect <session_id_or_path> --json

# Output full inspection payload as YAML
agy_watch inspect <session_id_or_path> --yaml
```

### 5. Attach Directly (`agy_watch attach`)

```bash
# Open interactive TUI focused on a specific session ID
agy_watch attach <session_id>
```

### 6. Auto-Hook Management (`agy_watch watch` / `unwatch` / `status`)

```bash
# Install .pth auto-hook in the current Python environment
agy_watch watch

# Install .pth auto-hook in a specific virtual environment path
agy_watch watch /path/to/.venv

# View active watched environments and proxy binary path
agy_watch status

# Remove auto-hook from current environment
agy_watch unwatch

# Remove auto-hooks from all registered environments
agy_watch unwatch --all
```

### 7. Run Scripts with Wire-Tapping (`agy_watch run`)

```bash
# Execute any Python agent script with transparent loopback proxy
agy_watch run my_agent.py --prompt "Analyze codebase"
```

### 8. Universal Proxy Binary Path (`agy_watch proxy-path`)

```bash
# Output the absolute path to the universal harness proxy binary
agy_watch proxy-path
```

---

> [!IMPORTANT]
> **Disclaimer**: `agy_watch` is a developer debugging and observability tool and is **NOT an official Google product or framework**. It is designed for testing, inspecting, and profiling autonomous agents built with Google Antigravity and the Antigravity Python SDK.

---

## Architecture & Internals

For in-depth technical details on the WebSocket hook implementation, brain transcript streaming, SQLite WAL synchronization, CAS blob offloading, MCP wire formats, and stream deduplication, see **[docs/INTERNALS.md](docs/INTERNALS.md)**.
