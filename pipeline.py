"""
VaultMind Auto Video Pipeline — v3
Upgrades over v2:
  - 1080x1920 full-HD resolution
  - OpenAI TTS (onyx) as primary, ElevenLabs → OpenAI → edge-tts fallback chain
  - Auto-download background music via Pixabay API if file missing
  - Programmatic SFX synthesis (whoosh + impact) — no extra API keys
  - Complete visual redesign: full-bleed BG, dark gradient, modern captions
  - SFX injected at every scene cut in MoviePy audio mix
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
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import scipy.signal

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

VIDEO_W, VIDEO_H = 1080, 1920
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
SFX_WHOOSH    = "sfx_whoosh.mp3"
SFX_IMPACT    = "sfx_impact.mp3"
PIXABAY_KEY   = os.getenv("PIXABAY_KEY", "")


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
    """ElevenLabs — tries current user's voices, returns True on success."""
    key      = os.getenv("ELEVENLABS_KEY")
    voice_id = os.getenv("ELEVEN_VOICE_ID", "")
    if not key:
        return False
    # Discover first available voice if no ID set
    if not voice_id:
        r = requests.get(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": key}, timeout=10,
        )
        if r.status_code == 200:
            voices = r.json().get("voices", [])
            if voices:
                voice_id = voices[0]["voice_id"]
                log.info("ElevenLabs: using voice %s (%s)", voice_id, voices[0].get("name"))
    if not voice_id:
        return False
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": "eleven_turbo_v2_5",
            "voice_settings": {
                "stability": 0.35,
                "similarity_boost": 0.80,
                "style": 0.45,           # expressiveness
                "use_speaker_boost": True,
            },
        },
        timeout=90,
    )
    if r.status_code == 200:
        Path(out_path).write_bytes(r.content)
        log.info("ElevenLabs TTS: OK")
        return True
    log.warning("ElevenLabs failed (%d)", r.status_code)
    return False


def _openai_tts(text: str, out_path: str) -> bool:
    """OpenAI TTS (onyx voice) — much more expressive than edge-tts."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return False
    r = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": "tts-1-hd",
            "voice": "onyx",       # deep, authoritative — best for Shorts
            "input": text,
            "speed": 1.05,         # slightly faster = more energy
        },
        timeout=90,
    )
    if r.status_code == 200:
        Path(out_path).write_bytes(r.content)
        log.info("OpenAI TTS: OK")
        return True
    log.warning("OpenAI TTS failed (%d)", r.status_code)
    return False


def _edgetts_tts(text: str, out_path: str) -> None:
    """edge-tts last-resort fallback — use Ryan (more dynamic than Guy)."""
    async def _run():
        # en-US-RyanMultilingualNeural sounds more natural than GuyNeural
        comm = edge_tts.Communicate(text, "en-US-RyanMultilingualNeural",
                                    rate="+8%", volume="+10%")
        await comm.save(out_path)
    asyncio.run(_run())
    log.info("edge-tts TTS: OK")


def generate_voiceover(full_text: str, out_path: str) -> None:
    """
    Fallback chain: ElevenLabs → OpenAI TTS → edge-tts.
    First available key wins.
    """
    if _elevenlabs_tts(full_text, out_path):
        return
    if _openai_tts(full_text, out_path):
        return
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

# ══════════════════════════════════════════════════════════════════════
# ASSET BOOTSTRAP — music download + SFX synthesis
# ══════════════════════════════════════════════════════════════════════

def _synthesize_whoosh(path: str) -> None:
    """Generate a 300ms frequency-sweep whoosh (3kHz→200Hz) with pydub."""
    sr       = 44100
    dur      = 0.30
    n        = int(sr * dur)
    t        = np.linspace(0, dur, n)
    freq     = np.exp(np.linspace(np.log(3000), np.log(200), n))
    phase    = np.cumsum(2 * np.pi * freq / sr)
    envelope = np.exp(-3 * t / dur)          # decay
    wave     = (np.sin(phase) * envelope * 28000).astype(np.int16)
    seg = (AudioSegment(wave.tobytes(), frame_rate=sr, sample_width=2, channels=1)
           .fade_in(20).fade_out(80))
    seg.export(path, format="mp3")
    log.info("SFX whoosh synthesised → %s", path)


def _synthesize_impact(path: str) -> None:
    """Generate a 120ms noise-burst impact hit."""
    sr       = 44100
    n        = int(sr * 0.12)
    noise    = np.random.uniform(-1, 1, n)
    # Low-pass filter to make it punchy rather than harsh
    b, a     = scipy.signal.butter(4, 800 / (sr / 2), btype="low")
    filtered = scipy.signal.lfilter(b, a, noise)
    decay    = np.exp(-np.linspace(0, 10, n))
    wave     = (filtered * decay * 32000).astype(np.int16)
    seg = (AudioSegment(wave.tobytes(), frame_rate=sr, sample_width=2, channels=1)
           .fade_in(5).fade_out(30))
    seg.export(path, format="mp3")
    log.info("SFX impact synthesised → %s", path)


def _synthesize_ambient(path: str, duration_s: float = 120) -> None:
    """
    Generate a simple lo-fi ambient pad (A minor chord layers) as
    last-resort background music when no file and no Pixabay key.
    """
    sr   = 44100
    n    = int(sr * duration_s)
    t    = np.linspace(0, duration_s, n)
    # A-minor pad: A2(110) E3(165) A3(220) C4(262) E4(330)
    wave = (
        0.28 * np.sin(2 * np.pi * 110 * t) +
        0.20 * np.sin(2 * np.pi * 165 * t) +
        0.16 * np.sin(2 * np.pi * 220 * t) +
        0.12 * np.sin(2 * np.pi * 262 * t) +
        0.08 * np.sin(2 * np.pi * 330 * t)
    )
    # Slow tremolo + subtle noise texture
    tremolo = 0.75 + 0.25 * np.sin(2 * np.pi * 0.25 * t)
    wave    = wave * tremolo
    wave    = wave / (np.max(np.abs(wave)) + 1e-9) * 26000
    wave    = wave.astype(np.int16)
    seg = AudioSegment(wave.tobytes(), frame_rate=sr, sample_width=2, channels=1)
    seg = seg.fade_in(3000).fade_out(3000)
    seg.export(path, format="mp3")
    log.info("Ambient pad synthesised → %s (%.0fs)", path, duration_s)


def ensure_music(duration_s: float = 120) -> str:
    """
    Returns a path to a background music file.
    Priority: existing MUSIC_PATH → Pixabay API → synthesised ambient pad.
    """
    if Path(MUSIC_PATH).exists():
        log.info("Using existing music: %s", MUSIC_PATH)
        return MUSIC_PATH

    # ── Pixabay free music search ─────────────────────────────────────
    if PIXABAY_KEY:
        for query in ("background cinematic", "ambient lofi", "motivational"):
            try:
                r = requests.get(
                    "https://pixabay.com/api/videos/music/",
                    params={"key": PIXABAY_KEY, "q": query, "per_page": 5},
                    timeout=10,
                )
                hits = r.json().get("hits", [])
                if hits:
                    url  = random.choice(hits)["audio"]["url"]
                    data = requests.get(url, timeout=30).content
                    Path(MUSIC_PATH).write_bytes(data)
                    log.info("Pixabay music downloaded: %s", query)
                    return MUSIC_PATH
            except Exception as e:
                log.warning("Pixabay music fetch failed: %s", e)

    # ── Last resort: synthesise ───────────────────────────────────────
    log.warning("No music file and Pixabay unavailable — synthesising ambient pad")
    _synthesize_ambient(MUSIC_PATH, duration_s + 10)
    return MUSIC_PATH


def ensure_sfx() -> tuple[str, str]:
    """Returns (whoosh_path, impact_path) — synthesises if missing."""
    if not Path(SFX_WHOOSH).exists():
        _synthesize_whoosh(SFX_WHOOSH)
    if not Path(SFX_IMPACT).exists():
        _synthesize_impact(SFX_IMPACT)
    return SFX_WHOOSH, SFX_IMPACT


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
# STEP 5 — SCENE IMAGE GENERATION  (v3 — viral TikTok/Shorts style)
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


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _draw_text_with_shadow(
    draw: ImageDraw.ImageDraw,
    pos: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: str = "#FFFFFF",
    shadow_offset: int = 4,
    shadow_alpha: int = 200,
) -> None:
    """Draw text with a strong drop shadow for legibility on any background."""
    sx, sy = pos[0] + shadow_offset, pos[1] + shadow_offset
    draw.text((sx, sy), text, font=font, fill=(0, 0, 0, shadow_alpha))
    draw.text(pos, text, font=font, fill=fill)


def render_scene_image(
    scene_text: str,
    niche:      str,
    scene_idx:  int,
    total_scenes: int = 16,
    pexels_img: Optional[Image.Image] = None,
) -> Image.Image:
    """
    Viral TikTok/Shorts style frame (1080×1920):
    - Full-bleed Pexels background (smart center-crop)
    - Cinematic dark vignette gradient bottom 55%
    - Large bold caption text centered in bottom third
    - Niche-colored accent line + glow beneath text block
    - Progress bar at very bottom
    - Scene number pill top-left
    - Subtle "VAULTMIND" watermark top-right
    """
    cfg   = NICHES[niche]
    ac    = _hex_to_rgb(cfg["color"])          # accent color RGB
    frame = Image.new("RGBA", (VIDEO_W, VIDEO_H), (*_hex_to_rgb(cfg["bg"]), 255))

    # ── Full-bleed background ──────────────────────────────────────────
    if pexels_img:
        bg = pexels_img.convert("RGB")
        # Smart center-crop to 9:16
        bw, bh = bg.size
        target_ratio = VIDEO_W / VIDEO_H
        src_ratio    = bw / bh
        if src_ratio > target_ratio:          # too wide → crop sides
            new_w = int(bh * target_ratio)
            x0    = (bw - new_w) // 2
            bg    = bg.crop((x0, 0, x0 + new_w, bh))
        else:                                 # too tall → crop top/bottom
            new_h = int(bw / target_ratio)
            y0    = (bh - new_h) // 3        # bias to top third (faces)
            bg    = bg.crop((0, y0, bw, y0 + new_h))
        bg = bg.resize((VIDEO_W, VIDEO_H), Image.LANCZOS)
        frame.paste(bg.convert("RGBA"), (0, 0))

    # ── Cinematic gradient vignette (bottom 65%) ──────────────────────
    grad = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    gd   = ImageDraw.Draw(grad)
    start_y = int(VIDEO_H * 0.35)
    for y in range(start_y, VIDEO_H):
        progress = (y - start_y) / (VIDEO_H - start_y)
        alpha    = int(220 * (progress ** 0.6))   # smooth curve
        gd.line([(0, y), (VIDEO_W, y)], fill=(0, 0, 0, alpha))
    frame = Image.alpha_composite(frame, grad)

    # ── Accent glow band ───────────────────────────────────────────────
    glow_y = int(VIDEO_H * 0.62)
    glow   = Image.new("RGBA", (VIDEO_W, 80), (0, 0, 0, 0))
    gd2    = ImageDraw.Draw(glow)
    for i in range(40):
        alpha = int(140 * (1 - i / 40))
        gd2.line([(0, i), (VIDEO_W, i)], fill=(*ac, alpha))
        gd2.line([(0, 79 - i), (VIDEO_W, 79 - i)], fill=(*ac, alpha // 2))
    frame.paste(glow, (0, glow_y), glow)

    # ── Main accent bar ────────────────────────────────────────────────
    draw = ImageDraw.Draw(frame)
    bar_y = int(VIDEO_H * 0.63)
    draw.rectangle([(0, bar_y), (VIDEO_W, bar_y + 6)], fill=(*ac, 255))

    # ── Caption text block (bottom third) ─────────────────────────────
    font_main  = _load_font(72)    # big & punchy
    font_small = _load_font(42)
    font_ui    = _load_font(32)

    lines     = textwrap.wrap(scene_text, width=20)
    line_h    = 72 + 18             # font size + line gap
    block_h   = len(lines) * line_h
    text_top  = int(VIDEO_H * 0.66)

    # Semi-transparent text backing for guaranteed readability
    pad = 30
    backing_box = [
        pad,
        text_top - pad,
        VIDEO_W - pad,
        text_top + block_h + pad,
    ]
    backing = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    ImageDraw.Draw(backing).rounded_rectangle(
        backing_box, radius=28, fill=(0, 0, 0, 140)
    )
    frame = Image.alpha_composite(frame, backing)
    draw  = ImageDraw.Draw(frame)

    y_cur = text_top
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font_main)
        lw   = bbox[2] - bbox[0]
        x    = (VIDEO_W - lw) // 2
        # First line gets accent color highlight, rest white
        color = cfg["color"] if i == 0 and len(lines) > 1 else "#FFFFFF"
        _draw_text_with_shadow(draw, (x, y_cur), line, font_main,
                               fill=color, shadow_offset=5, shadow_alpha=220)
        y_cur += line_h

    # ── Progress bar (bottom 28px) ─────────────────────────────────────
    bar_h      = 10
    bar_top    = VIDEO_H - bar_h - 12
    progress   = (scene_idx + 1) / max(total_scenes, 1)
    fill_w     = int(VIDEO_W * progress)
    draw.rectangle([(0, bar_top), (VIDEO_W, bar_top + bar_h)],
                   fill=(30, 30, 30, 200))
    draw.rectangle([(0, bar_top), (fill_w, bar_top + bar_h)],
                   fill=(*ac, 240))

    # ── Scene number pill (top-left) ──────────────────────────────────
    pill_text = f"{scene_idx + 1}/{total_scenes}"
    bbox      = draw.textbbox((0, 0), pill_text, font=font_ui)
    pw        = bbox[2] - bbox[0] + 28
    draw.rounded_rectangle([20, 28, 20 + pw, 28 + 46], radius=14,
                            fill=(*ac, 230))
    draw.text((34, 34), pill_text, font=font_ui, fill="#FFFFFF")

    # ── Watermark (top-right) ─────────────────────────────────────────
    wm_font = _load_font(28)
    wm_text = "VAULTMIND"
    wb      = draw.textbbox((0, 0), wm_text, font=wm_font)
    ww      = wb[2] - wb[0]
    draw.text((VIDEO_W - ww - 24, 36), wm_text, font=wm_font,
              fill=(255, 255, 255, 100))

    return frame.convert("RGB")


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
    scenes:          list[dict],
    niche:           str,
    audio_path:      str,
    aai_data:        dict,
    cut_points_s:    list[float],
    output_path:     str,
    music_path:      str = "",
    sfx_whoosh_path: str = "",
    sfx_impact_path: str = "",
    *,
    use_gameplay_bg: bool = True,
) -> str:
    """
    v3 MoviePy render:
      1. Scene images (Ken Burns) → crossfade concat
      2. Gameplay BG composite (darkened)
      3. Voice + looped music (always loaded via ensure_music) + SFX at cuts
      4. 1080×1920 H.264 export
    """
    from moviepy import CompositeAudioClip
    log.info("Building video with MoviePy v3 (%d scenes)…", len(scenes))

    total_audio_s = aai_data["total_ms"] / 1000.0
    boundaries    = [0.0] + cut_points_s + [total_audio_s]
    durations     = [boundaries[i+1] - boundaries[i]
                     for i in range(len(boundaries) - 1)]
    while len(scenes) < len(durations):
        scenes.append(scenes[-1])
    scenes = scenes[:len(durations)]
    n_scenes = len(scenes)

    # ── Render scene images ────────────────────────────────────────────
    scene_clips = []
    for idx, (scene, dur) in enumerate(zip(scenes, durations)):
        pexels_img = fetch_pexels_image(
            scene.get("pexels_query", scene["text"][:40])
        )
        img  = render_scene_image(scene["text"], niche, idx, n_scenes, pexels_img)
        clip = _scene_image_to_clip(img, max(dur, 0.5))
        scene_clips.append(clip)
        log.info("  Scene %d/%d rendered (%.1fs)", idx + 1, n_scenes, dur)

    # ── Crossfade transitions ─────────────────────────────────────────
    transitioned = [scene_clips[0]]
    for clip in scene_clips[1:]:
        transitioned.append(clip.with_effects([CrossFadeIn(TRANSITION_DUR)]))
    final_video = concatenate_videoclips(
        transitioned, method="compose", padding=-TRANSITION_DUR,
    )
    final_video = final_video.with_effects([FadeIn(FADE_IN_DUR), FadeOut(FADE_OUT_DUR)])

    # ── Gameplay background composite ─────────────────────────────────
    if use_gameplay_bg and Path(GAMEPLAY_PATH).exists():
        log.info("Compositing gameplay background…")
        bg       = VideoFileClip(GAMEPLAY_PATH, audio=False)
        bg_dur   = final_video.duration
        loops    = int(np.ceil(bg_dur / bg.duration))
        bg_loop  = concatenate_videoclips([bg] * loops).subclipped(0, bg_dur)
        bg_loop  = bg_loop.resized((VIDEO_W, VIDEO_H))
        bg_dark  = bg_loop.image_transform(
            lambda f: (f * 0.35).astype(np.uint8)   # 65% darker
        )
        final_video = CompositeVideoClip(
            [bg_dark, final_video], size=(VIDEO_W, VIDEO_H)
        ).with_duration(bg_dur)
    else:
        log.warning("Gameplay BG not found — skipping")

    # ── Audio mix: voice + music + SFX ────────────────────────────────
    log.info("Mixing audio…")
    vid_dur    = final_video.duration
    voice_clip = AudioFileClip(audio_path).subclipped(0, vid_dur)
    audio_tracks = [voice_clip]

    # Music — always present (ensure_music already called in run_pipeline)
    mp = music_path or MUSIC_PATH
    if Path(mp).exists():
        music_raw    = AudioSegment.from_file(mp)
        music_ducked = music_raw + MUSIC_DUCK_DB
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            music_tmp = f.name
        music_ducked.export(music_tmp, format="mp3")
        mc = AudioFileClip(music_tmp)
        if mc.duration < vid_dur:
            reps = int(np.ceil(vid_dur / mc.duration))
            mc   = concatenate_audioclips([mc] * reps)
        audio_tracks.append(mc.subclipped(0, vid_dur))
        log.info("Music track added (%.0fs, ducked %ddB)", vid_dur, MUSIC_DUCK_DB)
    else:
        log.warning("No music file found at %s", mp)

    # SFX — inject whoosh at each cut point, impact on first scene
    whoosh_p = sfx_whoosh_path or SFX_WHOOSH
    impact_p = sfx_impact_path or SFX_IMPACT
    if Path(whoosh_p).exists() and Path(impact_p).exists():
        sfx_segs = AudioSegment.silent(duration=int(vid_dur * 1000))
        whoosh   = AudioSegment.from_file(whoosh_p) - 6    # -6dB
        impact   = AudioSegment.from_file(impact_p) - 4
        # Impact on first frame
        sfx_segs = sfx_segs.overlay(impact, position=0)
        # Whoosh at every cut
        for cut_s in cut_points_s:
            pos_ms = max(0, int(cut_s * 1000) - 150)   # 150ms before cut
            sfx_segs = sfx_segs.overlay(whoosh, position=pos_ms)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            sfx_tmp = f.name
        sfx_segs.export(sfx_tmp, format="mp3")
        sfx_clip = AudioFileClip(sfx_tmp).subclipped(0, vid_dur)
        audio_tracks.append(sfx_clip)
        log.info("SFX track added (%d cuts + intro impact)", len(cut_points_s))
    else:
        log.warning("SFX files not found — skipping")

    mixed_audio = CompositeAudioClip(audio_tracks)
    final_video = final_video.with_audio(mixed_audio)

    # ── Export ─────────────────────────────────────────────────────────
    log.info("Exporting %dx%d video → %s", VIDEO_W, VIDEO_H, output_path)
    final_video.write_videofile(
        output_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        temp_audiofile="temp_audio.m4a",
        remove_temp=True,
        preset="medium",
        ffmpeg_params=["-crf", "21", "-movflags", "+faststart"],
        logger=None,
    )
    log.info("Export complete: %s", output_path)
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

    # Upload SRT captions if provided (best-effort — skip if scope missing)
    if srt_path and Path(srt_path).exists():
        try:
            youtube.captions().insert(
                part="snippet",
                body={
                    "snippet": {
                        "videoId":  video_id,
                        "language": "en",
                        "name":     "Auto",
                        "isDraft":  False,
                    }
                },
                media_body=MediaFileUpload(srt_path, mimetype="text/plain"),
            ).execute()
            log.info("Captions uploaded")
        except Exception as e:
            log.warning("Caption upload skipped (%s)", e)

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
    log.info("═══ VaultMind Pipeline v3 — niche: %s ═══", niche)

    # ── Bootstrap assets (music + SFX) before entering tmpdir ─────────
    music_path          = ensure_music(duration_s=120)
    sfx_whoosh, sfx_impact = ensure_sfx()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # 1. Script
        script    = generate_script(niche)
        scenes    = script["scenes"]
        title     = script["title"]
        full_text = " ".join(s["text"] for s in scenes)
        log.info("Title: %s | Scenes: %d", title, len(scenes))

        # 2. Voiceover (ElevenLabs → OpenAI TTS → edge-tts)
        audio_path = str(tmp / "voice.mp3")
        generate_voiceover(full_text, audio_path)

        # 3. AssemblyAI word-level transcription
        aai_data = transcribe_with_assemblyai(audio_path)

        # 4. Smart cut points (pydub silence + AssemblyAI pauses)
        cut_points = detect_cut_points(
            audio_path, aai_data, n_scenes=len(scenes),
        )

        # 5. Captions → SRT
        captions = build_caption_data(aai_data)
        srt_path = str(tmp / "captions.srt")
        export_srt(captions, srt_path)

        # 6. MoviePy render (1080×1920, SFX at cuts, music always loaded)
        video_path = str(tmp / "output.mp4")
        build_video_moviepy(
            scenes          = scenes,
            niche           = niche,
            audio_path      = audio_path,
            aai_data        = aai_data,
            cut_points_s    = cut_points,
            output_path     = video_path,
            music_path      = music_path,
            sfx_whoosh_path = sfx_whoosh,
            sfx_impact_path = sfx_impact,
        )

        # 7. YouTube upload
        description = (
            f"{title}\n\n"
            f"#shorts #{niche} #viral #vaultmind"
        )
        video_url = upload_youtube(video_path, title, description, srt_path)

        # 8. Dashboard
        update_dashboard(niche, title, video_url, run_ok=True)

    log.info("═══ Pipeline v3 complete: %s ═══", video_url or "no URL")


if __name__ == "__main__":
    import sys
    niche_arg = sys.argv[1] if len(sys.argv) > 1 else None
    run_pipeline(niche_arg)
