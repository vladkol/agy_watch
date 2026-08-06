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

"""03: Multi-Subagent Tree Orchestration.

Demonstrates:
- Hierarchical recursive subagent tree visualization
- PreTool hook correlation for worker prompts and role definitions
- Multi-lane concurrent subagent filtering
"""

import os
import asyncio
from google.antigravity import Agent, LocalAgentConfig, CapabilitiesConfig, types
from google.antigravity.hooks import policy


async def main() -> None:
    workspace_dir = os.path.abspath("./.local/tasks/subagents_tree")
    os.makedirs(workspace_dir, exist_ok=True)

    config = LocalAgentConfig(
        workspaces=[workspace_dir],
        app_data_dir=workspace_dir,
        save_dir=os.path.join(workspace_dir, ".trajectories"),
        capabilities=CapabilitiesConfig(
            enabled_tools=types.BuiltinTools.all_tools()
        ),
        system_instructions="You are a coordinator agent. When given a task with multiple steps, delegate them to specialized subagents using invoke_subagent.",
        policies=[policy.allow_all()],
    )

    prompt = (
        "Please spawn 2 subagents concurrently: "
        "1. Subagent 'Math Specialist': calculate the sum of prime numbers under 20. "
        "2. Subagent 'Poet': write a short 2-line haiku about mathematics. "
        "Summarize their outputs when they report back."
    )
    print(f"User: {prompt}\n")

    async with Agent(config) as agent:
        response = await agent.chat(prompt)
        text = await response.text()
        print(f"Agent: {text}\n")


if __name__ == "__main__":
    asyncio.run(main())
