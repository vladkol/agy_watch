import os
import sys
import asyncio
import logging
import click
from dotenv import load_dotenv

from examples.yolo_agent.agent import _agent_func

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("examples.yolo_agent")


@click.command()
@click.option("--project", "-p", envvar="GOOGLE_CLOUD_PROJECT", help="Google Cloud Project ID")
@click.option("--location", "-l", envvar="GOOGLE_CLOUD_LOCATION", default="global", help="Google Cloud Location")
@click.option("--model", "-m", envvar="MODEL", default="auto", help="Model name or 'auto'")
@click.option("--workspace-dir", "-w", envvar="WORKSPACE_DIR", default="./workspace", help="Workspace directory path")
@click.option("--task-id", "-t", envvar="TASK_ID", default="local-run", help="Unique Task ID")
@click.option("--task-prompt", envvar="TASK_PROMPT", help="Task prompt string")
@click.option("--task-prompt-file", "-f", type=click.Path(exists=True), help="File containing task prompt")
@click.option("--api-key", envvar="GEMINI_API_KEY", help="Gemini API Key (if using Developer API)")
def main(
    project: str | None,
    location: str | None,
    model: str | None,
    workspace_dir: str,
    task_id: str,
    task_prompt: str | None,
    task_prompt_file: str | None,
    api_key: str | None,
):
    """Runs the YOLO autonomous agent example with live agy_watch wire-tapping."""
    load_dotenv()

    # Priority: direct prompt > file prompt > prompt from stdin
    prompt = task_prompt
    if not prompt and task_prompt_file:
        with open(task_prompt_file, "r") as f:
            prompt = f.read()

    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()

    if not prompt:
        click.echo("Error: Please provide a task prompt via --task-prompt, --task-prompt-file, or stdin.", err=True)
        sys.exit(1)

    asyncio.run(
        _agent_func(
            project=project,
            location=location,
            model=model,
            workspace_dir=workspace_dir,
            task_id=task_id,
            task_prompt=prompt,
            api_key=api_key,
        )
    )


if __name__ == "__main__":
    main()
