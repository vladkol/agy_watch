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

"""Example demonstrating Custom Python tools and external MCP Server tools."""

import asyncio
import os
import sys
from google.antigravity import Agent, LocalAgentConfig, types
from google.antigravity.hooks import policy


# -----------------------------------------------------------------------------
# 1. Custom Python-Based Tool
# -----------------------------------------------------------------------------
def calculate_travel_carbon_footprint(distance_km: float, transport_mode: str) -> dict:
    """Calculates estimated CO2 emissions in kg for a given travel distance and mode.

    Args:
        distance_km: The travel distance in kilometers.
        transport_mode: The mode of transportation ('train', 'flight', 'car', 'electric_vehicle').

    Returns:
        A dictionary containing the emissions calculation and comparative analysis.
    """
    emission_factors = {
        "train": 0.041,
        "electric_vehicle": 0.053,
        "car": 0.171,
        "flight": 0.255,
    }
    mode = transport_mode.lower()
    factor = emission_factors.get(mode, 0.15)
    emissions = round(distance_km * factor, 2)

    return {
        "trip": {
            "distance_km": distance_km,
            "transport_mode": mode,
        },
        "carbon_footprint": {
            "emissions_kg_co2": emissions,
            "emission_factor": factor,
            "tree_months_to_offset": round(emissions / 1.83, 1),
        },
        "status": "success",
    }


async def main() -> None:
    print("=" * 60)
    print("  Custom Python Tool & MCP Server Demonstration")
    print("=" * 60)

    # Configure custom tool & public MCP server
    mcp_servers = [
        types.McpStdioServer(
            name="everything",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-everything"],
        )
    ]

    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "vladkol-wt-test-rnr-03")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")

    config = LocalAgentConfig(
        system_instructions=(
            "You are an eco-travel consultant assistant with access to "
            "a custom carbon footprint calculation tool and external MCP tools. "
            "When asked to calculate emissions, call the calculate_travel_carbon_footprint tool. "
            "When asked to echo or test MCP, use the 'echo' tool from the everything server."
        ),
        tools=[calculate_travel_carbon_footprint],
        mcp_servers=mcp_servers,
        policies=[policy.allow_all()],
        project=project,
        location=location,
        vertex=True,
    )

    prompt = (
        "1. Calculate the carbon footprint for a 650 km trip from Paris to Amsterdam by train.\n"
        "2. Then call the MCP echo tool with message 'Trip planning verified'."
    )
    print(f"\n>>> User Prompt:\n{prompt}\n")

    async with Agent(config) as agent:
        response = await agent.chat(prompt)
        async for chunk in response.chunks:
            if isinstance(chunk, types.Text):
                sys.stdout.write(chunk.text)
                sys.stdout.flush()
            elif isinstance(chunk, types.ToolCall):
                print(f"\n  [Tool Invoked] {chunk.name}({chunk.args})")
        print()


if __name__ == "__main__":
    asyncio.run(main())
