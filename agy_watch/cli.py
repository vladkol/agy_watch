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

"""Command-Line Interface (CLI) for agy_watch.

Provides both an interactive Textual TUI dashboard and non-interactive subcommands
(list, tail, inspect, attach) with JSON and YAML output support.
"""

import os
import sys
import json
import time
import click
import yaml
from typing import Any, Dict, List, Optional

from agy_watch.registry import get_global_registry, GlobalRegistry
from agy_watch.watcher import SessionWatcher
from agy_watch.tui import AgyWatchApp


def format_output(data: Any, fmt: str = "text") -> str:
    """Formats structured data as JSON, YAML, or plain text."""
    if fmt == "json":
        return json.dumps(data, indent=2, default=str)
    elif fmt == "yaml":
        return yaml.dump(data, sort_keys=False, default_flow_style=False)
    return str(data)


@click.group(invoke_without_command=True)
@click.option("--attach", "-a", "attach_id", type=str, default=None, help="Directly attach TUI to a specific session ID.")
@click.option("--registry-db", type=click.Path(dir_okay=False), default=None, help="Custom registry.db path.")
@click.version_option(version="0.1.0", prog_name="agy_watch")
@click.pass_context
def main(ctx: click.Context, attach_id: Optional[str], registry_db: Optional[str]) -> None:
    """agy_watch: Real-Time Multi-Agent Observability Console and Wire-Tap SDK."""
    if ctx.invoked_subcommand is None:
        # Default action: Launch interactive 3-pane Textual TUI
        app = AgyWatchApp(initial_session_id=attach_id, registry_db=registry_db)
        app.run()


@main.command("list")
@click.option("--status", type=click.Choice(["live", "idle", "all"], case_sensitive=False), default="all", help="Filter sessions by status.")
@click.option("--limit", "-n", type=int, default=50, help="Maximum number of sessions to display.")
@click.option("--json", "out_json", is_flag=True, help="Output as JSON.")
@click.option("--yaml", "out_yaml", is_flag=True, help="Output as YAML.")
def list_cmd(status: str, limit: int, out_json: bool, out_yaml: bool) -> None:
    """List all registered agent sessions on this machine."""
    registry = get_global_registry()
    sessions = registry.list_sessions(limit=limit)

    if status == "live":
        sessions = [s for s in sessions if s.get("is_live")]
    elif status == "idle":
        sessions = [s for s in sessions if not s.get("is_live")]

    if out_json:
        click.echo(format_output(sessions, "json"))
        return
    elif out_yaml:
        click.echo(format_output(sessions, "yaml"))
        return

    if not sessions:
        click.echo(click.style("No agent sessions found in registry (~/.antigravity/samples/agy_watch/registry.db).", fg="yellow"))
        return

    click.echo(click.style(f"{'STATUS':<8} {'SESSION ID':<18} {'WORKERS':<8} {'TOKENS':<10} {'TITLE':<35} {'UPDATED'}", bold=True))
    click.echo("─" * 95)

    for s in sessions:
        is_live = s.get("is_live", False)
        status_tag = click.style("● LIVE", fg="green", bold=True) if is_live else click.style("○ IDLE", fg="bright_black")
        sid = s["session_id"][:16]
        workers = f"{s.get('subagent_count', 0)} subs"
        tokens = f"{s.get('total_tokens', 0) / 1000:.1f}k tok"
        title = (s.get("title") or "Session")[:33]
        updated = time.strftime("%H:%M:%S", time.localtime(s.get("updated_at", 0)))

        click.echo(f"{status_tag:<17} {sid:<18} {workers:<8} {tokens:<10} {title:<35} {updated}")


@main.command("attach")
@click.argument("session_id", required=True)
def attach_cmd(session_id: str) -> None:
    """Launch interactive TUI attached directly to a specific session."""
    app = AgyWatchApp(initial_session_id=session_id)
    app.run()


@main.command("tail")
@click.argument("session_id", required=True)
@click.option("--follow", "-f", is_flag=True, help="Follow events in real time as they arrive.")
@click.option("--poll-interval", type=float, default=0.2, help="Polling interval in seconds when following.")
@click.option("--json", "out_json", is_flag=True, help="Stream events as JSON lines.")
@click.option("--yaml", "out_yaml", is_flag=True, help="Output as YAML.")
def tail_cmd(session_id: str, follow: bool, poll_interval: float, out_json: bool, out_yaml: bool) -> None:
    """Stream live events from an active session's wire_tap.db."""
    registry = get_global_registry()
    sess = registry.get_session(session_id)

    if not sess:
        # Check if direct DB path was passed
        if os.path.exists(session_id) and session_id.endswith(".db"):
            db_path = session_id
        else:
            click.echo(click.style(f"Error: Session '{session_id}' not found in registry.", fg="red"), err=True)
            sys.exit(1)
    else:
        db_path = sess["db_path"]

    watcher = SessionWatcher(db_path)

    try:
        while True:
            sess_info, events = watcher.poll()
            for ev in events:
                if out_json:
                    click.echo(json.dumps(ev, default=str))
                elif out_yaml:
                    click.echo(yaml.dump([ev], sort_keys=False))
                else:
                    ts = time.strftime("%H:%M:%S", time.localtime(ev.get("timestamp", 0)))
                    actor = click.style("ROOT AGENT", fg="magenta", bold=True) if ev.get("is_main") else click.style(f"SUBAGENT ({ev.get('subagent_id', '')[:8]})", fg="cyan", bold=True)
                    direction_arrow = ">>" if ev.get("direction") == "TO_HARNESS" else "<<"
                    stype = click.style(f"{ev.get('step_type'):<16}", fg="yellow", bold=True)

                    detail = ""
                    if ev.get("prompt"):
                        detail = click.style(f"{ev['prompt'][:60]}...", fg="green")
                    elif ev.get("tool_name"):
                        detail = click.style(f"TOOL: {ev['tool_name']}", fg="bright_yellow")
                    elif ev.get("text"):
                        detail = f"{ev['text'][:60]}..."
                    elif ev.get("thinking"):
                        detail = click.style(f"THINKING: {ev['thinking'][:50]}...", dim=True)
                    else:
                        detail = ev.get("message_type")

                    click.echo(f"[{ts}] {direction_arrow} {actor:<25} {stype} | {detail}")

            if not follow:
                break
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        pass


@main.command("inspect")
@click.argument("session_id", required=True)
@click.option("--step", "-s", type=int, default=None, help="Inspect a specific step index.")
@click.option("--json", "out_json", is_flag=True, help="Output full payload as JSON.")
@click.option("--yaml", "out_yaml", is_flag=True, help="Output full payload as YAML.")
def inspect_cmd(session_id: str, step: Optional[int], out_json: bool, out_yaml: bool) -> None:
    """Inspect full details and payloads of a session or specific execution step."""
    registry = get_global_registry()
    sess = registry.get_session(session_id)

    if not sess:
        if os.path.exists(session_id) and session_id.endswith(".db"):
            db_path = session_id
        else:
            click.echo(click.style(f"Error: Session '{session_id}' not found.", fg="red"), err=True)
            sys.exit(1)
    else:
        db_path = sess["db_path"]

    watcher = SessionWatcher(db_path)
    sess_info, events = watcher.poll()

    if step is not None:
        events = [e for e in events if e.get("step_index") == step or e.get("id") == step]

    output_data = {
        "session": sess_info,
        "events": events,
    }

    if out_json:
        click.echo(format_output(output_data, "json"))
    elif out_yaml:
        click.echo(format_output(output_data, "yaml"))
    else:
        click.echo(click.style(f"=== Session Inspection: {sess_info.get('session_id')} ===", bold=True, fg="cyan"))
        click.echo(f"Title:        {sess_info.get('title')}")
        click.echo(f"Status:       {sess_info.get('status')}")
        click.echo(f"Total Tokens: {sess_info.get('total_tokens'):,} (Prompt: {sess_info.get('prompt_tokens'):,}, Candidates: {sess_info.get('candidates_tokens'):,})")
        click.echo(f"Subagents:    {sess_info.get('subagent_count')}")
        click.echo(f"Total Events: {len(events)}")
        click.echo("─" * 80)
        for ev in events:
            ts = time.strftime("%H:%M:%S", time.localtime(ev.get("timestamp", 0)))
            actor = "ROOT AGENT" if ev.get("is_main") else f"SUBAGENT ({ev.get('subagent_id')})"
            click.echo(f"[{ts}] Step {ev.get('step_index') or ev.get('id')} | {actor} | {ev.get('step_type')}")
            if ev.get("prompt"):
                click.echo(f"  Prompt: {ev['prompt']}")
            if ev.get("tool_name"):
                click.echo(f"  Tool: {ev['tool_name']} (args: {json.dumps(ev.get('tool_args') or {})})")
            if ev.get("text"):
                click.echo(f"  Response: {ev['text']}")
            if ev.get("artifacts"):
                for art in ev["artifacts"]:
                    click.echo(f"  Artifact: {art['filename']} ({art['type']}) -> {art['path']}")


if __name__ == "__main__":
    main()
