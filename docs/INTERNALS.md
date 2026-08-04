# agy_watch: System Architecture & Internals

> [!IMPORTANT]
> **Disclaimer**: `agy_watch` is a personal developer debugging and observability tool and is **NOT an official Google product or framework**. It is designed for testing, inspecting, and profiling autonomous agents built with [`google-antigravity==0.1.9`](https://pypi.org/project/google-antigravity/) and its bundled `localharness` binary.
The tool is built by Antigravity itself by observing Antigravity SDK and localharness protocol.
There is no guaranty for this tool to work prior or beyond this version.
If any changes are required, you are welcome to file an issue and/or make a Pull Request.

---

## 1. Architectural Problem & Motivation

The [Google Antigravity Python SDK](https://github.com/google/antigravity) is designed for high-performance agent execution. It pairs a clean Python interface with a specialized Go daemon (`localharness`) running as a local subprocess. The Python SDK (`LocalConnectionStrategy`) coordinates with `localharness` over a private loopback WebSocket IPC channel (`ws://127.0.0.1:<ephemeral_port>`).

```text
┌─────────────────────────┐          Private Loopback WS IPC           ┌──────────────────────────┐
│ Google Antigravity SDK  │ ◄────────────────────────────────────────► │ localharness (Go Daemon) │
│ (Python Process)        │          ws://127.0.0.1:<port>             │ (Models & Execution)     │
└─────────────────────────┘                                            └──────────────────────────┘
```

### Why Wire-Tapping?

While this decoupled architecture makes the Antigravity SDK exceptionally fast and isolated, developers building complex, multi-agent hierarchies often need deeper visibility into the internal execution stream:

1. **Observing Sub-Agent Lifecycles**: When agents orchestrate concurrent subagents, inspecting their internal thought loops and individual tool calls in real time accelerates development and debugging.
2. **Recovering Correlated Tool Arguments**: In the raw streaming protocol, `stepUpdate` frames emit lightweight action stubs (e.g. `invokeSubagent: {}`), while the complete argument payloads (such as worker prompts and subagent role definitions) are transmitted in a preceding hook event (`CALL_HOOK_PRETOOL`). Correlating these events provides full visibility into tool dispatching.
3. **Stream Deduplication**: Streaming LLMs emit frequent token deltas (`textDelta`, `thinkingDelta`). Deduplicating these deltas into coherent logical steps creates clean, readable execution timelines.
4. **Managing Multimodal Artifacts**: Modern agents frequently produce images, videos, and large tool outputs. Offloading payloads exceeding 64 KB to content-addressable storage keeps database operations instantaneous and lightweight.

`agy_watch` solves these challenges by non-intrusively wrapping the WebSocket connection, indexing events in SQLite WAL mode, and presenting a rich terminal user interface.

---

## 2. End-to-End System Architecture

```text
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ AGENT PROCESS (Python)                                                                                 │
│                                                                                                        │
│   from agy_watch import install_wire_tap                                                               │
│   install_wire_tap("./workspace")                                                                      │
│          │                                                                                             │
│          ▼ Monkey-patches LocalConnectionStrategy._connect_websocket                                   │
│   ┌────────────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │ TappedWebSocket Wrapper                                                                        │   │
│   │                                                                                                │   │
│   │   send(data) ──► JSON Parse ──► [CAS BlobStore Offload] ──► WireTapDB.record_outbound()       │   │
│   │   recv()     ──► JSON Parse ──► [CAS BlobStore Offload] ──► WireTapDB.record_inbound()        │   │
│   └────────────────────────────────────────────────────────────────────────────────────────────────┘   │
│          │                                                                │                            │
│          ▼ (Writes WAL SQLite)                                            ▼ (Registers Session)        │
│   ┌──────────────────────────────────────────────┐                 ┌───────────────────────────────┐   │
│   │ Workspace DB:                                │                 │ Host-Wide Machine Registry:   │   │
│   │ <workspace>/.trajectories/wire_tap.db        │                 │ ~/.antigravity/samples/       │   │
│   │ ├── wire_events (Append-only event log)      │                 │ agy_watch/registry.db         │   │
│   │ ├── session_meta (Aggregated session stats)  │                 │ └── global_sessions           │   │
│   │ └── blobs/ (CAS SHA-256 binary storage)      │                 │     (PID, Status, Tokens)     │   │
│   └──────────────────────────────────────────────┘                 └───────────────────────────────┘   │
└───────────────────────────────────────────────────────────────────────────────────▲────────────────────┘
                                                                                    │
┌───────────────────────────────────────────────────────────────────────────────────┼────────────────────┐
│ OBSERVABILITY PROCESS (agy_watch CLI / TUI)                                       │                    │
│                                                                                   │                    │
│   ┌───────────────────────┐         ┌──────────────────────────────┐       ┌──────┴────────────────┐   │
│   │ GlobalRegistry Reader │ ◄────── │ SessionWatcher (WAL Cursor)  │ ◄──── │ AgyWatchApp (Textual) │   │
│   │ (Reads active PIDs)   │         │ (Incremental seq > last_seq) │       │ (3-Pane Dashboard)    │   │
│   └───────────────────────┘         └──────────────────────────────┘       └───────────────────────┘   │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Core Subsystems Deep Dive

### 3.1 Transparent WebSocket Interception ([wire_tap.py](../agy_watch/wire_tap.py))

The `install_wire_tap(save_dir=None)` function dynamically hooks `google.antigravity.connections.local.local_connection.LocalConnectionStrategy._connect_websocket`.

When the Python SDK establishes its connection to `localharness`, the original `WebSocketClientProtocol` is wrapped inside a [`TappedWebSocket`](../agy_watch/wire_tap.py):

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

### 3.2 Content-Addressable Storage (CAS `BlobStore`) ([wire_tap.py](../agy_watch/wire_tap.py))

To keep database queries fast and responsive, string or binary payloads exceeding `threshold_bytes` (default: 64 KB) are saved to disk under `<save_dir>/.trajectories/blobs/<sha256[:2]>/<sha256>.<ext>`, replaced with a reference pointer:

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

### 3.3 Zero-Lock SQLite WAL Architecture ([wire_tap.py](../agy_watch/wire_tap.py))

* **WAL Mode**: Databases are initialized with `PRAGMA journal_mode=WAL;` and `PRAGMA synchronous=NORMAL;`.
* **Reader Concurrency**: `SessionWatcher` opens connections in read-only mode with a 5.0-second busy timeout. Multiple CLI instances can tail the exact same session simultaneously without blocking the active agent writer.
* **Schema Design**:
  * `wire_events`: Immutable, append-only log of raw WebSocket frames (`seq_num`, `timestamp`, `direction`, `message_type`, `trajectory_id`, `step_index`, `is_main`, `payload_json`).
  * `session_meta`: Aggregated summary statistics updated atomically on every turn (`session_id`, `status`, `total_tokens`, `prompt_tokens`, `candidates_tokens`, `thoughts_tokens`, `cached_tokens`, `subagent_count`, `step_count`, `updated_at`).

### 3.4 Hook Correlation & Role Classification ([watcher.py](../agy_watch/watcher.py))

#### The Role of PreTool Hooks
When the agent executes tools:
1. `localharness` sends a `CALL_HOOK_PRETOOL` frame containing full tool arguments:
   ```json
   {
     "callHookRequest": {
       "name": "PreTool",
       "preToolArgs": {
         "toolName": "invoke_subagent",
         "argumentsJson": "{\"Subagents\": [{\"Prompt\": \"Write worker1.txt\", \"Role\": \"Worker 1\"}]}"
       }
     }
   }
   ```
2. The subsequent `STEP_UPDATE` frame contains the action descriptor `{"invokeSubagent": {}}`.
3. In child sub-agents, the initial instruction prompt arrives as `stepUpdate` with `source: SOURCE_USER` and `target: TARGET_MODEL`.

#### How `SessionWatcher` Correlates Events
* **PreTool Argument Buffering**: `SessionWatcher` buffers incoming `preToolArgs.argumentsJson` by tool name. When the matching `TOOL_CALL` action frame arrives, the buffered arguments are attached directly to the tool event.
* **Role Disambiguation**:
  * `source == "SOURCE_USER"` & `target == "TARGET_MODEL"` in subagents $\rightarrow$ Classified as **`SUBAGENT_PROMPT`** (*"SUBAGENT INSTRUCTION PROMPT"*).
  * `source == "SOURCE_MODEL"` $\rightarrow$ Classified as **`TEXT_RESPONSE`** / **`MODEL_REASONING`**.

---

## 4. TUI State Management & Stream Deduplication ([tui.py](../agy_watch/tui.py))

### 4.1 In-Place Step Deduplication
Streaming models emit token deltas (`textDelta`, `thinkingDelta`) across multiple WebSocket frames for the same step index. Rather than adding dozens of redundant tree nodes:

* `AgyWatchApp` maintains an internal dictionary `self.step_nodes: Dict[Tuple[str, int, str], TreeNode]` keyed by `(trajectory_id, step_index, step_type)`.
* When a streaming delta arrives for an existing `step_index`, the TUI updates the existing `TreeNode` label and payload **in-place**.
* A new tree node is only allocated when a new logical step index begins.

### 4.2 Recursive Tree Construction
* When `TOOL_CALL: invoke_subagent` is received, an expandable root node is mounted.
* When events from child `trajectory_id`s arrive (`is_main == False`), a dedicated subagent container branch (`Subagent (<id>) [Active]`) is mounted under the parent agent.
* Internal subagent tool calls (`write_to_file`, `run_command`, `view_file`) attach as children under that specific worker branch.
* When the subagent reaches `STATE_DONE`, its branch badge updates dynamically to `[Done]`.

### 4.3 Flicker-Free Session List Diffing
The session polling timer (1.5s interval) calculates a 5-tuple hash signature of all registered sessions:
$$\text{Signature} = \Big( (\text{sid}_i, \text{updated\_at}_i, \text{status}_i, \text{total\_tokens}_i, \text{step\_count}_i) \Big)_{i=1}^N$$
If the signature is identical to the previous tick, the `ListView` is not re-rendered, completely eliminating UI flicker and maintaining user cursor selection.

---

## 5. Edge Cases & Resilience Matrix

| Edge Case | Scenario | How `agy_watch` Handles It |
| :--- | :--- | :--- |
| **Agent Process Crash / SIGKILL** | Session was active when the process terminated. | `GlobalRegistry.list_sessions()` uses `os.kill(pid, 0)` to verify process existence. Inactive PIDs are updated to `○ IDLE`. |
| **Concurrent Agents on Same Machine** | Multiple agents running across different directories. | `GlobalRegistry` indexes sessions by unique `session_id` in `~/.antigravity/samples/agy_watch/registry.db`. |
| **Numeric Strings in Token Usage** | Raw payloads send strings like `"100"` in `usageMetadata`. | `_to_int()` parsing safely coerces all token counts to native integers. |
| **Read-Only SQLite Access** | Readers inspecting databases in shared environments. | Readers never issue write PRAGMAs; WAL mode is configured exclusively by the writer during initialization. |
| **Missing Workspace Directory** | Agent initialized without an explicit workspace path. | `install_wire_tap(save_dir=None)` defaults to a timestamped directory under `~/.antigravity/samples/agy_watch/workspaces/session_<timestamp>`. |
