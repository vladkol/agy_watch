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

"""Example demonstrating error tracking, tool exceptions, and security policy blocks in Antigravity SDK.

This script executes:
1. Tool Exception: A custom tool (`failing_database_query`) raises an unhandled database exception.
2. Security Policy Block: A security policy intercepts and denies an unauthorized command.
"""

import asyncio
import os
import sys

from google.antigravity import Agent, LocalAgentConfig
from google.antigravity.hooks import policy


# 1. Custom tool that raises an exception
async def failing_database_query(query: str) -> str:
    """Executes a SQL query against the primary production database.

    Args:
        query: The SQL query string.
    """
    print(f"  [Tool] Executing SQL: {query}")
    raise ConnectionRefusedError("FATAL: database 'prod_analytics' at 10.0.4.12:5432 connection refused (OOMKilled)")


# 2. Custom tool that attempts to delete system files
async def purge_cache_files(target_dir: str) -> str:
    """Purges cached system files in a directory.

    Args:
        target_dir: The directory path to purge.
    """
    return f"Purged files in {target_dir}"


async def main() -> None:
    print("🚀 Starting Agent Error & Security Policy Observability Run...\n")

    # Configure agent with failing tool, and strict security policy
    config = LocalAgentConfig(
        tools=[failing_database_query, purge_cache_files],
        policies=[
            # Block any destructive purge operations
            policy.deny(
                "purge_cache_files",
                name="block-destructive-purge",
            ),
        ],
    )

    async with Agent(config) as agent:
        # --- Scenario 1: Tool Exception ---
        print("=== 1. Triggering Tool Exception ===")
        p1 = "Call failing_database_query with 'SELECT * FROM users WHERE active = 1;'"
        print(f"User: {p1}")
        resp1 = await agent.chat(p1)
        async for chunk in resp1:
            sys.stdout.write(chunk)
            sys.stdout.flush()
        print("\n")

        # --- Scenario 2: Policy Block ---
        print("=== 2. Triggering Security Policy Block ===")
        p2 = "Call purge_cache_files with target_dir='/var/log/system'"
        print(f"User: {p2}")
        resp2 = await agent.chat(p2)
        async for chunk in resp2:
            sys.stdout.write(chunk)
            sys.stdout.flush()
        print("\n")

    print("✓ Agent Error & Policy Demonstration Complete!")


if __name__ == "__main__":
    asyncio.run(main())
