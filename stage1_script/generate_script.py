"""
Stage 1 — Script + Character + Shot List generation.

Runs on the always-on Oracle box (or anywhere with internet + a Gemini API key).
Free tier: https://aistudio.google.com/apikey

Input:  a topic string (CLI arg or STDIN)
Output: a new project folder under ./projects/<project_id>/ containing manifest.json + script.md

Usage:
    export GEMINI_API_KEY=xxxxx
    python generate_script.py "A short fable about a fox who learns patience"
"""

import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

GEMINI_MODEL = "gemini-2.5-flash"  # free tier, fast, good enough for scripting
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)
PROJECTS_DIR = Path(__file__).resolve().parent.parent / "projects"

SYSTEM_PROMPT = """You are a video pre-production assistant. Given a topic, produce a
complete pre-production package as STRICT JSON — no markdown fences, no commentary,
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
- Keep character descriptions stable and reusable -- these will be used to generate a \
reference sheet that every future image of that character must match.
- Output ONLY the JSON object.
"""


def call_gemini(topic: str) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("Set GEMINI_API_KEY first (free key: https://aistudio.google.com/apikey)")

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": f"Topic: {topic}"}]}],
        "generationConfig": {"temperature": 0.9, "response_mime_type": "application/json"},
    }

    resp = requests.post(
        GEMINI_URL,
        params={"key": api_key},
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()

    text = data["candidates"][0]["content"]["parts"][0]["text"]
    text = re.sub(r"^```json\s*|\s*```$", "", text.strip())
    return json.loads(text)


def build_manifest(project_id: str, topic: str, package: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat()

    characters = [
        {
            "id": c["id"],
            "name": c["name"],
            "description": c["description"],
            "reference_sheet": f"characters/{c['id']}/sheet.png",
            "lora_path": None,
            "status": "pending",
        }
        for c in package["characters"]
    ]

    shots = [
        {
            "shot_id": s["shot_id"],
            "scene": s["scene"],
            "description": s["description"],
            "characters_present": s["characters_present"],
            "dialogue": s.get("dialogue"),
            "duration_seconds": s.get("duration_seconds", 4),
            "image": {"status": "pending"},
            "video": {"status": "pending"},
            "audio": {"narration_status": "pending", "sfx_status": "pending"},
        }
        for s in package["shots"]
    ]

    return {
        "project_id": project_id,
        "status": "script_ready",
        "created_at": now,
        "updated_at": now,
        "topic": topic,
        "language": "en",
        "script": {
            "status": "done",
            "title": package["title"],
            "summary": package["summary"],
            "raw_file": "script.md",
        },
        "characters": characters,
        "shots": shots,
        "assembly": {"status": "not_started", "output_path": None, "music_track": None, "subtitles_path": None},
        "review": {"status": "not_started", "telegram_message_id": None, "decision": None, "notes": None},
        "publish": {
            "status": "not_started",
            "youtube_video_id": None,
            "title": package["title"],
            "description": package["summary"],
            "synthetic_media_disclosed": True,
            "uploaded_at": None,
        },
    }


def main():
    if len(sys.argv) > 1:
        topic = " ".join(sys.argv[1:])
    else:
        topic = input("Topic for the video: ").strip()

    if not topic:
        sys.exit("No topic given.")

    package = call_gemini(topic)

    project_id = f"project_{uuid.uuid4().hex[:8]}"
    project_dir = PROJECTS_DIR / project_id
    (project_dir / "characters").mkdir(parents=True, exist_ok=True)
    (project_dir / "shots").mkdir(parents=True, exist_ok=True)

    manifest = build_manifest(project_id, topic, package)

    (project_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (project_dir / "script.md").write_text(package["script_markdown"])

    print(f"Created {project_dir}")
    print(f"  characters: {[c['name'] for c in manifest['characters']]}")
    print(f"  shots: {len(manifest['shots'])}")
    print("\nNext step: push this project folder to your Hugging Face dataset repo,")
    print("then open the Kaggle notebook (stage2_kaggle) and set PROJECT_ID to:")
    print(f"  {project_id}")


if __name__ == "__main__":
    main()
