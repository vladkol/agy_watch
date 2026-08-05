import os
import logging
from google.antigravity import Agent, LocalAgentConfig, CapabilitiesConfig, types
from google.antigravity.hooks import policy

logger = logging.getLogger("examples.yolo_agent")


async def _agent_func(
    project: str | None = None,
    location: str | None = None,
    model: str | None = None,
    workspace_dir: str | None = None,
    task_id: str | None = None,
    task_prompt: str | None = None,
    use_global_config: bool = True,
    api_key: str | None = None,
    **kwargs,
):
    """Runs the Antigravity SDK YOLO Agent on the given task prompt."""
    if not task_prompt:
        logger.error("No task prompt provided.")
        return

    # Check for APP_MODE and DEV_USER_ID from environment
    app_mode = os.environ.get("APP_MODE", "local")
    user_id = os.environ.get("DEV_USER_ID", "test_engineer")

    # Ensure workspace directory exists
    if workspace_dir:
        workspace_dir = os.path.abspath(workspace_dir)
        save_dir = os.path.join(workspace_dir, ".trajectories")
        os.makedirs(save_dir, exist_ok=True)
    else:
        save_dir = None

    system_instructions = (
        "You are an autonomous AI Agent."
    )

    # Build Antigravity Agent Configuration
    config_kwargs = {
        "capabilities": CapabilitiesConfig(
            enabled_tools=types.BuiltinTools.all_tools()
        ),
        "system_instructions": system_instructions,
        "policies": [policy.allow_all()],
    }

    if model and model != "auto":
        config_kwargs["model"] = model

    resolved_api_key = (
        api_key
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
    )
    if resolved_api_key:
        config_kwargs["api_key"] = resolved_api_key
        config_kwargs["vertex"] = False
    else:
        resolved_project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not resolved_project:
            try:
                from examples.yolo_agent.gcloud import get_gcloud_project
                resolved_project = get_gcloud_project()
            except Exception:
                resolved_project = None
        if resolved_project:
            config_kwargs["project"] = resolved_project
            config_kwargs["location"] = location or os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"
            config_kwargs["vertex"] = True

    if workspace_dir:
        config_kwargs["workspaces"] = [workspace_dir]
        config_kwargs["app_data_dir"] = workspace_dir
        config_kwargs["save_dir"] = save_dir

    config = LocalAgentConfig(**config_kwargs)

    async with Agent(config) as agent:
        response = await agent.chat(task_prompt)
        response_text = await response.text()
        try:
            async for step in agent.conversation.receive_steps():
                if step.is_complete_response and step.content:
                    response_text = step.content
        except Exception as e:
            logger.debug("Finished multi-turn receive_steps loop: %s", e)

        final_resp = agent.conversation.last_response or response_text
        logger.info("==================================================")
        logger.info(" Agent Execution Result:")
        logger.info("==================================================")
        logger.info(final_resp)
        return response_text
