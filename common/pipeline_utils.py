"""
Shared helpers: every stage pulls/pushes projects from the same Hugging Face
dataset repo, which is acting as the pipeline's shared filesystem since GitHub
Actions runners are stateless (fresh VM every run) and Kaggle sessions are ephemeral.

Env vars required (set as GitHub Actions secrets / Kaggle secrets):
    HF_TOKEN   - Hugging Face token with write access
    HF_REPO    - e.g. "yourname/video-pipeline-assets" (a dataset repo, can be private)
"""

import json
import os
import tempfile
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download, list_repo_files


def _api() -> HfApi:
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("HF_TOKEN not set")
    return HfApi(token=token)


def _repo() -> str:
    repo = os.environ.get("HF_REPO")
    if not repo:
        raise RuntimeError("HF_REPO not set")
    return repo


def list_project_ids() -> list[str]:
    """Every top-level folder in the dataset repo that has a manifest.json."""
    files = list_repo_files(_repo(), repo_type="dataset", token=os.environ.get("HF_TOKEN"))
    ids = set()
    for f in files:
        if f.endswith("manifest.json"):
            ids.add(f.split("/")[0])
    return sorted(ids)


def load_manifest(project_id: str) -> dict:
    path = hf_hub_download(
        repo_id=_repo(), repo_type="dataset",
        filename=f"{project_id}/manifest.json",
        token=os.environ.get("HF_TOKEN"),
    )
    return json.load(open(path))


def save_manifest(project_id: str, manifest: dict) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(manifest, f, indent=2)
        tmp_path = f.name
    _api().upload_file(
        path_or_fileobj=tmp_path,
        path_in_repo=f"{project_id}/manifest.json",
        repo_id=_repo(), repo_type="dataset",
    )
    os.unlink(tmp_path)


def download_project_file(project_id: str, relative_path: str) -> str:
    """Download one file belonging to a project, return its local path."""
    return hf_hub_download(
        repo_id=_repo(), repo_type="dataset",
        filename=f"{project_id}/{relative_path}",
        token=os.environ.get("HF_TOKEN"),
    )


def upload_project_file(project_id: str, local_path: str, relative_path: str) -> None:
    _api().upload_file(
        path_or_fileobj=local_path,
        path_in_repo=f"{project_id}/{relative_path}",
        repo_id=_repo(), repo_type="dataset",
    )


def file_url(project_id: str, relative_path: str) -> str:
    """A direct HTTPS URL to a file in the repo (works for private repos only
    when accompanied by a token; fine for Telegram since we upload the bytes
    directly rather than relying on this for private files)."""
    return f"https://huggingface.co/datasets/{_repo()}/resolve/main/{project_id}/{relative_path}"


def projects_with_status(status_field_path: list[str], value: str) -> list[dict]:
    """Return [(project_id, manifest), ...] where manifest[a][b][...] == value."""
    results = []
    for pid in list_project_ids():
        try:
            m = load_manifest(pid)
        except Exception:
            continue
        node = m
        ok = True
        for key in status_field_path:
            if not isinstance(node, dict) or key not in node:
                ok = False
                break
            node = node[key]
        if ok and node == value:
            results.append((pid, m))
    return results


# --- tiny key/value state store, used by the Telegram bot to remember which
#     update_id it last processed (bots polling via cron can't keep in-memory state) ---

def load_state(key: str, default: dict) -> dict:
    try:
        path = hf_hub_download(
            repo_id=_repo(), repo_type="dataset",
            filename=f"_state/{key}.json", token=os.environ.get("HF_TOKEN"),
        )
        return json.load(open(path))
    except Exception:
        return default


def save_state(key: str, value: dict) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(value, f, indent=2)
        tmp_path = f.name
    _api().upload_file(
        path_or_fileobj=tmp_path,
        path_in_repo=f"_state/{key}.json",
        repo_id=_repo(), repo_type="dataset",
    )
    os.unlink(tmp_path)
