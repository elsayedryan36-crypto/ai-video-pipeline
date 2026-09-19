# AI Video Pipeline — fully cloud, $0

Topic queue → script + characters + shots (Gemini) → character-consistent images
+ video clips (Kaggle GPU, ComfyUI) → narration + assembly (GitHub Actions,
edge-tts + FFmpeg) → your approval (Telegram) → YouTube upload (GitHub Actions).

Nothing runs on your machine after setup. Two scheduled GitHub Actions workflows
drive everything; you only touch Telegram to approve or reject.

```
you add a topic ──▶ GitHub Actions (hourly)              GitHub Actions (daily)
                     ├─ Stage 1: script+shots (Gemini)     └─ Stage 2: render on Kaggle GPU
                     ├─ Stage 3: assemble (edge-tts+ffmpeg)     (character sheets → shot
                     ├─ Stage 4: send to Telegram for review     images → video clips)
                     └─ Stage 5: upload approved videos to YouTube
```

## One-time setup (does take a real hour — this is the part that can't be automated)

### 1. Hugging Face — the shared storage between all stages
- Create a free account, then a new **dataset** repo (private is fine):
  `huggingface.co/new-dataset` → e.g. `yourname/video-pipeline-assets`
- Settings → Access Tokens → create one with **write** access → `HF_TOKEN`
- Repo id → `HF_REPO` (e.g. `yourname/video-pipeline-assets`)

### 2. Gemini — free script generation
- `aistudio.google.com/apikey` → create key → `GEMINI_API_KEY`

### 3. Kaggle — free GPU rendering
- Create account, verify your phone number (required to enable GPUs)
- Settings → API → Create New Token → downloads `kaggle.json` → gives you
  `KAGGLE_USERNAME` and `KAGGLE_KEY`
- Upload `stage2_kaggle/character_consistent_pipeline.ipynb` to Kaggle **once**
  manually (Notebooks → New Notebook → File → Import Notebook), then:
  - Settings (right panel) → Accelerator → **GPU T4 x2**, Internet → **On**
  - Add-ons → Secrets → add `HF_TOKEN` and `HF_REPO`
  - Save it once so the kernel exists — note its slug from the URL, e.g.
    `kaggle.com/code/yourname/character-consistent-pipeline` → slug is
    `yourname/character-consistent-pipeline` → `KAGGLE_KERNEL_SLUG`

### 4. Telegram — your review gate
- Message **@BotFather** → `/newbot` → follow the prompts → copy the token →
  `TELEGRAM_BOT_TOKEN`
- Message your new bot literally anything (e.g. "hi"), then open in a browser:
  `https://api.telegram.org/bot<TOKEN>/getUpdates` and read off
  `"chat":{"id": 123456789}` → `TELEGRAM_CHAT_ID`

### 5. YouTube — publishing
- `console.cloud.google.com` → new project → enable **YouTube Data API v3**
- OAuth consent screen → External → add your own Google account as a **Test User**
- Credentials → Create Credentials → OAuth client ID → **Desktop app** → download
  the JSON as `client_secret.json`
- On your own machine (not in the cloud, this step needs a browser):
  ```bash
  cd stage5_publish
  pip install google-auth-oauthlib
  python get_refresh_token.py
  ```
  This prints `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`.
  ⚠️ While your OAuth app is in "Testing" mode (the free default), this refresh
  token expires after 7 days of inactivity. Either submit the app for Google's
  verification, or re-run this script every so often.

### 6. Put it all in GitHub
- Push this folder to a new GitHub repo (**public**, so Actions minutes are
  unlimited and free — private repos only get 2,000 free minutes/month)
- Repo → Settings → Secrets and variables → Actions → add all of these:

  | Secret | From step |
  |---|---|
  | `HF_TOKEN`, `HF_REPO` | 1 |
  | `GEMINI_API_KEY` | 2 |
  | `KAGGLE_USERNAME`, `KAGGLE_KEY`, `KAGGLE_KERNEL_SLUG` | 3 |
  | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | 4 |
  | `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` | 5 |

- The two workflows in `.github/workflows/` pick these up automatically:
  - `pipeline_cpu.yml` — script gen, assembly, Telegram, YouTube upload (hourly)
  - `pipeline_kaggle.yml` — GPU rendering on Kaggle (daily; GPU quota is the
    real bottleneck so there's no benefit running this more often)

## Using it, day to day

**Add a topic** — append a line to `topics_queue.txt` in your HF repo:
```bash
echo "A short fable about a fox who learns patience" >> topics_queue.txt
huggingface-cli upload yourname/video-pipeline-assets topics_queue.txt topics_queue.txt --repo-type dataset
```
Next hourly run picks it up and writes a new project with status `script_ready`.

**Rendering happens automatically** the next time `pipeline_kaggle.yml` fires
(daily, or trigger it manually from the Actions tab → Run workflow).

**You'll get a Telegram message** once a video is assembled, with Approve/Reject
buttons. Tap one.

**Approved videos get uploaded** to YouTube (as **private**, on purpose — see
below) on the next hourly run.

## Deliberate safety defaults, don't remove these without thinking it through

- Videos upload as **`privacyStatus: private`** in `stage5_publish/upload_youtube.py`.
  Flip to `public` once you've watched a few end-to-end and trust the pipeline.
- `containsSyntheticMedia: True` is set on every upload — this is YouTube's
  required disclosure for realistic AI-generated content
  ([API docs](https://developers.google.com/youtube/v3/revision_history), Oct 2024 entry).
- The Telegram approval step is a real gate, not a formality: YouTube's
  "inauthentic content" monetization policy specifically targets mass-produced,
  templated output with no meaningful human input. Actually watching and
  rejecting bad ones is what keeps a channel eligible for monetization, not
  just a nice workflow feature.
- `DAILY_UPLOAD_CAP = 6` in `upload_youtube.py` matches YouTube's default quota
  (10,000 units/day ÷ 1,600 per upload).

## Free-tier limits that set your real throughput

| Resource | Limit | Consequence |
|---|---|---|
| Kaggle GPU | ~30 hrs/week, 12h/session | ~6-8 GPU-hrs per 3-min video → 3-4 videos/week |
| GitHub Actions (public repo) | unlimited | not a bottleneck |
| GitHub Actions job | 6h max | Kaggle trigger workflow polls up to 5.5h, resumes next day if still rendering |
| Gemini free tier | generous per-day quota | fine at this volume |
| YouTube Data API | 10,000 units/day | ~6 uploads/day max |
| Telegram bot upload | 50MB per video via Bot API | larger finals send a download link instead |
| YouTube OAuth (testing mode) | refresh token expires after 7 days idle | re-run `get_refresh_token.py` periodically, or get the app verified |

## File map

```
common/pipeline_utils.py           shared HF-repo read/write used by every stage
stage1_script/
  generate_script.py               manual/local: topic → project (for testing)
  generate_script_auto.py          cloud: pops topics_queue.txt → project
stage2_kaggle/
  character_consistent_pipeline.ipynb   runs on Kaggle: sheets → shot images → clips
  trigger_kaggle_run.py            triggers + polls the above via Kaggle API
stage3_assembly/assemble.py        edge-tts narration + ffmpeg concat/mux/music/subs
stage4_review/telegram_bot.py      sends video for review, collects approve/reject
stage5_publish/
  get_refresh_token.py             ONE-TIME, run locally, not in the cloud
  upload_youtube.py                uploads approved videos, sets disclosure flag
.github/workflows/
  pipeline_cpu.yml                 hourly: stages 1, 3, 4, 5
  pipeline_kaggle.yml              daily: stage 2
manifest_example.json              the shape of the state file every stage shares
```

## Debugging a stuck project

Every project's `manifest.json` in the HF repo has a top-level `status` field
that tells you exactly where it's stuck:

`script_ready` → waiting for the Kaggle workflow to pick it up
`rendered_pending_assembly` → waiting for the next hourly Actions run
`awaiting_review` → waiting for you to tap a Telegram button
`approved` → waiting for the next hourly Actions run to upload it
`published` → done, check `manifest.publish.youtube_video_id`
`rejected` → dead end, nothing more happens to it

Each stage also only processes projects whose status matches what it expects,
so re-running any workflow manually (Actions tab → Run workflow) is always safe.
