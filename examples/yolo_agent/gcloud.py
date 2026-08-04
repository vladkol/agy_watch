import subprocess
import logging

logger = logging.getLogger("examples.yolo_agent.gcloud")


def get_gcloud_project() -> str | None:
    """Attempts to retrieve the active Google Cloud project from the gcloud CLI."""
    try:
        res = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True,
            text=True,
            check=True,
        )
        project = res.stdout.strip()
        return project if project else None
    except Exception as e:
        logger.debug(f"Failed to get gcloud project: {e}")
        return None
