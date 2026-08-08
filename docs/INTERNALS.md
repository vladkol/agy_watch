# agy_watch: Protocol & System Internals

> [!IMPORTANT]
> **Disclaimer**: `agy_watch` is a personal developer debugging and observability tool and is **NOT an official Google product or framework**. It is designed for testing, inspecting, and profiling autonomous agents built with [`google-antigravity==0.1.9`](https://pypi.org/project/google-antigravity/) and its bundled `localharness` binary.
> The tool is built by Antigravity itself by observing Antigravity SDK and localharness protocol.
> There is no guarantee for this tool to work prior or beyond this version.
> If any changes are required, you are welcome to file an issue and/or make a Pull Request.

---

## 1. Dual-Engine Observability Architecture

`agy_watch` unifies two distinct agent execution and data pipelines under a single real-time observability umbrella:

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 agy_watch TUI / CLI                                    │
│       (Unified Machine-Wide Session Registry • Dynamic Lazy Pagination • 60 FPS)       │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │
                    ┌───────────────────────┴───────────────────────┐
                    ▼                                               ▼
┌───────────────────────────────────────┐       ┌───────────────────────────────────────┐
│ ENGINE 1: SDK Agent Sessions ([sdk])  │       │ ENGINE 2: App Sessions ([antigravity])│
│ - Custom Python / Multi-Lang Agents   │       │ - Native Antigravity IDE Agent & CLI  │
│ - Loopback WebSocket Wire-Tap Hook    │       │ - Live JSONL Transcript Streaming     │
│ - SQLite WAL Database (wire_tap.db)   │       │ - 2-Step Tool Transaction Merging     │
│ - Content-Addressable Blob Storage    │       │ - mtime-Cached Discovery Engine       │
└───────────────────────────────────────┘       └───────────────────────────────────────┘
```

---

## 2. Engine 1: Antigravity SDK & Loopback Wire-Tap Protocol

The [Google Antigravity SDK](https://github.com/google-antigravity/antigravity-sdk-python) pairs a high-level SDK interface in Python with a specialized Go daemon (`localharness`) running as a local child process.

```text
┌──────────────────────────────────────┐          Loopback WebSocket IPC          ┌──────────────────────────┐
│ Antigravity SDK (e.g. Python Process)│ ◄──────────────────────────────────────► │ localharness (Go Daemon) │
│ - Agent Orchestration               │          ws://127.0.0.1:<port>           │ - Model Reasoning        │
│ - Tool Callbacks & MCP Transports    │          Protobuf / JSON Wire Frames     │ - Subagent Trajectories  │
│ - Security Policy Callbacks          │                                          │ - Execution Sandboxes    │
└──────────────────────────────────────┘                                          └──────────────────────────┘
```

### 2.1 Boot Lifecycle & Handshake
1. **Daemon Spawning**: The SDK starts the `localharness` binary as a child subprocess, passing an `InputConfig` payload via `stdin`.
2. **Port Allocation**: `localharness` binds an internal WebSocket server to an ephemeral loopback port (`127.0.0.1:<port>`) and generates an authentication token.
3. **Handshake Discovery**: `localharness` writes an `OutputConfig` JSON line containing `{ "port": <port>, "apiKey": "<token>" }` to `stdout`.
4. **IPC Connection**: The SDK reads `OutputConfig` from `stdout` and establishes a private WebSocket connection (`LocalConnectionStrategy._connect_websocket`) to `ws://127.0.0.1:<port>/`.
5. **Turn Execution**: All subsequent agent turns, reasoning traces, tool dispatches, subagent lifecycles, and security hooks flow bidirectionally across this WebSocket stream.

---

## 3. Zero-Code Interception Architecture (SDK Sessions)

`agy_watch` captures the complete conversation stream without requiring changes to agent code through two complementary mechanisms:

```text
                                        ┌────────────────────────────────────────────────────────┐
                                        │ AGENT RUNTIME                                          │
                                        │                                                        │
┌───────────────────────────┐           │   [Python In-Memory Hook]  OR  [Universal Proxy Shim]  │
│ agy_watch watch (In-Venv) │ ────────► │   auto_hook.pth                ANTIGRAVITY_HARNESS_PATH│
└───────────────────────────┘           │             │                             │            │
                                        │             ▼                             ▼            │
                                        │   ┌────────────────────────────────────────────────┐   │
                                        │   │ Bidirectional Frame Interception               │   │
                                        │   │ - Parse JSON/Protobuf                          │   │
                                        │   │ - Offload Blobs (>64 KB) to CAS Storage        │   │
                                        │   │ - Append-Only Write to wire_tap.db (WAL Mode)  │   │
                                        │   └────────────────────────────────────────────────┘   │
                                        └───────────────────────────┬────────────────────────────┘
                                                                    │
                                                                    ▼
                                        ┌────────────────────────────────────────────────────────┐
                                        │ PERSISTENT DATA LAYER                                  │
                                        │ <workspace>/.trajectories/                             │
                                        │ ├── wire_tap.db (wire_events, session_meta)            │
                                        │ └── blobs/<sha256[:2]>/<sha256>.<ext>                  │
                                        └────────────────────────────────────────────────────────┘
```

### 2.1 In-Memory Python Hook (`install_wire_tap`)
For Python environments, `agy_watch` dynamically wraps `LocalConnectionStrategy._connect_websocket`. When the SDK establishes its WebSocket connection, the underlying `WebSocketClientProtocol` is transparently wrapped in a `TappedWebSocket`:

```python
class TappedWebSocket:
    def __init__(self, raw_ws: Any, wire_tap_db: WireTapDB):
        self._raw_ws = raw_ws
        self._db = wire_tap_db

    async def send(self, data: Any) -> None:
        payload = json.loads(data) if isinstance(data, (str, bytes)) else data
        self._db.record_outbound(payload)
        await self._raw_ws.send(data)

    async def recv(self) -> Any:
        msg = await self._raw_ws.recv()
        payload = json.loads(msg) if isinstance(msg, (str, bytes)) else msg
        self._db.record_inbound(payload)
        return msg
```

When enabled via `agy_watch watch`, a `.pth` file (`agy_watch_hook.pth`) is placed into the virtual environment's `site-packages/`. During Python startup, the standard library `site` module imports `agy_watch.auto_hook`, activating the wire-tap automatically.

### 2.2 Universal Multi-Language Proxy (`agy-harness-proxy`)
For agents written in Node.js/TypeScript, Go, Rust, or standalone binary workflows, setting `ANTIGRAVITY_HARNESS_PATH="$(agy_watch proxy-path)"` inserts an intermediate proxy process:
1. The agent spawns `agy-harness-proxy` thinking it is `localharness`.
2. The proxy launches the real `localharness` binary as a child process and relays `stdin` / `InputConfig`.
3. It intercepts the real `OutputConfig(port=P, apiKey=K)` from the child's `stdout`.
4. It starts an ephemeral WebSocket proxy server on port $P'$ and emits a rewritten `OutputConfig(port=P', apiKey=K)` to the parent agent.
5. The proxy transparently forwards all frames bidirectionally while recording every frame to `wire_tap.db`.

---

## 3. Wire Protocol & Message Taxonomy

The communication between the SDK and `localharness` consists of structured JSON/protobuf frames categorized by direction and purpose:

```text
Direction       Message Type                 Payload Envelope / Key
────────────────────────────────────────────────────────────────────────────────
TO_HARNESS   │  USER_PROMPT               │  userInput, complexUserInput
TO_HARNESS   │  USER_ANSWER               │  questionResponse
TO_HARNESS   │  POLICY_DECISION           │  callHookResponse (preToolResult)
TO_HARNESS   │  TOOL_RESPONSE             │  toolResponse (responseJson, errorMessage)
─────────────┼────────────────────────────┼─────────────────────────────────────
FROM_HARNESS │  INIT_CONVERSATION         │  initializeConversationResponse
FROM_HARNESS │  STEP_UPDATE               │  stepUpdate (text, thinking, state)
FROM_HARNESS │  CALL_HOOK_PRETOOL         │  callHookRequest (preToolArgs)
FROM_HARNESS │  TOOL_CALL                 │  toolCall, stepUpdate.<actionKey>
FROM_HARNESS │  TRAJECTORY_STATE_UPDATE   │  trajectoryStateUpdate
FROM_HARNESS │  TELEMETRY                 │  usageMetadata (tokens)
```

### 3.1 Conversation Initialization
- `initializeConversationResponse`: Emitted by `localharness` upon connection. Delivers the primary session identifier (`cascadeId`).

### 3.2 Reasoning & Step Streaming
- `stepUpdate`: Emitted iteratively during model generation.
  - `trajectoryId`: Unique identifier for the execution thread (distinguishing the root agent from spawned subagents).
  - `stepIndex`: Zero-based step counter within the trajectory.
  - `source` & `target`: Identifies message origin (`SOURCE_MODEL`, `SOURCE_USER`, `TARGET_ENVIRONMENT`).
  - `textDelta` & `thinkingDelta`: Incremental streaming chunks for model reasoning and final response text.
  - `state`: Trajectory lifecycle state (`STATE_ACTIVE`, `STATE_DONE`, `STATE_ERROR`).

### 3.3 Lifecycle Hooks & Security Policy
- `callHookRequest` (`PreTool`): Emitted by the harness before executing any tool. Contains `requestId`, `toolName`, and serialized `argumentsJson`.
- `callHookResponse`: Sent by the SDK back to the harness with the policy decision:
  - `decision`: `"ALLOW"` or `"DENY"`.
  - `reason`: Optional explanation string for policy denials.

### 3.4 Tool Actions & Execution
- In-process tools and MCP servers receive a `toolCall` frame containing `{ "id": "...", "name": "...", "argumentsJson": "..." }`.
- Upon completion, the SDK returns a `toolResponse` frame containing `{ "id": "...", "responseJson": "...", "errorMessage": "..." }`.

---

## 4. Decoding, Correlation & Normalization

Raw wire messages from `localharness` arrive as decoupled streaming events. `agy_watch` normalizes and correlates these events into a cohesive execution graph.

### 4.1 Correlating Pre-Tool Hooks with Tool Calls
In the streaming protocol, `stepUpdate` frames often deliver lightweight action stubs (e.g. `invokeSubagent: {}`), while the complete parameters (worker prompts, role titles, full target file paths) were transmitted in the preceding `callHookRequest`.

`SessionWatcher` maintains a pending pre-tool argument buffer (`pending_pretool_args`). When the subsequent `TOOL_CALL` or `stepUpdate` action arrives, the parameters are merged to reconstruct the complete invocation context.

### 4.2 Subagent Hierarchy & Two-Way Hook Correlation
When an agent calls `invoke_subagent`, `localharness` instantiates child trajectories (e.g. `3ce168f3`, `cdc5e41e`):
1. **Trajectory Disambiguation**: The first trajectory in a session is registered as the canonical Root Agent (`is_main = True`). Any event with a different `trajectoryId` is categorized as a Subagent (`is_main = False`).
2. **Hook Trajectory Resolution**: `callHookRequest` and `callHookResponse` frames do not carry a top-level `trajectoryId` in their protobuf envelope. `agy_watch` resolves subagent ownership via two-way correlation:
   - **Inbound Tracking**: When `callHookRequest` arrives during an active subagent turn, its `requestId` is mapped to the active subagent trajectory.
   - **Outbound Inheritance**: When the SDK sends `callHookResponse`, it looks up `requestId` to inherit the subagent trajectory ID.
   - **Target Back-Linking**: When a subagent executes a tool (`editFile`, `createFile`, `runCommand`), `SessionWatcher` inspects preceding unmatched hook events. If the target arguments (e.g. file path or tool name family) match, the hook events are retroactively attached to that subagent's execution branch.

### 4.3 Action Key Normalization
Antigravity tool definitions and wire protobuf actions use slightly different schemas depending on whether an action is evaluated at the SDK hook level or recorded in the harness trajectory:
- `create_file` / `write_to_file` $\longleftrightarrow$ `createFile` / `editFile` (with `Overwrite: true`).
- `run_command` $\longleftrightarrow$ `runCommand`.
- `ask_question` $\longleftrightarrow$ `questionsRequest`.

`agy_watch` normalizes these into unified tool families during event dispatching and artifact extraction.

### 4.4 Error & Interception Classification
`SessionWatcher` differentiates three distinct failure modes:
1. **Security / Policy Denials**: `POLICY_DECISION` with `decision: "DENY"`. Surfaces the policy rule and rejection reason.
2. **Tool Execution Exceptions**: `toolResponse` with `errorMessage`. Captures stderr or exception tracebacks from local tool runners.
3. **Model Step Errors**: `stepUpdate` with `state: "STATE_ERROR"`.

---

## 5. Storage, Blob Offloading & Real-Time Streaming

```text
<workspace>/.trajectories/
├── wire_tap.db
│   ├── wire_events       (Immutable append-only frame log)
│   └── session_meta      (Aggregated token & status summary)
└── blobs/
    └── 4f/
        └── 4f7a18b9c2...png  (CAS SHA-256 binary storage for payloads >64 KB)
```

### 5.1 Append-Only SQLite Architecture
- **WAL Mode**: Databases use `PRAGMA journal_mode=WAL;` and `PRAGMA synchronous=NORMAL;` to ensure that active agent writes never block concurrent CLI or TUI reader processes.
- **`wire_events` Table**: An immutable event ledger indexing raw payloads with `seq_num`, `timestamp`, `direction`, `message_type`, `trajectory_id`, `step_index`, and `is_main`.
- **`session_meta` Table**: Atomically maintained summary record containing token counts (`prompt`, `candidates`, `thoughts`, `cached`), subagent counts, step counts, and session status.

### 5.2 Content-Addressable Storage (CAS `BlobStore`)
When models emit high-resolution images, video frames, or large file outputs, inline database storage would cause database bloat and degrade query performance. Payloads exceeding `threshold_bytes` (64 KB) are hashed with SHA-256 and offloaded to disk under `.trajectories/blobs/<sha256[:2]>/<sha256>.<ext>`, replaced in the database with a lightweight reference pointer:

```json
{
  "_blob_ref": "4f7a18b9c2...",
  "size_bytes": 1048576,
  "mime_type": "image/png",
  "filename": "generated_image.png",
  "file_path": "/path/to/.trajectories/blobs/4f/4f7a18b9c2.png",
  "preview": "data:image/png;base64,iVBORw0KGgoAAA... [OFFLOADED TO BLOB STORAGE]"
}
```

### 5.3 Incremental Streaming Cursors
`SessionWatcher` streams events by tracking `last_event_id`:
```sql
SELECT id, seq_num, timestamp, direction, message_type, trajectory_id, step_index, is_main, payload_json
FROM wire_events WHERE id > ? ORDER BY id ASC;
```
This enables zero-polling lag and instantaneous updates as new frames are written by the agent.

### 5.4 Multimodal Shared Brain Discovery
When tools write media or markdown artifacts, files may reside in the local workspace directory or in shared Antigravity brain storage. `extract_event_artifacts()` dynamically resolves and indexes files across:
1. `<workspace>/` and `<workspace>/.trajectories/blobs/`.
2. All valid `~/.gemini/<app>/brain/<session_id>/` sub-directories (dynamically discovered via `get_all_gemini_brain_dirs()`).
3. `~/.antigravity/brain/<session_id>/`.

---

## 6. Engine 2: Antigravity App Sessions & Brain Transcript Streaming

For native **Antigravity Agent** and **Antigravity CLI** sessions, communication is not routed through local loopback WebSocket sockets. Instead, trajectories are persisted as append-only JSONL transcripts across all valid `~/.gemini/<app>/brain/` directories:

```text
~/.gemini/<app>/brain/<session_id>/
├── .system_generated/logs/
│   ├── transcript.jsonl       (Token-efficient stream with truncated bulk texts)
│   └── transcript_full.jsonl  (Complete untruncated conversation history)
├── .system_generated/tasks/   (Background task execution logs)
└── <artifacts>                (Generated markdown documents, reports, and code)
```

### 6.1 `mtime`-Based Machine-Wide Discovery Cache
Scanning hundreds of workspace directories on every tick causes high CPU overhead. `_brain_discovery_cache` tracks directory `mtime` stamps, skipping unchanged sessions in $< 2.1\text{ ms}$ (a 55x performance improvement).

### 6.2 2-Step Tool Transaction Merging
Gemini Brain transcripts represent tool executions in two distinct asynchronous steps:
1. **Step $N$ (`PLANNER_RESPONSE`)**: Model intent containing tool invocation requests (`tool_calls: [{"name": "...", "args": {...}}]`) and reasoning thoughts.
2. **Step $N+1$ (Tool Return Output)**: Harness execution output (`LIST_DIRECTORY`, `VIEW_FILE`, `RUN_COMMAND`, `GREP_SEARCH`, `CODE_ACTION`, etc.) containing the tool's raw output.

`BrainTranscriptWatcher` correlates these into a **single unified `TOOL_CALL` event**:
- Merges tool name, arguments, execution diffs, and return values into a single DOM node.
- Eliminates phantom `THINKING...` nodes and empty parameter blocks.
- Emits in-flight `STATE_RUNNING` while a tool execution is underway at EOF.

---

## 7. Dynamic Lazy Pagination & Session Selection Memory

To render sessions with 5,000+ turns at 60 FPS without memory or DOM lag, `agy_watch` implements interactive windowed pagination:

### 7.1 Sliding Window Architecture (`PAGE_SIZE = 150`)
- **Initial Load**: Only the most recent 150 steps are inserted into the Textual DOM tree upon opening a session, rendering instantly in $< 5\text{ ms}$.
- **Top Pagination Node**: When a session exceeds 150 steps, an interactive node appears at the top of the tree:
  `🔼 [bold cyan]▲ Load earlier 150 steps (150/3,268 showing) - Press 'u' or Click[/bold cyan]`
- **On-Demand Expansion**: Pressing `u` (or clicking the node) expands the window by $+150$ steps. Pressing `U` (`Shift+U`) expands to load all steps.

### 7.2 Automatic Initial Focus & Per-Session Selection Memory
- **First Open**: The cursor and inspector automatically focus on the **most recent event** (bottom-most node).
- **Persistent Selection Memory**: User navigation saves `(trajectory_id, step_index, step_type)` in `session_selected_keys`. Switching across sessions (Session A $\to$ Session B $\to$ Session A) immediately restores the exact event previously selected in Session A.

---

## 8. Resilience & Edge Cases Matrix

| Protocol Scenario | Root Cause on Wire | How `agy_watch` Handles It |
| :--- | :--- | :--- |
| **Missing `trajectoryId` on Hook Frames** | Protobuf `callHookRequest` lacks top-level trajectory ID. | Correlates `requestId` with the active subagent turn and links matching target arguments back to the subagent branch. |
| **Action Key Divergence** | SDK calls `create_file`, but harness emits `editFile`. | Normalizes file mutation tool families to correctly match pre-tool approvals with tool execution. |
| **Two-Step Tool Asynchrony (Brain)** | Brain transcripts split model intent and tool stdout into separate steps. | Merges `PLANNER_RESPONSE` and subsequent tool outputs into unified `TOOL_CALL` nodes with in-flight `STATE_RUNNING`. |
| **Agent Process Termination (SIGKILL)** | Agent dies without sending a terminal `stepUpdate`. | `GlobalRegistry` verifies active process existence via `os.kill(pid, 0)` and marks stale sessions as `⚪ IDLE`. |
| **High-Frequency Streaming Token Deltas** | Models emit hundreds of partial text tokens per second. | Deduplicates streaming deltas in-memory by `(trajectory_id, step_index, step_type)`, updating states in-place. |
| **String-Formatted Token Counts** | Telemetry delivers numeric strings in `usageMetadata`. | Safely coerces token fields to native integers with fallback defaults. |
| **Concurrent Multi-Agent Execution** | Multiple agents running concurrently across different directories. | Namespaces databases per workspace and indexes sessions globally in `~/.antigravity/samples/agy_watch/registry.db`. |
| **Large Multimodal Binary Payloads** | Base64 image/video data exceeding database limits. | Offloads payloads >64 KB to content-addressable SHA-256 disk storage. |
| **Massive 5,000+ Turn Sessions** | Loading thousands of DOM nodes causes UI stutter. | Windowed lazy pagination (`PAGE_SIZE = 150`) with interactive on-demand expansion and persistent selection memory. |
