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
import shutil
import subprocess
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
    """agy_watch: Antigravity SDK Observability Console."""
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


@main.command("watch")
@click.argument("env_path", required=False, type=click.Path(exists=True, file_okay=False, dir_okay=True))
def watch_cmd(env_path: Optional[str]) -> None:
    """Install transparent .pth auto-hook into a Python virtual environment."""
    site_packages = _resolve_site_packages(env_path)
    if not site_packages or not os.path.isdir(site_packages):
        click.echo(click.style(f"Error: Could not locate site-packages in '{env_path or sys.prefix}'.", fg="red"), err=True)
        sys.exit(1)

    pth_file = os.path.join(site_packages, "agy_watch_hook.pth")
    try:
        with open(pth_file, "w", encoding="utf-8") as f:
            f.write("import agy_watch.auto_hook\n")
    except Exception as e:
        click.echo(click.style(f"Error writing hook to {pth_file}: {e}", fg="red"), err=True)
        sys.exit(1)

    # Register in watched_envs.json
    envs = _load_watched_envs()
    norm_path = os.path.abspath(site_packages)
    if not any(e.get("site_packages") == norm_path for e in envs):
        envs.append({
            "site_packages": norm_path,
            "pth_file": pth_file,
            "created_at": time.time(),
        })
        _save_watched_envs(envs)

    click.echo(click.style("✓ Successfully installed agy_watch auto-hook!", fg="green", bold=True))
    click.echo(f"  Target:   {norm_path}")
    click.echo(f"  Hook:     {pth_file}")
    click.echo(click.style("\nAll Antigravity SDK agents in this environment will be automatically observed on run.", fg="cyan"))


@main.command("unwatch")
@click.argument("env_path", required=False, type=click.Path(exists=True, file_okay=False, dir_okay=True))
@click.option("--all", "unwatch_all", is_flag=True, help="Remove auto-hooks from all registered environments.")
def unwatch_cmd(env_path: Optional[str], unwatch_all: bool) -> None:
    """Remove agy_watch auto-hook from Python virtual environment(s)."""
    envs = _load_watched_envs()
    remaining = []

    if unwatch_all:
        for e in envs:
            pth = e.get("pth_file")
            if pth and os.path.exists(pth):
                try:
                    os.remove(pth)
                    click.echo(f"✓ Removed hook from {e.get('site_packages')}")
                except Exception as err:
                    click.echo(click.style(f"Warning: Failed to remove {pth}: {err}", fg="yellow"))
        _save_watched_envs([])
        click.echo(click.style("✓ Unwatched all registered environments.", fg="green"))
        return

    site_packages = _resolve_site_packages(env_path)
    if not site_packages:
        click.echo(click.style(f"Error: Could not locate site-packages in '{env_path or sys.prefix}'.", fg="red"), err=True)
        sys.exit(1)

    pth_file = os.path.join(site_packages, "agy_watch_hook.pth")
    if os.path.exists(pth_file):
        try:
            os.remove(pth_file)
            click.echo(click.style(f"✓ Successfully removed agy_watch auto-hook from {site_packages}", fg="green"))
        except Exception as e:
            click.echo(click.style(f"Error removing {pth_file}: {e}", fg="red"), err=True)
            sys.exit(1)
    else:
        click.echo(click.style(f"No active agy_watch hook found in {site_packages}", fg="yellow"))

    norm_path = os.path.abspath(site_packages)
    for e in envs:
        if e.get("site_packages") != norm_path:
            remaining.append(e)
    _save_watched_envs(remaining)


@main.command("status")
def status_cmd() -> None:
    """Display active watched virtual environments and registry statistics."""
    envs = _load_watched_envs()
    registry = get_global_registry()
    sessions = registry.list_sessions(limit=100)
    live_count = sum(1 for s in sessions if s.get("is_live"))

    click.echo(click.style("=== agy_watch Observability Status ===", bold=True, fg="cyan"))
    click.echo(f"Global Registry:  {registry.db_path}")
    click.echo(f"Total Sessions:   {len(sessions)} ({live_count} currently active)\n")

    click.echo(click.style("Watched Python Environments (.pth Auto-Hook):", bold=True))
    if not envs:
        click.echo("  No environments currently watched. (Run 'agy_watch watch' to observe one)")
    else:
        for idx, e in enumerate(envs, 1):
            pth = e.get("pth_file", "")
            is_active = os.path.exists(pth)
            status_badge = click.style("[ACTIVE]", fg="green", bold=True) if is_active else click.style("[MISSING]", fg="red")
            created_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.get("created_at", 0)))
            click.echo(f"  {idx}. {status_badge} {e.get('site_packages')} (Added: {created_str})")

    # Show proxy path
    proxy_path = _get_proxy_executable_path()
    click.echo(f"\nUniversal Proxy Executable:")
    click.echo(f"  Path: {proxy_path}")
    click.echo(f"  Usage: ANTIGRAVITY_HARNESS_PATH=\"$(agy_watch proxy-path)\" ./my-agent")


@main.command("proxy-path")
def proxy_path_cmd() -> None:
    """Print ONLY the absolute path to the universal agy-harness-proxy binary."""
    # Write only the path to stdout without extra formatting or newlines
    path = _get_proxy_executable_path()
    sys.stdout.write(path + "\n")
    sys.stdout.flush()


@main.command("run", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.argument("script_path", required=True, type=click.Path(exists=True))
@click.pass_context
def run_cmd(ctx: click.Context, script_path: str) -> None:
    """Run any Python agent script with transparent wire-tapping enabled."""
    proxy_path = _get_proxy_executable_path()
    extra_args = list(ctx.args)

    env = os.environ.copy()
    env["ANTIGRAVITY_HARNESS_PATH"] = proxy_path

    cmd = [sys.executable, script_path] + extra_args
    sys.exit(subprocess.call(cmd, env=env))


def _resolve_site_packages(path: Optional[str] = None) -> Optional[str]:
    """Resolves the site-packages directory from an environment or path."""
    import glob

    if path:
        abs_path = os.path.abspath(path)
        if abs_path.endswith("site-packages") and os.path.isdir(abs_path):
            return abs_path

        # Check inside .venv or venv
        candidates = [
            os.path.join(abs_path, "lib", f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages"),
            os.path.join(abs_path, ".venv", "lib", f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages"),
            os.path.join(abs_path, "venv", "lib", f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages"),
        ]
        for c in candidates:
            if os.path.isdir(c):
                return c

        # Glob for any lib/python*/site-packages
        glob_matches = glob.glob(os.path.join(abs_path, "**", "site-packages"), recursive=True)
        if glob_matches:
            return glob_matches[0]

    # Fallback to current virtualenv
    try:
        import site
        sp_list = site.getsitepackages()
        if sp_list:
            return sp_list[0]
    except Exception:
        pass

    fallback = os.path.join(sys.prefix, "lib", f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages")
    if os.path.isdir(fallback):
        return fallback
    return None


WATCHED_ENVS_PATH = os.path.expanduser("~/.antigravity/samples/agy_watch/watched_envs.json")


def _load_watched_envs() -> List[Dict[str, Any]]:
    if os.path.exists(WATCHED_ENVS_PATH):
        try:
            with open(WATCHED_ENVS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_watched_envs(envs: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(WATCHED_ENVS_PATH), exist_ok=True)
    with open(WATCHED_ENVS_PATH, "w", encoding="utf-8") as f:
        json.dump(envs, f, indent=2)


def _get_proxy_executable_path() -> str:
    """Returns the absolute path to the agy-harness-proxy executable."""
    # 1. Check if agy-harness-proxy is in PATH
    if p := shutil.which("agy-harness-proxy"):
        return os.path.abspath(p)

    # 2. Check in current Python sys.prefix bin/
    suffix = "bin/agy-harness-proxy.exe" if sys.platform == "win32" else "bin/agy-harness-proxy"
    bin_cand = os.path.join(sys.prefix, suffix)
    if os.path.isfile(bin_cand):
        return os.path.abspath(bin_cand)

    # 3. Fallback to proxy.py script
    script_cand = os.path.join(os.path.dirname(__file__), "proxy.py")
    return os.path.abspath(script_cand)


if __name__ == "__main__":
    main()
