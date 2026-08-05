# agy_watch Examples & Demonstrations

All examples in this directory are written using **100% pure Google Antigravity SDK code** with **zero modifications, zero extra dependencies, and zero `agy_watch` imports**.

---

## How to Run & Observe Examples

### Option 1: In-Venv Auto-Observability (Recommended)
Enable auto-observability on your virtual environment once:
```bash
agy_watch watch
```

Then run any example with standard Python:
```bash
python examples/01_minimal_chat.py
```

### Option 2: On-Demand CLI Runner
Run any example directly through the proxy:
```bash
agy_watch run examples/01_minimal_chat.py
```

### Open the Dashboard
While the agent is executing (or anytime after), launch the 3-pane console in another terminal:
```bash
agy_watch
```

---

## Example Catalog

### 1. Minimal Chat Agent ([01_minimal_chat.py](examples/01_minimal_chat.py))
* **Description**: A basic 5-line agent performing a conversational prompt.
* **What to Observe in `agy_watch`**:
  * **Machine-Wide Discovery**: Left pane immediately displays the session with a green `● LIVE` status and real-time PID tracking.
  * **Stream Deduplication**: Token deltas are smoothly assembled into clean `USER_INPUT`, `THINKING`, and `TEXT_RESPONSE` step nodes.
  * **Token Telemetry**: Right pane displays accurate token metrics (prompt tokens, candidate tokens, reasoning tokens, cached tokens).

```bash
python examples/01_minimal_chat.py
```

---

### 2. Multimodal Image Generation ([02_multimodal_tools.py](examples/02_multimodal_tools.py))
* **Description**: An agent configured with tool capabilities that calls `generate_image` to create digital artwork.
* **What to Observe in `agy_watch`**:
  * **Tool Execution Inspection**: Middle pane displays `TOOL: generate_image` with formatted arguments JSON.
  * **Dynamic Artifacts Tab**: Right pane automatically enables the `[Artifacts & Files]` tab when generated `.png` files are detected.
  * **In-Terminal Image Preview**: Select the image artifact to view a Rich ANSI thumbnail; press `f` for full-screen inspection or `o` to open in your OS default image viewer.

```bash
python examples/02_multimodal_tools.py
```

---

### 3. Multi-Subagent Tree Orchestration ([03_subagents_tree.py](examples/03_subagents_tree.py))
* **Description**: A coordinator agent that concurrently spawns multiple specialized subagents (`Math Specialist` and `Poet`) to execute sub-tasks.
* **What to Observe in `agy_watch`**:
  * **Hierarchical Tree View**: Middle pane renders an expandable subagent tree branching sub-workers under the root agent.
  * **PreTool Hook Correlation**: `CALL_HOOK_PRETOOL` frames are matched to tool calls, exposing worker prompts and role configurations.
  * **Subagent Filtering**: Press numeric keys (`1`, `2`, `3`) to isolate specific subagent lanes, or press `0` to view the unified execution graph.

```bash
python examples/03_subagents_tree.py
```
