"""
Stage 2 trigger — runs the ComfyUI render notebook on Kaggle's free GPU via the
Kaggle API instead of you opening the browser and clicking Run.

One-time setup:
  1. kaggle.com -> Settings -> Create New API Token -> downloads kaggle.json
  2. Push the notebook to Kaggle once manually (Kaggle requires a kernel to
     exist before the API can "version" it): open
     stage2_kaggle/character_consistent_pipeline.ipynb on kaggle.com, save it
     as a notebook (this creates the kernel slug, e.g. yourname/video-pipeline-render),
     turn ON internet + GPU T4 x2 in its settings, and add the HF_TOKEN / HF_REPO
     Kaggle Secrets as described in that notebook.
  3. Note the kernel slug (yourusername/video-pipeline-render)

This script:
  1. Picks the oldest project with status == "script_ready"
  2. Writes that project_id into the notebook's first cell
  3. Pushes the updated notebook to Kaggle (kaggle kernels push) -- this queues
     a run on Kaggle's GPU
  4. Polls kernel status until it finishes (Kaggle sessions can run up to 12h,
     so this script polls for a long time -- run it as a background/long job,
     not a 5-minute cron tick)

Required secrets: KAGGLE_USERNAME, KAGGLE_KEY, HF_TOKEN, HF_REPO,
                   KAGGLE_KERNEL_SLUG (e.g. "yourname/video-pipeline-render")

Usage:
    python trigger_kaggle_run.py
"""

import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common.pipeline_utils import projects_with_status

NOTEBOOK_PATH = Path(__file__).resolve().parent / "character_consistent_pipeline.ipynb"
POLL_SECONDS = 120
# GitHub Actions caps a single job at 6h even on the free tier, which is shorter
# than Kaggle's 12h session cap -- so this script polls for under that limit and
# just exits if the render isn't done yet. Re-running the workflow later will
# see the kernel is already "running" and poll it rather than restarting it.
MAX_WAIT_SECONDS = 5 * 3600 + 30 * 60


def set_kaggle_credentials():
    """The kaggle CLI wants ~/.kaggle/kaggle.json rather than plain env vars."""
    cred_dir = Path.home() / ".kaggle"
    cred_dir.mkdir(exist_ok=True)
    cred_file = cred_dir / "kaggle.json"
    cred_file.write_text(json.dumps({
        "username": os.environ["KAGGLE_USERNAME"],
        "key": os.environ["KAGGLE_KEY"],
    }))
    cred_file.chmod(0o600)


def pick_project() -> str | None:
    targets = projects_with_status(["status"], "script_ready")
    return targets[0][0] if targets else None


def set_project_id_in_notebook(project_id: str) -> None:
    nb = json.loads(NOTEBOOK_PATH.read_text())
    for cell in nb["cells"]:
        if cell["cell_type"] == "code" and any("PROJECT_ID" in line for line in cell["source"]):
            cell["source"] = [re.sub(r'PROJECT_ID = ".*"', f'PROJECT_ID = "{project_id}"', line)
                               for line in cell["source"]]
            break
    NOTEBOOK_PATH.write_text(json.dumps(nb, indent=1))


def push_and_run(kernel_slug: str) -> None:
    # kaggle kernels push needs a kernel-metadata.json next to the notebook
    meta_path = NOTEBOOK_PATH.parent / "kernel-metadata.json"
    meta_path.write_text(json.dumps({
        "id": kernel_slug,
        "title": kernel_slug.split("/")[-1],
        "code_file": NOTEBOOK_PATH.name,
        "language": "python",
        "kernel_type": "notebook",
        "is_private": True,
        "enable_gpu": True,
        "enable_internet": True,
    }, indent=2))

    from kaggle.api.kaggle_api_extended import KaggleApi
    api = KaggleApi()
    api.authenticate()

    current = api.kernels_status(kernel_slug)
    if current.get("status") == "running":
        print(f"{kernel_slug} is already running from a previous trigger -- polling, not re-pushing.")
    else:
        api.kernels_push(str(NOTEBOOK_PATH.parent))
        print(f"pushed new version of {kernel_slug}, Kaggle is queuing the run...")

    waited = 0
    while waited < MAX_WAIT_SECONDS:
        status = api.kernels_status(kernel_slug)
        print(f"  status: {status.get('status')} ({waited // 60}m elapsed)")
        if status.get("status") in ("complete", "error", "cancelAcknowledged"):
            return status.get("status")
        time.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
    return "timeout"


def main():
    project_id = pick_project()
    if not project_id:
        print("No project waiting for rendering (status=script_ready).")
        return

    print(f"rendering {project_id} on Kaggle...")
    set_kaggle_credentials()
    set_project_id_in_notebook(project_id)

    kernel_slug = os.environ["KAGGLE_KERNEL_SLUG"]
    result = push_and_run(kernel_slug)
    print(f"Kaggle run finished with status: {result}")
    print("Check manifest.json in the HF repo for the new status "
          "(should be 'rendered_pending_assembly' if it succeeded).")


if __name__ == "__main__":
    main()
