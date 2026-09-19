"""
Stage 4 — Human review gate, via Telegram.

Designed to run every few minutes as a GitHub Actions cron job rather than as a
long-lived bot process -- there's no server to keep alive. State (which update_id
we last saw, which projects we've already notified about) is stashed in the HF
repo under _state/telegram_bot.json.

One-time setup:
  1. Message @BotFather on Telegram, /newbot, copy the token -> TELEGRAM_BOT_TOKEN
  2. Message your new bot anything, then visit
     https://api.telegram.org/bot<TOKEN>/getUpdates
     and copy your numeric "chat":{"id": ...} -> TELEGRAM_CHAT_ID

Each run does two things:
  A. NOTIFY: for every project with status == "awaiting_review" that hasn't been
     sent yet, push the video (or a link, if it's too big for Telegram's 50MB bot
     upload limit) with Approve / Reject inline buttons.
  B. COLLECT: poll getUpdates for any button presses since the last run, and flip
     that project's status to "approved" or "rejected" accordingly.

Usage:
    python telegram_bot.py
"""

import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common.pipeline_utils import (
    download_project_file,
    file_url,
    load_manifest,
    load_state,
    projects_with_status,
    save_manifest,
    save_state,
)

TELEGRAM_MAX_BOT_UPLOAD_BYTES = 45 * 1024 * 1024  # stay under the 50MB bot API limit


def _token() -> str:
    t = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not t:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
    return t


def _chat_id() -> str:
    c = os.environ.get("TELEGRAM_CHAT_ID")
    if not c:
        raise RuntimeError("TELEGRAM_CHAT_ID not set")
    return c


def api(method: str, **kwargs):
    resp = requests.post(f"https://api.telegram.org/bot{_token()}/{method}", data=kwargs.get("data"),
                          files=kwargs.get("files"), timeout=120)
    resp.raise_for_status()
    return resp.json()


def notify_pending_reviews(state: dict) -> dict:
    notified = set(state.get("notified", []))
    pending = projects_with_status(["status"], "awaiting_review")

    for project_id, manifest in pending:
        if project_id in notified:
            continue

        title = manifest["publish"].get("title") or manifest["script"].get("title", project_id)
        caption = f"🎬 {title}\n\nReady for review — approve to publish to YouTube."
        keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Approve", "callback_data": f"approve:{project_id}"},
                {"text": "❌ Reject", "callback_data": f"reject:{project_id}"},
            ]]
        }

        local_video = download_project_file(project_id, "final.mp4")
        size = os.path.getsize(local_video)

        if size <= TELEGRAM_MAX_BOT_UPLOAD_BYTES:
            with open(local_video, "rb") as f:
                result = api(
                    "sendVideo",
                    data={"chat_id": _chat_id(), "caption": caption,
                          "reply_markup": _json(keyboard)},
                    files={"video": f},
                )
        else:
            link = file_url(project_id, "final.mp4")
            result = api(
                "sendMessage",
                data={"chat_id": _chat_id(),
                      "text": f"{caption}\n\n(too large to preview here — download: {link})",
                      "reply_markup": _json(keyboard)},
            )

        message_id = result["result"]["message_id"]
        manifest["review"]["telegram_message_id"] = message_id
        manifest["review"]["status"] = "sent"
        save_manifest(project_id, manifest)

        notified.add(project_id)
        print(f"notified about {project_id}")

    state["notified"] = list(notified)
    return state


def collect_decisions(state: dict) -> dict:
    last_update_id = state.get("last_update_id", 0)
    updates = api("getUpdates", data={"offset": last_update_id + 1, "timeout": 0})["result"]

    for update in updates:
        state["last_update_id"] = update["update_id"]
        cq = update.get("callback_query")
        if not cq:
            continue

        data = cq.get("data", "")
        if ":" not in data:
            continue
        action, project_id = data.split(":", 1)

        try:
            manifest = load_manifest(project_id)
        except Exception:
            api("answerCallbackQuery", data={"callback_query_id": cq["id"], "text": "Project not found."})
            continue

        if action == "approve":
            manifest["review"]["status"] = "approved"
            manifest["review"]["decision"] = "approved"
            manifest["status"] = "approved"
            ack_text = "Approved — queued for YouTube upload."
        elif action == "reject":
            manifest["review"]["status"] = "rejected"
            manifest["review"]["decision"] = "rejected"
            manifest["status"] = "rejected"
            ack_text = "Rejected."
        else:
            continue

        save_manifest(project_id, manifest)
        api("answerCallbackQuery", data={"callback_query_id": cq["id"], "text": ack_text})

        chat_id = cq["message"]["chat"]["id"]
        message_id = cq["message"]["message_id"]
        api("editMessageReplyMarkup", data={
            "chat_id": chat_id, "message_id": message_id,
            "reply_markup": _json({"inline_keyboard": [[{"text": ack_text, "callback_data": "noop"}]]}),
        })
        print(f"{project_id} -> {action}")

    return state


def _json(obj) -> str:
    import json
    return json.dumps(obj)


def main():
    state = load_state("telegram_bot", {"last_update_id": 0, "notified": []})
    state = notify_pending_reviews(state)
    state = collect_decisions(state)
    save_state("telegram_bot", state)


if __name__ == "__main__":
    main()
