"""
Stage 5 — Publish.

For every project with manifest.status == "approved": upload final.mp4 to
YouTube, attach captions.srt, mark the video as containing synthetic media
(required disclosure for AI-generated realistic content), and flip status to
"published".

Runs on: GitHub Actions (scheduled).

Required secrets:
    YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN   (see get_refresh_token.py)
    HF_TOKEN, HF_REPO

Usage:
    python upload_youtube.py            # process every approved project
    python upload_youtube.py PROJECT_ID # process just one
"""

import os
import sys
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common.pipeline_utils import (
    download_project_file,
    load_manifest,
    projects_with_status,
    save_manifest,
)

# YouTube quota: an upload costs 1,600 of your 10,000 daily units -> ~6 uploads/day max.
DAILY_UPLOAD_CAP = 6


def youtube_client():
    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("youtube", "v3", credentials=creds)


def publish_project(youtube, project_id: str) -> None:
    print(f"=== publishing {project_id} ===")
    manifest = load_manifest(project_id)

    video_path = download_project_file(project_id, "final.mp4")
    title = manifest["publish"].get("title") or manifest["script"]["title"]
    description = manifest["publish"].get("description") or manifest["script"]["summary"]

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "categoryId": "24",  # Entertainment
        },
        "status": {
            "privacyStatus": "private",  # flip to "public" once you trust the pipeline
            "selfDeclaredMadeForKids": False,
            # Required disclosure for realistic AI-generated/altered content.
            # https://developers.google.com/youtube/v3/revision_history (Oct 30, 2024)
            "containsSyntheticMedia": True,
        },
    }

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"  upload progress: {int(status.progress() * 100)}%")

    video_id = response["id"]
    print(f"  uploaded -> https://youtu.be/{video_id}")

    # Attach captions if we made an .srt in Stage 3
    try:
        srt_path = download_project_file(project_id, "captions.srt")
        youtube.captions().insert(
            part="snippet",
            body={"snippet": {"videoId": video_id, "language": manifest.get("language", "en"),
                               "name": "English", "isDraft": False}},
            media_body=MediaFileUpload(srt_path, mimetype="application/octet-stream"),
        ).execute()
        print("  captions attached")
    except Exception as e:
        print(f"  no captions uploaded ({e})")

    manifest["publish"]["status"] = "done"
    manifest["publish"]["youtube_video_id"] = video_id
    from datetime import datetime, timezone
    manifest["publish"]["uploaded_at"] = datetime.now(timezone.utc).isoformat()
    manifest["status"] = "published"
    save_manifest(project_id, manifest)


def main():
    youtube = youtube_client()

    if len(sys.argv) > 1:
        publish_project(youtube, sys.argv[1])
        return

    targets = projects_with_status(["status"], "approved")
    if not targets:
        print("Nothing approved and waiting to publish.")
        return

    for project_id, _ in targets[:DAILY_UPLOAD_CAP]:
        try:
            publish_project(youtube, project_id)
        except Exception as e:
            print(f"FAILED {project_id}: {e}")


if __name__ == "__main__":
    main()
