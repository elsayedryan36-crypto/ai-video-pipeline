"""
Stage 3 — Assembly.

For every project whose manifest.status == "rendered_pending_assembly":
  1. Generate narration audio per shot with edge-tts (free, local, no API key)
  2. Mux narration onto each shot's silent video clip
  3. Concatenate all shots into one video
  4. Duck in a background music bed under the narration
  5. Write an .srt subtitle file (soft subs, uploaded separately in Stage 5 --
     more robust than burning text into the video)
  6. Push final.mp4 + captions.srt back to the HF repo, set status to
     "awaiting_review"

Runs on: GitHub Actions (ubuntu-latest ships ffmpeg) or any Linux box.

Usage:
    python assemble.py            # process every eligible project
    python assemble.py PROJECT_ID # process just one
"""

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

import edge_tts

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common.pipeline_utils import (
    download_project_file,
    load_manifest,
    projects_with_status,
    save_manifest,
    upload_project_file,
)

# Rotate through these free edge-tts voices for characters that don't have one assigned.
# Full voice list: `edge-tts --list-voices`
DEFAULT_VOICES = [
    "en-US-GuyNeural", "en-US-JennyNeural", "en-US-AriaNeural",
    "en-US-DavisNeural", "en-US-JaneNeural",
]
NARRATOR_VOICE = "en-US-ChristopherNeural"


def voice_for_character(manifest: dict, character_id: str) -> str:
    for i, c in enumerate(manifest["characters"]):
        if c["id"] == character_id:
            return c.get("voice") or DEFAULT_VOICES[i % len(DEFAULT_VOICES)]
    return NARRATOR_VOICE


def parse_dialogue(dialogue: str | None) -> tuple[str | None, str]:
    """'Renn: \"Why so slow?\"' -> ('fox_01'-ish name, spoken line). Falls back to
    treating the whole string as narration if it doesn't match Speaker: "line" form."""
    if not dialogue:
        return None, ""
    if ":" in dialogue:
        speaker, line = dialogue.split(":", 1)
        return speaker.strip(), line.strip().strip('"')
    return None, dialogue.strip()


async def synthesize(text: str, voice: str, out_path: str) -> None:
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(out_path)


def ffprobe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def mux_shot(clip_path: str, narration_path: str | None, target_duration: float, out_path: str) -> None:
    """Combine a silent video clip with its narration track, padding/trimming
    audio to match the clip length so timing never drifts."""
    if narration_path:
        cmd = [
            "ffmpeg", "-y", "-i", clip_path, "-i", narration_path,
            "-filter_complex",
            f"[1:a]apad=whole_dur={target_duration}[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-shortest", out_path,
        ]
    else:
        # silent shot: add a silent audio track so every shot has an audio stream
        # (needed for a clean concat later)
        cmd = [
            "ffmpeg", "-y", "-i", clip_path,
            "-f", "lavfi", "-t", str(target_duration), "-i", "anullsrc=r=44100:cl=stereo",
            "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-shortest", out_path,
        ]
    subprocess.run(cmd, check=True, capture_output=True)


def concat_clips(clip_paths: list[str], out_path: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for p in clip_paths:
            f.write(f"file '{p}'\n")
        list_path = f.name
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
         "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", out_path],
        check=True, capture_output=True,
    )


def duck_music_under(video_path: str, music_path: str | None, out_path: str) -> None:
    if not music_path:
        subprocess.run(["cp", video_path, out_path], check=True)
        return
    duration = ffprobe_duration(video_path)
    cmd = [
        "ffmpeg", "-y", "-i", video_path, "-stream_loop", "-1", "-i", music_path,
        "-filter_complex",
        f"[1:a]atrim=0:{duration},volume=0.15[music];[0:a][music]amix=inputs=2:duration=first[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def write_srt(manifest: dict, out_path: str) -> None:
    lines = []
    t = 0.0
    idx = 1
    for shot in manifest["shots"]:
        dur = shot["duration_seconds"]
        _, text = parse_dialogue(shot.get("dialogue"))
        if text:
            start = _fmt_ts(t)
            end = _fmt_ts(t + dur)
            lines.append(f"{idx}\n{start} --> {end}\n{text}\n")
            idx += 1
        t += dur
    Path(out_path).write_text("\n".join(lines))


def _fmt_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def process_project(project_id: str) -> None:
    print(f"=== assembling {project_id} ===")
    manifest = load_manifest(project_id)
    workdir = Path(tempfile.mkdtemp(prefix=f"{project_id}_"))
    muxed_clips = []

    for shot in manifest["shots"]:
        clip_local = download_project_file(project_id, f"generated/{shot['shot_id']}_clip.mp4")
        speaker, line = parse_dialogue(shot.get("dialogue"))
        narration_path = None

        if line:
            char_id = None
            for cid in shot.get("characters_present", []):
                for c in manifest["characters"]:
                    if c["id"] == cid and c["name"].lower() == (speaker or "").lower():
                        char_id = cid
            voice = voice_for_character(manifest, char_id) if char_id else NARRATOR_VOICE
            narration_path = str(workdir / f"{shot['shot_id']}_narration.mp3")
            asyncio.run(synthesize(line, voice, narration_path))

        muxed_path = str(workdir / f"{shot['shot_id']}_muxed.mp4")
        mux_shot(clip_local, narration_path, shot["duration_seconds"], muxed_path)
        muxed_clips.append(muxed_path)
        shot["audio"]["narration_status"] = "done" if narration_path else "skipped"
        print(f"  muxed {shot['shot_id']}")

    concat_path = str(workdir / "concat.mp4")
    concat_clips(muxed_clips, concat_path)

    music_local = None
    if manifest["assembly"].get("music_track"):
        try:
            music_local = download_project_file(project_id, manifest["assembly"]["music_track"])
        except Exception:
            print("  no music track found in repo, skipping music bed")

    final_path = str(workdir / "final.mp4")
    duck_music_under(concat_path, music_local, final_path)

    srt_path = str(workdir / "captions.srt")
    write_srt(manifest, srt_path)

    upload_project_file(project_id, final_path, "final.mp4")
    upload_project_file(project_id, srt_path, "captions.srt")

    manifest["assembly"]["status"] = "done"
    manifest["assembly"]["output_path"] = "final.mp4"
    manifest["assembly"]["subtitles_path"] = "captions.srt"
    manifest["status"] = "awaiting_review"
    save_manifest(project_id, manifest)
    print(f"  done -> final.mp4, status=awaiting_review")


def main():
    if len(sys.argv) > 1:
        process_project(sys.argv[1])
        return

    targets = projects_with_status(["status"], "rendered_pending_assembly")
    if not targets:
        print("No projects ready for assembly.")
        return
    for project_id, _ in targets:
        try:
            process_project(project_id)
        except Exception as e:
            print(f"FAILED {project_id}: {e}")


if __name__ == "__main__":
    main()
