"""Image Generation Agent Example with Antigravity SDK and agy_watch wire-tapping.

Demonstrates an agent calling the `generate_image` tool, with agy_watch automatically
capturing the generated visual asset on disk and previewing it inside the TUI Artifacts tab!

To run:
  python examples/image_generation/generate_image_agent.py
  (or: uv run python examples/image_generation/generate_image_agent.py)

To observe in another terminal window:
  agy_watch
"""

import os
import asyncio
from google.antigravity import Agent, LocalAgentConfig, CapabilitiesConfig, types
from google.antigravity.hooks import policy
from agy_watch import install_wire_tap


async def main() -> None:
    workspace_dir = os.path.abspath("./.local/tasks/image_gen_demo")
    os.makedirs(workspace_dir, exist_ok=True)

    # 1. Enable transparent wire-tapping
    install_wire_tap(workspace_dir)

    # 2. Configure agent with full tool capabilities (including generate_image)
    config = LocalAgentConfig(
        workspaces=[workspace_dir],
        app_data_dir=workspace_dir,
        save_dir=os.path.join(workspace_dir, ".trajectories"),
        capabilities=CapabilitiesConfig(
            enabled_tools=types.BuiltinTools.all_tools()
        ),
        system_instructions="You are a creative visual AI assistant. When asked to create an image, always use the generate_image tool.",
        policies=[policy.allow_all()],
    )

    prompt = "Please create a vibrant, beautiful picture of a cute fluffy puppy playing in a field of sunflowers under golden hour sunshine."
    print(f"User Request: {prompt}")

    async with Agent(config) as agent:
        response = await agent.chat(prompt)
        response_text = await response.text()
        print(f"\nAgent Response:\n{response_text}")
        print("\n▶ Tip: Launch `agy_watch` in your terminal to view the generated image in the Artifacts tab!")


if __name__ == "__main__":
    asyncio.run(main())
