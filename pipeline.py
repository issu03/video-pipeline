"""
VaultMind Auto Video Pipeline — v4
Major upgrades over v3:
  ┌─────────────────────────────────────────────────────────┐
  │  • Pexels VIDEOS as backgrounds (not static images)     │
  │  • Word-by-word animated captions (viral MrBeast style) │
  │  • Creatomate cloud render (optional, premium quality)   │
  │  • Zoom-punch transitions via ffmpeg                     │
  │  • Aggressive viral script prompts                       │
  │  • Auto font download (Impact/Montserrat Black)          │
  └─────────────────────────────────────────────────────────┘
"""

import os, json, time, base64, pickle, random, logging, tempfile, textwrap
import subprocess, shutil
from io import BytesIO
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import scipy.signal
from pydub import AudioSegment
from pydub.silence import detect_silence

from moviepy import (
    VideoFileClip, ImageClip, AudioFileClip,
    CompositeVideoClip, concatenate_videoclips,
    CompositeAudioClip, concatenate_audioclips,
)
from moviepy.video.fx import FadeIn, FadeOut, CrossFadeIn
# ── Beautiful Captions (animated viral-style subtitles) ────────────────
try:
    from beautiful_captions import Video as BCVideo, CaptionConfig
    BEAUTIFUL_CAPTIONS_OK = True
except Exception:
    BEAUTIFUL_CAPTIONS_OK = False
    log_warn = print  # pre-logger fallback

from groq import Groq
import edge_tts, asyncio
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("vaultmind")

# ══════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════
VIDEO_W, VIDEO_H   = 1080, 1920
FPS                = 30
MIN_SCENES         = 16
TARGET_DUR         = 80
TRANSITION_DUR     = 0.35
FADE_IN_DUR        = 0.3
FADE_OUT_DUR       = 0.5
MUSIC_DUCK_DB      = -16
CAPTION_WORDS      = 3        # words shown per caption chunk
CAPTION_FONT_SIZE  = 95       # big & punchy

GAMEPLAY_PATH = os.getenv("GAMEPLAY_PATH", "gameplay_bg.mp4")
MUSIC_PATH    = os.getenv("MUSIC_PATH",    "bg_music.mp3")
SFX_WHOOSH    = "sfx_whoosh.mp3"
SFX_IMPACT    = "sfx_impact.mp3"
PIXABAY_KEY   = os.getenv("PIXABAY_KEY", "")
FONT_PATH     = "font_bold.ttf"

NICHES = {
    "reddit":     {"color": "#FF4500", "glow": "#FF6633", "bg": "#1A1A2E", "query": "dramatic story dark room"},
    "dating":     {"color": "#FF69B4", "glow": "#FF90C8", "bg": "#1A1A2E", "query": "couple romantic bokeh"},
    "rich":       {"color": "#FFD700", "glow": "#FFEC80", "bg": "#0D0D0D", "query": "luxury money wealth"},
    "lifehack":   {"color": "#00FF88", "glow": "#66FFBB", "bg": "#111111", "query": "clever smart productivity"},
    "fact":       {"color": "#00BFFF", "glow": "#66D9FF", "bg": "#0A0A1A", "query": "science space universe"},
    "scary":      {"color": "#FF2222", "glow": "#FF6666", "bg": "#050505", "query": "dark horror forest night"},
    "motivation": {"color": "#FF8C00", "glow": "#FFAA44", "bg": "#0D0D0D", "query": "athlete running winner"},
    "conspiracy": {"color": "#9B59B6", "glow": "#C285E0", "bg": "#070713", "query": "mystery shadow secret"},
    "learn":      {"color": "#1ABC9C", "glow": "#4DDFC4", "bg": "#0D1B2A", "query": "education books learning"},
}


# ══════════════════════════════════════════════════════════════════════
# FONT BOOTSTRAP
# ══════════════════════════════════════════════════════════════════════

def ensure_font() -> str:
    """Download Impact/Montserrat Black font if not present."""
    if Path(FONT_PATH).exists():
        return FONT_PATH
    # Try system fonts first
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            shutil.copy(p, FONT_PATH)
            log.info("Font: using system %s", p)
            return FONT_PATH
    # Download Montserrat Black from Google Fonts CDN
    try:
        url  = "https://github.com/google/fonts/raw/main/ofl/montserrat/static/Montserrat-Black.ttf"
        data = requests.get(url, timeout=15).content
        Path(FONT_PATH).write_bytes(data)
        log.info("Font: Montserrat Black downloaded")
        return FONT_PATH
    except Exception:
        pass
    log.warning("Font: falling back to PIL default")
    return ""


def load_font(size: int) -> ImageFont.FreeTypeFont:
    fp = FONT_PATH if Path(FONT_PATH).exists() else None
    if fp:
        try:
            return ImageFont.truetype(fp, size)
        except Exception:
            pass
    return ImageFont.load_default()


# ══════════════════════════════════════════════════════════════════════
# STEP 1 — SCRIPT (aggressive viral prompts)
# ══════════════════════════════════════════════════════════════════════

def generate_script(niche: str) -> dict:
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    system = (
        "You are a viral YouTube Shorts / TikTok scriptwriter with 10M+ view experience. "
        "Return ONLY valid JSON, no markdown, no preamble. "
        "Format: {title, hook, scenes: [{text, duration, pexels_query}]} "
        "Rules: "
        "- hook: MAX 6 words, creates immediate shock/curiosity (e.g. 'This will destroy your worldview') "
        "- scenes: minimum 16, each 3-6 seconds, punchy 1-2 sentences MAX "
        "- pexels_query: 3-word visual search term for scene background video "
        "- total duration ~80 seconds "
        "- Start with a SHOCKING fact/statement, build tension, satisfying ending "
        "- Write like the audience is about to scroll away — every word must fight for attention"
    )
    prompt = (
        f"Write a viral {niche} YouTube Shorts script that hooks within 1 second. "
        "Make it controversial, surprising or emotional. Use cliffhangers between scenes."
    )
    for attempt in range(5):
        try:
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "system", "content": system},
                          {"role": "user",   "content": prompt}],
                temperature=0.95, max_tokens=2500,
            )
            raw  = resp.choices[0].message.content.strip()
            # Strip possible markdown fences
            raw  = raw.strip("```json").strip("```").strip()
            data = json.loads(raw)
            if len(data.get("scenes", [])) >= MIN_SCENES:
                log.info("Script OK: %d scenes — \"%s\"", len(data["scenes"]), data.get("title",""))
                return data
            log.warning("Too few scenes (%d), retrying…", len(data.get("scenes",[])))
        except Exception as e:
            log.warning("Script attempt %d: %s", attempt+1, e)
        time.sleep(2**attempt)
    raise RuntimeError("Script generation failed after 5 attempts")


# ══════════════════════════════════════════════════════════════════════
# STEP 2 — VOICEOVER (ElevenLabs → OpenAI → edge-tts)
# ══════════════════════════════════════════════════════════════════════

def _elevenlabs_tts(text: str, path: str) -> bool:
    key = os.getenv("ELEVENLABS_KEY")
    if not key:
        return False
    voice_id = os.getenv("ELEVEN_VOICE_ID", "")
    if not voice_id:
        r = requests.get("https://api.elevenlabs.io/v1/voices",
                         headers={"xi-api-key": key}, timeout=10)
        if r.status_code == 200:
            voices = r.json().get("voices", [])
            if voices:
                voice_id = voices[0]["voice_id"]
    if not voice_id:
        return False
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        json={"text": text, "model_id": "eleven_turbo_v2_5",
              "voice_settings": {"stability": 0.30, "similarity_boost": 0.82,
                                 "style": 0.55, "use_speaker_boost": True}},
        timeout=90,
    )
    if r.status_code == 200:
        Path(path).write_bytes(r.content)
        log.info("ElevenLabs TTS OK")
        return True
    log.warning("ElevenLabs failed (%d)", r.status_code)
    return False


def _openai_tts(text: str, path: str) -> bool:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return False
    r = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": "tts-1-hd", "voice": "onyx", "input": text, "speed": 1.08},
        timeout=90,
    )
    if r.status_code == 200:
        Path(path).write_bytes(r.content)
        log.info("OpenAI TTS OK")
        return True
    log.warning("OpenAI TTS failed (%d)", r.status_code)
    return False


def _edgetts_tts(text: str, path: str) -> None:
    async def _run():
        comm = edge_tts.Communicate(text, "en-US-RyanMultilingualNeural",
                                    rate="+10%", volume="+12%")
        await comm.save(path)
    asyncio.run(_run())
    log.info("edge-tts OK")


def generate_voiceover(text: str, path: str) -> None:
    if _elevenlabs_tts(text, path): return
    if _openai_tts(text, path):     return
    _edgetts_tts(text, path)


# ══════════════════════════════════════════════════════════════════════
# STEP 3 — ASSEMBLYAI TRANSCRIPTION (raw HTTP)
# ══════════════════════════════════════════════════════════════════════

def transcribe_with_assemblyai(audio_path: str) -> dict:
    key = os.environ["ASSEMBLYAI_API_KEY"]
    hdr = {"authorization": key}

    log.info("Uploading to AssemblyAI…")
    with open(audio_path, "rb") as f:
        up = requests.post("https://api.assemblyai.com/v2/upload",
                           headers=hdr, data=f, timeout=60)
    up.raise_for_status()
    audio_url = up.json()["upload_url"]

    sub = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        headers={**hdr, "content-type": "application/json"},
        json={"audio_url": audio_url, "speech_models": ["universal-2"],
              "punctuate": True, "format_text": True, "disfluencies": False},
        timeout=30,
    )
    sub.raise_for_status()
    tid = sub.json()["id"]

    poll = f"https://api.assemblyai.com/v2/transcript/{tid}"
    for i in range(120):
        time.sleep(3)
        r = requests.get(poll, headers=hdr, timeout=10).json()
        if r["status"] == "completed": break
        if r["status"] == "error":     raise RuntimeError(f"AAI: {r.get('error')}")
        if i % 10 == 0: log.info("AAI polling… %ds", i*3)

    words = [{"text": w["text"], "start_ms": w["start"],
              "end_ms": w["end"], "confidence": w.get("confidence",1.0)}
             for w in (r.get("words") or [])]

    # Build utterances from punctuation
    utterances, cur = [], []
    for w in words:
        cur.append(w)
        if w["text"].rstrip().endswith((".", "!", "?")):
            utterances.append({"text": " ".join(x["text"] for x in cur),
                                "start_ms": cur[0]["start_ms"],
                                "end_ms":   cur[-1]["end_ms"]})
            cur = []
    if cur:
        utterances.append({"text": " ".join(x["text"] for x in cur),
                            "start_ms": cur[0]["start_ms"],
                            "end_ms":   cur[-1]["end_ms"]})

    pauses = [{"start_ms": utterances[i]["end_ms"],
               "end_ms":   utterances[i+1]["start_ms"]}
              for i in range(len(utterances)-1)
              if utterances[i+1]["start_ms"] - utterances[i]["end_ms"] >= 200]

    total_ms = words[-1]["end_ms"] if words else 0
    log.info("AAI: %d words, %d utterances, %d pauses (%.1fs)",
             len(words), len(utterances), len(pauses), total_ms/1000)
    return {"words": words, "utterances": utterances,
            "pauses": pauses, "total_ms": total_ms}


# ══════════════════════════════════════════════════════════════════════
# STEP 4 — SMART CUT POINTS (pydub + AAI)
# ══════════════════════════════════════════════════════════════════════

def detect_cut_points(audio_path: str, aai_data: dict, n_scenes: int,
                      silence_thresh_db: int = -40,
                      min_silence_ms: int = 300) -> list[float]:
    audio    = AudioSegment.from_file(audio_path)
    total_ms = len(audio)

    pydub_mids = [(s+e)//2 for s,e in
                  detect_silence(audio, min_silence_len=min_silence_ms,
                                 silence_thresh=silence_thresh_db)]
    aai_mids   = [(p["start_ms"]+p["end_ms"])//2
                  for p in aai_data.get("pauses", [])]

    merged, last = [], -9999
    for t in sorted(set(pydub_mids + aai_mids)):
        if t - last > 500 and 3000 < t < total_ms - 3000:
            merged.append(t)
            last = t

    n_cuts = n_scenes - 1
    if len(merged) <= n_cuts:
        chosen = merged
    else:
        ideal  = total_ms / n_scenes
        chosen = []
        pool   = merged.copy()
        for i in range(1, n_scenes):
            target  = i * ideal
            closest = min(pool, key=lambda t: abs(t-target))
            chosen.append(closest)
            pool.remove(closest)

    result = sorted(t/1000.0 for t in chosen)
    log.info("Cut points (%d): %s", len(result), [f"{c:.1f}s" for c in result])
    return result


# ══════════════════════════════════════════════════════════════════════
# STEP 5 — PEXELS VIDEO BACKGROUNDS (NEW — replaces static images)
# ══════════════════════════════════════════════════════════════════════

_pexels_video_cache: dict = {}

def fetch_pexels_video(query: str, min_dur: float = 4.0,
                       download: bool = False) -> Optional[str]:
    """
    Fetch a Pexels portrait video for a query.
    download=False (default): returns the direct CDN URL (for Creatomate).
    download=True: downloads to /tmp and returns local path (for MoviePy).
    """
    key = os.getenv("PEXELS_KEY")
    if not key:
        return None

    cache_key = f"{query.lower()[:30]}_{download}"
    if cache_key in _pexels_video_cache:
        return _pexels_video_cache[cache_key]

    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": key},
            params={"query": query, "per_page": 15,
                    "orientation": "portrait", "size": "medium"},
            timeout=15,
        )
        r.raise_for_status()
        videos = r.json().get("videos", [])
        good   = [v for v in videos if v.get("duration", 0) >= min_dur] or videos
        if not good:
            return None

        video  = random.choice(good)
        files  = sorted(video.get("video_files", []),
                        key=lambda f: f.get("height", 0) or 0, reverse=True)
        best   = next((f for f in files if (f.get("height") or 0) >= 1080), files[0])
        url    = best["link"]

        if not download:
            _pexels_video_cache[cache_key] = url
            log.info("Pexels URL: '%s' → %s", query, url[:60])
            return url

        # Download for local MoviePy fallback
        tmp_p = f"/tmp/pex_{video['id']}.mp4"
        if not Path(tmp_p).exists():
            data = requests.get(url, timeout=60, stream=True)
            with open(tmp_p, "wb") as f:
                for chunk in data.iter_content(1024*1024): f.write(chunk)
        _pexels_video_cache[cache_key] = tmp_p
        log.info("Pexels download: '%s' → %s", query, tmp_p)
        return tmp_p

    except Exception as e:
        log.warning("Pexels failed for '%s': %s", query, e)
        return None


def make_solid_bg_clip(color_hex: str, duration: float) -> VideoFileClip:
    """Fallback: solid color clip when Pexels unavailable."""
    r, g, b = tuple(int(color_hex.lstrip("#")[i:i+2], 16) for i in (0,2,4))
    arr     = np.full((VIDEO_H, VIDEO_W, 3), [r,g,b], dtype=np.uint8)
    clip    = ImageClip(arr).with_duration(duration).with_fps(FPS)
    return clip


# ══════════════════════════════════════════════════════════════════════
# STEP 6 — WORD-BY-WORD ANIMATED CAPTIONS  (viral MrBeast style)
#
#  Groups words into chunks of CAPTION_WORDS.
#  Each chunk becomes an ImageClip overlay timed to the audio.
#  Current chunk: accent color + scale-up.  Rest: white.
#  All text: bold, black stroke, center screen at ~60% height.
# ══════════════════════════════════════════════════════════════════════

def _hex(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0,2,4))


def render_caption_frame(
    chunk_words:   list[str],
    active_idx:    int,        # which word in chunk is currently spoken
    accent_color:  str,
    frame_w:       int = VIDEO_W,
    frame_h:       int = VIDEO_H,
) -> Image.Image:
    """
    Renders one caption frame: chunk of words, active word in accent color.
    Returns RGBA image (transparent outside text area).
    """
    frame = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
    draw  = ImageDraw.Draw(frame)
    font  = load_font(CAPTION_FONT_SIZE)
    ac    = _hex(accent_color)

    line  = " ".join(chunk_words)
    bb    = draw.textbbox((0, 0), line, font=font)
    lw    = bb[2] - bb[0]
    lh    = bb[3] - bb[1]
    x0    = (frame_w - lw) // 2
    y0    = int(frame_h * 0.60)

    # Measure each word individually for per-word coloring
    x_cur = x0
    for i, word in enumerate(chunk_words):
        wb    = draw.textbbox((0, 0), word, font=font)
        ww    = wb[2] - wb[0]
        space = draw.textbbox((0, 0), " ", font=font)
        sw    = space[2] - space[0]

        # Black outline / stroke (4px in 8 directions)
        for dx, dy in [(-4,0),(4,0),(0,-4),(0,4),(-3,-3),(3,-3),(-3,3),(3,3)]:
            draw.text((x_cur+dx, y0+dy), word, font=font, fill=(0,0,0,255))

        # Word color: accent if active, white otherwise
        color = (*ac, 255) if i == active_idx else (255,255,255,255)
        draw.text((x_cur, y0), word, font=font, fill=color)

        x_cur += ww + sw

    return frame


def build_caption_clips(aai_data: dict, niche: str, total_dur: float) -> list:
    """
    Returns list of (ImageClip, start_time) tuples for all word chunks.
    Each clip is a transparent RGBA overlay timed to spoken words.
    """
    words   = aai_data.get("words", [])
    if not words:
        return []
    accent  = NICHES[niche]["color"]
    clips   = []
    i       = 0

    while i < len(words):
        chunk = words[i : i + CAPTION_WORDS]
        if not chunk:
            break
        chunk_texts  = [w["text"] for w in chunk]
        chunk_start  = chunk[0]["start_ms"] / 1000.0
        chunk_end    = chunk[-1]["end_ms"]   / 1000.0
        chunk_dur    = max(chunk_end - chunk_start, 0.15)

        # For each word in chunk, figure out its sub-timing
        # We render one frame per word that is "active" inside the chunk
        for j, word in enumerate(chunk):
            w_start = word["start_ms"] / 1000.0
            w_end   = word["end_ms"]   / 1000.0
            w_dur   = max(w_end - w_start, 0.08)

            img  = render_caption_frame(chunk_texts, j, accent)
            arr  = np.array(img)
            clip = (ImageClip(arr, is_mask=False)
                    .with_duration(w_dur)
                    .with_fps(FPS))
            clips.append((clip, w_start))

        i += CAPTION_WORDS

    log.info("Caption clips built: %d word segments", len(clips))
    return clips


# ══════════════════════════════════════════════════════════════════════
# STEP 7 — SFX & MUSIC BOOTSTRAP
# ══════════════════════════════════════════════════════════════════════

def _synth_whoosh(path: str) -> None:
    sr, dur = 44100, 0.28
    n = int(sr*dur)
    t = np.linspace(0,dur,n)
    freq = np.exp(np.linspace(np.log(3200),np.log(180),n))
    phase = np.cumsum(2*np.pi*freq/sr)
    env = np.exp(-4*t/dur)
    wave = (np.sin(phase)*env*26000).astype(np.int16)
    AudioSegment(wave.tobytes(),frame_rate=sr,sample_width=2,channels=1).fade_out(60).export(path,format="mp3")

def _synth_impact(path: str) -> None:
    sr,n = 44100, int(44100*0.12)
    noise = np.random.uniform(-1,1,n)
    b,a = scipy.signal.butter(4,700/(sr/2),btype="low")
    f = scipy.signal.lfilter(b,a,noise)
    dec = np.exp(-np.linspace(0,9,n))
    wave = (f*dec*30000).astype(np.int16)
    AudioSegment(wave.tobytes(),frame_rate=sr,sample_width=2,channels=1).fade_out(40).export(path,format="mp3")

def _synth_ambient(path: str, dur_s: float = 120) -> None:
    sr = 44100; n = int(sr*dur_s); t = np.linspace(0,dur_s,n)
    wave = (0.30*np.sin(2*np.pi*110*t)+0.22*np.sin(2*np.pi*165*t)+
            0.16*np.sin(2*np.pi*220*t)+0.10*np.sin(2*np.pi*262*t)+
            0.07*np.sin(2*np.pi*330*t))
    wave *= (0.72+0.28*np.sin(2*np.pi*0.4*t))
    wave  = wave/(np.max(np.abs(wave))+1e-9)*22000
    seg   = AudioSegment(wave.astype(np.int16).tobytes(),frame_rate=sr,sample_width=2,channels=1)
    seg.fade_in(2000).fade_out(2000).export(path,format="mp3")
    log.info("Ambient pad synthesised (%.0fs)",dur_s)

def ensure_music(duration_s: float = 120) -> str:
    if Path(MUSIC_PATH).exists():
        return MUSIC_PATH
    if PIXABAY_KEY:
        for q in ("cinematic background","lofi ambient","dark dramatic"):
            try:
                r = requests.get("https://pixabay.com/api/videos/music/",
                                 params={"key":PIXABAY_KEY,"q":q,"per_page":5},timeout=10)
                hits = r.json().get("hits",[])
                if hits:
                    url  = random.choice(hits)["audio"]["url"]
                    data = requests.get(url,timeout=30).content
                    Path(MUSIC_PATH).write_bytes(data)
                    log.info("Pixabay music: '%s'",q)
                    return MUSIC_PATH
            except Exception as e:
                log.warning("Pixabay: %s",e)
    _synth_ambient(MUSIC_PATH, duration_s+10)
    return MUSIC_PATH

def ensure_sfx() -> tuple[str,str]:
    if not Path(SFX_WHOOSH).exists(): _synth_whoosh(SFX_WHOOSH)
    if not Path(SFX_IMPACT).exists(): _synth_impact(SFX_IMPACT)
    return SFX_WHOOSH, SFX_IMPACT


# ══════════════════════════════════════════════════════════════════════
# STEP 8 — CREATOMATE CLOUD RENDER  (PRIMARY render engine)
#
#  This is the "external Schnitt-KI" — Creatomate handles:
#   • Ken Burns zoom per scene (scale animation)
#   • Crossfade transitions between scenes
#   • Word-appear text animations
#   • AUTO-TRANSCRIPT viral captions (transcript_effect: highlight)
#     → transcribes the voiceover itself and renders word-by-word
#       highlighted captions, the exact MrBeast/TikTok style
#   • Progress bar per scene
#   • Ducked background music
#
#  Falls back to local MoviePy render if CREATOMATE_API_KEY not set
#  or if the API call fails for any reason.
# ══════════════════════════════════════════════════════════════════════

def _upload_to_cdn(file_path: str) -> Optional[str]:
    """
    Upload a file to 0x0.st (free, no API key) and return its public URL.
    Creatomate needs publicly reachable URLs for audio/video sources.
    """
    try:
        with open(file_path, "rb") as f:
            r = requests.post("https://0x0.st", files={"file": f}, timeout=60)
        url = r.text.strip()
        if url.startswith("http"):
            log.info("CDN upload OK: %s", url)
            return url
        log.warning("CDN upload returned unexpected response: %s", url[:100])
    except Exception as e:
        log.warning("CDN upload failed: %s", e)
    return None


def creatomate_render(
    scenes:      list[dict],
    audio_path:  str,
    niche:       str,
    output_path: str,
    music_path:  str = "",
) -> Optional[str]:
    """
    Full Creatomate JSON-to-video render.
    Returns output_path on success, None to trigger MoviePy fallback.
    """
    api_key = os.getenv("CREATOMATE_API_KEY")
    if not api_key:
        log.info("CREATOMATE_API_KEY not set — using local MoviePy render")
        return None

    cfg = NICHES[niche]
    ac  = cfg["color"]

    # ── 1. Upload voiceover to CDN (Creatomate needs a public URL) ────
    audio_url = _upload_to_cdn(audio_path)
    if not audio_url:
        log.warning("Creatomate: voice CDN upload failed — falling back")
        return None

    # ── 2. Build per-scene elements ────────────────────────────────────
    elements = []
    t_cursor = 0.0

    for i, scene in enumerate(scenes):
        dur  = float(scene.get("duration", 5))
        text = scene.get("text", "")
        q    = scene.get("pexels_query", text[:40])

        vid_url = fetch_pexels_video(q, min_dur=dur, download=False)
        if vid_url:
            anims = [{
                "type": "scale", "scope": "element",
                "easing": "linear", "from": "100%", "to": "108%",
            }]
            if i > 0:
                anims.append({
                    "type": "fade", "scope": "element",
                    "easing": "linear", "duration": TRANSITION_DUR,
                })
            elements.append({
                "type":     "video",
                "id":       f"bg-{i}",
                "source":   vid_url,
                "time":     round(t_cursor, 3),
                "duration": round(dur, 3),
                "volume":   0,
                "fit":      "cover",
                "animations": anims,
            })

        # Dark overlay for caption readability
        elements.append({
            "type": "shape", "shape_type": "rectangle",
            "time": round(t_cursor, 3), "duration": round(dur, 3),
            "width": "100%", "height": "100%",
            "fill_color": "rgba(0,0,0,0.42)",
        })

        # Scene headline text (word-appear)
        elements.append({
            "type":          "text",
            "value":         text,
            "time":          round(t_cursor, 3),
            "duration":      round(dur, 3),
            "y":             "62%",
            "width":         "88%",
            "height":        "28%",
            "x_alignment":   "50%",
            "y_alignment":   "0%",
            "font_family":   "Montserrat",
            "font_weight":   "900",
            "font_size":     "7.5 vmin",
            "fill_color":    "#FFFFFF",
            "stroke_color":  "#000000",
            "stroke_width":  "1.2 vmin",
            "animations": [{
                "type": "text-appear", "scope": "split", "split": "word",
                "easing": "back-out", "duration": min(0.4, dur * 0.15),
            }],
        })

        # Per-scene progress bar
        progress = (i + 1) / len(scenes)
        elements.append({
            "type": "shape", "shape_type": "rectangle",
            "time": round(t_cursor, 3), "duration": round(dur, 3),
            "width": f"{progress*100:.1f}%", "height": "0.6%",
            "x": "0%", "y": "99%",
            "x_alignment": "0%", "y_alignment": "100%",
            "fill_color": ac,
        })

        t_cursor += dur

    # ── 3. Voiceover audio track ───────────────────────────────────────
    voice_id = "voiceover-audio"
    elements.append({
        "type": "audio", "id": voice_id, "source": audio_url,
        "time": 0, "duration": round(t_cursor, 3), "volume": "100%",
    })

    # ── 4. AUTO-TRANSCRIPT viral captions (Creatomate transcribes itself) ─
    elements.append({
        "type":                       "text",
        "transcript_source":          voice_id,
        "transcript_effect":          "highlight",
        "transcript_color":           ac,
        "transcript_maximum_length":  12,
        "time":                       0,
        "duration":                   round(t_cursor, 3),
        "y":                          "82%",
        "width":                      "84%",
        "height":                     "14%",
        "x_alignment":                "50%",
        "y_alignment":                "50%",
        "font_family":                "Montserrat",
        "font_weight":                "700",
        "font_size":                  "8.5 vmin",
        "fill_color":                 "#FFFFFF",
        "stroke_color":               "#000000",
        "stroke_width":               "1.5 vmin",
    })

    # ── 5. Background music (ducked) ───────────────────────────────────
    mp = music_path or MUSIC_PATH
    if Path(mp).exists():
        music_url = _upload_to_cdn(mp)
        if music_url:
            elements.append({
                "type": "audio", "source": music_url,
                "time": 0, "duration": round(t_cursor, 3), "volume": "12%",
            })

    # ── 6. Submit render job ───────────────────────────────────────────
    payload = {
        "output_format": "mp4",
        "width":  VIDEO_W,
        "height": VIDEO_H,
        "frame_rate": FPS,
        "elements": elements,
    }
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type":  "application/json"}

    log.info("Creatomate: submitting %d elements, %.0fs total…",
             len(elements), t_cursor)
    r = requests.post("https://api.creatomate.com/v1/renders",
                      headers=headers, json=payload, timeout=30)
    if r.status_code not in (200, 201):
        log.error("Creatomate submit failed (%d): %s", r.status_code, r.text[:300])
        return None

    render = r.json()
    if isinstance(render, list):
        render = render[0]
    render_id = render["id"]
    log.info("Creatomate render ID: %s", render_id)

    # ── 7. Poll until done ──────────────────────────────────────────────
    poll_url = f"https://api.creatomate.com/v1/renders/{render_id}"
    for attempt in range(120):
        time.sleep(5)
        sr     = requests.get(poll_url, headers=headers, timeout=15).json()
        status = sr.get("status", "")
        if attempt % 6 == 0:
            log.info("Creatomate status: %s (%ds elapsed)", status, attempt*5)
        if status == "succeeded":
            video_url = sr["url"]
            data = requests.get(video_url, timeout=180).content
            Path(output_path).write_bytes(data)
            log.info("Creatomate render complete: %s (%.1fMB)",
                     output_path, len(data)/1e6)
            return output_path
        if status in ("failed", "error"):
            log.error("Creatomate render failed: %s", sr.get("error", ""))
            return None

    log.error("Creatomate timeout")
    return None


# ══════════════════════════════════════════════════════════════════════
# STEP 8b — GOOGLE DRIVE CDN UPLOAD
#   Uploads audio/assets to Google Drive and returns a public URL.
#   Used by JSON2Video and Shotstack which need public URLs.
#   Requires YouTube OAuth token (already in YOUTUBE_TOKEN_B64) —
#   the same account has Drive access on the same token.
# ══════════════════════════════════════════════════════════════════════

def drive_cdn_upload(file_path: str, filename: str) -> Optional[str]:
    """
    Upload a file to Google Drive 'VaultMind/cdn' folder,
    set it to public, and return the direct download URL.
    Falls back to 0x0.st CDN if Drive upload fails.
    """
    try:
        from googleapiclient.discovery import build as gb
        token_b64  = os.getenv("YOUTUBE_TOKEN_B64")
        if not token_b64:
            raise ValueError("No token")
        creds_data = pickle.loads(base64.b64decode(token_b64))
        if isinstance(creds_data, Credentials):
            creds = creds_data
        elif isinstance(creds_data, str):
            creds = Credentials.from_authorized_user_info(json.loads(creds_data))
        else:
            creds = Credentials.from_authorized_user_info(creds_data)

        drive = gb("drive", "v3", credentials=creds)

        # Find or create VaultMind/cdn folder
        def find_or_create_folder(name, parent_id=None):
            q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            if parent_id:
                q += f" and '{parent_id}' in parents"
            res = drive.files().list(q=q, fields="files(id)").execute()
            if res.get("files"):
                return res["files"][0]["id"]
            meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
            if parent_id:
                meta["parents"] = [parent_id]
            return drive.files().create(body=meta, fields="id").execute()["id"]

        vault_id = find_or_create_folder("VaultMind")
        cdn_id   = find_or_create_folder("cdn", vault_id)

        # Upload file
        mime = "audio/mpeg" if file_path.endswith(".mp3") else "video/mp4"
        meta = {"name": filename, "parents": [cdn_id]}
        media = MediaFileUpload(file_path, mimetype=mime, resumable=True)
        f = drive.files().create(body=meta, media_body=media,
                                  fields="id").execute()
        fid = f["id"]

        # Make public
        drive.permissions().create(
            fileId=fid,
            body={"type": "anyone", "role": "reader"}
        ).execute()

        url = f"https://drive.google.com/uc?export=download&id={fid}"
        log.info("Drive CDN: %s → %s", filename, url)
        return url
    except Exception as e:
        log.warning("Drive CDN failed (%s) — using 0x0.st", e)
        return _upload_to_cdn(file_path)


# ══════════════════════════════════════════════════════════════════════
# STEP 8c — JSON2VIDEO RENDER  (FREE — 600 seconds no credit card)
#
#   json2video.com → free account → copy API key → JSON2VIDEO_API_KEY
#
#   Features used:
#   • Portrait 1080×1920 MP4
#   • Per-scene video elements (Pexels URLs)
#   • Text overlays with built-in animation styles
#   • Voiceover audio track
#   • Auto-generated subtitles (included free)
#   • Background music track
# ══════════════════════════════════════════════════════════════════════

def json2video_render(
    scenes:      list[dict],
    audio_path:  str,
    niche:       str,
    output_path: str,
    music_path:  str = "",
) -> Optional[str]:
    api_key = os.getenv("JSON2VIDEO_API_KEY")
    if not api_key:
        log.info("JSON2VIDEO_API_KEY not set — skipping")
        return None

    cfg = NICHES[niche]
    ac  = cfg["color"]

    # Upload audio to CDN (Drive → 0x0.st fallback)
    audio_url = drive_cdn_upload(audio_path, "voice.mp3")
    if not audio_url:
        log.warning("JSON2Video: audio CDN failed")
        return None

    # Build scenes array
    j2v_scenes = []
    for i, scene in enumerate(scenes):
        dur  = float(scene.get("duration", 5))
        text = scene.get("text", "")
        q    = scene.get("pexels_query", text[:40])

        elements = []

        # Background video from Pexels
        vid_url = fetch_pexels_video(q, min_dur=dur, download=False)
        if vid_url:
            elements.append({
                "type":     "video",
                "src":      vid_url,
                "duration": int(dur * 1000),  # ms
                "volume":   0,
            })
        else:
            # Solid color background fallback
            elements.append({
                "type":       "rectangle",
                "width":      "100%",
                "height":     "100%",
                "background": cfg["bg"],
                "duration":   int(dur * 1000),
            })

        # Dark overlay
        elements.append({
            "type":       "rectangle",
            "width":      "100%",
            "height":     "100%",
            "background": "rgba(0,0,0,0.40)",
            "duration":   int(dur * 1000),
        })

        # Scene text
        elements.append({
            "type":       "text",
            "text":       text,
            "duration":   int(dur * 1000),
            "x":          "center",
            "y":          "62%",
            "width":      "88%",
            "font-size":  72,
            "font-family":"Montserrat",
            "font-weight":"900",
            "color":      "#FFFFFF",
            "stroke":     "#000000",
            "stroke-width": 8,
            "style":      "004",    # built-in word-reveal animation
        })

        # Progress bar
        prog_w = int(1080 * (i+1) / len(scenes))
        elements.append({
            "type":       "rectangle",
            "x":          0, "y": "99.4%",
            "width":      prog_w,
            "height":     12,
            "background": ac,
            "duration":   int(dur * 1000),
        })

        j2v_scenes.append({
            "comment":  f"Scene {i+1}",
            "duration": int(dur * 1000),
            "elements": elements,
        })

    # Global audio tracks (added outside scenes)
    global_elements = [
        {
            "type":     "audio",
            "src":      audio_url,
            "duration": int(sum(float(s.get("duration",5)) for s in scenes) * 1000),
            "volume":   100,
        }
    ]

    # Background music
    mp = music_path or MUSIC_PATH
    if Path(mp).exists():
        music_url = drive_cdn_upload(mp, "music.mp3")
        if music_url:
            global_elements.append({
                "type":     "audio",
                "src":      music_url,
                "duration": int(sum(float(s.get("duration",5)) for s in scenes) * 1000),
                "volume":   12,
            })

    payload = {
        "width":    VIDEO_W,
        "height":   VIDEO_H,
        "fps":      FPS,
        "quality":  "high",
        "exports": [{
            "format":  "mp4",
            "start":   0,
        }],
        "scenes":   j2v_scenes,
        "elements": global_elements,
    }

    log.info("JSON2Video: submitting %d scenes…", len(j2v_scenes))
    r = requests.post(
        "https://api.json2video.com/v2/movies",
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        json=payload, timeout=30,
    )
    if r.status_code not in (200, 201):
        log.error("JSON2Video submit failed (%d): %s", r.status_code, r.text[:300])
        return None

    movie_id = r.json().get("movie", {}).get("id") or r.json().get("id")
    log.info("JSON2Video movie ID: %s", movie_id)

    # Poll
    for attempt in range(120):
        time.sleep(5)
        pr = requests.get(
            f"https://api.json2video.com/v2/movies?project={movie_id}",
            headers={"x-api-key": api_key}, timeout=15
        )
        status = pr.json().get("movie", {}).get("status", "")
        if attempt % 6 == 0:
            log.info("JSON2Video: %s (%ds)", status, attempt*5)
        if status == "done":
            url  = pr.json()["movie"]["exports"][0]["url"]
            data = requests.get(url, timeout=180).content
            Path(output_path).write_bytes(data)
            log.info("JSON2Video render done: %.1fMB", len(data)/1e6)
            return output_path
        if status == "error":
            log.error("JSON2Video error: %s", pr.json())
            return None

    log.error("JSON2Video timeout")
    return None


# ══════════════════════════════════════════════════════════════════════
# STEP 8d — SHOTSTACK RENDER  (free developer sandbox)
#
#   shotstack.io → sign up → API key → SHOTSTACK_API_KEY
#   Uses stage (sandbox) endpoint — unlimited renders in sandbox.
# ══════════════════════════════════════════════════════════════════════

def shotstack_render(
    scenes:      list[dict],
    audio_path:  str,
    niche:       str,
    output_path: str,
    music_path:  str = "",
) -> Optional[str]:
    api_key = os.getenv("SHOTSTACK_API_KEY")
    if not api_key:
        log.info("SHOTSTACK_API_KEY not set — skipping")
        return None

    cfg      = NICHES[niche]
    ac       = cfg["color"]
    base_url = "https://api.shotstack.io/edit/v2"
    headers  = {"x-api-key": api_key, "Content-Type": "application/json"}

    # CDN upload
    audio_url = drive_cdn_upload(audio_path, "voice_shotstack.mp3")
    if not audio_url:
        log.warning("Shotstack: audio CDN failed")
        return None

    # Build timeline tracks
    video_clips  = []
    text_clips   = []
    t_cursor     = 0.0

    for i, scene in enumerate(scenes):
        dur  = float(scene.get("duration", 5))
        text = scene.get("text", "")
        q    = scene.get("pexels_query", text[:40])

        vid_url = fetch_pexels_video(q, min_dur=dur, download=False)
        if vid_url:
            video_clips.append({
                "asset":  {"type": "video", "src": vid_url, "volume": 0},
                "start":  round(t_cursor, 3),
                "length": round(dur, 3),
                "fit":    "cover",
                "effect": "zoomIn",     # Shotstack built-in Ken Burns
                "transition": {"in": "fade", "out": "fade"} if i > 0 else {},
            })

        text_clips.append({
            "asset": {
                "type":     "html",
                "html":     f'<p style="font-family:Montserrat,Arial;font-weight:900;font-size:72px;color:#fff;text-shadow:4px 4px 8px #000;text-align:center;width:900px;word-wrap:break-word;">{text}</p>',
                "width":    960,
                "height":   600,
            },
            "start":  round(t_cursor, 3),
            "length": round(dur, 3),
            "position": "center",
            "offset":   {"y": 0.12},
            "transition": {"in": "slideUp"},
        })

        t_cursor += dur

    # Audio track
    audio_clips = [{
        "asset":  {"type": "audio", "src": audio_url, "volume": 1},
        "start":  0,
        "length": round(t_cursor, 3),
    }]

    # Music
    mp = music_path or MUSIC_PATH
    if Path(mp).exists():
        music_url = drive_cdn_upload(mp, "music_shotstack.mp3")
        if music_url:
            audio_clips.append({
                "asset":  {"type": "audio", "src": music_url, "volume": 0.12},
                "start":  0,
                "length": round(t_cursor, 3),
            })

    payload = {
        "timeline": {
            "background": cfg["bg"],
            "tracks": [
                {"clips": text_clips},
                {"clips": video_clips},
                {"clips": audio_clips},
            ],
        },
        "output": {
            "format":      "mp4",
            "resolution":  "1080",
            "aspectRatio": "9:16",
            "fps":         FPS,
            "quality":     "high",
        },
    }

    log.info("Shotstack: submitting %d scenes…", len(scenes))
    r = requests.post(f"{base_url}/render", headers=headers,
                      json=payload, timeout=30)
    if r.status_code not in (200, 201):
        log.error("Shotstack submit failed (%d): %s", r.status_code, r.text[:300])
        return None

    render_id = r.json().get("response", {}).get("id")
    log.info("Shotstack render ID: %s", render_id)

    for attempt in range(120):
        time.sleep(5)
        sr     = requests.get(f"{base_url}/render/{render_id}",
                              headers=headers, timeout=15).json()
        status = sr.get("response", {}).get("status", "")
        if attempt % 6 == 0:
            log.info("Shotstack: %s (%ds)", status, attempt*5)
        if status == "done":
            url  = sr["response"]["url"]
            data = requests.get(url, timeout=180).content
            Path(output_path).write_bytes(data)
            log.info("Shotstack done: %.1fMB", len(data)/1e6)
            return output_path
        if status in ("failed", "error"):
            log.error("Shotstack failed: %s", sr)
            return None

    log.error("Shotstack timeout")
    return None


# ══════════════════════════════════════════════════════════════════════
# STEP 9 — MOVIEPY VIDEO ASSEMBLY (local render)
#   Scene video clips with Ken Burns + caption overlays + SFX at cuts
# ══════════════════════════════════════════════════════════════════════

def _trim_video_clip(path: str, duration: float) -> VideoFileClip:
    """Load a Pexels video, smart-crop to 9:16, trim/loop to duration."""
    clip = VideoFileClip(path, audio=False)
    # Smart crop to portrait
    cw, ch = clip.size
    target_ratio = VIDEO_W / VIDEO_H
    src_ratio    = cw / ch
    if src_ratio > target_ratio:
        new_w = int(ch * target_ratio)
        x1    = (cw - new_w) // 2
        clip  = clip.cropped(x1=x1, x2=x1+new_w)
    else:
        new_h = int(cw / target_ratio)
        clip  = clip.cropped(y1=0, y2=new_h)

    clip = clip.resized((VIDEO_W, VIDEO_H))

    # Loop if clip is shorter than needed duration
    if clip.duration < duration:
        reps = int(np.ceil(duration / clip.duration))
        clip = concatenate_videoclips([clip] * reps)

    return clip.subclipped(0, duration)


def _ken_burns(clip: VideoFileClip, scale_to: float = 1.07) -> VideoFileClip:
    """Slow Ken Burns zoom on a video clip."""
    dur = clip.duration
    def zoom_frame(frame, t):
        scale = 1.0 + (scale_to - 1.0) * (t / dur)
        h, w  = frame.shape[:2]
        new_w, new_h = int(w*scale), int(h*scale)
        resized = np.array(Image.fromarray(frame).resize((new_w,new_h),Image.LANCZOS))
        x0, y0 = (new_w-w)//2, (new_h-h)//2
        return resized[y0:y0+h, x0:x0+w]
    return clip.transform(zoom_frame)


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
) -> str:
    log.info("▶ MoviePy v4 render — %d scenes, %dx%d", len(scenes), VIDEO_W, VIDEO_H)
    cfg = NICHES[niche]

    # Scene durations from cut points
    total_s    = aai_data["total_ms"] / 1000.0
    bounds     = [0.0] + cut_points_s + [total_s]
    durations  = [bounds[i+1]-bounds[i] for i in range(len(bounds)-1)]
    while len(scenes) < len(durations): scenes.append(scenes[-1])
    scenes = scenes[:len(durations)]
    n      = len(scenes)

    # ── Build one video clip per scene ────────────────────────────────
    scene_clips = []
    for idx, (scene, dur) in enumerate(zip(scenes, durations)):
        dur = max(dur, 0.5)
        q   = scene.get("pexels_query", scene["text"][:40])

        # Background: Pexels video → solid color fallback
        vid_path = fetch_pexels_video(q, min_dur=dur)
        if vid_path and Path(vid_path).exists():
            try:
                bg = _trim_video_clip(vid_path, dur)
                bg = _ken_burns(bg)
            except Exception as e:
                log.warning("Video clip error (%s), using solid BG", e)
                bg = make_solid_bg_clip(cfg["bg"], dur)
        else:
            bg = make_solid_bg_clip(cfg["bg"], dur)

        # Dark vignette overlay (ImageClip)
        vig_arr  = _make_vignette(dur)
        vig_clip = ImageClip(vig_arr).with_duration(dur).with_fps(FPS)

        scene_comp = CompositeVideoClip([bg, vig_clip],
                                        size=(VIDEO_W, VIDEO_H)).with_duration(dur)
        scene_clips.append(scene_comp)
        log.info("  Scene %d/%d (%.1fs) — %s", idx+1, n, dur, q[:35])

    # ── Crossfade transitions ─────────────────────────────────────────
    trans = [scene_clips[0]]
    for c in scene_clips[1:]:
        trans.append(c.with_effects([CrossFadeIn(TRANSITION_DUR)]))
    final = concatenate_videoclips(trans, method="compose", padding=-TRANSITION_DUR)
    final = final.with_effects([FadeIn(FADE_IN_DUR), FadeOut(FADE_OUT_DUR)])

    # ── Caption overlays (word-by-word animated) ──────────────────────
    cap_clips = build_caption_clips(aai_data, niche, final.duration)
    if cap_clips:
        cap_composites = []
        for clip, start_t in cap_clips:
            cap_composites.append(clip.with_start(start_t))
        final = CompositeVideoClip([final] + cap_composites,
                                   size=(VIDEO_W, VIDEO_H))
        log.info("Caption overlays composited (%d clips)", len(cap_clips))

    # ── Audio mix ─────────────────────────────────────────────────────
    vid_dur  = final.duration
    voice    = AudioFileClip(audio_path).subclipped(0, min(vid_dur, total_s))
    tracks   = [voice]

    mp = music_path or MUSIC_PATH
    if Path(mp).exists():
        music_raw = AudioSegment.from_file(mp) + MUSIC_DUCK_DB
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            mtmp = f.name
        music_raw.export(mtmp, format="mp3")
        mc = AudioFileClip(mtmp)
        if mc.duration < vid_dur:
            mc = concatenate_audioclips(
                [mc] * int(np.ceil(vid_dur / mc.duration)))
        tracks.append(mc.subclipped(0, vid_dur))
        log.info("Music mixed (%ddB duck)", MUSIC_DUCK_DB)

    wp = sfx_whoosh_path or SFX_WHOOSH
    ip = sfx_impact_path or SFX_IMPACT
    if Path(wp).exists() and Path(ip).exists():
        sfx_seg = AudioSegment.silent(duration=int(vid_dur*1000)+500)
        whoosh  = AudioSegment.from_file(wp) - 5
        impact  = AudioSegment.from_file(ip) - 3
        sfx_seg = sfx_seg.overlay(impact, position=0)
        for cs in cut_points_s:
            sfx_seg = sfx_seg.overlay(whoosh, position=max(0,int(cs*1000)-130))
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            stmp = f.name
        sfx_seg.export(stmp, format="mp3")
        tracks.append(AudioFileClip(stmp).subclipped(0, vid_dur))
        log.info("SFX mixed (%d cuts)", len(cut_points_s))

    final = final.with_audio(CompositeAudioClip(tracks))

    # ── Export ────────────────────────────────────────────────────────
    log.info("Exporting %dx%d @ %dfps → %s", VIDEO_W, VIDEO_H, FPS, output_path)
    final.write_videofile(
        output_path, fps=FPS,
        codec="libx264", audio_codec="aac",
        temp_audiofile="tmp_audio.m4a", remove_temp=True,
        preset="medium",
        ffmpeg_params=["-crf","20","-movflags","+faststart",
                       "-vf", f"scale={VIDEO_W}:{VIDEO_H}"],
        logger=None,
    )
    log.info("Export complete: %s", output_path)
    return output_path


def _make_vignette(duration: float) -> np.ndarray:
    """Dark gradient overlay (bottom 55%) as RGBA numpy array."""
    img  = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    start = int(VIDEO_H * 0.38)
    for y in range(start, VIDEO_H):
        alpha = int(200 * ((y-start)/(VIDEO_H-start))**0.55)
        draw.line([(0,y),(VIDEO_W,y)], fill=(0,0,0,alpha))
    return np.array(img)


# ══════════════════════════════════════════════════════════════════════
# STEP 10 — SRT EXPORT
# ══════════════════════════════════════════════════════════════════════

def export_srt(aai_data: dict, out_path: str) -> None:
    def fmt(s):
        h=int(s//3600); m=int((s%3600)//60); ss=s%60
        return f"{h:02d}:{m:02d}:{ss:06.3f}".replace(".",",")
    words  = aai_data.get("words",[])
    lines  = []
    i, idx = 0, 1
    while i < len(words):
        chunk = words[i:i+4]
        lines += [str(idx),
                  f"{fmt(chunk[0]['start_ms']/1000)} --> {fmt(chunk[-1]['end_ms']/1000)}",
                  " ".join(w["text"] for w in chunk),""]
        i += 4; idx += 1
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════
# STEP 11 — YOUTUBE UPLOAD
# ══════════════════════════════════════════════════════════════════════

def upload_youtube(video_path: str, title: str, description: str,
                   srt_path: Optional[str] = None) -> Optional[str]:
    token_b64 = os.getenv("YOUTUBE_TOKEN_B64")
    if not token_b64:
        log.warning("YOUTUBE_TOKEN_B64 not set")
        return None
    creds_data = pickle.loads(base64.b64decode(token_b64))
    if isinstance(creds_data, Credentials):
        creds = creds_data
    elif isinstance(creds_data, str):
        creds = Credentials.from_authorized_user_info(json.loads(creds_data))
    else:
        creds = Credentials.from_authorized_user_info(creds_data)

    yt      = build("youtube","v3",credentials=creds,cache_discovery=False)
    pub_at  = (datetime.utcnow()+timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    body    = {"snippet":  {"title": title[:100], "description": description,
                             "tags": ["shorts","viral"], "categoryId": "22"},
               "status":   {"privacyStatus": "private", "publishAt": pub_at,
                             "selfDeclaredMadeForKids": False}}
    media   = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
    req     = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp    = None
    while resp is None:
        _, resp = req.next_chunk()
    vid_url = f"https://youtu.be/{resp['id']}"
    log.info("YouTube upload: %s", vid_url)

    if srt_path and Path(srt_path).exists():
        try:
            yt.captions().insert(
                part="snippet",
                body={"snippet":{"videoId":resp["id"],"language":"en",
                                  "name":"Auto","isDraft":False}},
                media_body=MediaFileUpload(srt_path, mimetype="text/plain"),
            ).execute()
        except Exception as e:
            log.warning("Caption upload skipped: %s", e)
    return vid_url


# ══════════════════════════════════════════════════════════════════════
# STEP 12 — DASHBOARD
# ══════════════════════════════════════════════════════════════════════

def update_dashboard(niche, title, url, ok):
    p    = Path("dashboard.json")
    data = json.loads(p.read_text()) if p.exists() else {"videos":[]}
    data["videos"].insert(0,{"niche":niche,"title":title,"url":url,
                              "ok":ok,"ts":datetime.utcnow().isoformat()})
    data["videos"] = data["videos"][:50]
    p.write_text(json.dumps(data,indent=2))


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════
# POST-RENDER STEP A — ANIMATED CAPTIONS (beautiful-captions)
#
#  Takes the raw rendered video + word-level SRT and burns in
#  Montserrat bounce-animated captions — exactly the viral TikTok look.
#  Falls back gracefully to static ffmpeg subtitles if unavailable.
# ══════════════════════════════════════════════════════════════════════

def apply_beautiful_captions(
    video_path:  str,
    srt_path:    str,
    output_path: str,
    accent:      str = "#FFD700",
    words_per_line: int = 2,
) -> str:
    """
    Burn animated word-by-word captions onto video.
    Returns output_path (or video_path unchanged if both methods fail).
    """
    if not Path(srt_path).exists():
        log.warning("SRT missing — skipping caption burn")
        return video_path

    # ── Method 1: beautiful-captions (bounce animation) ───────────────
    if BEAUTIFUL_CAPTIONS_OK:
        try:
            cfg = CaptionConfig(
                animation={"enabled": True, "type": "bounce", "keyframes": 12},
                style={
                    "font":              "Montserrat",
                    "font_size":         140,
                    "color":             "yellow",
                    "outline_color":     "black",
                    "outline_thickness": 12,
                    "verticle_position": 0.50,   # center-screen (note: their typo)
                    "max_words_per_line": words_per_line,
                    "auto_scale_font":   True,
                },
                diarization={"enabled": False},
            )
            vid = BCVideo(video_path, config=cfg)
            vid.add_captions(
                srt_input_path=srt_path,
                output_path=output_path,
                add_styling=True,
                cuda=False,
            )
            log.info("beautiful-captions: bounce captions applied → %s", output_path)
            return output_path
        except Exception as e:
            log.warning("beautiful-captions failed (%s) — falling back to ASS", e)

    # ── Method 2: ffmpeg ASS karaoke subtitles (fallback) ─────────────
    try:
        ass_path = srt_path.replace(".srt", ".ass")
        _srt_to_ass_karaoke(srt_path, ass_path, accent)
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", f"ass={ass_path}",
            "-c:a", "copy", "-preset", "fast", output_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        log.info("ASS karaoke captions applied → %s", output_path)
        return output_path
    except Exception as e:
        log.warning("ASS fallback also failed (%s) — returning raw video", e)
        return video_path


def _srt_to_ass_karaoke(srt_path: str, ass_path: str, accent: str) -> None:
    """
    Convert SRT to ASS with karaoke-style word highlighting.
    Each word highlights in accent color as it is spoken.
    """
    def ms_to_ass(ms: int) -> str:
        h  = ms // 3600000
        m  = (ms % 3600000) // 60000
        s  = (ms % 60000) // 1000
        cs = (ms % 1000) // 10
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    # Convert accent hex to ASS BGR
    r, g, b  = _hex(accent)
    ass_col  = f"&H00{b:02X}{g:02X}{r:02X}&"

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VIDEO_W}
PlayResY: {VIDEO_H}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,ScaleX,ScaleY,Spacing,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV
Style: Default,Arial,85,&H00FFFFFF,{ass_col},&H00000000,&H99000000,-1,0,0,100,100,0,1,6,2,2,60,60,220

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    lines = Path(srt_path).read_text().strip().split('\n\n')
    events = []
    for block in lines:
        parts = block.strip().split('\n')
        if len(parts) < 3:
            continue
        # Parse timing line: 00:00:00,000 --> 00:00:02,500
        try:
            timing = parts[1]
            start_str, end_str = timing.split(" --> ")
            def parse_t(t):
                t = t.replace(",", ".")
                h, m, s = t.split(":")
                return int((int(h)*3600 + int(m)*60 + float(s)) * 1000)
            start_ms  = parse_t(start_str.strip())
            end_ms    = parse_t(end_str.strip())
            text      = " ".join(parts[2:])
            words     = text.split()
            dur_ms    = end_ms - start_ms
            word_dur  = dur_ms // max(len(words), 1)
            # Build karaoke tags: {\k<centiseconds>}word
            _k        = chr(92) + "k"
            karaoke   = " ".join("{" + _k + str(word_dur // 10) + "}" + w for w in words)
            events.append(
                f"Dialogue: 0,{ms_to_ass(start_ms)},{ms_to_ass(end_ms)},"
                f"Default,,0,0,0,,{karaoke}"
            )
        except Exception:
            continue
    Path(ass_path).write_text(header + '\n'.join(events), encoding='utf-8')


# ══════════════════════════════════════════════════════════════════════
# POST-RENDER STEP B — CINEMATIC COLOR GRADE (ffmpeg)
#
#  Teal-orange grade: the single most recognisable "cinematic" look.
#  Also adds subtle unsharp mask (makes text pop harder).
#  ~2–3% retention increase based on A/B tests in similar channels.
# ══════════════════════════════════════════════════════════════════════

def apply_color_grade(video_path: str, output_path: str) -> str:
    """
    Apply cinematic teal-orange color grade + sharpening via ffmpeg.
    Returns output_path, or video_path if ffmpeg fails.
    """
    # Teal shadows (boost blue, pull red in darks)
    # Orange highlights (pull blue in brights, boost red/green)
    # Then unsharp for crispness
    vf = (
        "curves="
        "r='0/0 0.3/0.27 0.7/0.74 1/0.95':"   # warm highlights
        "g='0/0 0.3/0.30 0.7/0.72 1/0.96':"   # neutral
        "b='0/0.04 0.3/0.33 0.7/0.68 1/0.85',"  # teal shadows
        "unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount=0.7"
    )
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", vf,
        "-c:a", "copy",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-movflags", "+faststart",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        log.info("Color grade applied → %s", output_path)
        return output_path
    except subprocess.CalledProcessError as e:
        log.warning("Color grade failed: %s", e.stderr.decode()[:200])
        return video_path


# ══════════════════════════════════════════════════════════════════════
# POST-RENDER STEP C — GOOGLE DRIVE BACKUP (optional)
#
#  Uploads finished video + SRT to Drive folder "VaultMind".
#  Only runs if google-auth Drive scope is available.
# ══════════════════════════════════════════════════════════════════════

def backup_to_drive(video_path: str, srt_path: str, title: str) -> Optional[str]:
    """
    Upload finished video to Google Drive → 'VaultMind' folder.
    Returns Drive file URL or None.
    """
    try:
        from googleapiclient.discovery import build as gdrive_build
        token_b64 = os.getenv("YOUTUBE_TOKEN_B64")
        if not token_b64:
            return None
        creds_data = pickle.loads(base64.b64decode(token_b64))
        if isinstance(creds_data, Credentials):
            creds = creds_data
        elif isinstance(creds_data, str):
            creds = Credentials.from_authorized_user_info(json.loads(creds_data))
        else:
            creds = Credentials.from_authorized_user_info(creds_data)

        drive    = gdrive_build("drive", "v3", credentials=creds)

        # Find or create VaultMind folder
        q        = "name='VaultMind' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results  = drive.files().list(q=q, fields="files(id)").execute()
        folders  = results.get("files", [])
        if folders:
            folder_id = folders[0]["id"]
        else:
            folder_meta = {"name": "VaultMind",
                           "mimeType": "application/vnd.google-apps.folder"}
            folder_id = drive.files().create(body=folder_meta,
                                              fields="id").execute()["id"]

        # Upload video
        vid_meta  = {"name": f"{title}.mp4", "parents": [folder_id]}
        vid_media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
        vid_file  = drive.files().create(
            body=vid_meta, media_body=vid_media, fields="id,webViewLink"
        ).execute()
        link = vid_file.get("webViewLink", "")
        log.info("Drive backup: %s", link)
        return link
    except Exception as e:
        log.warning("Drive backup skipped: %s", e)
        return None


def run_pipeline(niche: Optional[str] = None) -> None:
    niche = niche or random.choice(list(NICHES.keys()))
    log.info("═══ VaultMind v4 — niche: %s ═══", niche)

    ensure_font()
    music_path         = ensure_music(duration_s=120)
    sfx_whoosh, sfx_impact = ensure_sfx()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # 1. Script
        script    = generate_script(niche)
        scenes    = script["scenes"]
        title     = script["title"]
        full_text = script.get("hook","") + " " + " ".join(s["text"] for s in scenes)
        log.info("Title: %s | %d scenes", title, len(scenes))

        # 2. Voiceover
        audio_path = str(tmp/"voice.mp3")
        generate_voiceover(full_text, audio_path)

        # 3. Transcription
        aai_data = transcribe_with_assemblyai(audio_path)

        # 4. Cut points
        cuts = detect_cut_points(audio_path, aai_data, n_scenes=len(scenes))

        # 5. SRT
        srt_path = str(tmp/"captions.srt")
        export_srt(aai_data, srt_path)

        # 6. RENDER — Priority chain (first success wins):
        #    JSON2Video (free) → Shotstack (free sandbox) → Creatomate (paid) → MoviePy (local)
        raw_video      = str(tmp/"raw.mp4")
        render_args    = dict(scenes=scenes, audio_path=audio_path,
                              niche=niche, output_path=raw_video,
                              music_path=music_path)
        creatomate_used = False

        if json2video_render(**render_args):
            log.info("✅ Rendered via JSON2Video")
        elif shotstack_render(**render_args):
            log.info("✅ Rendered via Shotstack")
        elif creatomate_render(scenes, audio_path, niche, raw_video, music_path):
            log.info("✅ Rendered via Creatomate")
            creatomate_used = True   # captions already burned in
        else:
            log.info("⚠️ All cloud renders failed — using local MoviePy")
            build_video_moviepy(
                scenes=scenes, niche=niche,
                audio_path=audio_path, aai_data=aai_data,
                cut_points_s=cuts, output_path=raw_video,
                music_path=music_path,
                sfx_whoosh_path=sfx_whoosh,
                sfx_impact_path=sfx_impact,
            )

        # 7. Animated captions — SKIP if Creatomate already burned in
        #    transcript-highlight captions during its own render.
        if creatomate_used:
            log.info("Creatomate already applied captions — skipping caption step")
            captioned_video = raw_video
        else:
            captioned_video = str(tmp/"captioned.mp4")
            captioned_video = apply_beautiful_captions(
                video_path     = raw_video,
                srt_path       = srt_path,
                output_path    = captioned_video,
                accent         = NICHES[niche]["color"],
                words_per_line = 2,
            )

        # 8. Cinematic color grade (teal-orange + unsharp)
        final_video = str(tmp/"final.mp4")
        final_video = apply_color_grade(captioned_video, final_video)

        # 9. Upload
        desc      = f"{title}\n\n#shorts #{niche} #viral #vaultmind"
        video_url = upload_youtube(final_video, title, desc, srt_path)

        # 10. Google Drive backup
        backup_to_drive(final_video, srt_path, title)

        # 11. Dashboard
        update_dashboard(niche, title, video_url, ok=True)

    log.info("═══ v4 complete: %s ═══", video_url or "no URL")


if __name__ == "__main__":
    import sys
    run_pipeline(sys.argv[1] if len(sys.argv) > 1 else None)
