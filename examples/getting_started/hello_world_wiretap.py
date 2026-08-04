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

"""Simple Hello World example for Google Antigravity SDK with agy_watch wire-tapping.

This example demonstrates how to:
1. Wire-tap your agent with a single line: `install_wire_tap(workspace_dir)`
2. Run standard Antigravity SDK Agent interactions.
3. Observe the session live in real-time by running `agy_watch` in your terminal!

To run:
  python examples/getting_started/hello_world_wiretap.py
  (or: uv run python examples/getting_started/hello_world_wiretap.py)

To observe in another terminal window:
  agy_watch
"""

import os
import asyncio
from google.antigravity import Agent, LocalAgentConfig
from agy_watch import install_wire_tap


async def main() -> None:
    workspace_dir = os.path.abspath("./.local/tasks/hello_world")
    os.makedirs(workspace_dir, exist_ok=True)

    # 1. Enable transparent wire-tapping for this agent session
    install_wire_tap(workspace_dir)

    # 2. Configure and run the agent
    config = LocalAgentConfig(
        workspaces=[workspace_dir],
        app_data_dir=workspace_dir,
        save_dir=os.path.join(workspace_dir, ".trajectories"),
    )

    async with Agent(config) as my_agent:
        prompt = "Say 'Hello World from Antigravity SDK and agy_watch!'"
        print(f"  User: {prompt}")

        response = await my_agent.chat(prompt)

        # Await the full aggregated text response
        response_text = await response.text()
        print(f"  Agent: {response_text}")


if __name__ == "__main__":
    asyncio.run(main())
