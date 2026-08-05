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

"""02: Multimodal Image Generation Agent.

Demonstrates:
- Tool call argument correlation
- Automatic detection and indexing of generated image artifacts
- Live image inspection in agy_watch Artifacts tab
"""

import os
import asyncio
from google.antigravity import Agent, LocalAgentConfig, CapabilitiesConfig, types
from google.antigravity.hooks import policy


async def main() -> None:
    workspace_dir = os.path.abspath("./.local/tasks/02_multimodal_tools")
    os.makedirs(workspace_dir, exist_ok=True)

    config = LocalAgentConfig(
        workspaces=[workspace_dir],
        app_data_dir=workspace_dir,
        save_dir=os.path.join(workspace_dir, ".trajectories"),
        capabilities=CapabilitiesConfig(
            enabled_tools=types.BuiltinTools.all_tools()
        ),
        system_instructions="You are a visual assistant. When asked to create an image, always use the generate_image tool.",
        policies=[policy.allow_all()],
    )

    prompt = "Create a vibrant digital art image of an astronaut observing a glowing nebula from a spaceship observatory."
    print(f"User: {prompt}\n")

    async with Agent(config) as agent:
        response = await agent.chat(prompt)
        text = await response.text()
        print(f"Agent: {text}\n")


if __name__ == "__main__":
    asyncio.run(main())
