"""
VaultMind Auto Video Pipeline — v2
Upgrades:
  - AssemblyAI for accurate word-level timestamps + sentence boundaries
  - pydub silence detection for natural scene cut points
  - MoviePy for clip assembly with crossfade / fade transitions
  - Shotstack (optional) for cloud-rendered final output
"""

import os
import json
import time
import base64
import pickle
import random
import logging
import tempfile
import textwrap
from io import BytesIO
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ── Audio ──────────────────────────────────────────────────────────────
from pydub import AudioSegment
from pydub.silence import detect_silence, detect_nonsilent

# AssemblyAI — raw HTTP (no SDK, avoids speech_model versioning issues)

# ── MoviePy 2.x ────────────────────────────────────────────────────────
from moviepy import (
    VideoFileClip,
    ImageClip,
    AudioFileClip,
    CompositeVideoClip,
    concatenate_videoclips,
    ColorClip,
    concatenate_audioclips,
)
from moviepy.video.fx import FadeIn, FadeOut, CrossFadeIn
from moviepy.audio.fx import AudioFadeIn, AudioFadeOut

# ── Script generation ──────────────────────────────────────────────────
from groq import Groq
import edge_tts
import asyncio

# ── YouTube ────────────────────────────────────────────────────────────
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("vaultmind")

# ══════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════

VIDEO_W, VIDEO_H = 720, 1280
FPS              = 30
TARGET_DURATION  = 80        # seconds, minimum
MIN_SCENES       = 16
TRANSITION_DUR   = 0.4       # seconds for crossfade between scenes
FADE_IN_DUR      = 0.3
FADE_OUT_DUR     = 0.5
MUSIC_DUCK_DB    = -18       # background music level vs voice

NICHES = {
    "reddit":      {"color": "#FF4500", "font_color": "#FFFFFF", "bg": "#1A1A2E"},
    "dating":      {"color": "#FF69B4", "font_color": "#FFFFFF", "bg": "#1A1A2E"},
    "rich":        {"color": "#FFD700", "font_color": "#FFFFFF", "bg": "#0D0D0D"},
    "lifehack":    {"color": "#00FF88", "font_color": "#000000", "bg": "#111111"},
    "fact":        {"color": "#00BFFF", "font_color": "#FFFFFF", "bg": "#0A0A1A"},
    "scary":       {"color": "#FF2222", "font_color": "#FFFFFF", "bg": "#050505"},
    "motivation":  {"color": "#FF8C00", "font_color": "#FFFFFF", "bg": "#0D0D0D"},
    "conspiracy":  {"color": "#9B59B6", "font_color": "#FFFFFF", "bg": "#070713"},
    "learn":       {"color": "#1ABC9C", "font_color": "#FFFFFF", "bg": "#0D1B2A"},
}

GAMEPLAY_PATH = os.getenv("GAMEPLAY_PATH", "gameplay_bg.mp4")
MUSIC_PATH    = os.getenv("MUSIC_PATH",    "bg_music.mp3")


# ══════════════════════════════════════════════════════════════════════
# STEP 1 — SCRIPT GENERATION (unchanged, robust retry)
# ══════════════════════════════════════════════════════════════════════

def generate_script(niche: str) -> dict:
    """Generate a viral short-form script via Groq (llama-3.3-70b)."""
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    system = (
        "You are a viral YouTube Shorts / TikTok scriptwriter. "
        "Return ONLY valid JSON with keys: title (str), hook (str), "
        "scenes (list of {text: str, duration: int}) — at least 16 scenes, "
        "total narration ~80 seconds. No markdown, no preamble."
    )
    prompt = (
        f"Write a viral {niche} short video script. "
        "Hook must be under 5 words and extremely curiosity-driving. "
        "Each scene: 1-3 short punchy sentences."
    )
    for attempt in range(5):
        try:
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "system", "content": system},
                           {"role": "user",   "content": prompt}],
                temperature=0.9,
                max_tokens=2000,
            )
            raw = resp.choices[0].message.content.strip()
            data = json.loads(raw)
            if len(data.get("scenes", [])) >= MIN_SCENES:
                log.info("Script generated: %d scenes", len(data["scenes"]))
                return data
            log.warning("Too few scenes (%d), retrying…", len(data.get("scenes", [])))
        except Exception as e:
            log.warning("Script attempt %d failed: %s", attempt + 1, e)
        time.sleep(2 ** attempt)
    raise RuntimeError("Failed to generate a valid script after 5 attempts")


# ══════════════════════════════════════════════════════════════════════
# STEP 2 — VOICEOVER GENERATION
# ══════════════════════════════════════════════════════════════════════

def _elevenlabs_tts(text: str, out_path: str) -> bool:
    """ElevenLabs Brian — returns True on success."""
    key      = os.getenv("ELEVENLABS_KEY")
    voice_id = os.getenv("ELEVEN_VOICE_ID", "nPczCjzI2devNBz1zQrb")
    if not key:
        return False
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {"xi-api-key": key, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": "eleven_turbo_v2",
        "voice_settings": {"stability": 0.4, "similarity_boost": 0.75},
    }
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    if r.status_code == 200:
        Path(out_path).write_bytes(r.content)
        log.info("ElevenLabs TTS: OK")
        return True
    log.warning("ElevenLabs failed (%d), falling back to edge-tts", r.status_code)
    return False


def _edgetts_tts(text: str, out_path: str) -> None:
    """edge-tts fallback (Guy Neural)."""
    async def _run():
        comm = edge_tts.Communicate(text, "en-US-GuyNeural")
        await comm.save(out_path)
    asyncio.run(_run())
    log.info("edge-tts TTS: OK")


def generate_voiceover(full_text: str, out_path: str) -> None:
    """Generate final voiceover MP3."""
    if not _elevenlabs_tts(full_text, out_path):
        _edgetts_tts(full_text, out_path)


# ══════════════════════════════════════════════════════════════════════
# STEP 3 — ASSEMBLYAI TRANSCRIPTION (NEW)
#   Replaces edge-tts WordBoundary for timestamps.
#   Returns word-level timestamps + utterances for smart cut detection.
# ══════════════════════════════════════════════════════════════════════

def transcribe_with_assemblyai(audio_path: str) -> dict:
    """
    Upload audio to AssemblyAI via raw HTTP (no SDK).
    Avoids all SDK versioning issues with speech_model vs speech_models.

    Returns:
      words:      [{text, start_ms, end_ms, confidence}]
      utterances: [{text, start_ms, end_ms}]
      pauses:     [{start_ms, end_ms}]
      total_ms:   int
    """
    api_key = os.environ["ASSEMBLYAI_API_KEY"]
    headers = {"authorization": api_key}

    # ── 1. Upload audio file ───────────────────────────────────────────
    log.info("Uploading audio to AssemblyAI…")
    with open(audio_path, "rb") as f:
        upload_r = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers=headers,
            data=f,
            timeout=60,
        )
    upload_r.raise_for_status()
    audio_url = upload_r.json()["upload_url"]
    log.info("Audio uploaded: %s", audio_url)

    # ── 2. Submit transcription job ────────────────────────────────────
    json_headers = {**headers, "content-type": "application/json"}
    submit_r = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        headers=json_headers,
        json={
            "audio_url":    audio_url,
            "speech_models": ["universal-2"],   # new API field (plural)
            "punctuate":    True,
            "format_text":  True,
            "disfluencies": False,
        },
        timeout=30,
    )
    submit_r.raise_for_status()
    transcript_id = submit_r.json()["id"]
    log.info("AssemblyAI job submitted: %s", transcript_id)

    # ── 3. Poll until complete ─────────────────────────────────────────
    poll_url = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
    for attempt in range(120):          # max 6 minutes
        time.sleep(3)
        poll_r  = requests.get(poll_url, headers=headers, timeout=10)
        poll_r.raise_for_status()
        result  = poll_r.json()
        status  = result["status"]
        if attempt % 10 == 0:
            log.info("AssemblyAI status: %s (%ds elapsed)", status, attempt * 3)
        if status == "completed":
            break
        if status == "error":
            raise RuntimeError(f"AssemblyAI transcription error: {result.get('error')}")
    else:
        raise RuntimeError("AssemblyAI polling timed out after 6 minutes")

    # ── 4. Parse words ─────────────────────────────────────────────────
    raw_words = result.get("words") or []
    words = [
        {
            "text":       w["text"],
            "start_ms":   w["start"],
            "end_ms":     w["end"],
            "confidence": w.get("confidence", 1.0),
        }
        for w in raw_words
    ]

    # ── 5. Build utterances (split on sentence-ending punctuation) ─────
    utterances = []
    current    = []
    for w in words:
        current.append(w)
        if w["text"].rstrip().endswith((".", "!", "?")):
            utterances.append({
                "text":     " ".join(x["text"] for x in current),
                "start_ms": current[0]["start_ms"],
                "end_ms":   current[-1]["end_ms"],
            })
            current = []
    if current:
        utterances.append({
            "text":     " ".join(x["text"] for x in current),
            "start_ms": current[0]["start_ms"],
            "end_ms":   current[-1]["end_ms"],
        })

    # ── 6. Detect pauses between utterances ───────────────────────────
    pauses = []
    for i in range(len(utterances) - 1):
        gap_start = utterances[i]["end_ms"]
        gap_end   = utterances[i + 1]["start_ms"]
        if gap_end - gap_start >= 200:
            pauses.append({"start_ms": gap_start, "end_ms": gap_end})

    total_ms = words[-1]["end_ms"] if words else 0
    log.info(
        "AssemblyAI: %d words, %d utterances, %d pauses (total %.1fs)",
        len(words), len(utterances), len(pauses), total_ms / 1000,
    )
    return {
        "words":      words,
        "utterances": utterances,
        "pauses":     pauses,
        "total_ms":   total_ms,
    }


# ══════════════════════════════════════════════════════════════════════
# STEP 4 — SILENCE DETECTION & SMART CUT POINTS (NEW)
#   Uses pydub to find actual silence in audio.
#   Merges with AssemblyAI pause data for maximum accuracy.
# ══════════════════════════════════════════════════════════════════════

def detect_cut_points(
    audio_path:  str,
    aai_data:    dict,
    n_scenes:    int,
    *,
    silence_thresh_db: int = -40,
    min_silence_ms:    int = 300,
) -> list[float]:
    """
    Returns a sorted list of cut-point timestamps (in seconds) where
    scene transitions should happen.

    Strategy (layered):
    1. pydub finds silence regions in the audio waveform.
    2. AssemblyAI pause data confirms / adds natural speech gaps.
    3. We pick `n_scenes - 1` evenly-spaced candidates from these
       natural boundaries (no hard cuts on mid-word frames).
    """
    audio  = AudioSegment.from_file(audio_path)
    total_ms = len(audio)

    # ── pydub silence regions ──────────────────────────────────────────
    pydub_silences = detect_silence(
        audio,
        min_silence_len=min_silence_ms,
        silence_thresh=silence_thresh_db,
    )
    # Convert to midpoint timestamps (ms)
    pydub_mids = [(s + e) // 2 for s, e in pydub_silences]
    log.info("pydub: %d silence regions found", len(pydub_silences))

    # ── AssemblyAI pause midpoints ─────────────────────────────────────
    aai_mids = [
        (p["start_ms"] + p["end_ms"]) // 2
        for p in aai_data.get("pauses", [])
    ]

    # ── Merge and deduplicate (within 500 ms window) ───────────────────
    all_candidates_ms = sorted(set(pydub_mids + aai_mids))
    merged = []
    last = -9999
    for t in all_candidates_ms:
        if t - last > 500:          # keep only if >500ms from previous
            merged.append(t)
            last = t

    # Exclude first/last 3 s (avoid cuts at start/end)
    merged = [t for t in merged if 3000 < t < total_ms - 3000]

    log.info("Merged cut candidates: %d", len(merged))

    # ── Select n_scenes-1 cuts spread across the audio ────────────────
    n_cuts = n_scenes - 1
    if len(merged) <= n_cuts:
        chosen_ms = merged
    else:
        # Pick candidates closest to evenly-spaced ideal positions
        ideal_spacing = total_ms / n_scenes
        chosen_ms = []
        for i in range(1, n_scenes):
            ideal = i * ideal_spacing
            closest = min(merged, key=lambda t: abs(t - ideal))
            chosen_ms.append(closest)
            merged.remove(closest)   # don't pick same point twice

    cut_seconds = sorted(t / 1000.0 for t in chosen_ms)
    log.info("Final cut points (s): %s", [f"{c:.2f}" for c in cut_seconds])
    return cut_seconds


# ══════════════════════════════════════════════════════════════════════
# STEP 5 — FRAME / SCENE IMAGE GENERATION (PIL, unchanged structure)
# ══════════════════════════════════════════════════════════════════════

def _load_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def render_scene_image(
    scene_text: str,
    niche:      str,
    scene_idx:  int,
    pexels_img: Optional[Image.Image] = None,
) -> Image.Image:
    """
    Render a single scene as a PIL Image (720×1280).
    Background: gameplay/pexels composite, text overlay with pill caption.
    """
    cfg   = NICHES[niche]
    frame = Image.new("RGB", (VIDEO_W, VIDEO_H), cfg["bg"])
    draw  = ImageDraw.Draw(frame)

    # ── Pexels background card (top 55%) ──────────────────────────────
    if pexels_img:
        card_h = int(VIDEO_H * 0.55)
        pexels_img = pexels_img.convert("RGB").resize(
            (VIDEO_W, card_h), Image.LANCZOS
        )
        # Crop to exact aspect ratio
        frame.paste(pexels_img, (0, 0))
        # Gradient overlay for text readability
        overlay = Image.new("RGBA", (VIDEO_W, card_h), (0, 0, 0, 0))
        for y in range(card_h):
            alpha = int(180 * (y / card_h))
            ImageDraw.Draw(overlay).line(
                [(0, y), (VIDEO_W, y)], fill=(0, 0, 0, alpha)
            )
        frame.paste(overlay, (0, 0), overlay)

    # ── Niche accent bar ───────────────────────────────────────────────
    bar_y = int(VIDEO_H * 0.55)
    r, g, b = tuple(int(cfg["color"].lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
    draw.rectangle([(0, bar_y), (VIDEO_W, bar_y + 6)], fill=(r, g, b))

    # ── Scene text (word-wrapped, large font) ─────────────────────────
    font_large = _load_font(52)
    font_small = _load_font(34)
    text_area_top = bar_y + 40
    padding = 40
    max_w   = VIDEO_W - padding * 2

    lines = textwrap.wrap(scene_text, width=28)
    y_cursor = text_area_top
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font_large)
        w    = bbox[2] - bbox[0]
        h    = bbox[3] - bbox[1]
        x    = (VIDEO_W - w) // 2
        # Pill background
        pad = 12
        pill_box = [x - pad, y_cursor - pad, x + w + pad, y_cursor + h + pad]
        draw.rounded_rectangle(pill_box, radius=16, fill=(0, 0, 0, 180))
        draw.text((x, y_cursor), line, font=font_large, fill=cfg["font_color"])
        y_cursor += h + 20

    # ── Scene counter badge ────────────────────────────────────────────
    badge_font = _load_font(28)
    badge_text = f"{scene_idx + 1}"
    bx, by = 24, bar_y + 14
    draw.rounded_rectangle(
        [bx, by, bx + 52, by + 42], radius=10,
        fill=(r, g, b)
    )
    draw.text((bx + 10, by + 6), badge_text, font=badge_font, fill="#FFFFFF")

    return frame


# ══════════════════════════════════════════════════════════════════════
# STEP 6 — PEXELS IMAGE FETCH
# ══════════════════════════════════════════════════════════════════════

def fetch_pexels_image(query: str) -> Optional[Image.Image]:
    key = os.getenv("PEXELS_KEY")
    if not key:
        return None
    url = "https://api.pexels.com/v1/search"
    r   = requests.get(
        url,
        headers={"Authorization": key},
        params={"query": query, "per_page": 5, "orientation": "portrait"},
        timeout=15,
    )
    if r.status_code != 200:
        return None
    photos = r.json().get("photos", [])
    if not photos:
        return None
    photo_url = random.choice(photos)["src"]["large"]
    img_r = requests.get(photo_url, timeout=15)
    return Image.open(BytesIO(img_r.content))


# ══════════════════════════════════════════════════════════════════════
# STEP 7 — MOVIEPY VIDEO ASSEMBLY (NEW)
#   Replaces raw PIL frame-by-frame rendering.
#   Each scene → ImageClip.  Gameplay loops behind everything.
#   Transitions: crossfade between adjacent clips.
# ══════════════════════════════════════════════════════════════════════

def _scene_image_to_clip(
    img:        Image.Image,
    duration:   float,
    *,
    ken_burns:  bool = True,
) -> ImageClip:
    """
    Convert a PIL image to a MoviePy ImageClip.
    Optionally apply a slow Ken Burns zoom (scale 1.0 → 1.08 over duration).
    """
    arr  = np.array(img)
    clip = ImageClip(arr).with_duration(duration)

    if ken_burns and duration > 1.5:
        def make_zoomed_frame(t):
            scale   = 1.0 + 0.08 * (t / duration)
            h, w    = arr.shape[:2]
            new_w   = int(w * scale)
            new_h   = int(h * scale)
            resized = np.array(
                Image.fromarray(arr).resize((new_w, new_h), Image.LANCZOS)
            )
            x0 = (new_w - w) // 2
            y0 = (new_h - h) // 2
            return resized[y0:y0 + h, x0:x0 + w]

        from moviepy.video.VideoClip import VideoClip
        ken_clip = VideoClip(make_zoomed_frame, duration=duration)
        return ken_clip.with_fps(FPS)

    return clip.with_fps(FPS)


def build_video_moviepy(
    scenes:         list[dict],          # [{text, duration, pexels_query}]
    niche:          str,
    audio_path:     str,
    aai_data:       dict,
    cut_points_s:   list[float],
    output_path:    str,
    *,
    use_gameplay_bg: bool = True,
) -> str:
    """
    Full MoviePy render pipeline:
      1. Render each scene as PIL → ImageClip (Ken Burns)
      2. Add crossfade transitions between clips
      3. Composite over looped gameplay background
      4. Mix voice + ducked background music
      5. Export final MP4

    Returns output_path.
    """
    log.info("Building video with MoviePy (%d scenes)…", len(scenes))

    # ── Scene durations from cut points ───────────────────────────────
    total_audio_s = aai_data["total_ms"] / 1000.0
    boundaries    = [0.0] + cut_points_s + [total_audio_s]
    durations     = [boundaries[i+1] - boundaries[i]
                     for i in range(len(boundaries) - 1)]
    # Pad / trim scenes list to match duration slots
    while len(scenes) < len(durations):
        scenes.append(scenes[-1])
    scenes = scenes[:len(durations)]

    # ── Render scene images ────────────────────────────────────────────
    scene_clips = []
    for idx, (scene, dur) in enumerate(zip(scenes, durations)):
        pexels_img = fetch_pexels_image(
            scene.get("pexels_query", scene["text"][:40])
        )
        img  = render_scene_image(scene["text"], niche, idx, pexels_img)
        clip = _scene_image_to_clip(img, max(dur, 0.5))
        scene_clips.append(clip)
        log.info("  Scene %d/%d rendered (%.1fs)", idx + 1, len(scenes), dur)

    # ── Apply crossfade transitions between clips ──────────────────────
    #   MoviePy 2.x: use .with_effects([CrossFadeIn(dur)]) + negative padding
    transitioned = [scene_clips[0]]
    for clip in scene_clips[1:]:
        clip_with_fade = clip.with_effects([CrossFadeIn(TRANSITION_DUR)])
        transitioned.append(clip_with_fade)

    final_video = concatenate_videoclips(
        transitioned,
        method="compose",
        padding=-TRANSITION_DUR,
    )

    # ── Global fade in / out ───────────────────────────────────────────
    final_video = final_video.with_effects([FadeIn(FADE_IN_DUR), FadeOut(FADE_OUT_DUR)])

    # ── Gameplay background composite ─────────────────────────────────
    if use_gameplay_bg and Path(GAMEPLAY_PATH).exists():
        log.info("Compositing gameplay background…")
        bg          = VideoFileClip(GAMEPLAY_PATH, audio=False)
        bg_duration = final_video.duration
        loops_needed = int(np.ceil(bg_duration / bg.duration))
        bg_looped = concatenate_videoclips([bg] * loops_needed).subclipped(0, bg_duration)
        bg_looped = bg_looped.resized((VIDEO_W, VIDEO_H))
        bg_dark   = bg_looped.image_transform(
            lambda frame: (frame * 0.4).astype(np.uint8)
        )
        final_video = CompositeVideoClip(
            [bg_dark, final_video],
            size=(VIDEO_W, VIDEO_H),
        ).with_duration(bg_duration)
    else:
        log.warning("Gameplay background not found, skipping composite")

    # ── Mix audio ──────────────────────────────────────────────────────
    log.info("Mixing audio…")
    voice_clip = AudioFileClip(audio_path).subclipped(0, final_video.duration)

    if Path(MUSIC_PATH).exists():
        music_raw    = AudioSegment.from_file(MUSIC_PATH)
        music_ducked = music_raw + MUSIC_DUCK_DB
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            music_out = f.name
        music_ducked.export(music_out, format="mp3")
        music_clip = AudioFileClip(music_out)
        if music_clip.duration < final_video.duration:
            repeats    = int(np.ceil(final_video.duration / music_clip.duration))
            music_clip = concatenate_audioclips([music_clip] * repeats)
        music_clip  = music_clip.subclipped(0, final_video.duration)
        from moviepy import CompositeAudioClip
        mixed_audio = CompositeAudioClip([voice_clip, music_clip])
    else:
        mixed_audio = voice_clip

    final_video = final_video.with_audio(mixed_audio)

    # ── Export ─────────────────────────────────────────────────────────
    log.info("Exporting video → %s", output_path)
    final_video.write_videofile(
        output_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        temp_audiofile="temp_audio.m4a",
        remove_temp=True,
        preset="medium",
        ffmpeg_params=["-crf", "23", "-movflags", "+faststart"],
        logger=None,    # suppress MoviePy progress bar in CI
    )
    log.info("Video export complete: %s", output_path)
    return output_path


# ══════════════════════════════════════════════════════════════════════
# STEP 8 — ASSEMBLYAI CAPTION SYNC
#   Build per-word caption data from AssemblyAI timestamps.
#   This replaces the edge-tts WordBoundary approach.
# ══════════════════════════════════════════════════════════════════════

def build_caption_data(aai_data: dict) -> list[dict]:
    """
    Returns a list of caption events:
      {word, start_s, end_s, highlight: bool}
    Ready for frame-level burn-in if needed (or SRT export).
    """
    captions = []
    for w in aai_data["words"]:
        captions.append({
            "word":      w["text"],
            "start_s":   w["start_ms"] / 1000.0,
            "end_s":     w["end_ms"]   / 1000.0,
            "highlight": w.get("confidence", 1.0) > 0.85,
        })
    return captions


def export_srt(captions: list[dict], out_path: str) -> None:
    """Export captions as .srt subtitle file (for YouTube auto-upload)."""
    def _fmt(s: float) -> str:
        h  = int(s // 3600)
        m  = int((s % 3600) // 60)
        ss = s % 60
        return f"{h:02d}:{m:02d}:{ss:06.3f}".replace(".", ",")

    lines = []
    # Group into ~5-word chunks for readable subtitle blocks
    chunk, start = [], None
    for i, c in enumerate(captions):
        if start is None:
            start = c["start_s"]
        chunk.append(c["word"])
        if len(chunk) >= 5 or i == len(captions) - 1:
            lines.append(f"{len(lines) + 1}")
            lines.append(f"{_fmt(start)} --> {_fmt(c['end_s'])}")
            lines.append(" ".join(chunk))
            lines.append("")
            chunk, start = [], None

    Path(out_path).write_text("\n".join(lines), encoding="utf-8")
    log.info("SRT exported: %s", out_path)


# ══════════════════════════════════════════════════════════════════════
# STEP 9 — SHOTSTACK OPTIONAL RENDER (NEW)
#   Cloud-based final render for maximum quality.
#   Falls back gracefully if SHOTSTACK_API_KEY not set.
# ══════════════════════════════════════════════════════════════════════

def render_via_shotstack(
    scene_image_urls: list[str],   # publicly accessible image URLs
    audio_url:        str,         # publicly accessible audio URL
    cut_points_s:     list[float],
    niche:            str,
    output_path:      str,
) -> Optional[str]:
    """
    Submit a Shotstack render job using scene images + audio.
    Returns local path of downloaded result, or None if unavailable.

    Requirements:
      - SHOTSTACK_API_KEY env var
      - scene_image_urls must be publicly accessible (e.g. Cloudflare R2 / S3)
    """
    api_key = os.getenv("SHOTSTACK_API_KEY")
    if not api_key:
        log.info("SHOTSTACK_API_KEY not set — skipping Shotstack render")
        return None

    cfg        = NICHES[niche]
    base_url   = "https://api.shotstack.io/stage/render"
    headers    = {"x-api-key": api_key, "Content-Type": "application/json"}

    # Build timeline clips from scene images
    boundaries = [0.0] + cut_points_s
    clips      = []
    for i, (img_url, start_s) in enumerate(zip(scene_image_urls, boundaries)):
        end_s    = cut_points_s[i] if i < len(cut_points_s) else None
        duration = (end_s - start_s) if end_s else 5.0
        clips.append({
            "asset": {"type": "image", "src": img_url},
            "start":    start_s,
            "length":   duration,
            "effect":   "zoomIn",         # Ken Burns in Shotstack
            "transition": {
                "in":  "fade",
                "out": "fade",
            },
        })

    # Audio track
    audio_clip = {
        "asset": {"type": "audio", "src": audio_url},
        "start": 0,
        "length": boundaries[-1] if boundaries else 80,
        "volume": 1.0,
    }

    payload = {
        "timeline": {
            "background": cfg["bg"],
            "tracks": [
                {"clips": clips},
                {"clips": [audio_clip]},
            ],
        },
        "output": {
            "format":     "mp4",
            "resolution": "hd",
            "aspectRatio": "9:16",
            "fps":         FPS,
        },
    }

    log.info("Submitting Shotstack render job…")
    r = requests.post(base_url, headers=headers, json=payload, timeout=30)
    if r.status_code not in (200, 201):
        log.error("Shotstack submit failed: %d %s", r.status_code, r.text)
        return None

    render_id = r.json()["response"]["id"]
    poll_url  = f"{base_url}/{render_id}"
    log.info("Shotstack render ID: %s — polling…", render_id)

    for _ in range(60):   # poll up to 5 minutes
        time.sleep(5)
        status_r = requests.get(poll_url, headers=headers, timeout=10)
        status   = status_r.json()["response"]["status"]
        log.info("Shotstack status: %s", status)
        if status == "done":
            video_url = status_r.json()["response"]["url"]
            video_data = requests.get(video_url, timeout=120).content
            Path(output_path).write_bytes(video_data)
            log.info("Shotstack render downloaded → %s", output_path)
            return output_path
        if status in ("failed", "timed out"):
            log.error("Shotstack render failed")
            return None

    log.error("Shotstack polling timed out")
    return None


# ══════════════════════════════════════════════════════════════════════
# STEP 10 — YOUTUBE UPLOAD (unchanged)
# ══════════════════════════════════════════════════════════════════════

def upload_youtube(
    video_path:  str,
    title:       str,
    description: str,
    srt_path:    Optional[str] = None,
) -> Optional[str]:
    """Upload to YouTube Shorts as a scheduled private video."""
    token_b64 = os.getenv("YOUTUBE_TOKEN_B64")
    if not token_b64:
        log.warning("YOUTUBE_TOKEN_B64 not set — skipping upload")
        return None

    creds_data = pickle.loads(base64.b64decode(token_b64))
    if isinstance(creds_data, Credentials):
        creds = creds_data                                      # already unpickled
    elif isinstance(creds_data, str):
        creds = Credentials.from_authorized_user_info(json.loads(creds_data))
    else:
        creds = Credentials.from_authorized_user_info(creds_data)
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

    # Schedule publish +1 day
    publish_at = (datetime.utcnow() + timedelta(days=1)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    body = {
        "snippet": {
            "title":       title[:100],
            "description": description,
            "tags":        ["shorts", "viral"],
            "categoryId":  "22",
        },
        "status": {
            "privacyStatus":       "private",
            "publishAt":           publish_at,
            "selfDeclaredMadeForKids": False,
        },
    }
    media   = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media
    )
    response = None
    while response is None:
        _, response = request.next_chunk()

    video_id = response["id"]
    video_url = f"https://youtu.be/{video_id}"
    log.info("YouTube upload complete: %s", video_url)

    # Upload SRT captions if provided
    if srt_path and Path(srt_path).exists():
        youtube.captions().insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId":      video_id,
                    "language":     "en",
                    "name":         "Auto",
                    "isDraft":      False,
                }
            },
            media_body=MediaFileUpload(srt_path, mimetype="text/plain"),
        ).execute()
        log.info("Captions uploaded")

    return video_url


# ══════════════════════════════════════════════════════════════════════
# STEP 11 — DASHBOARD UPDATE
# ══════════════════════════════════════════════════════════════════════

def update_dashboard(
    niche:     str,
    title:     str,
    video_url: Optional[str],
    run_ok:    bool,
) -> None:
    dash_path = Path("dashboard.json")
    data = json.loads(dash_path.read_text()) if dash_path.exists() else {"videos": []}
    data["videos"].insert(0, {
        "niche":     niche,
        "title":     title,
        "url":       video_url,
        "ok":        run_ok,
        "timestamp": datetime.utcnow().isoformat(),
    })
    data["videos"] = data["videos"][:50]   # keep last 50
    dash_path.write_text(json.dumps(data, indent=2))
    log.info("dashboard.json updated")


# ══════════════════════════════════════════════════════════════════════
# MAIN PIPELINE ORCHESTRATION
# ══════════════════════════════════════════════════════════════════════

def run_pipeline(niche: Optional[str] = None) -> None:
    niche = niche or random.choice(list(NICHES.keys()))
    log.info("═══ VaultMind Pipeline v2 — niche: %s ═══", niche)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # 1. Script
        script    = generate_script(niche)
        scenes    = script["scenes"]
        title     = script["title"]
        full_text = " ".join(s["text"] for s in scenes)
        log.info("Title: %s", title)

        # 2. Voiceover
        audio_path = str(tmp / "voice.mp3")
        generate_voiceover(full_text, audio_path)

        # 3. AssemblyAI transcription
        aai_data = transcribe_with_assemblyai(audio_path)

        # 4. Smart cut points (pydub + AssemblyAI)
        cut_points = detect_cut_points(
            audio_path,
            aai_data,
            n_scenes=len(scenes),
        )

        # 5. Build captions + SRT
        captions = build_caption_data(aai_data)
        srt_path = str(tmp / "captions.srt")
        export_srt(captions, srt_path)

        # 6. MoviePy video render
        video_path = str(tmp / "output.mp4")
        build_video_moviepy(
            scenes      = scenes,
            niche       = niche,
            audio_path  = audio_path,
            aai_data    = aai_data,
            cut_points_s= cut_points,
            output_path = video_path,
        )

        # 7. (Optional) Shotstack cloud render — uncomment to enable
        # shotstack_result = render_via_shotstack(...)
        # if shotstack_result:
        #     video_path = shotstack_result

        # 8. YouTube upload
        description = (
            f"{title}\n\n"
            f"#shorts #{niche} #viral #vaultmind"
        )
        video_url = upload_youtube(video_path, title, description, srt_path)

        # 9. Dashboard
        update_dashboard(niche, title, video_url, run_ok=True)

    log.info("═══ Pipeline complete: %s ═══", video_url or "no URL")


if __name__ == "__main__":
    import sys
    niche_arg = sys.argv[1] if len(sys.argv) > 1 else None
    run_pipeline(niche_arg)
