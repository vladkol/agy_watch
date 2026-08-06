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

"""01: Minimal Antigravity SDK Agent.

Demonstrates:
- Transparent wire-tapping with zero configuration
- Real-time token usage streaming and reasoning trace capture
- Live status detection in the agy_watch dashboard
"""

import os
import asyncio
from google.antigravity import Agent, LocalAgentConfig


async def main() -> None:
    workspace_dir = os.path.abspath("./.local/tasks/minimal_chat")
    os.makedirs(workspace_dir, exist_ok=True)

    config = LocalAgentConfig(
        workspaces=[workspace_dir],
        app_data_dir=workspace_dir,
        save_dir=os.path.join(workspace_dir, ".trajectories"),
    )

    async with Agent(config) as agent:
        prompt = "Explain in 2 sentences why observing agent reasoning helps software engineers."
        print(f"User: {prompt}\n")

        response = await agent.chat(prompt)
        text = await response.text()
        print(f"Agent: {text}\n")


if __name__ == "__main__":
    asyncio.run(main())
