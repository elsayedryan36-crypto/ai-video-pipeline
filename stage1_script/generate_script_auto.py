"""
Stage 1 (automated) — pops one topic off a shared queue and turns it into a
new project on the HF repo, ready for Stage 2 to render.

Queue file: a plain text file `topics_queue.txt` in the HF dataset repo, one
topic per line. Add topics to it whenever you want new videos made.

This script peeks at the first line, generates the project, and only removes
that line from the queue once the project is safely saved -- so a failed
Gemini call never silently discards a topic. It also retries automatically
on transient 503/429 errors from Gemini before giving up.

Required secrets: GEMINI_API_KEY, HF_TOKEN, HF_REPO
"""

import json
import os
import re
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common.pipeline_utils import _api, _repo  # reuse the HF client/repo helpers
from huggingface_hub import hf_hub_download

GEMINI_MODEL = "gemini-flash-latest"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)

SYSTEM_PROMPT = """You are a video pre-production assistant. Given a topic, produce a
complete pre-production package as STRICT JSON -- no markdown fences, no commentary,
just the JSON object matching this exact shape:

{
  "title": "string",
  "summary": "one or two sentence summary",
  "script_markdown": "the full narration/dialogue script in markdown",
  "characters": [
    {
      "id": "lowercase_snake_case_id",
      "name": "Display Name",
      "description": "a detailed, visually specific description an image model can use \
consistently across many shots: species/age, coloring, distinguishing marks, build, \
typical expression. Avoid vague words like 'nice' or 'cool' -- be concrete."
    }
  ],
  "shots": [
    {
      "shot_id": "shot_001",
      "scene": 1,
      "description": "a specific visual shot description: framing (wide/close-up/etc), \
action, setting",
      "characters_present": ["character_id", "..."],
      "dialogue": "Speaker: \"line\"  (or null if no dialogue)",
      "duration_seconds": 4
    }
  ]
}

Rules:
- Keep each shot 3-6 seconds. A 60-90 second video is usually 12-20 shots.
- Every character_id in "characters_present" must exist in "characters".
- Keep character descriptions stable and reusable.
- Output ONLY the JSON object.
"""


def call_gemini(topic: str) -> dict:
    api_key = os.environ["GEMINI_API_KEY"]
    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": f"Topic: {topic}"}]}],
        "generationConfig": {"temperature": 0.9, "response_mime_type": "application/json"},
    }

    last_error = None
    for attempt in range(4):
        resp = requests.post(GEMINI_URL, params={"key": api_key}, json=payload, timeout=60)
        if resp.status_code in (503, 429):  # overloaded / rate-limited -- worth retrying
            last_error = resp
            wait = 5 * (2 ** attempt)
            print(f"Gemini returned {resp.status_code}, retrying in {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        text = re.sub(r"^```json\s*|\s*```$", "", text.strip())
        return json.loads(text)

    last_error.raise_for_status()  # give up and surface the real error after 4 tries


def peek_next_topic() -> tuple[str, list[str]] | None:
    """Look at the queue without removing anything yet."""
    try:
        path = hf_hub_download(repo_id=_repo(), repo_type="dataset",
                                filename="topics_queue.txt", token=os.environ.get("HF_TOKEN"))
    except Exception:
        print("No topics_queue.txt found in the repo yet.")
        return None

    lines = [l.strip() for l in Path(path).read_text().splitlines() if l.strip()]
    if not lines:
        return None
    return lines[0], lines[1:]


def remove_topic_from_queue(remaining: list[str]) -> None:
    """Only called after the project was successfully created."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("\n".join(remaining))
        tmp_path = f.name
    _api().upload_file(path_or_fileobj=tmp_path, path_in_repo="topics_queue.txt",
                        repo_id=_repo(), repo_type="dataset")


def build_manifest(project_id: str, topic: str, package: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    characters = [
        {"id": c["id"], "name": c["name"], "description": c["description"],
         "reference_sheet": f"characters/{c['id']}/sheet.png",
         "lora_path": None, "status": "pending"}
        for c in package["characters"]
    ]
    shots = [
        {"shot_id": s["shot_id"], "scene": s["scene"], "description": s["description"],
         "characters_present": s["characters_present"], "dialogue": s.get("dialogue"),
         "duration_seconds": s.get("duration_seconds", 4),
         "image": {"status": "pending"}, "video": {"status": "pending"},
         "audio": {"narration_status": "pending", "sfx_status": "pending"}}
        for s in package["shots"]
    ]
    return {
        "project_id": project_id, "status": "script_ready",
        "created_at": now, "updated_at": now, "topic": topic, "language": "en",
        "script": {"status": "done", "title": package["title"],
                   "summary": package["summary"], "raw_file": "script.md"},
        "characters": characters, "shots": shots,
        "assembly": {"status": "not_started", "output_path": None,
                     "music_track": None, "subtitles_path": None},
        "review": {"status": "not_started", "telegram_message_id": None,
                   "decision": None, "notes": None},
        "publish": {"status": "not_started", "youtube_video_id": None,
                    "title": package["title"], "description": package["summary"],
                    "synthetic_media_disclosed": True, "uploaded_at": None},
    }


def main():
    picked = peek_next_topic()
    if not picked:
        print("Queue empty -- nothing to do. Add lines to topics_queue.txt in the HF repo.")
        return
    topic, remaining = picked

    print(f"generating project for topic: {topic}")
    package = call_gemini(topic)  # if this raises, the topic stays in the queue -- safe to retry

    project_id = f"project_{uuid.uuid4().hex[:8]}"
    manifest = build_manifest(project_id, topic, package)

    with tempfile.TemporaryDirectory() as tmp:
        manifest_path = Path(tmp) / "manifest.json"
        script_path = Path(tmp) / "script.md"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        script_path.write_text(package["script_markdown"])

        _api().upload_file(path_or_fileobj=str(manifest_path),
                            path_in_repo=f"{project_id}/manifest.json",
                            repo_id=_repo(), repo_type="dataset")
        _api().upload_file(path_or_fileobj=str(script_path),
                            path_in_repo=f"{project_id}/script.md",
                            repo_id=_repo(), repo_type="dataset")

    # only remove the topic from the queue once the project is safely saved
    remove_topic_from_queue(remaining)

    print(f"created {project_id} ({len(manifest['characters'])} characters, "
          f"{len(manifest['shots'])} shots) -- status=script_ready")


if __name__ == "__main__":
    main()
