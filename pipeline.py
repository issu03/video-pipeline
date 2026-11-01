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

import os, re, json, time, base64, pickle, random, logging, tempfile, textwrap
import subprocess, shutil, resource
from io import BytesIO
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
import scipy.signal
from pydub import AudioSegment
from pydub.silence import detect_silence

from moviepy import (
    VideoFileClip, ImageClip, AudioFileClip,
    CompositeVideoClip, concatenate_videoclips,
    CompositeAudioClip, concatenate_audioclips,
)
from moviepy.video.fx import FadeIn, FadeOut, CrossFadeIn, CrossFadeOut
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
MIN_SCENES         = 12        # FLOOR only — real target is "as many scenes as the story
                                # supports" (see generate_script prompt), not a fixed count
TARGET_DUR         = 85        # soft target, NOT a monetization requirement — YouTube has
                                # no minimum Shorts length for ad eligibility. Script prompt
                                # now flexes 40-90s so simple facts aren't padded with filler
PUBLISH_HOUR_UTC   = int(os.getenv("PUBLISH_HOUR_UTC", "22"))
                                # target publish hour (UTC), overridable per workflow run —
                                # daily_video.yml now sets this per slot: ~17 UTC (midday
                                # US audience window) or ~22 UTC (evening US window), based
                                # on research-backed Shorts engagement peaks (12-3pm & 7-9pm
                                # local). Falls back to 22 if not set (e.g. local runs).
TRANSITION_DUR     = 0.22      # snappier cuts — matches faster viral edit rhythm
FADE_IN_DUR        = 0.3
FADE_OUT_DUR       = 0.5
MUSIC_DUCK_DB      = -12      # background-music gain in the mix. Was -16: the music is now 4 dB
                              # louder (a Jamendo track sits at about -32 dBFS instead of -36)
CAPTION_WORDS      = 3        # words shown per caption chunk
CAPTION_FONT_SIZE  = 95       # big & punchy

GAMEPLAY_PATH = os.getenv("GAMEPLAY_PATH", "gameplay_bg.mp4")
MUSIC_PATH    = os.getenv("MUSIC_PATH",    "bg_music.mp3")
# Cuts get NO sound effect any more. The "Piep" came from the whoosh (a sine
# sweeping 3200 -> 180 Hz = a beep) and the pop that were laid on every cut.
# Only a soft thud under the very first frame is left. New file name, so a
# stale committed sfx_impact.mp3 from an older version can't come back.
SFX_IMPACT    = "sfx_impact_v2.mp3"
PIXABAY_KEY   = os.getenv("PIXABAY_KEY", "")

# CORRECTION: Pixabay has no public API for its music library at all — only
# images and videos (https://pixabay.com/api/docs/ covers exactly those two).
# The "https://pixabay.com/api/videos/music/" endpoint used previously
# doesn't exist and always returned an empty/HTML response, which is why
# music always fell back to the synthesised ambient pad regardless of
# PIXABAY_KEY. Jamendo has a real, free, verified public API for CC-licensed
# music (client_id only, no OAuth needed for search/download).
JAMENDO_CLIENT_ID = os.getenv("JAMENDO_CLIENT_ID", "")
FONT_PATH     = "font_bold.ttf"

# ── Groq models (free tier) ────────────────────────────────────────────
# Groq retired the models this pipeline used before — llama-3.3-70b-versatile
# (08/16/26), meta-llama/llama-4-scout-17b-16e-instruct (07/17/26) and even
# their suggested vision stand-in qwen/qwen3.6-27b (09/14/26); see
# console.groq.com/docs/deprecations. Current replacements:
#  - GROQ_TEXT_MODEL: reasoning model. Its thinking tokens count against
#    max_completion_tokens, so every call below sets reasoning_effort="low"
#    (valid: low/medium/high — there is no "none") and a generous token cap.
#  - GROQ_FAST_MODEL: the small sibling for the per-scene Pexels query —
#    faster/cheaper for a 4-word answer, and Groq lists rate limits per model.
#  - GROQ_VISION_MODEL: Groq's only vision model; max 3 images per request.
#    reasoning_effort="none" = instruct mode, no thinking tokens.
# Free tier for all three: 30 RPM / 1K RPD / 8K TPM / 200K TPD.
GROQ_TEXT_MODEL   = "openai/gpt-oss-120b"
GROQ_FAST_MODEL   = "openai/gpt-oss-20b"
GROQ_VISION_MODEL = "qwen/qwen3.8-27b"

# ── TikTok upload — Content Posting API (official, kept for reference) ─
# 2026-09: TikTok rejected this app for production — "does not support
# personal or internal company use." Stays SELF_ONLY forever for this
# account, so it's not called by run_pipeline() anymore. See
# upload_tiktok() further down for the active path (via Zernio).
# Same "refresh token stored as a secret" shape as YOUTUBE_TOKEN_B64, but
# TikTok's access_token only lives 24h so it's refreshed on every run
# (see _tiktok_refresh_access_token). One-time setup: run
# tiktok_oauth_setup.py locally once to obtain these three values.
# IMPORTANT: until this API client passes TikTok's own audit, every post
# is forced to SELF_ONLY (private) — upload_tiktok_official() reads whatever
# privacy level TikTok's creator_info/query actually allows right now and
# uses that, so it starts posting PUBLIC_TO_EVERYONE automatically the
# moment the audit clears, with no code change needed.
TIKTOK_CLIENT_KEY    = os.getenv("TIKTOK_CLIENT_KEY", "")
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "")
TIKTOK_REFRESH_TOKEN = os.getenv("TIKTOK_REFRESH_TOKEN", "")
TIKTOK_CHUNK_SIZE    = 10_000_000   # bytes/chunk — inside TikTok's 5-64MB rule

# ── Self-learning feedback loop (own video performance) ────────────────
# Simple public API key (Google Cloud Console → APIs & Services →
# Credentials → "API key", YouTube Data API v3 enabled), NOT the OAuth
# token used for uploading. Free, no extra scope/re-auth needed since
# view/like/comment counts are public data.
YOUTUBE_API_KEY            = os.getenv("YOUTUBE_API_KEY", "")
PERFORMANCE_FILE           = "performance_history.json"
PERFORMANCE_CHECK_DELAY_D  = 3     # wait this long after upload before scoring
PERFORMANCE_HISTORY_CAP    = 300   # keep at most this many scored videos
NICHE_EXPLORATION_FLOOR    = 0.30  # worst niche still gets >=30% of best niche's odds

NICHES = {
    # Each niche has:
    #  color/glow/bg — visual identity
    #  color_grade   — ffmpeg curves filter for cinematic look
    #  video_queries — pool of Pexels search terms rotated randomly
    #                  (avoids same footage appearing every video)
    "reddit": {
        "color": "#FF4500", "glow": "#FF6633", "bg": "#1A1A2E",
        "color_grade": (
            "curves=r='0/0 0.5/0.55 1/0.95':g='0/0 0.5/0.48 1/0.88':b='0/0 0.5/0.44 1/0.80',"
            "unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount=0.6"
        ),
        "video_queries": [
            "person alone dark room", "dramatic phone screen night",
            "crowded subway strangers", "rainy window city night",
            "person walking alone", "shadow dramatic lighting",
            "empty hallway dark", "late night city",
        ],
    },
    "dating": {
        "color": "#FF69B4", "glow": "#FF90C8", "bg": "#1A1A2E",
        "color_grade": (
            "curves=r='0/0.02 0.5/0.58 1/0.98':g='0/0 0.5/0.46 1/0.88':b='0/0 0.5/0.50 1/0.90',"
            "unsharp=luma_msize_x=3:luma_msize_y=3:luma_amount=0.4"
        ),
        "video_queries": [
            "romantic couple bokeh", "love hands holding",
            "couple coffee date", "sunset romantic silhouette",
            "first date restaurant", "flowers bokeh soft",
            "couple walking city", "heartbreak alone rain",
        ],
    },
    "rich": {
        "color": "#FFD700", "glow": "#FFEC80", "bg": "#0D0D0D",
        "color_grade": (
            "curves=r='0/0 0.3/0.32 0.7/0.78 1/1.0':g='0/0 0.3/0.30 0.7/0.72 1/0.96':"
            "b='0/0 0.3/0.22 0.7/0.58 1/0.78',"  # warm golden tone, pull blue
            "unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount=0.7"
        ),
        "video_queries": [
            "luxury mansion interior", "private jet interior",
            "sports car driving", "rolex watch close",
            "penthouse city view", "yacht ocean luxury",
            "cash money briefcase", "monaco luxury lifestyle",
        ],
    },
    "lifehack": {
        "color": "#00FF88", "glow": "#66FFBB", "bg": "#111111",
        "color_grade": (
            "curves=r='0/0 0.5/0.50 1/0.95':g='0/0 0.5/0.55 1/1.0':b='0/0 0.5/0.48 1/0.88',"
            "unsharp=luma_msize_x=3:luma_msize_y=3:luma_amount=0.5"
        ),
        "video_queries": [
            "person working laptop coffee", "productivity desk setup",
            "person writing notes", "organized workspace minimal",
            "hands typing keyboard", "brainstorm whiteboard",
            "morning routine person", "phone productivity app",
        ],
    },
    "fact": {
        "color": "#00BFFF", "glow": "#66D9FF", "bg": "#0A0A1A",
        "color_grade": (
            "curves=r='0/0 0.3/0.27 0.7/0.72 1/0.92':"
            "g='0/0 0.3/0.30 0.7/0.74 1/0.95':"
            "b='0/0.05 0.3/0.38 0.7/0.78 1/1.0',"  # cool blue science feel
            "unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount=0.8"
        ),
        "video_queries": [
            "galaxy stars universe timelapse", "ocean deep underwater",
            "earth from space", "human brain neurons",
            "microscope cells biology", "lightning storm dramatic",
            "volcano eruption lava", "coral reef ocean life",
        ],
    },
    "scary": {
        "color": "#FF2222", "glow": "#FF6666", "bg": "#050505",
        "color_grade": (
            "curves=r='0/0 0.3/0.35 0.7/0.75 1/0.95':"
            "g='0/0 0.3/0.22 0.7/0.55 1/0.78':"
            "b='0/0 0.3/0.22 0.7/0.55 1/0.78',"  # desaturated red horror
            "unsharp=luma_msize_x=7:luma_msize_y=7:luma_amount=1.0"
        ),
        "video_queries": [
            "dark forest fog night", "abandoned building corridor",
            "horror shadows flickering", "empty road night fog",
            "old door creaking dark", "basement dark stairs",
            "person alone dark house", "storm lightning night",
        ],
    },
    "motivation": {
        "color": "#FF8C00", "glow": "#FFAA44", "bg": "#0D0D0D",
        "color_grade": (
            "curves=r='0/0 0.3/0.35 0.7/0.80 1/1.0':"
            "g='0/0 0.3/0.30 0.7/0.72 1/0.92':"
            "b='0/0 0.3/0.20 0.7/0.55 1/0.78',"  # warm orange energy
            "unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount=0.7"
        ),
        "video_queries": [
            "athlete training gym intense", "runner marathon sunrise",
            "person climbing mountain", "winner podium celebration",
            "bodybuilder workout sweat", "sunrise horizon epic",
            "boxing training fight", "marathon finish line",
        ],
    },
    "conspiracy": {
        "color": "#9B59B6", "glow": "#C285E0", "bg": "#070713",
        "color_grade": (
            "curves=r='0/0 0.3/0.25 0.7/0.65 1/0.85':"
            "g='0/0 0.3/0.22 0.7/0.58 1/0.80':"
            "b='0/0.04 0.3/0.35 0.7/0.70 1/0.95',"  # dark purple mystery
            "unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount=0.9"
        ),
        "video_queries": [
            "government building shadows", "cctv surveillance camera",
            "secret document papers", "person shadows mystery",
            "eye surveillance dramatic", "underground tunnel dark",
            "newspaper headlines dramatic", "shadow figure silhouette",
        ],
    },
    "learn": {
        "color": "#1ABC9C", "glow": "#4DDFC4", "bg": "#0D1B2A",
        "color_grade": (
            "curves=r='0/0 0.3/0.28 0.7/0.72 1/0.93':"
            "g='0/0 0.3/0.32 0.7/0.75 1/0.97':"
            "b='0/0.02 0.3/0.33 0.7/0.70 1/0.92',"  # clean teal academic
            "unsharp=luma_msize_x=3:luma_msize_y=3:luma_amount=0.5"
        ),
        "video_queries": [
            "student studying library", "books open knowledge",
            "science experiment lab", "technology innovation future",
            "whiteboard teaching class", "person reading focus",
            "coding computer screen", "universe stars learning",
        ],
    },
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

def recent_titles(n: int = 15) -> list[str]:
    """Last N titles already posted (any niche), for prompt-level dedup —
    stops the exact-duplicate problem (e.g. one title shipped 3x)."""
    p = Path("dashboard.json")
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except Exception:
        return []
    return [v["title"] for v in data.get("videos", [])[:n] if v.get("title")]


def _json_from_llm(raw: Optional[str]) -> dict:
    """Parses the JSON object out of an LLM reply. Tolerates ```json fences or
    a stray sentence around the object — more likely with a reasoning model
    than with the llama models this pipeline used before. Raises ValueError
    (json.JSONDecodeError is one) when there is nothing parsable, e.g. an
    empty reply because the thinking tokens used up the whole token cap."""
    raw = (raw or "").strip()
    i, j = raw.find("{"), raw.rfind("}")
    if i != -1 and j > i:
        raw = raw[i:j + 1]
    return json.loads(raw)


# Labels the model must never echo into a title/hook — safety net in case it
# still leaks one despite the STYLE LEAK RULE in the system prompt below.
_HOOK_LABEL_LEAK_PATTERNS = ("OUTCOME-FIRST", "QUESTION/WHY-HOW", "CONTRARIAN")


def generate_script(niche: str) -> dict:
    """
    Script generation tuned to 2026 viral-format research:
    - Outcome/result-first hooks (or Question/Why-How — both top performers)
    - Third-person narration (narrate, don't star)
    - FAST CUT PACING: many short scenes (2.5-4.5s each); total runtime
      flexes 40-90s based on how much the topic genuinely supports — no
      padding just to hit a fixed length
    - 8-12 hashtags generated as part of the script (7-10 niche + 1-2 generic,
      no #fyp — TikTok-only convention with no YouTube discovery value)
    - Mid-video "re-hooks" every ~15-20s to fight drop-off, each worded
      differently so scripts stop sounding interchangeable
    - Ends on a loop-back or open question to encourage rewatches
    """
    client = Groq(api_key=os.environ["GROQ_API_KEY"])

    hook_styles = [
        ("outcome_first",
         "Open by showing/stating the shocking result or fact immediately — "
         "the viewer must know the payoff within 2 seconds."),
        ("question_why_how",
         "Open with a sharp specific question the viewer needs answered "
         "('Why does X happen?', 'How is X even possible?')."),
        ("contrarian",
         "Open by contradicting a widely-held belief ('Everyone thinks X. "
         "They're wrong.')."),
    ]
    chosen_hook_name, chosen_hook_instruction = random.choice(hook_styles)

    system = (
        "You are a viral YouTube Shorts / TikTok scriptwriter optimized for "
        "2026 platform research data. Return ONLY valid JSON, no markdown, "
        "no preamble. "
        "Format: {title, hook, tone, hashtags: [string], scenes: [{text, duration, pexels_query}]} "
        ""
        "TONE RULE: tone = how the narrator should SOUND for this script, exactly "
        "one of: " + ", ".join(VOICE_TONES) + ". "
        + " ".join(f"{name} = {t['about']}." for name, t in VOICE_TONES.items()) +
        " Pick the one that fits this script's topic and mood. "
        ""
        "HOOK RULE — the opening line must follow this instruction: "
        f"{chosen_hook_instruction} "
        "STYLE LEAK RULE: that instruction is for YOU only — never print a "
        "style label (the words 'OUTCOME-FIRST', 'QUESTION/WHY-HOW', or "
        "'CONTRARIAN') anywhere in the title, hook, or scene text. "
        ""
        "PACING RULE (critical — this is what separates viral from flat): "
        "- Let the STORY set the length — most scripts land at 12-26 scenes "
        "and 40-90 seconds total. Never pad with filler scenes to hit a "
        "target; a tight 45s script beats a stretched, draggy 90s one. "
        "- EACH scene is 2.5-4.5 seconds, ONE short punchy sentence or fragment "
        "- Never let two consecutive scenes run long — vary rhythm like a "
        "real edit: short-short-medium-short "
        "- On longer scripts, every 4-5 scenes insert a mini cliffhanger or "
        "re-hook line to fight viewer drop-off. Vary the wording every time "
        "— draw on different angles, e.g. 'But here's the part nobody talks "
        "about...', 'And it gets weirder...', 'Here's where it gets dark...', "
        "'Nobody expected what happened next...', 'That's not even the "
        "strangest part...', 'Stay with me, this changes everything...'. "
        "Never reuse the same re-hook line twice in one script. "
        "- LOOP RULE: end on a line that flows naturally back into the "
        "opening hook, or on an open question — aim for an instant rewatch "
        "instead of a scroll-away. "
        ""
        "NARRATION RULE: third-person narrator voice — describe the fact/story, "
        "never 'I' or personal anecdotes, like a documentary voiceover "
        ""
        "HASHTAG RULE: generate exactly 8-12 hashtags — 7-10 specific to the "
        "niche/topic content, 1-2 generic tags (#shorts, optionally #viral or "
        "#trending). Never use #fyp — it's a TikTok-only convention with no "
        "YouTube discovery value. "
        ""
        "pexels_query: 3-word visual search term per scene for stock footage"
    )
    # Load patterns from previous viral video analysis (free Groq Vision)
    patterns     = load_viral_patterns()
    pattern_hint = patterns_to_prompt_hints(patterns, niche)
    # Load THIS channel's own past performance (self-learning feedback loop)
    own_hint     = own_performance_hint(niche)
    # Recent titles across ALL niches, so the model doesn't recycle one
    # ("You Won't Believe What Happens When You Die" had already shipped 3x)
    recent     = recent_titles(15)
    dedup_hint = (
        "AVOID reusing or closely rephrasing any of these recent titles:\n"
        + "\n".join(f"- {t}" for t in recent)
        if recent else ""
    )

    prompt = (
        f"Write a viral {niche} YouTube Shorts script. "
        "Make it genuinely surprising, controversial or emotionally charged — "
        "something people will want to comment on or share. "
        "Fast pacing, short scenes, frequent re-hooks. "
        "HUMOR IS MANDATORY, not optional: include at least 2-3 distinct "
        "funny beats spread across the script (not just bunched at the "
        "end) — a dry one-liner, an absurd comparison, a sarcastic aside, "
        "or a self-aware joke about the topic. Write like a witty friend "
        "telling this story at a party, not a narrator reading facts off "
        "a card. Flat, purely informational delivery is the #1 reason "
        "viewers scroll away on Shorts. "
        "Let the story's own substance set the length — don't stretch it."
        + (f"\n\n{pattern_hint}" if pattern_hint else "")
        + (f"\n\n{own_hint}" if own_hint else "")
        + (f"\n\n{dedup_hint}" if dedup_hint else "")
    )

    # gpt-oss is a reasoning model: its thinking tokens are drawn from
    # max_completion_tokens too, so the cap must cover thinking (kept short
    # with effort "low") plus the ~1.5K-token JSON script. Input is ~1.5-2K
    # tokens, so 4500 keeps input + cap under the free tier's 8K tokens/minute.
    llm_kwargs = dict(
        model=GROQ_TEXT_MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user",   "content": prompt}],
        temperature=0.78,
        reasoning_effort="low",
        max_completion_tokens=4500,
    )

    for attempt in range(5):
        try:
            try:
                # Groq JSON mode — forces syntactically valid JSON output,
                # eliminates the "Expecting ',' delimiter" parse failures
                # that used to cost 1-2 wasted retries on most runs.
                resp = client.chat.completions.create(
                    **llm_kwargs, response_format={"type": "json_object"})
            except Exception:
                # Some Groq models/versions may reject response_format —
                # fall back to the plain call so this never hard-fails.
                resp = client.chat.completions.create(**llm_kwargs)
            choice = resp.choices[0]
            if choice.finish_reason == "length":
                raise ValueError("reply cut off at the token cap (thinking + JSON too long)")
            data = _json_from_llm(choice.message.content)

            n_scenes  = len(data.get("scenes", []))
            total_dur = sum(float(s.get("duration", 0)) for s in data.get("scenes", []))
            hashtags  = data.get("hashtags", [])
            title     = data.get("title", "")

            leaked = any(pat in title.upper() for pat in _HOOK_LABEL_LEAK_PATTERNS)
            duped  = title in recent

            if n_scenes >= MIN_SCENES and 40 <= total_dur <= 95 and not leaked and not duped:
                data["hook_style_used"] = chosen_hook_name   # short name — for performance tracking
                data["tone"] = pick_voice_tone(data.get("tone"), niche)   # model's pick, niche default if invalid
                log.info(
                    "Script OK: %d scenes, %.0fs, %d hashtags, voice tone '%s' — \"%s\"",
                    n_scenes, total_dur, len(hashtags), data["tone"], title
                )
                return data
            log.warning(
                "Script rejected (scenes=%d, dur=%.0fs, leaked=%s, duped=%s) — retrying…",
                n_scenes, total_dur, leaked, duped
            )
        except Exception as e:
            log.warning("Script attempt %d: %s", attempt + 1, e)
        time.sleep(2 ** attempt)
    raise RuntimeError("Script generation failed after 5 attempts")


# ══════════════════════════════════════════════════════════════════════
# STEP 2 — VOICEOVER (ElevenLabs → OpenAI → edge-tts)
# ══════════════════════════════════════════════════════════════════════

# ── Voice delivery per topic ───────────────────────────────────────────
# One narrator setting for every video sounded the same whether the topic was
# a horror story or a money tip. Now every video has a TONE: the script writer
# picks one of these for the topic (NICHE_TONE is the fallback), and the tone
# sets how each TTS provider delivers the text:
#   eleven : voice_settings — lower `stability` = broader emotional range,
#            `style` = exaggeration, `speed` (docs: elevenlabs.io/docs/api-
#            reference/text-to-speech/convert).
#   openai : tts-1-hd voice + speed.
#   edge   : voices tried in order + rate/pitch.
# (OpenAI announced that tts-1-hd is removed on 2027-01-06 — after that this
#  chain simply continues with edge-tts.)
VOICE_TONES = {
    "ominous": {
        "about":  "slow, low, hushed and suspenseful",
        "eleven": dict(stability=0.22, similarity_boost=0.82, style=0.70, speed=0.92),
        "openai": dict(voice="onyx", speed=0.94),
        "edge":   dict(voices=["en-US-ChristopherNeural", "en-US-GuyNeural", "en-US-EricNeural"],
                       rate="-8%", pitch="-6Hz"),
    },
    "mysterious": {
        "about":  "quiet, intrigued, conspiratorial",
        "eleven": dict(stability=0.30, similarity_boost=0.82, style=0.60, speed=0.95),
        "openai": dict(voice="echo", speed=0.97),
        "edge":   dict(voices=["en-US-GuyNeural", "en-US-ChristopherNeural", "en-US-EricNeural"],
                       rate="-4%", pitch="-3Hz"),
    },
    "energetic": {
        "about":  "fast, punchy, pumped up",
        "eleven": dict(stability=0.28, similarity_boost=0.82, style=0.65, speed=1.10),
        "openai": dict(voice="ash", speed=1.14),
        "edge":   dict(voices=["en-US-GuyNeural", "en-US-ChristopherNeural", "en-US-EricNeural"],
                       rate="+14%", pitch="+2Hz"),
    },
    "warm": {
        "about":  "friendly, intimate, soft",
        "eleven": dict(stability=0.45, similarity_boost=0.82, style=0.45, speed=0.98),
        "openai": dict(voice="nova", speed=1.02),
        "edge":   dict(voices=["en-US-JennyNeural", "en-US-AriaNeural", "en-US-GuyNeural"],
                       rate="+2%", pitch="+0Hz"),
    },
    "authoritative": {
        "about":  "confident, steady, deep",
        "eleven": dict(stability=0.50, similarity_boost=0.82, style=0.35, speed=1.00),
        "openai": dict(voice="onyx", speed=1.04),
        "edge":   dict(voices=["en-US-ChristopherNeural", "en-US-EricNeural", "en-US-GuyNeural"],
                       rate="+2%", pitch="-2Hz"),
    },
    "curious": {
        "about":  "bright, engaged, clear",
        "eleven": dict(stability=0.38, similarity_boost=0.82, style=0.50, speed=1.05),
        "openai": dict(voice="alloy", speed=1.08),
        "edge":   dict(voices=["en-US-GuyNeural", "en-US-EricNeural", "en-US-ChristopherNeural"],
                       rate="+8%", pitch="+0Hz"),
    },
    "storyteller": {
        "about":  "natural, expressive, conversational",
        "eleven": dict(stability=0.32, similarity_boost=0.82, style=0.55, speed=1.00),
        "openai": dict(voice="fable", speed=1.04),
        "edge":   dict(voices=["en-US-GuyNeural", "en-US-ChristopherNeural", "en-US-EricNeural"],
                       rate="+4%", pitch="+0Hz"),
    },
}
NICHE_TONE = {        # used when the script gives no (valid) tone
    "reddit": "storyteller", "dating": "warm", "rich": "authoritative",
    "lifehack": "energetic", "fact": "curious", "scary": "ominous",
    "motivation": "energetic", "conspiracy": "mysterious", "learn": "curious",
}


def pick_voice_tone(tone, niche: Optional[str] = None) -> str:
    """The script's tone if it is one of VOICE_TONES, else the niche default."""
    t = str(tone or "").strip().lower()
    return t if t in VOICE_TONES else NICHE_TONE.get(niche or "", "curious")


def _voice_profile(tone, niche: Optional[str] = None) -> dict:
    name = pick_voice_tone(tone, niche)
    return {"name": name, **VOICE_TONES[name]}


def _elevenlabs_tts(text: str, path: str, profile: Optional[dict] = None) -> bool:
    key = os.getenv("ELEVENLABS_KEY")
    if not key:
        log.info("ElevenLabs: no key set, skipping")
        return False
    voice_id = os.getenv("ELEVEN_VOICE_ID", "")
    if not voice_id:
        try:
            r = requests.get("https://api.elevenlabs.io/v1/voices",
                             headers={"xi-api-key": key}, timeout=10)
            if r.status_code != 200:
                log.warning(
                    "ElevenLabs: voice list failed (%d) — %s. "
                    "FIX: enable 'voices_read' permission for this API key "
                    "at elevenlabs.io → Settings → API Keys, or set "
                    "ELEVEN_VOICE_ID secret to a specific voice ID manually.",
                    r.status_code, r.text[:150]
                )
                return False
            voices = r.json().get("voices", [])
            if not voices:
                log.warning("ElevenLabs: account has zero voices available")
                return False
            voice_id = voices[0]["voice_id"]
            log.info("ElevenLabs: auto-selected voice '%s'", voices[0].get("name"))
        except Exception as e:
            log.warning("ElevenLabs: voice discovery error: %s", e)
            return False

    settings = {"stability": 0.30, "similarity_boost": 0.82, "style": 0.55, "use_speaker_boost": True}
    if profile:
        settings.update(profile["eleven"])
    try:
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": key, "Content-Type": "application/json"},
            json={"text": text, "model_id": "eleven_turbo_v2_5", "voice_settings": settings},
            timeout=90,
        )
        if r.status_code in (400, 422) and "speed" in settings:
            # in case the API rejects the speed setting, still deliver the tone via stability/style
            log.warning("ElevenLabs rejected the speed setting (%d) — retrying without it", r.status_code)
            settings = {k: v for k, v in settings.items() if k != "speed"}
            r = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers={"xi-api-key": key, "Content-Type": "application/json"},
                json={"text": text, "model_id": "eleven_turbo_v2_5", "voice_settings": settings},
                timeout=90,
            )
    except Exception as e:
        log.warning("ElevenLabs: request error: %s", e)
        return False

    if r.status_code == 200:
        Path(path).write_bytes(r.content)
        log.info("ElevenLabs TTS OK (%d bytes, tone=%s)", len(r.content), profile["name"] if profile else "default")
        return True
    # Surface the actual reason — quota, invalid key, etc.
    log.warning("ElevenLabs failed (%d): %s", r.status_code, r.text[:200])
    return False


def _openai_tts(text: str, path: str, profile: Optional[dict] = None) -> bool:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        log.info("OpenAI TTS: no key set, skipping")
        return False
    cfg = {"voice": "onyx", "speed": 1.08}
    if profile:
        cfg.update(profile["openai"])
    r = None
    for voice in dict.fromkeys([cfg["voice"], "onyx"]):        # the tone's voice, then the proven default
        try:
            r = requests.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": "tts-1-hd", "voice": voice, "input": text, "speed": cfg["speed"]},
                timeout=90,
            )
        except Exception as e:
            log.warning("OpenAI TTS: request error: %s", e)
            return False
        if r.status_code == 200:
            Path(path).write_bytes(r.content)
            log.info("OpenAI TTS OK (%d bytes, voice=%s, speed=%.2f)", len(r.content), voice, cfg["speed"])
            return True
        if r.status_code != 400:          # 400 = e.g. voice not offered for this model -> try the default voice
            break
    log.warning("OpenAI TTS failed (%d): %s", r.status_code, r.text[:200])
    return False


def _edgetts_tts(text: str, path: str, profile: Optional[dict] = None) -> bool:
    """
    edge-tts fallback. Uses standard (non-multilingual) voices, since
    multilingual variants are more prone to 'NoAudioReceived' errors in
    CI environments. Retries up to 3 times with backoff — Microsoft's
    endpoint occasionally rate-limits/blocks datacenter IPs (GitHub Actions).
    The tone picks the voice order, rate and pitch; the three voices that
    have always worked here stay as the last resort.
    """
    cfg = {"voices": [], "rate": "+8%", "pitch": "+0Hz"}
    if profile:
        cfg.update(profile["edge"])
    voices_to_try = list(dict.fromkeys(
        cfg["voices"] + ["en-US-GuyNeural", "en-US-ChristopherNeural", "en-US-EricNeural"]))

    for voice in voices_to_try:
        for attempt in range(3):
            try:
                async def _run():
                    comm = edge_tts.Communicate(text, voice, rate=cfg["rate"], volume="+10%",
                                                pitch=cfg["pitch"])
                    await comm.save(path)
                asyncio.run(_run())
                if Path(path).exists() and Path(path).stat().st_size > 1000:
                    log.info("edge-tts OK (voice=%s, rate=%s, pitch=%s, attempt=%d)",
                             voice, cfg["rate"], cfg["pitch"], attempt + 1)
                    return True
            except Exception as e:
                log.warning("edge-tts failed (voice=%s, attempt=%d): %s",
                           voice, attempt + 1, e)
                time.sleep(2 * (attempt + 1))
    log.warning("edge-tts: all voices/retries exhausted")
    return False


def _gtts_fallback(text: str, path: str) -> bool:
    """
    Absolute last resort: Google Translate TTS (gTTS).
    Free, no API key, but lower quality and has a ~100-char chunk limit
    internally (gTTS handles chunking automatically). Robotic but reliable.
    """
    try:
        from gtts import gTTS
        tts = gTTS(text=text, lang="en", slow=False)
        tts.save(path)
        if Path(path).exists() and Path(path).stat().st_size > 1000:
            log.info("gTTS OK (final fallback)")
            return True
    except Exception as e:
        log.error("gTTS fallback also failed: %s", e)
    return False


def generate_voiceover(text: str, path: str, niche: Optional[str] = None,
                       tone: Optional[str] = None) -> None:
    """
    Voice fallback chain (first success wins):
    ElevenLabs → OpenAI TTS → edge-tts (3+ voices × 3 retries) → gTTS
    The delivery follows the video's tone (see VOICE_TONES). Raises
    RuntimeError only if every single option fails.
    """
    profile = _voice_profile(tone, niche)
    log.info("Voice tone '%s' (%s) for niche '%s'", profile["name"], profile["about"], niche)
    if _elevenlabs_tts(text, path, profile):
        return
    if _openai_tts(text, path, profile):
        return
    if _edgetts_tts(text, path, profile):
        return
    if _gtts_fallback(text, path):
        return
    raise RuntimeError(
        "All TTS providers failed (ElevenLabs, OpenAI, edge-tts, gTTS). "
        "Check API keys and network/firewall settings."
    )


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

# Below this, a cut reads as a flash/glitch rather than a deliberate quick
# cut — e.g. a natural pause at 3.6s and an unrelated word-boundary snap at
# 4.0s used to slip through and create a 0.4s "scene".
MIN_SCENE_GAP_MS = 900

def _enforce_min_gap(cuts_ms: list[int], min_gap_ms: int = MIN_SCENE_GAP_MS) -> list[int]:
    """Drop any cut point that would create a sub-min_gap_ms scene. Keeps the
    earlier cut of any too-close pair so downstream ordering stays stable."""
    cuts_ms = sorted(cuts_ms)
    filtered: list[int] = []
    for t in cuts_ms:
        if not filtered or t - filtered[-1] >= min_gap_ms:
            filtered.append(t)
    return filtered


def detect_cut_points(audio_path: str, aai_data: dict, n_scenes: int,
                      silence_thresh_db: int = -40,
                      min_silence_ms: int = 300) -> list[float]:
    """
    Returns n_scenes-1 cut points (seconds), prioritizing real silence/
    pauses but FILLING IN additional cuts snapped to word-end boundaries
    when natural pauses aren't enough to support the target scene count.

    Without this fallback, a script with e.g. 22 scenes but only 5 real
    pauses would collapse down to 6 giant scenes — defeating the entire
    point of fast viral-style cut pacing.
    """
    audio    = AudioSegment.from_file(audio_path)
    total_ms = len(audio)
    words    = aai_data.get("words", [])

    # ── 1. "Hard" cuts — real silence/pauses (highest quality) ────────
    pydub_mids = [(s+e)//2 for s,e in
                  detect_silence(audio, min_silence_len=min_silence_ms,
                                 silence_thresh=silence_thresh_db)]
    aai_mids   = [(p["start_ms"]+p["end_ms"])//2
                  for p in aai_data.get("pauses", [])]

    hard, last = [], -9999
    for t in sorted(set(pydub_mids + aai_mids)):
        if t - last > 500 and 3000 < t < total_ms - 3000:
            hard.append(t)
            last = t

    n_cuts = n_scenes - 1
    log.info("Natural pause candidates: %d (need %d cuts)", len(hard), n_cuts)

    if len(hard) >= n_cuts:
        # Plenty of real pauses — pick the ones closest to even spacing
        ideal = total_ms / n_scenes
        pool, chosen = hard.copy(), []
        for i in range(1, n_scenes):
            target  = i * ideal
            closest = min(pool, key=lambda t: abs(t-target))
            chosen.append(closest)
            pool.remove(closest)
        chosen = _enforce_min_gap(chosen)
        result = sorted(t/1000.0 for t in chosen)
        log.info("Cut points (%d, all natural): %s",
                 len(result), [f"{c:.1f}s" for c in result])
        return result

    # ── 2. Not enough natural pauses — fill gaps with word-boundary cuts ─
    # Build a sorted list of every word-end timestamp as a fallback
    # candidate. These are never mid-word (they're exactly where one
    # word finishes), so audio never gets cut inside a syllable.
    word_ends = sorted(w["end_ms"] for w in words
                       if 3000 < w["end_ms"] < total_ms - 3000)

    ideal    = total_ms / n_scenes
    targets  = [i * ideal for i in range(1, n_scenes)]
    chosen   = []
    used_hard, used_words = set(), set()

    for target in targets:
        # Prefer a natural pause near this target position
        nearby_hard = [t for t in hard if t not in used_hard
                      and abs(t - target) < ideal * 0.5]
        if nearby_hard:
            pick = min(nearby_hard, key=lambda t: abs(t-target))
            used_hard.add(pick)
            chosen.append(pick)
            continue
        # Otherwise snap to the nearest unused word-end boundary
        candidates = [w for w in word_ends if w not in used_words]
        if candidates:
            pick = min(candidates, key=lambda t: abs(t-target))
            used_words.add(pick)
            chosen.append(pick)

    chosen = _enforce_min_gap(chosen)
    result = sorted(set(t/1000.0 for t in chosen))
    log.info("Cut points (%d, %d natural + %d word-snapped): %s",
             len(result), len(used_hard), len(used_words),
             [f"{c:.1f}s" for c in result])
    return result


# ══════════════════════════════════════════════════════════════════════
# STEP 5 — PEXELS VIDEO BACKGROUNDS (NEW — replaces static images)
# ══════════════════════════════════════════════════════════════════════

_pexels_video_cache: dict = {}

# ══════════════════════════════════════════════════════════════════════
# GROQ-POWERED SMART PEXELS QUERY (free — Groq has no cost)
#
#  Instead of using the generic 3-word pexels_query from the script,
#  this function uses Groq to analyze the scene text + niche and
#  generate the most visually specific search term possible.
#  Falls back to the niche's rotating video_queries pool instantly
#  if Groq is unavailable, so zero extra latency risk.
# ══════════════════════════════════════════════════════════════════════

_groq_query_cache: dict = {}
_groq_query_warned: list = []

def smart_pexels_query(scene_text: str, niche: str,
                       fallback_query: str = "") -> str:
    """
    Use Groq to generate the most visually relevant Pexels search query
    for a given scene. Caches results to avoid duplicate API calls.
    Falls back to niche rotation pool if Groq fails.
    """
    cache_key = scene_text[:60]
    if cache_key in _groq_query_cache:
        return _groq_query_cache[cache_key]

    # Niche fallback pool — always available instantly
    pool = NICHES[niche].get("video_queries", ["cinematic nature landscape"])
    niche_fallback = random.choice(pool)

    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        return niche_fallback

    try:
        # max_retries=0: a rate-limited or failed call must drop to the pool
        # fallback at once, not let the SDK sleep through a retry-after.
        client = Groq(api_key=groq_key, max_retries=0, timeout=20)
        resp = client.chat.completions.create(
            model=GROQ_FAST_MODEL,
            messages=[{
                "role": "system",
                "content": (
                    "You are a video director. Given a short script line and a niche, "
                    "output ONLY a 3-5 word Pexels video search query that would find "
                    "the most visually striking, emotionally relevant portrait-oriented "
                    "footage to play behind this voiceover. "
                    "No explanation. No punctuation. Just the search query."
                )
            }, {
                "role": "user",
                "content": f"Niche: {niche}\nLine: {scene_text}\nQuery:"
            }],
            temperature=0.7,
            reasoning_effort="low",
            max_completion_tokens=300,   # thinking tokens count too; the answer is ~6
        )
        choice = resp.choices[0]
        query  = (choice.message.content or "").strip().strip('"').strip("'")
        if 2 <= len(query.split()) <= 6:
            _groq_query_cache[cache_key] = query
            log.info("Smart query: '%s' → '%s'", scene_text[:40], query)
            return query
        log.info("Smart query unusable (%r, finish_reason=%s) — using pool fallback",
                 query[:60], choice.finish_reason)
    except Exception as e:
        if not _groq_query_warned:     # first failure of a run is visible, the rest stay quiet
            _groq_query_warned.append(True)
            log.warning("Smart query via Groq failed (%s: %s) — using the niche pool; "
                        "further failures this run are only logged at debug level",
                        type(e).__name__, e)
        else:
            log.debug("Smart query failed: %s — using pool fallback", e)

    _groq_query_cache[cache_key] = niche_fallback
    return niche_fallback


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

def _synth_impact(path: str) -> None:
    """Soft low thud (low-passed noise, 120 ms) — the only sound effect left,
    played once under the first frame. (The whoosh and pop that used to sit on
    every cut are gone: the whoosh was a swept sine tone, i.e. a beep.)"""
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

# One Pixabay query per niche so "scary" and "rich" don't end up with the
# same generic track — and so the fallback synth pad (if PIXABAY_KEY is
# unset) at least sounds different across niches.
NICHE_MUSIC_QUERIES = {
    "reddit":     "dramatic tension ambient",
    "dating":     "romantic soft piano",
    "rich":       "luxury lounge cinematic",
    "lifehack":   "upbeat corporate clean",
    "fact":       "curious ambient documentary",
    "scary":      "horror ambient tension",
    "motivation": "epic cinematic uplifting",
    "conspiracy": "dark mysterious ambient",
    "learn":      "clean documentary ambient",
}

def ensure_music(niche: str = "fact", duration_s: float = 120) -> str:
    """
    Cached PER NICHE (bg_music_<niche>.mp3) instead of one global bg_music.mp3
    that — once committed/downloaded once — silently got reused forever for
    every niche and every video. Falls back to a synthesised ambient pad only
    if JAMENDO_CLIENT_ID is unset or the API call fails.
    """
    niche_music_path = f"bg_music_{niche}.mp3"
    if Path(niche_music_path).exists():
        return niche_music_path

    # Back-compat: if an old global bg_music.mp3 is committed and this is
    # the first "fact"-niche run, reuse it once instead of re-downloading.
    if niche == "fact" and Path(MUSIC_PATH).exists():
        return MUSIC_PATH

    if JAMENDO_CLIENT_ID:
        query = NICHE_MUSIC_QUERIES.get(niche, "cinematic background")
        try:
            r = requests.get(
                "https://api.jamendo.com/v3.0/tracks/",
                params={"client_id": JAMENDO_CLIENT_ID, "format": "json",
                        "limit": 10, "audioformat": "mp32",
                        "search": query, "order": "popularity_total",
                        # No vocals — a track with singing/lyrics competes
                        # with the narration and is what made the music
                        # sound "unpassend" (fighting the voiceover).
                        "vocalinstrumental": "instrumental"},
                timeout=10,
            )
            resp_json = r.json()
            results   = resp_json.get("results", [])
            if results:
                track = random.choice(results)
                url   = track.get("audio") or track.get("audiodownload")
                if url:
                    data = requests.get(url, timeout=30).content
                    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                        raw_tmp = f.name
                    Path(raw_tmp).write_bytes(data)
                    # Jamendo tracks are mastered at wildly different
                    # loudness levels — the fixed dB duck downstream assumed
                    # a consistent input level, so a loud source track came
                    # out "too loud" no matter the duck amount. Normalize to
                    # a fixed target here so ducking behaves consistently.
                    seg = AudioSegment.from_file(raw_tmp)
                    seg = seg.apply_gain(-20.0 - seg.dBFS)
                    seg.export(niche_music_path, format="mp3")
                    log.info("Jamendo music: '%s' → '%s' (niche=%s, "
                             "normalized to -20dBFS)",
                             query, track.get("name"), niche)
                    return niche_music_path
            else:
                # Jamendo often returns HTTP 200 even for a bad request,
                # with the real error hidden in resp_json["headers"] instead
                # of an HTTP error — log it so a silent "0 results" isn't a
                # dead end next time.
                log.warning("Jamendo returned 0 results for '%s' — API "
                            "response headers: %s", query,
                            resp_json.get("headers", resp_json))
        except Exception as e:
            log.warning("Jamendo: %s", e)
    else:
        log.warning("JAMENDO_CLIENT_ID not set — using synthesised ambient "
                     "pad instead of real music. Sign up free at "
                     "developer.jamendo.com and add the JAMENDO_CLIENT_ID "
                     "secret for real niche-matched tracks.")
    _synth_ambient(niche_music_path, duration_s + 10)
    return niche_music_path

def ensure_sfx() -> str:
    """Path of the soft opening thud (synthesised on first use). Cuts get no
    sound effects — see the note at SFX_IMPACT."""
    if not Path(SFX_IMPACT).exists():
        _synth_impact(SFX_IMPACT)
    return SFX_IMPACT


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

        q = smart_pexels_query(scene.get("text",""), niche, fallback_query=q)
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
        q = smart_pexels_query(scene.get("text",""), niche, fallback_query=q)
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

        q = smart_pexels_query(scene.get("text",""), niche, fallback_query=q)
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
# STEP 8e — PICTURE INSERTS (photo cards that fade in over the footage)
#
#  Besides the stock footage in the background, a matching photo now fades
#  in every few seconds — as a rounded card in the upper middle of the frame,
#  never full screen — while the narrator talks about exactly that thing.
#   1. plan   : Groq picks 4-7 lines of the SPOKEN text that name something
#               concrete and gives a photo search for each. The lines come from
#               the real word timestamps, so the photo appears when the thing
#               is mentioned (scene boundaries only roughly follow the script).
#   2. fetch  : Pexels photos (Pixabay as backup); a photo is only used if its
#               description/tags contain the words of the search.
#   3. render : rounded card with white frame and shadow, alpha fade + small
#               slide, composited on top of the finished footage.
#  Every step is non-fatal: no key / no match / an error just skips that
#  insert, and the video renders without it.
# ══════════════════════════════════════════════════════════════════════
IMG_MAX_INSERTS    = 7        # per video
IMG_MIN_GAP_S      = 4.5      # min distance between the starts of two inserts
IMG_SHOW_MIN_S     = 1.8      # on-screen time, fades included
IMG_SHOW_MAX_S     = 3.2
IMG_START_SAFE_S   = 1.0      # nothing during the opening second ...
IMG_END_SAFE_S     = 1.4      # ... or over the closing fade-out
IMG_CARD_W         = 860      # px of 1080: the card never takes the whole screen
IMG_CARD_MIN_AR    = 1.39     # card aspect ratio (w/h) limits -> card height 483..619 px
IMG_CARD_MAX_AR    = 1.78
IMG_CARD_CENTER_Y  = 700      # px from the top: clear of the Shorts UI at the top and far
                              # above the captions (which start at about y=1400)
IMG_FADE_S         = 0.28
IMG_SLIDE_PX       = 26
IMG_MIN_RELEVANCE  = 0.5      # share of the search words that must appear in the photo's text

_INSERT_STOP = frozenset("a an the of in on at with and or for to from by up close closeup "
                         "view photo picture image stock".split())


def _clean_query(q) -> str:
    """1-5 plain words, or '' if the model's search term is unusable."""
    words = re.sub(r"[^A-Za-z0-9 \-]", " ", str(q or "")).lower().split()
    return " ".join(words) if 1 <= len(words) <= 5 else ""


def _transcript_lines(aai_data: dict) -> list[dict]:
    """The spoken text as sentence-like lines with their real start/end times
    (a sentence end, a pause of 450 ms+, or 16 words close a line)."""
    lines: list[dict] = []
    cur: list[dict] = []

    def flush() -> None:
        if cur:
            lines.append({"text": " ".join(str(w["text"]) for w in cur),
                          "start_s": cur[0]["start_ms"] / 1000.0,
                          "end_s":   cur[-1]["end_ms"] / 1000.0})
            cur.clear()

    for w in aai_data.get("words", []):
        if cur and (w["start_ms"] - cur[-1]["end_ms"] >= 450 or len(cur) >= 16):
            flush()
        cur.append(w)
        if str(w["text"]).rstrip().endswith((".", "!", "?")):
            flush()
    flush()
    return lines


def _groq_pick_inserts(lines: list[dict], niche: str, title: str = "") -> list[dict]:
    """[{line, query}] — which spoken lines deserve a photo, and which one."""
    key = os.getenv("GROQ_API_KEY")
    if not key or len(lines) < 4:
        return []
    listing = "\n".join(f"{i} [{ln['start_s']:.1f}s] {ln['text']}" for i, ln in enumerate(lines))
    system = (
        "You choose photo inserts for a vertical short video (YouTube Shorts / TikTok). "
        "You get the narration split into numbered lines with their start times. Pick 4 to 7 "
        "lines, spread over the whole video and at least 5 seconds apart, where the narrator names "
        "something concrete that can be PHOTOGRAPHED — an animal, a place, a landmark, a building, an "
        "object, food, a vehicle, a plant, a natural phenomenon, a planet, a machine or tool. For each "
        "give a plain English stock-photo search of 2-4 words that shows exactly that main thing "
        "(e.g. 'anglerfish deep sea', 'eiffel tower night', 'rusty old key'). "
        "NEVER pick: abstract ideas or feelings, named real people or celebrities, brands, logos, text, "
        "screenshots, charts, the first line or the last line. Avoid generic 'person smiling' stock shots. "
        "Return ONLY JSON: {\"inserts\": [{\"line\": <line number>, \"query\": \"<search>\"}]}"
    )
    user = f"Niche: {niche}\nTitle: {title}\nLines:\n{listing}"
    try:
        client = Groq(api_key=key, max_retries=1, timeout=30)
        kwargs = dict(
            model=GROQ_TEXT_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.4,
            reasoning_effort="low",
            max_completion_tokens=2500,        # thinking tokens count against this too
        )
        try:
            resp = client.chat.completions.create(**kwargs, response_format={"type": "json_object"})
        except Exception:
            resp = client.chat.completions.create(**kwargs)
        data = _json_from_llm(resp.choices[0].message.content)
    except Exception as e:
        log.warning("Picture inserts: Groq planning failed (%s: %s)", type(e).__name__, e)
        return []
    picks, seen_q, seen_l = [], set(), set()
    for it in (data.get("inserts") or []):
        try:
            idx = int(it["line"])
        except Exception:
            continue
        q = _clean_query(it.get("query") if isinstance(it, dict) else "")
        if not q or not (0 <= idx < len(lines)) or q in seen_q or idx in seen_l:
            continue
        seen_q.add(q); seen_l.add(idx)
        picks.append({"line": idx, "query": q})
    return picks


def _fallback_insert_picks(lines: list[dict], scenes: list[dict], cut_points_s: list[float]) -> list[dict]:
    """No usable Groq answer: use the script's own 3-word visual search term of
    every second scene, placed at the line that is spoken when that scene starts."""
    bounds, picks = [0.0] + list(cut_points_s), []
    for i, sc in enumerate(scenes[: len(bounds)]):
        q = _clean_query(sc.get("pexels_query"))
        li = next((k for k, ln in enumerate(lines) if ln["end_s"] >= bounds[i]), None)
        if q and li is not None:
            picks.append({"line": li, "query": q})
    return picks[1::2]


def _schedule_inserts(picks: list[dict], lines: list[dict], total_s: float) -> list[dict]:
    """picks -> [{query, start_s, dur_s}]: in time order, spaced, inside the safe zones."""
    out, last_start = [], -1e9
    for p in sorted(picks, key=lambda p: lines[p["line"]]["start_s"]):
        ln    = lines[p["line"]]
        start = ln["start_s"] + 0.10
        dur   = max(IMG_SHOW_MIN_S, min(IMG_SHOW_MAX_S, (ln["end_s"] - ln["start_s"]) + 0.6))
        dur   = min(dur, total_s - IMG_END_SAFE_S - start)
        if start < IMG_START_SAFE_S or dur < IMG_SHOW_MIN_S - 0.3:
            continue
        if start - last_start < IMG_MIN_GAP_S:
            continue
        out.append({"query": p["query"], "start_s": round(start, 2), "dur_s": round(dur, 2)})
        last_start = start
        if len(out) >= IMG_MAX_INSERTS:
            break
    return out


def plan_picture_inserts(aai_data: dict, scenes: list[dict], cut_points_s: list[float],
                         niche: str, title: str = "") -> list[dict]:
    lines   = _transcript_lines(aai_data)
    total_s = aai_data["total_ms"] / 1000.0
    picks   = _groq_pick_inserts(lines, niche, title)
    plan    = _schedule_inserts(picks, lines, total_s) if picks else []
    if not plan:
        plan = _schedule_inserts(_fallback_insert_picks(lines, scenes, cut_points_s), lines, total_s)
    return plan


def _stem_words(s: str) -> set:
    out = set()
    for w in re.findall(r"[a-z0-9]+", str(s).lower()):
        if w in _INSERT_STOP:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.add(w)
    return out


def _relevance(query: str, text: str) -> float:
    """Share of the query's words that appear in the photo's description/tags."""
    q = _stem_words(query)
    return len(q & _stem_words(text)) / len(q) if q else 0.0


def _pexels_photo(query: str, used: set) -> Optional[tuple]:
    """Best Pexels photo for the query: (relevance, rank, url, uid) or None."""
    key = os.getenv("PEXELS_KEY")
    if not key:
        return None
    r = requests.get("https://api.pexels.com/v1/search", headers={"Authorization": key},
                     params={"query": query, "per_page": 15, "orientation": "landscape",
                             "size": "medium"}, timeout=15)
    r.raise_for_status()
    best = None
    for rank, p in enumerate(r.json().get("photos", [])):
        uid = f"pexels:{p.get('id')}"
        src = p.get("src") or {}
        url = src.get("large2x") or src.get("large")
        if uid in used or not url or (p.get("width") or 0) < 1000 or (p.get("height") or 0) < 600:
            continue
        slug = (p.get("url") or "").rstrip("/").rsplit("/", 1)[-1]        # e.g. brown-fox-on-snow-1234
        rel  = _relevance(query, f"{p.get('alt') or ''} {slug}")
        if best is None or (rel, -rank) > (best[0], -best[1]):
            best = (rel, rank, url, uid)
    return best


def _pixabay_photo(query: str, used: set) -> Optional[tuple]:
    if not PIXABAY_KEY:
        return None
    r = requests.get("https://pixabay.com/api/",
                     params={"key": PIXABAY_KEY, "q": query, "image_type": "photo",
                             "orientation": "horizontal", "safesearch": "true",
                             "per_page": 20, "min_width": 1000}, timeout=15)
    r.raise_for_status()
    best = None
    for rank, h in enumerate(r.json().get("hits", [])):
        uid, url = f"pixabay:{h.get('id')}", h.get("largeImageURL")
        if uid in used or not url:
            continue
        rel = _relevance(query, h.get("tags", ""))
        if best is None or (rel, -rank) > (best[0], -best[1]):
            best = (rel, rank, url, uid)
    return best


def _download_image(url: str) -> Image.Image:
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    if len(r.content) > 15_000_000:
        raise ValueError("image too large")
    img = Image.open(BytesIO(r.content))
    img.load()
    return ImageOps.exif_transpose(img).convert("RGB")


def fetch_insert_image(query: str, used: set) -> Optional[tuple]:
    """(PIL image, uid, relevance) of a photo that matches `query`, or None.
    Tries the full search, then its first two words; Pexels, then Pixabay."""
    tries = [query] + ([" ".join(query.split()[:2])] if len(query.split()) > 2 else [])
    for q in tries:
        for finder in (_pexels_photo, _pixabay_photo):
            try:
                hit = finder(q, used)
                if hit and hit[0] >= IMG_MIN_RELEVANCE:
                    img = _download_image(hit[2])
                    used.add(hit[3])
                    return img, hit[3], hit[0]
            except Exception as e:
                log.warning("Picture insert '%s' via %s: %s: %s",
                            q, finder.__name__.strip("_"), type(e).__name__, e)
    return None


def _make_insert_card(img: Image.Image) -> np.ndarray:
    """RGBA card: the photo with rounded corners, a white frame and a soft shadow."""
    W   = IMG_CARD_W
    ar  = min(max(img.width / float(img.height), IMG_CARD_MIN_AR), IMG_CARD_MAX_AR)
    H   = int(round(W / ar))
    pad, R, B = 46, 38, 8                                   # shadow room, corner radius, frame width
    photo  = ImageOps.fit(img, (W - 2 * B, H - 2 * B), method=Image.LANCZOS,
                          centering=(0.5, 0.42)).convert("RGBA")
    canvas = Image.new("RGBA", (W + 2 * pad, H + 2 * pad), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([pad, pad + 14, pad + W, pad + H + 14], R, fill=(0, 0, 0, 170))
    canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(20)))
    ImageDraw.Draw(canvas).rounded_rectangle([pad, pad, pad + W, pad + H], R, fill=(255, 255, 255, 255))
    mask = Image.new("L", photo.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, photo.width - 1, photo.height - 1], R - B, fill=255)
    canvas.paste(photo, (pad + B, pad + B), mask)
    return np.array(canvas)


def _insert_clip(card: np.ndarray, start_s: float, dur_s: float) -> ImageClip:
    """The card as a timed clip: alpha fade in/out and a small slide up on the way in."""
    clip  = ImageClip(card).with_start(start_s).with_duration(dur_s)    # RGBA -> picture + alpha mask
    x     = (VIDEO_W - clip.w) // 2
    y_top = int(IMG_CARD_CENTER_Y - clip.h / 2)

    def pos(t: float):
        k = min(1.0, max(0.0, t / IMG_FADE_S))
        k = 1.0 - (1.0 - k) ** 3                              # ease-out
        return (x, int(round(y_top + IMG_SLIDE_PX * (1.0 - k))))

    return clip.with_position(pos).with_effects([CrossFadeIn(IMG_FADE_S), CrossFadeOut(IMG_FADE_S)])


def build_picture_inserts(aai_data: dict, scenes: list[dict], cut_points_s: list[float],
                          niche: str, title: str = "") -> list:
    """The timed photo-card clips for this video ([] if none could be made)."""
    try:
        plan = plan_picture_inserts(aai_data, scenes, cut_points_s, niche, title)
    except Exception as e:
        log.warning("Picture inserts: planning failed (%s: %s) — none in this video", type(e).__name__, e)
        return []
    if not plan:
        log.info("Picture inserts: nothing planned")
        return []
    clips, used = [], set()
    for it in plan:
        try:
            got = fetch_insert_image(it["query"], used)
            if not got:
                log.info("Picture insert '%s': no matching photo — skipped", it["query"])
                continue
            img, uid, rel = got
            clips.append(_insert_clip(_make_insert_card(img), it["start_s"], it["dur_s"]))
            log.info("Picture insert: '%s' at %.1fs for %.1fs (%s, relevance %.2f)",
                     it["query"], it["start_s"], it["dur_s"], uid, rel)
        except Exception as e:
            log.warning("Picture insert '%s' failed (%s: %s) — skipped", it["query"], type(e).__name__, e)
    log.info("Picture inserts: %d of %d planned", len(clips), len(plan))
    return clips


# ══════════════════════════════════════════════════════════════════════
# STEP 9 — MOVIEPY VIDEO ASSEMBLY (local render)
#   Scene footage + crossfades + photo inserts + voice/music mix
#   (captions are burned in afterwards by apply_beautiful_captions)
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
    def zoom_frame(get_frame, t):
        # FIX: MoviePy 2.x's clip.transform(func) passes func(get_frame, t) —
        # get_frame is a CALLABLE, not the raw frame array. The old code
        # treated the first argument as if it were already a numpy frame
        # and called .shape on it, which silently crashed on every single
        # scene with "'function' object has no attribute 'shape'" and fell
        # back to the solid-color background — even after Pexels downloads
        # started working correctly.
        frame = get_frame(t)
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
    sfx_impact_path: str = "",
    title:           str = "",
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
        # Every crossfade below overlaps two scenes by TRANSITION_DUR
        # (padding=-TRANSITION_DUR), which pulled each following scene that much
        # earlier than its cut point: by the last scene the picture ran ~0.2s per
        # cut (up to ~4s) ahead of the narration, and the video ended that much
        # before the voice did. Building every scene but the last TRANSITION_DUR
        # longer puts each scene start exactly on its cut point again.
        build_dur = dur + (TRANSITION_DUR if idx < n - 1 else 0.0)
        q   = scene.get("pexels_query", scene["text"][:40])
        # Groq generates a more visually specific search query per scene
        q   = smart_pexels_query(scene.get("text",""), niche, fallback_query=q)

        # Background: Pexels video → solid color fallback
        # FIX: download=True — without this, fetch_pexels_video() returns a
        # remote CDN URL (meant for cloud render), and Path(url).exists()
        # is always False, so every scene silently fell back to the near-
        # black solid-color background even when Pexels had good results.
        vid_path = fetch_pexels_video(q, min_dur=build_dur, download=True)
        if vid_path and Path(vid_path).exists():
            try:
                bg = _trim_video_clip(vid_path, build_dur)
                # DISABLED: _ken_burns() re-processes every single output
                # frame through a Python/PIL callback (clip.transform()).
                # For a ~90s/24-scene video at 30fps that's 2000+ frames run
                # through Python one at a time — likely the cause of the
                # 13-minute silent stall observed in the export step right
                # after all scenes finished building, which then got the
                # whole job killed with no useful error. A static (non-
                # zooming) background is a reasonable trade for a render
                # that actually finishes. Re-enable only after replacing it
                # with a native ffmpeg zoompan filter instead of the
                # per-frame Python version.
                # bg = _ken_burns(bg)
            except Exception as e:
                log.warning("Video clip error (%s), using solid BG", e)
                bg = make_solid_bg_clip(cfg["bg"], build_dur)
        else:
            bg = make_solid_bg_clip(cfg["bg"], build_dur)

        # Dark vignette overlay (ImageClip)
        vig_arr  = _make_vignette(build_dur)
        vig_clip = ImageClip(vig_arr).with_duration(build_dur).with_fps(FPS)

        scene_comp = CompositeVideoClip([bg, vig_clip],
                                        size=(VIDEO_W, VIDEO_H)).with_duration(build_dur)
        scene_clips.append(scene_comp)
        rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        log.info("  Scene %d/%d (%.1fs) — %s [rss=%.0fMB]",
                  idx+1, n, dur, q[:35], rss_mb)

    # ── Crossfade transitions ─────────────────────────────────────────
    trans = [scene_clips[0]]
    for c in scene_clips[1:]:
        trans.append(c.with_effects([CrossFadeIn(TRANSITION_DUR)]))
    final = concatenate_videoclips(trans, method="compose", padding=-TRANSITION_DUR)
    log.info("Crossfades done, %d scenes concatenated (%.1fs total, voice %.1fs)",
             len(scene_clips), final.duration, total_s)

    # ── Picture inserts: matching photos fade in over the footage ─────
    # Cards in the upper middle of the frame (never full screen), timed to
    # the moment the narrator mentions the thing. Added before the global
    # fade in/out so they fade with the rest. Any problem = no inserts.
    try:
        inserts = build_picture_inserts(aai_data, scenes, cut_points_s, niche, title)
        if inserts:
            # The base is the already-composed scene track. Dropping its
            # (all-opaque) mask and using it as the background clip keeps
            # the outer composite cheap: a nested composite with a mask
            # would otherwise be alpha-composited at full resolution for
            # every single frame (+50 % render time, measured), and the
            # CI job has a hard time limit. Pixel output is identical.
            base = final.copy()
            base.mask = None
            final = CompositeVideoClip([base] + inserts, size=(VIDEO_W, VIDEO_H),
                                       use_bgclip=True).with_duration(final.duration)
    except Exception as e:
        log.warning("Picture inserts skipped: %s: %s", type(e).__name__, e)
    final = final.with_effects([FadeIn(FADE_IN_DUR), FadeOut(FADE_OUT_DUR)])

    # ── Caption overlays (word-by-word animated) ──────────────────────
    # DISABLED: this baked a full word-by-word caption layer into raw.mp4
    # using NICHES[niche]["color"] directly (no NO_ACCENT_NICHES override),
    # and apply_beautiful_captions() then burned a SECOND, differently
    # -styled/-timed caption layer on top of that afterward — this is the
    # real source of "two different captions" that the redundant YouTube
    # caption-track upload fix didn't catch, because this duplication is
    # baked directly into the video pixels, not just an extra upload.
    # apply_beautiful_captions() is the more complete, niche-aware system
    # (correct accent color incl. NO_ACCENT_NICHES, text_animation from
    # viral_patterns.json) and should remain the only caption source.
    # cap_clips = build_caption_clips(aai_data, niche, final.duration)
    # if cap_clips:
    #     cap_composites = []
    #     for clip, start_t in cap_clips:
    #         cap_composites.append(clip.with_start(start_t))
    #     final = CompositeVideoClip([final] + cap_composites,
    #                                size=(VIDEO_W, VIDEO_H))
    #     log.info("Caption overlays composited (%d clips)", len(cap_clips))

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
        try:
            voice_db = AudioSegment.from_file(audio_path).dBFS
        except Exception:
            voice_db = float("nan")
        # the levels go into the log, so "too quiet / too loud" can be judged by numbers
        log.info("Music mixed (%+d dB): music %.1f dBFS, voice %.1f dBFS → music %.1f dB under the voice",
                 MUSIC_DUCK_DB, music_raw.dBFS, voice_db, voice_db - music_raw.dBFS)

    # Only a soft thud under the first frame — NO sound effects at the cuts
    # (the old whoosh/pop there were the recurring "Piep").
    ip = sfx_impact_path or SFX_IMPACT
    if Path(ip).exists():
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            stmp = f.name
        (AudioSegment.from_file(ip) - 7).export(stmp, format="mp3")
        tracks.append(AudioFileClip(stmp))
        log.info("Opening thud mixed — no sound effects at the cuts")

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

def publish_target_utc() -> datetime:
    """The moment a video goes live on EVERY platform: tomorrow at
    PUBLISH_HOUR_UTC:00 UTC — a fixed audience window instead of "whenever the
    cron happened to fire" (see PUBLISH_HOUR_UTC in CONFIG). run_pipeline()
    computes it once and hands it to both uploads, so YouTube and TikTok
    publish together. Naive datetime, UTC."""
    return (datetime.utcnow() + timedelta(days=1)).replace(
        hour=PUBLISH_HOUR_UTC, minute=0, second=0, microsecond=0)


def upload_youtube(video_path: str, title: str, description: str,
                   srt_path: Optional[str] = None,
                   tags: Optional[list[str]] = None,
                   publish_at: Optional[datetime] = None) -> Optional[str]:
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

    yt        = build("youtube","v3",credentials=creds,cache_discovery=False)
    # Fixed target hour instead of "whenever the cron happened to fire + 24h"
    # — lands content during a deliberately chosen audience window instead
    # of a random time of day (see publish_target_utc / PUBLISH_HOUR_UTC).
    _target = publish_at or publish_target_utc()
    pub_at    = _target.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    tag_list  = [t.lstrip("#") for t in (tags or ["shorts", "viral"])][:15]
    body      = {"snippet":  {"title": title[:100], "description": description,
                             "tags": tag_list, "categoryId": "22"},
               "status":   {"privacyStatus": "private", "publishAt": pub_at,
                             "selfDeclaredMadeForKids": False}}
    media   = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
    req     = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp    = None
    while resp is None:
        _, resp = req.next_chunk()
    vid_url = f"https://youtu.be/{resp['id']}"
    log.info("YouTube upload: %s", vid_url)

    # NOTE: captions are already burned into the video pixels via
    # apply_beautiful_captions(). Uploading the SRT as a native YouTube
    # caption track on top of that used to show TWO overlapping, differently
    # -timed caption sets whenever a viewer had captions toggled on in their
    # player. Disabled — burned-in captions already cover accessibility for
    # viewers watching muted, which is the main Shorts use case.
    # If you want real toggleable captions for hard-of-hearing viewers
    # instead, that's a legitimate reason to re-enable this — just be aware
    # it will double up with the burned-in ones.
    UPLOAD_NATIVE_CAPTIONS = False
    if UPLOAD_NATIVE_CAPTIONS and srt_path and Path(srt_path).exists():
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
# STEP 9b — TIKTOK UPLOAD (Content Posting API — Direct Post)
#
#  Mirrors upload_youtube()'s contract (returns a URL or None) but is
#  wrapped in its own try/except so a TikTok-side problem can never take
#  the rest of the pipeline down — the YouTube upload has already
#  succeeded by the time this runs.
#
#  Flow (see https://developers.tiktok.com/doc/content-posting-api-get-started):
#   1. refresh_token → 24h access_token       (own endpoint, no rate cap)
#   2. creator_info/query   → allowed privacy levels for THIS account
#   3. video/init            → publish_id + upload_url, chunked per TikTok's
#                               5-64MB/chunk rule (last chunk absorbs the
#                               remainder, exactly like TikTok's own example)
#   4. PUT the file to upload_url in those chunks
#   5. status/fetch, polled  → PUBLISH_COMPLETE (or FAILED)
# ══════════════════════════════════════════════════════════════════════

def _tiktok_refresh_access_token() -> Optional[str]:
    """Exchange the long-lived refresh_token for a fresh access_token.
    TikTok's refresh_token is valid ~1 year and CAN be reissued on refresh
    — if that happens we only log it (updating the GitHub secret is a
    manual step; see tiktok_oauth_setup.py / setup notes)."""
    if not (TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET and TIKTOK_REFRESH_TOKEN):
        log.warning("TikTok secrets not set (TIKTOK_CLIENT_KEY/_SECRET/_REFRESH_TOKEN) — skipping TikTok upload")
        return None
    try:
        r = requests.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Cache-Control": "no-cache"},
            data={"client_key": TIKTOK_CLIENT_KEY,
                  "client_secret": TIKTOK_CLIENT_SECRET,
                  "grant_type": "refresh_token",
                  "refresh_token": TIKTOK_REFRESH_TOKEN},
            timeout=20,
        )
        data = r.json()
        if "access_token" not in data:
            log.warning("TikTok token refresh failed: %s", data)
            return None
        new_rt = data.get("refresh_token")
        if new_rt and new_rt != TIKTOK_REFRESH_TOKEN:
            log.warning("TikTok issued a NEW refresh_token — update the "
                        "TIKTOK_REFRESH_TOKEN GitHub secret to this value "
                        "or TikTok uploads will eventually start failing: %s", new_rt)
        return data["access_token"]
    except Exception as e:
        log.warning("TikTok token refresh error: %s", e)
        return None


def _tiktok_creator_info(access_token: str) -> Optional[dict]:
    """Required before every Direct Post — tells us which privacy levels
    and features (duet/stitch/comment) TikTok currently allows for this
    account, per TikTok's UX guidelines."""
    try:
        r = requests.post(
            "https://open.tiktokapis.com/v2/post/publish/creator_info/query/",
            headers={"Authorization": f"Bearer {access_token}",
                     "Content-Type": "application/json; charset=UTF-8"},
            timeout=20,
        )
        data = r.json()
        if data.get("error", {}).get("code") != "ok":
            log.warning("TikTok creator_info failed: %s", data.get("error"))
            return None
        return data["data"]
    except Exception as e:
        log.warning("TikTok creator_info error: %s", e)
        return None


def upload_tiktok_official(video_path: str, title: str) -> Optional[str]:
    """Direct Post to TikTok via the official API. Returns the post URL
    on success, or None — never raises. Not called by run_pipeline() —
    see upload_tiktok() below."""
    try:
        access_token = _tiktok_refresh_access_token()
        if not access_token:
            return None

        creator = _tiktok_creator_info(access_token)
        if not creator:
            return None

        options = creator.get("privacy_level_options", [])
        privacy = "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in options \
                  else (options[0] if options else "SELF_ONLY")
        if privacy != "PUBLIC_TO_EVERYONE":
            log.info("TikTok client not yet audited (or account private) — "
                     "posting as %s. Will switch to public automatically "
                     "once TikTok approves the app — no code change needed.", privacy)

        video_size = Path(video_path).stat().st_size
        if video_size <= TIKTOK_CHUNK_SIZE:
            chunk_size, total_chunks = video_size, 1
        else:
            chunk_size   = TIKTOK_CHUNK_SIZE
            total_chunks = video_size // chunk_size   # floor — last chunk absorbs
                                                        # the remainder (TikTok's own formula)

        init_body = {
            "post_info": {
                "title": title[:2200],       # TikTok's own cap (UTF-16 runes)
                "privacy_level": privacy,
                "disable_duet": False,
                "disable_stitch": False,
                "disable_comment": False,    # channel wants MORE engagement, not less
                "is_aigc": True,             # script + voice are AI-generated —
                                              # TikTok's own required disclosure field
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunks,
            },
        }
        r = requests.post(
            "https://open.tiktokapis.com/v2/post/publish/video/init/",
            headers={"Authorization": f"Bearer {access_token}",
                     "Content-Type": "application/json; charset=UTF-8"},
            json=init_body, timeout=20,
        )
        resp = r.json()
        if resp.get("error", {}).get("code") != "ok":
            log.warning("TikTok init failed: %s", resp.get("error"))
            return None
        publish_id = resp["data"]["publish_id"]
        upload_url = resp["data"]["upload_url"]

        with open(video_path, "rb") as f:
            for i in range(total_chunks):
                start = i * chunk_size
                end   = video_size - 1 if i == total_chunks - 1 else start + chunk_size - 1
                f.seek(start)
                chunk = f.read(end - start + 1)
                pr = requests.put(
                    upload_url,
                    headers={"Content-Type": "video/mp4",
                             "Content-Length": str(len(chunk)),
                             "Content-Range": f"bytes {start}-{end}/{video_size}"},
                    data=chunk, timeout=120,
                )
                if pr.status_code not in (200, 201, 206):
                    log.warning("TikTok chunk %d/%d upload failed (%d): %s",
                                i + 1, total_chunks, pr.status_code, pr.text[:300])
                    return None

        # Publishing is async — poll status/fetch. Paced at 1 call/12s to
        # stay comfortably under TikTok's 6 requests/min per-token cap;
        # typical completion is 30s-2min per TikTok's own docs.
        for _ in range(15):   # up to 3 minutes
            time.sleep(12)
            sr = requests.post(
                "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
                headers={"Authorization": f"Bearer {access_token}",
                         "Content-Type": "application/json; charset=UTF-8"},
                json={"publish_id": publish_id}, timeout=20,
            )
            status = sr.json().get("data", {})
            if status.get("status") == "PUBLISH_COMPLETE":
                post_ids = status.get("publicaly_available_post_id") or []  # [sic] — TikTok's own field name
                username = creator.get("creator_username", "")
                tt_url = (f"https://www.tiktok.com/@{username}/video/{post_ids[0]}"
                          if post_ids and username else "posted (private — no public link yet)")
                log.info("TikTok upload: %s", tt_url)
                return tt_url
            if status.get("status") == "FAILED":
                log.warning("TikTok publish failed: %s", status.get("fail_reason"))
                return None

        log.warning("TikTok still processing after 3min — check TikTok Studio manually (publish_id: %s)", publish_id)
        return None
    except Exception as e:
        log.warning("TikTok upload error (non-fatal, pipeline continues): %s", e)
        return None


# ══════════════════════════════════════════════════════════════════════
# STEP 9c — TIKTOK UPLOAD (active path — via Zernio)
#
#  TikTok rejected the official app for production (see note above).
#  Zernio (zernio.com, formerly "Late") already holds TikTok's own
#  approval for ITS app, so connecting VaultMind's TikTok account through
#  Zernio posts via TikTok's real Content Posting API — no audit, no
#  cookies, no browser automation, no ToS/ban risk on our side. Free for
#  up to 2 connected accounts with no post-count cap (checked 2026-09
#  against several independent reviews, not just Zernio's own site) —
#  unlike Upload-Post/Postproxy/Bundle.social, whose free tiers cap at
#  10-20 posts/month, too low for this pipeline's ~60/month.
#
#  One-time setup (do this once in Zernio's dashboard, not scriptable):
#   1. Sign up at zernio.com, connect VaultMind's TikTok account (@vaultmindss).
#   2. Store ZERNIO_API_KEY as a repo secret. That's it — the account id is
#      looked up by username (TIKTOK_HANDLE) in _zernio_tiktok_account_id(),
#      so a second connected TikTok account (e.g. Ball Fight, on the free
#      2-account tier) can never receive this channel's videos by mistake.
#      ZERNIO_TIKTOK_ACCOUNT_ID stays as an optional hard override.
#
#  How this was verified (2026-10, no live API key was available):
#   - Method names/signatures were read from the installed zernio-sdk
#     (PyPI package "zernio-sdk", import name "zernio"):
#     media.get_media_presigned_url(), posts.create(), accounts.list_accounts(),
#     and the SDK's TikTokPlatformData model for the tiktok_settings keys.
#   - Videos here are ~15-60MB. media.upload() caps at 4MB and upload_large()
#     needs a separate Vercel Blob token, so this uses the presigned-URL flow
#     from docs.zernio.com/guides/media-uploads: presign -> PUT -> post.
#   - The whole path was run end-to-end against a local mock server.
#   NOT verified: Zernio's real server responses. If the first live run logs
#   a "Zernio ..." warning, it carries the API's own error text.
#
#  Timing: YouTube gets publishAt = tomorrow at PUBLISH_HOUR_UTC (see
#  upload_youtube). upload_tiktok() receives the SAME publish_at and creates
#  a SCHEDULED Zernio post (scheduledFor, explicit UTC "Z" — "taken as-is"
#  per docs.zernio.com/posts/create-post), so both platforms go live together.
#  Zernio copies the uploaded file to permanent storage when the post is
#  created ("A post scheduled or queued weeks or months ahead still publishes
#  with its media" — docs.zernio.com/guides/media-uploads). Without a
#  publish_at the post goes out immediately (publishNow). Scheduled posts have
#  no TikTok URL yet — the response only carries one for immediate posts — so
#  the dashboard link then points at the profile.
# ══════════════════════════════════════════════════════════════════════
TIKTOK_HANDLE            = "vaultmindss"   # https://www.tiktok.com/@vaultmindss
ZERNIO_API_KEY           = os.getenv("ZERNIO_API_KEY", "")
ZERNIO_TIKTOK_ACCOUNT_ID = os.getenv("ZERNIO_TIKTOK_ACCOUNT_ID", "")   # optional override

# Required by Zernio for every TikTok post. Key names are the SDK's
# TikTokPlatformData fields (nested keys are NOT auto-converted by the SDK,
# so they must be camelCase exactly like this).
#  - videoMadeWithAi: TikTok's "AI-generated content" label — script, voice
#    and editing are all machine-made (matches the site's privacy policy).
#  - contentPreviewConfirmed / expressConsentGiven: TikTok's UX-compliance
#    declarations; by running this automation the account owner makes them.
ZERNIO_TIKTOK_SETTINGS = {
    "privacyLevel":            "PUBLIC_TO_EVERYONE",
    "allowComment":            True,
    "allowDuet":               True,
    "allowStitch":             True,
    "videoMadeWithAi":         True,
    "contentPreviewConfirmed": True,
    "expressConsentGiven":     True,
}


def _zernio_tiktok_account_id(client) -> Optional[str]:
    """Returns the Zernio account id of the VaultMind TikTok account, or None.
    accounts.list_accounts(platform="tiktok") returns {"accounts": [{"_id",
    "platform", "username", "isActive", "needsReconnection", ...}]} (keys per
    the SDK's SocialAccount model). Matches on username so another connected
    TikTok account can't be picked by accident."""
    if ZERNIO_TIKTOK_ACCOUNT_ID:
        return ZERNIO_TIKTOK_ACCOUNT_ID
    data   = client.accounts.list_accounts(platform="tiktok")
    tiktok = [a for a in (data.get("accounts") or []) if a.get("platform") == "tiktok"]
    want   = TIKTOK_HANDLE.lower().lstrip("@")

    def name(a):
        return (a.get("username") or "").lower().lstrip("@")

    match = [a for a in tiktok if name(a) == want]
    if not match and len(tiktok) == 1 and not name(tiktok[0]):
        match = tiktok   # a single account without a username must be ours
    if not match:
        log.warning("Zernio: no connected TikTok account named @%s (found: %s) — connect "
                    "it in the Zernio dashboard or set ZERNIO_TIKTOK_ACCOUNT_ID",
                    want, [name(a) or "?" for a in tiktok] or "none")
        return None
    acc = match[0]
    if acc.get("isActive") is False or acc.get("needsReconnection"):
        log.warning("Zernio: TikTok account @%s must be reconnected in the Zernio dashboard "
                    "(token expired or revoked) — skipping TikTok upload", want)
        return None
    return acc.get("_id") or acc.get("id")


def _zernio_upload_video(client, video_path: str) -> str:
    """Presigned-URL upload (docs.zernio.com/guides/media-uploads):
    POST /v1/media/presign -> PUT the raw file to uploadUrl (no auth header,
    Content-Type must match) -> use publicUrl in the post. Returns publicUrl."""
    import re
    p            = Path(video_path)
    content_type = "video/quicktime" if p.suffix.lower() == ".mov" else "video/mp4"
    safe_name    = re.sub(r"[^A-Za-z0-9._-]", "_", p.name)

    pre        = client.media.get_media_presigned_url(safe_name, content_type,
                                                       size=p.stat().st_size)
    upload_url = pre.get("uploadUrl")
    public_url = pre.get("publicUrl")
    if not (upload_url and public_url):
        raise RuntimeError(f"unexpected presign response, keys: {sorted(pre)}")

    with open(p, "rb") as f:   # file object -> requests sends Content-Length, streamed
        r = requests.put(upload_url, data=f, headers={"Content-Type": content_type},
                         timeout=(15, 900))
    if r.status_code != 200:   # docs: success is an empty 200 with an ETag
        raise RuntimeError(f"storage upload failed: HTTP {r.status_code} {r.text[:200]}")
    return public_url


def upload_tiktok(video_path: str, title: str,
                  publish_at: Optional[datetime] = None) -> Optional[str]:
    """Posts to TikTok via Zernio (TikTok's real Content Posting API, under
    Zernio's own approved app). With publish_at (naive UTC datetime — pass the
    same one as upload_youtube) the post is SCHEDULED and Zernio publishes it
    then; without it, it goes out immediately. Returns the post URL when Zernio
    already has one (immediate posts), otherwise the account's profile URL once
    the post was accepted, or None on failure — never raises, so a TikTok-side
    problem can't break the run. The SDK deliberately does not retry a
    timed-out publish POST (it may have gone through), so a failure here can't
    create a duplicate post."""
    if not ZERNIO_API_KEY:
        log.warning("ZERNIO_API_KEY not set — skipping TikTok upload")
        return None
    try:
        from zernio import Zernio

        client     = Zernio(api_key=ZERNIO_API_KEY)
        account_id = _zernio_tiktok_account_id(client)
        if not account_id:
            return None

        public_url = _zernio_upload_video(client, video_path)
        log.info("Zernio: video uploaded, creating TikTok post …")

        # Same moment as YouTube: scheduledFor with an explicit "Z" is taken
        # as-is; publishNow would override it, so only one of the two is sent.
        when = ({"scheduled_for": publish_at.strftime("%Y-%m-%dT%H:%M:%SZ")}
                if publish_at else {"publish_now": True})
        resp = client.posts.create(
            content=title[:2200],   # TikTok caption cap
            platforms=[{"platform": "tiktok", "accountId": account_id}],
            media_items=[{"type": "video", "url": public_url}],
            tiktok_settings=ZERNIO_TIKTOK_SETTINGS,
            **when,
        )
        for w in (resp.warnings or []):
            log.warning("Zernio warning: %s", w)

        profile_url = f"https://www.tiktok.com/@{TIKTOK_HANDLE}"
        targets     = (resp.post.platforms if resp.post else None) or []
        t           = targets[0] if targets else None
        status      = ((t.status if t else None) or "").lower()

        if status == "failed":
            log.warning("TikTok post via Zernio FAILED: %s",
                        (t.errorMessage if t else None) or "no error text returned")
            return None
        if status == "published" and t.platformPostUrl:
            log.info("TikTok upload via Zernio: published — %s", t.platformPostUrl)
            return t.platformPostUrl
        if publish_at:
            log.info("TikTok post scheduled via Zernio for %s UTC (platform status=%s) — "
                     "it appears on %s then", publish_at.strftime("%Y-%m-%d %H:%M"),
                     status or "unknown", profile_url)
        else:
            log.info("TikTok upload via Zernio: accepted (status=%s) — check %s",
                     status or "unknown", profile_url)
        return profile_url
    except Exception as e:
        log.warning("TikTok upload via Zernio failed (non-fatal, pipeline continues): %s: %s",
                    type(e).__name__, e)
        return None


# ══════════════════════════════════════════════════════════════════════
# STEP 12 — DASHBOARD
# ══════════════════════════════════════════════════════════════════════

def update_dashboard(niche, title, url, ok, hook_style=None, tiktok_url=None):
    p    = Path("dashboard.json")
    data = json.loads(p.read_text()) if p.exists() else {"videos":[]}
    video_id = url.rstrip("/").split("/")[-1] if url else None
    data["videos"].insert(0,{"niche":niche,"title":title,"url":url,
                              "video_id":video_id,"hook_style":hook_style,
                              "tiktok_url":tiktok_url,
                              "ok":ok,"ts":datetime.utcnow().isoformat()})
    data["videos"] = data["videos"][:50]
    p.write_text(json.dumps(data,indent=2))


# ══════════════════════════════════════════════════════════════════════
# STEP 13 — SELF-LEARNING FEEDBACK LOOP (own video performance)
#
#  Runs as a SEPARATE scheduled job (check-performance.yml), a few days
#  after upload, so YouTube has had time to actually distribute the
#  video. It never blocks the main generate-video pipeline.
#
#  1. fetch_video_stats()     → public view/like/comment counts (free,
#                                simple API key, no OAuth re-auth needed)
#  2. check_performance()     → scores newly-eligible videos, updates
#                                performance_history.json
#  3. weighted_niche_choice() → future generate-video runs read this file
#                                and bias niche selection + prompts
#                                toward what has actually performed well
# ══════════════════════════════════════════════════════════════════════

def fetch_video_stats(video_id: str) -> Optional[dict]:
    """Public view/like/comment counts — no OAuth needed, just an API key."""
    if not YOUTUBE_API_KEY or not video_id:
        return None
    try:
        r = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"id": video_id, "part": "statistics",
                    "key": YOUTUBE_API_KEY},
            timeout=15,
        )
        items = r.json().get("items", [])
        if not items:
            log.warning("YouTube stats: no data for %s (private/deleted?)", video_id)
            return None
        stats = items[0]["statistics"]
        return {
            "views":    int(stats.get("viewCount", 0)),
            "likes":    int(stats.get("likeCount", 0)),
            "comments": int(stats.get("commentCount", 0)),
        }
    except Exception as e:
        log.warning("YouTube stats fetch failed for %s: %s", video_id, e)
        return None


def _engagement_score(stats: dict) -> float:
    """
    Views dominate the score (that's the real algorithm signal for Shorts),
    with a bonus for likes/comments per view (genuine engagement, not just
    impressions). Kept simple on purpose — this is a ranking signal for
    niche weighting, not a scientific metric.
    """
    views = max(stats["views"], 1)
    engagement_rate = (stats["likes"] + stats["comments"] * 3) / views
    return views * (1.0 + min(engagement_rate, 1.0))


def _load_performance() -> dict:
    p = Path(PERFORMANCE_FILE)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"scored_video_ids": [], "videos": [], "niche_stats": {}}


def _save_performance(perf: dict) -> None:
    perf["videos"] = perf["videos"][:PERFORMANCE_HISTORY_CAP]
    Path(PERFORMANCE_FILE).write_text(json.dumps(perf, indent=2))


def check_performance() -> None:
    """
    Entry point for the separate daily 'check-performance' workflow.
    Scores any dashboard.json videos that are old enough and not yet
    scored, then recomputes per-niche averages used by
    weighted_niche_choice() and own_performance_hint().
    """
    dash_path = Path("dashboard.json")
    if not dash_path.exists():
        log.info("No dashboard.json yet — nothing to score")
        return
    if not YOUTUBE_API_KEY:
        log.warning("YOUTUBE_API_KEY not set — cannot check performance. "
                    "Create a free API key in Google Cloud Console (APIs & "
                    "Services → Credentials) with YouTube Data API v3 "
                    "enabled, and add it as the YOUTUBE_API_KEY secret.")
        return

    dashboard = json.loads(dash_path.read_text())
    perf      = _load_performance()
    scored_ids = set(perf["scored_video_ids"])
    cutoff     = datetime.utcnow() - timedelta(days=PERFORMANCE_CHECK_DELAY_D)

    new_scores = 0
    for v in dashboard.get("videos", []):
        vid = v.get("video_id")
        if not v.get("ok") or not vid or vid in scored_ids:
            continue
        try:
            ts = datetime.fromisoformat(v["ts"])
        except Exception:
            continue
        if ts > cutoff:
            continue  # too young — check again on a later run

        stats = fetch_video_stats(vid)
        if stats is None:
            continue
        score = _engagement_score(stats)
        perf["videos"].insert(0, {
            "video_id": vid, "niche": v.get("niche"), "title": v.get("title"),
            "hook_style": v.get("hook_style"), "score": round(score, 2),
            **stats, "checked_at": datetime.utcnow().isoformat(),
        })
        scored_ids.add(vid)
        new_scores += 1
        log.info("Scored %s (%s): %d views, %d likes → score %.1f",
                 vid, v.get("niche"), stats["views"], stats["likes"], score)

    perf["scored_video_ids"] = list(scored_ids)[-PERFORMANCE_HISTORY_CAP*2:]

    # Recompute per-niche average score (simple mean of last N per niche)
    niche_scores: dict[str, list[float]] = {}
    for entry in perf["videos"]:
        niche_scores.setdefault(entry["niche"], []).append(entry["score"])
    perf["niche_stats"] = {
        n: {"avg_score": round(sum(s)/len(s), 2), "n": len(s)}
        for n, s in niche_scores.items()
    }

    _save_performance(perf)
    log.info("Performance check complete: %d newly scored, niche_stats=%s",
             new_scores, perf["niche_stats"])


def weighted_niche_choice() -> str:
    """
    Picks a niche weighted by past own-video performance instead of pure
    random.choice — better-performing niches get produced more often, but
    NICHE_EXPLORATION_FLOOR guarantees every niche still gets picked
    sometimes, so the pipeline keeps gathering data instead of collapsing
    onto one "winning" niche forever.
    """
    all_niches = list(NICHES.keys())
    perf = _load_performance()
    stats = perf.get("niche_stats", {})

    # Cold start / not enough data yet — behave exactly like before.
    if not stats:
        return random.choice(all_niches)

    scores = {n: stats.get(n, {}).get("avg_score", 0) for n in all_niches}
    max_score = max(scores.values()) or 1
    weights = [
        max(scores[n] / max_score, NICHE_EXPLORATION_FLOOR) if scores[n] > 0
        else NICHE_EXPLORATION_FLOOR
        for n in all_niches
    ]
    choice = random.choices(all_niches, weights=weights, k=1)[0]
    log.info("Niche weights (own performance): %s → chose '%s'",
             {n: round(w,2) for n,w in zip(all_niches, weights)}, choice)
    return choice


def own_performance_hint(niche: str) -> str:
    """
    Surfaces what has actually worked for THIS channel's own videos in this
    niche (title + hook style of the best scorer so far), as a companion
    to patterns_to_prompt_hints() which only knows about others' videos.
    """
    perf = _load_performance()
    own_videos = [v for v in perf.get("videos", []) if v.get("niche") == niche]
    if not own_videos:
        return ""
    best = max(own_videos, key=lambda v: v["score"])
    lines = [
        f"YOUR OWN PAST PERFORMANCE in {niche} ({len(own_videos)} videos scored):",
        f"- Best performer so far: \"{best['title']}\" ({best['views']} views)"
        + (f", hook style: {best['hook_style']}" if best.get("hook_style") else ""),
        "- Lean toward what has actually worked for this channel, not just "
        "generic viral theory.",
    ]
    return "\n".join(lines)


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

# Niches whose learned viral_patterns.json caption_style explicitly calls
# for NO accent color (e.g. horror doesn't use accent colors) — captions
# stay plain white/black for these regardless of the niche's brand color.
NO_ACCENT_NICHES = {"scary"}

# Caption highlight colors, tuned specifically for text legibility/appeal —
# NOT the same as NICHES[n]["color"] (which is for UI/vignette accents and
# includes a harsh neon green for lifehack + a teal-green for learn; those
# read badly as highlighted text, hence "nur grün sieht schlecht aus").
# Chosen to stay visually distinct from each other while avoiding harsh
# neons and low-contrast/dark hues.
CAPTION_HIGHLIGHT_COLORS = {
    "reddit":     "#FF4500",  # orange-red — unchanged, reads well
    "dating":     "#FF69B4",  # pink — unchanged, reads well
    "rich":       "#FFD700",  # gold — unchanged, classic highlight color
    "lifehack":   "#FFC107",  # was neon green #00FF88 → warm amber
    "fact":       "#00BFFF",  # blue — unchanged, reads well
    "scary":      "#FFFFFF",  # white — via NO_ACCENT_NICHES below
    "motivation": "#FF8C00",  # orange — unchanged, reads well
    "conspiracy": "#B57BE0",  # brightened from #9B59B6 for better contrast
    "learn":      "#4FC3F7",  # was teal-green #1ABC9C → light cyan-blue
}

def get_caption_style(niche: str) -> dict:
    """
    Derive caption color + animation type for a niche instead of hard-coding
    yellow/bounce for every niche. Prefers the learned viral_patterns.json
    (text_animation field from real analyzed videos), falls back to the
    niche's brand color from NICHES and a safe default animation.
    """
    color = "#FFFFFF" if niche in NO_ACCENT_NICHES else CAPTION_HIGHLIGHT_COLORS.get(niche, "#FFD700")
    animation = "bounce"
    try:
        patterns  = load_viral_patterns()
        niche_pat = patterns.get(niche, [])
        if niche_pat:
            animation = niche_pat[0].get("text_animation", animation)
    except Exception:
        pass
    if animation not in ("bounce", "pop", "fade"):
        animation = "bounce"
    return {"color": color, "animation": animation}


def apply_beautiful_captions(
    video_path:  str,
    srt_path:    str,
    output_path: str,
    niche:       str = "fact",
    words_per_line: int = 1,
    aai_data:    Optional[dict] = None,
) -> str:
    """
    Burn animated word-by-word captions onto video.
    Returns output_path (or video_path unchanged if both methods fail).
    """
    if not Path(srt_path).exists():
        log.warning("SRT missing — skipping caption burn")
        return video_path

    style = get_caption_style(niche)
    accent, anim_type = style["color"], style["animation"]

    # ── Method 1: ffmpeg ASS subtitles (PRIMARY) ────────────────────────
    # This is the primary method now, not the fallback: beautiful-captions
    # only animates how a caption CHUNK appears (bounce/pop/fade) — it has
    # no per-word "highlight the word being spoken right now" feature (its
    # own docs confirm "diarization" colors SPEAKERS, not the active word).
    # The ASS path highlights the spoken word (accent colour + pop) while
    # every word keeps a FIXED position — see "Stable caption layout" below.
    try:
        ass_path = srt_path.replace(".srt", ".ass")
        if aai_data and aai_data.get("words"):
            # Word-level timestamps available: the spoken word is coloured
            # and pops exactly while it is being spoken; nothing else moves.
            _build_word_level_ass(aai_data, ass_path, accent)
            method_desc = "word highlight + pop, fixed layout"
        else:
            # No word-level data (shouldn't normally happen) — same layout
            # and animation, word timing estimated from the coarser SRT.
            _srt_to_ass_karaoke(srt_path, ass_path, accent)
            method_desc = "word highlight + pop, fixed layout, timing estimated from SRT"
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", f"ass={ass_path}",
            "-c:a", "copy", "-preset", "fast", output_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        log.info("ASS captions applied (%s) → %s", method_desc, output_path)
        return output_path
    except Exception as e:
        log.warning("ASS karaoke failed (%s) — falling back to beautiful-captions", e)

    # ── Method 2: beautiful-captions (fallback — chunk-entrance animation,
    #    no active-word highlight, but still niche-colored) ─────────────
    if BEAUTIFUL_CAPTIONS_OK:
        try:
            cfg = CaptionConfig(
                animation={"enabled": True, "type": anim_type, "keyframes": 12},
                style={
                    "font":              "Anton",
                    "font_size":         140,
                    "color":             accent,
                    "outline_color":     "black",
                    "outline_thickness": 12,
                    # 0.0=bottom, 1.0=top (library docs). Kept off the very
                    # edge (not e.g. 0.10) so captions don't sit under
                    # YouTube Shorts' own UI overlay (like/comment/share
                    # buttons, sound title) at the bottom of the screen.
                    "verticle_position": 0.32,
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
            log.info("beautiful-captions: %s captions applied (%s) → %s",
                     anim_type, accent, output_path)
            return output_path
        except Exception as e:
            log.warning("beautiful-captions also failed (%s) — returning raw video", e)

    return video_path


def _group_words_into_chunks(words: list[dict], pause_ms: int = 300,
                             max_words: int = 5, max_dur_ms: int = 2000) -> list[list[dict]]:
    """
    Groups words the way CapCut/TikTok-style auto-captions do: break at a
    natural pause in speech, not at a fixed word count. A gap of
    pause_ms+ before a word starts a new chunk; max_words and max_dur_ms
    are just ceilings so a long pause-free run of speech still gets split
    into readable pieces (matches the common 3-7 words / 1-3s guidance).
    """
    if not words:
        return []
    chunks: list[list[dict]] = []
    current: list[dict] = []
    for w in words:
        if current:
            gap         = w["start_ms"] - current[-1]["end_ms"]
            dur_if_added = w["end_ms"] - current[0]["start_ms"]
            if gap >= pause_ms or len(current) >= max_words or dur_if_added > max_dur_ms:
                chunks.append(current)
                current = []
        current.append(w)
    if current:
        chunks.append(current)
    return chunks


# ── Stable caption layout ──────────────────────────────────────────────
# Why captions used to flicker: a caption block was ONE auto-wrapping, centred
# line, and the spoken word was scaled to 126% inside it. Every pulse made the
# line wider, so libass re-centred the whole block and — whenever the line was
# near the margin — re-wrapped it, jumping between 1 and 2 lines while it was
# on screen. On top of that every block faded out while the next one faded in,
# which dips the opacity at each block change. In a test render the block moved
# up to 234px inside a single caption and 9 of 21 captions changed line count.
# Same look (spoken word = accent colour + pop), different construction:
#  1. Line breaks are decided ONCE, here, with the real font's metrics, and
#     every word gets its own fixed position (\an5\pos). libass lays nothing
#     out any more, so nothing can re-wrap or re-centre.
#  2. The spoken word still pops, but around ITS OWN centre and in a layer
#     above the others — the words around it stay exactly where they are.
#  3. Events run back-to-back (end == next start), and a block only fades
#     in/out where the screen is empty before/after it (a real pause), so
#     there is never a dip between two blocks.
CAP_FONT_NAME    = "Liberation Sans"   # the style is Bold -> LiberationSans-Bold
CAP_FONT_SIZE    = 85                  # ASS Fontsize at PlayRes 1080x1920 (= line height, see _caption_width)
CAP_MARGIN_V     = 340                 # bottom margin of the block, px
CAP_MAX_LINE_W   = 880                 # px per line: 1080 - 2x60 margin, minus outline + metric slack
CAP_MIN_STATE_MS = 80                  # a highlight state stays up for >= 2-3 frames
CAP_HOLD_MS      = 140                 # last words stay up briefly after they are spoken
CAP_BRIDGE_MS    = 260                 # gaps shorter than this are bridged -> no blank frame between blocks
CAP_POP_SCALE    = 120                 # % the spoken word grows to at most (then settles back to 100%)
CAP_POP_MIN      = 108                 # ... and at least, even next to a neighbour (long words pop less)
CAP_WORD_GAP     = 8                   # extra px between words: room for the pop
CAP_LINE_GAP     = 8                   # extra px between the two lines
CAP_FADE_MS      = 70                  # fade in/out of a block, only next to a real pause

_CAP_FONT_FILES = (
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",   # wider than Liberation -> errs on the safe side
)
_cap_font_cache: dict = {}


def _caption_clean(text) -> str:
    """Strips what would be read as ASS markup and collapses whitespace."""
    return " ".join(str(text).replace("\\", "").replace("{", "").replace("}", "").split())


def _caption_width(text: str) -> float:
    """Width in px that libass gives `text` in the caption style. PIL measures
    the font at 1000px and scales it: ASS "Fontsize" is the LINE HEIGHT
    (ascent + descent), not the em size, so 85 means ~76px em for Liberation
    Sans (checked against real libass output)."""
    if "font" not in _cap_font_cache:
        _cap_font_cache["font"] = None
        for fp in _CAP_FONT_FILES:
            if Path(fp).exists():
                try:
                    f = ImageFont.truetype(fp, 1000)
                    asc, desc = f.getmetrics()
                    _cap_font_cache["font"] = (f, CAP_FONT_SIZE / float(asc + desc))
                    break
                except Exception:
                    continue
    font = _cap_font_cache["font"]
    if not font:                                   # no font file found: pessimistic estimate
        return len(text) * CAP_FONT_SIZE * 0.58
    f, scale = font
    return f.getlength(text) * scale


def _caption_lines(texts: list[str]) -> Optional[list[list[str]]]:
    """Splits the words into 1-2 lines that each fit CAP_MAX_LINE_W: one line
    if it fits, otherwise the most balanced two-line split. None = even two
    lines are too wide (the caller then splits the block)."""
    if _caption_width(" ".join(texts)) <= CAP_MAX_LINE_W:
        return [texts]
    best = None
    for k in range(1, len(texts)):
        wa = _caption_width(" ".join(texts[:k]))
        wb = _caption_width(" ".join(texts[k:]))
        if max(wa, wb) <= CAP_MAX_LINE_W and (best is None or abs(wa - wb) < best[0]):
            best = (abs(wa - wb), [texts[:k], texts[k:]])
    return best[1] if best else None


def _plan_caption_chunks(words: list[dict]) -> list[tuple]:
    """(chunk, lines, font_size) per caption block: the speech-pause grouping,
    with every group that doesn't fit on two lines split further. A single word
    wider than a line is shrunk once, statically."""
    plan: list[tuple] = []

    def place(chunk: list[dict]) -> None:
        texts = [_caption_clean(w["text"]) for w in chunk]
        lines = _caption_lines(texts)
        if lines is not None:
            plan.append((chunk, lines, CAP_FONT_SIZE))
        elif len(chunk) == 1:
            fs = max(40, int(CAP_FONT_SIZE * CAP_MAX_LINE_W / _caption_width(texts[0])))
            plan.append((chunk, [texts], fs))
        else:
            mid = len(chunk) // 2
            place(chunk[:mid])
            place(chunk[mid:])

    for ch in _group_words_into_chunks(words):
        place(ch)
    return plan


def _caption_place(lines: list[list[str]], fs: int = CAP_FONT_SIZE) -> list[tuple]:
    """(text, centre_x, centre_y, width, room) of every word of a block, in
    reading order; `room` = free px to the neighbouring word (None if the word
    is alone on its line). Each line is centred on the screen; the last line
    sits on the bottom margin and earlier lines stack above it — where a
    bottom-centred libass line would be, but decided here instead of by libass."""
    k      = fs / float(CAP_FONT_SIZE)
    gap    = (_caption_width(" ") + CAP_WORD_GAP) * k
    pitch  = (CAP_FONT_SIZE + CAP_LINE_GAP) * k
    bottom = VIDEO_H - CAP_MARGIN_V
    placed: list[tuple] = []
    for li, line in enumerate(lines):
        widths = [_caption_width(w) * k for w in line]
        x      = (VIDEO_W - (sum(widths) + gap * (len(line) - 1))) / 2.0
        cy     = bottom - CAP_FONT_SIZE * k / 2.0 - pitch * (len(lines) - 1 - li)
        for w, wd in zip(line, widths):
            placed.append((w, x + wd / 2.0, cy, wd, gap if len(line) > 1 else None))
            x += wd + gap
    return placed


def _build_word_level_ass(aai_data: dict, ass_path: str, accent: str) -> None:
    """
    Builds ASS captions from exact word-level timestamps. The spoken word is
    shown in the accent colour and pops (scales up, settles back) while it is
    spoken; all other words of the block stay white and, above all, stay put:
    every word is its own event at a fixed position (see "Stable caption
    layout" above).
    """
    words = [w for w in aai_data.get("words", []) if _caption_clean(w.get("text", ""))]
    if not words:
        raise ValueError("No word-level timestamps in aai_data")

    BS       = chr(92)                               # backslash, kept out of the f-strings
    r, g, b  = _hex(accent)
    hi       = f"&H{b:02X}{g:02X}{r:02X}&"

    def stamp(c: int) -> str:                        # centiseconds -> H:MM:SS.cc
        return f"{c // 360000}:{(c % 360000) // 6000:02d}:{(c % 6000) // 100:02d}.{c % 100:02d}"

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VIDEO_W}
PlayResY: {VIDEO_H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,ScaleX,ScaleY,Spacing,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV
Style: Default,{CAP_FONT_NAME},{CAP_FONT_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H99000000,-1,0,0,100,100,0,1,6,2,2,60,60,{CAP_MARGIN_V}

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    plan   = _plan_caption_chunks(words)
    events: list[tuple] = []                         # (start_cs, layer, seq, dialogue line)
    cursor = 0                                       # where the last block ended (ms)
    for ci, (chunk, lines, fs) in enumerate(plan):
        nxt  = plan[ci + 1][0][0]["start_ms"] if ci + 1 < len(plan) else None
        t0   = max(chunk[0]["start_ms"], cursor)
        t_end = chunk[-1]["end_ms"]
        if nxt is not None and nxt - t_end < CAP_BRIDGE_MS:
            t_end = nxt                              # close blocks: hold until the next one appears
        else:
            t_end += CAP_HOLD_MS                     # real pause: disappear shortly after the last word
            if nxt is not None:
                t_end = min(t_end, nxt)
        fade_in  = ci == 0 or t0 - cursor >= 100                     # screen was empty before this block
        fade_out = nxt is None or nxt - t_end >= 100                 # ... and stays empty after it

        # highlight-state boundaries: state j = [bounds[j], bounds[j+1])
        bounds = [t0]
        for w in chunk[1:]:
            bounds.append(max(w["start_ms"], bounds[-1] + CAP_MIN_STATE_MS))
        bounds.append(max(t_end, bounds[-1] + CAP_MIN_STATE_MS))
        cb = [int(round(x / 10.0)) for x in bounds]  # ASS clock = centiseconds
        for j in range(1, len(cb)):
            cb[j] = max(cb[j], cb[j - 1] + 1)        # strictly increasing, and end == next start
        cursor = cb[-1] * 10

        n      = len(chunk)
        placed = _caption_place(lines, fs)

        def emit(a: int, b: int, k: int, active: bool) -> None:
            """Word k from boundary a to boundary b. Active word: accent colour
            + pop in the layer above; others: plain white, never animated."""
            text, cx, cy, wd, room = placed[k]
            dur  = (cb[b] - cb[a]) * 10
            tags = f"{BS}an5{BS}pos({cx:.1f},{cy:.1f})"
            if fs != CAP_FONT_SIZE:
                tags += f"{BS}fs{fs}"
            fi = min(CAP_FADE_MS, dur // 2) if (a == 0 and fade_in) else 0
            fo = min(CAP_FADE_MS, dur // 2) if (b == n and fade_out) else 0
            if fi or fo:
                tags += f"{BS}fad({fi},{fo})"
            if active:
                pop   = max(30, min(90, int(dur * 0.35)))      # fast grow, slow settle
                if pop >= dur:
                    pop = max(10, dur // 2)
                # the word grows by (big-100)% of its width, half to each side: keep that
                # inside the gap to its neighbour, so long words pop a bit less than short ones
                big = CAP_POP_SCALE if room is None else \
                      int(max(CAP_POP_MIN, min(CAP_POP_SCALE, 100 + 200.0 * (room - 8) / max(wd, 1.0))))
                tags += (f"{BS}c{hi}{BS}fscx100{BS}fscy100"
                         f"{BS}t(0,{pop},{BS}fscx{big}{BS}fscy{big})"
                         f"{BS}t({pop},{dur},{BS}fscx100{BS}fscy100)")
            layer = 1 if active else 0
            events.append((cb[a], layer, len(events),
                           f"Dialogue: {layer},{stamp(cb[a])},{stamp(cb[b])},Default,,0,0,0,,{{{tags}}}{text}"))

        for k in range(n):
            if k > 0:
                emit(0, k, k, False)                 # not spoken yet
            emit(k, k + 1, k, True)                  # being spoken
            if k < n - 1:
                emit(k + 1, n, k, False)             # already spoken

    events.sort(key=lambda e: (e[0], e[1], e[2]))
    Path(ass_path).write_text(header + "\n".join(e[3] for e in events), encoding="utf-8")


def _srt_to_ass_karaoke(srt_path: str, ass_path: str, accent: str) -> None:
    """
    No word timestamps (shouldn't normally happen): spread each SRT block's
    words over the block by character count and reuse the stable builder, so
    this path gets the same fixed layout and highlight instead of its own
    auto-wrapping one.
    """
    def parse_t(t: str) -> int:
        h, m, s = t.strip().replace(",", ".").split(":")
        return int((int(h) * 3600 + int(m) * 60 + float(s)) * 1000)

    words = []
    for block in Path(srt_path).read_text(encoding="utf-8", errors="ignore").strip().split("\n\n"):
        parts = block.strip().split("\n")
        if len(parts) < 3:
            continue
        try:
            a, b = (parse_t(x) for x in parts[1].split(" --> "))
        except Exception:
            continue
        toks  = " ".join(parts[2:]).split()
        total = sum(len(t) + 1 for t in toks) or 1
        t     = float(a)
        for tok in toks:
            d = (b - a) * (len(tok) + 1) / total
            words.append({"text": tok, "start_ms": int(t), "end_ms": int(t + d)})
            t += d
    _build_word_level_ass({"words": words}, ass_path, accent)


# ══════════════════════════════════════════════════════════════════════
# POST-RENDER STEP B — CINEMATIC COLOR GRADE (ffmpeg)
#
#  Teal-orange grade: the single most recognisable "cinematic" look.
#  Also adds subtle unsharp mask (makes text pop harder).
#  ~2–3% retention increase based on A/B tests in similar channels.
# ══════════════════════════════════════════════════════════════════════

def apply_color_grade(video_path: str, output_path: str,
                      niche: str = "fact") -> str:
    """
    Apply niche-specific cinematic color grade via ffmpeg.
    Each niche has its own curated color curve (defined in NICHES dict).
    Falls back to teal-orange universal grade if niche grade missing.
    """
    # Get niche-specific grade, fall back to universal teal-orange
    vf = NICHES.get(niche, {}).get("color_grade") or (
        "curves="
        "r='0/0 0.3/0.27 0.7/0.74 1/0.95':"
        "g='0/0 0.3/0.30 0.7/0.72 1/0.96':"
        "b='0/0.04 0.3/0.33 0.7/0.68 1/0.85',"
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
        log.info("Color grade applied [%s] → %s", niche, output_path)
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



# ══════════════════════════════════════════════════════════════════════
# SELF-LEARNING: VIRAL VIDEO ANALYZER
#
#  Runs BEFORE script generation on each pipeline run.
#  Uses only FREE tools:
#    • yt-dlp   → download captions (no Whisper needed for YouTube)
#    • ffmpeg   → extract keyframes
#    • Groq Vision (qwen3.8-27b) → analyze frames (free tier, 1000 RPD)
#
#  Result: viral_patterns.json committed to repo.
#  Script generator reads it and adapts prompts automatically.
#
#  Curated viral Shorts per niche — all public YouTube URLs.
#  Update this list by adding YouTube Shorts links you like.
# ══════════════════════════════════════════════════════════════════════

VIRAL_REFERENCE_VIDEOS = {
    "fact":       ["https://www.youtube.com/shorts/2gpg7W7ht6A"],
    "scary":      ["https://youtube.com/shorts/JCEEvR-1b2A"],
    "motivation": ["https://youtube.com/shorts/5f7E4DQG6kk"],
    "reddit":     ["https://youtube.com/shorts/P0xBYEwcl-4"],
    "rich":       ["https://youtube.com/shorts/g1jmkEjykLk"],
    "conspiracy": ["https://youtube.com/shorts/VUyBmwGpzkE"],
    "dating":     ["https://youtube.com/shorts/2W4SDn_6WEw"],
    "lifehack":   ["https://youtube.com/shorts/klMk4UkYQ0o"],
    "learn":      [
        "https://youtube.com/shorts/kxOFEpLKNpk",
        "https://youtube.com/shorts/qHf2RSKLbsw",
    ],
}


def _extract_youtube_captions(url: str, out_path: str) -> Optional[str]:
    """Download auto-captions from YouTube via yt-dlp (free, no Whisper)."""
    try:
        import yt_dlp
        opts = {
            "skip_download":    True,
            "writeautomaticsub": True,
            "writesubtitles":   True,
            "subtitleslangs":   ["en"],
            "subtitlesformat":  "vtt",
            "outtmpl":          out_path.replace(".vtt",""),
            "quiet":            True,
            "nocheckcertificate": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        # yt-dlp appends .en.vtt
        vtt = out_path.replace(".vtt","") + ".en.vtt"
        if Path(vtt).exists():
            # Strip VTT timing tags → plain text
            text = Path(vtt).read_text(errors="ignore")
            lines = []
            for line in text.splitlines():
                line = line.strip()
                if line and "-->" not in line and not line.startswith("WEBVTT")                         and not line.startswith("NOTE") and not line.isdigit():
                    import re
                    clean = re.sub(r"<[^>]+>", "", line)
                    if clean and clean not in lines[-3:]:
                        lines.append(clean)
            return " ".join(lines)[:3000]
    except Exception as e:
        log.debug("Caption extraction failed: %s", e)
    return None


def _extract_frames(url: str, out_dir: str, n_frames: int = 8) -> list[str]:
    """
    Download video and extract n_frames keyframes as JPEGs.
    Uses yt-dlp + ffmpeg — both already in the GitHub Actions environment.
    """
    try:
        import yt_dlp
        video_path = os.path.join(out_dir, "clip.mp4")
        opts = {
            "format":    "bestvideo[height<=720][ext=mp4]/best[height<=720]",
            "outtmpl":   video_path,
            "quiet":     True,
            "nocheckcertificate": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        if not Path(video_path).exists():
            return []

        # Get duration
        probe = subprocess.run(
            ["ffprobe","-v","quiet","-show_entries","format=duration",
             "-of","default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True
        )
        duration = float(probe.stdout.strip() or "60")

        # Extract evenly-spaced frames
        frame_paths = []
        for i in range(n_frames):
            t        = duration * (i + 0.5) / n_frames
            out_jpg  = os.path.join(out_dir, f"frame_{i:02d}.jpg")
            subprocess.run(
                ["ffmpeg","-y","-ss",str(t),"-i",video_path,
                 "-frames:v","1","-q:v","3","-vf","scale=512:-1",
                 out_jpg],
                capture_output=True
            )
            if Path(out_jpg).exists():
                frame_paths.append(out_jpg)
        return frame_paths
    except Exception as e:
        log.debug("Frame extraction failed: %s", e)
        return []


def _analyze_frames_groq(frames: list[str], transcript: str,
                          niche: str) -> Optional[dict]:
    """
    Send frames + transcript to Groq Vision (qwen3.8-27b, free).
    Returns structured analysis dict or None on failure.
    """
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None
    try:
        import base64
        client = Groq(api_key=key)

        # Build image content blocks. Groq accepts max 3 images per request
        # (2048 input tokens each) — take first / middle / last of the frames
        # so the 3 still span the whole video. 3 images + prompt + 600 output
        # tokens ≈ 7.2K, just under the free tier's 8K tokens/minute.
        step  = (len(frames) - 1) / 2 if len(frames) > 3 else 1
        picks = [frames[round(k * step)] for k in range(min(3, len(frames)))]
        image_blocks = []
        for fp in picks:
            with open(fp, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            image_blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
            })

        prompt = (
            f'You are a viral short-form video analyst. Niche: {niche}.\n'
            f'Transcript snippet: "{transcript[:600]}"\n\n'
            'Analyze these video frames and return a JSON object with:\n'
            '- hook_style: how does the first 2 seconds grab attention?\n'
            '- caption_style: font size (small/medium/large), position (top/center/bottom), '
            'highlighted words yes/no, color\n'
            '- cut_speed: average scene length estimate in seconds (e.g. 2, 3, 4)\n'
            '- visual_style: dark/bright/colorful/minimal, any overlays or effects\n'
            '- text_animation: bounce/fade/none/pop\n'
            '- background_type: stock_footage/solid_color/gameplay/animation\n'
            '- emotional_trigger: curiosity/fear/inspiration/humor/shock\n'
            '- what_makes_it_viral: 1-2 sentences\n'
            'Return ONLY valid JSON, no explanation.'
        )

        resp = client.chat.completions.create(
            model=GROQ_VISION_MODEL,
            messages=[{
                "role": "user",
                "content": image_blocks + [{"type": "text", "text": prompt}]
            }],
            response_format={"type": "json_object"},
            reasoning_effort="none",     # instruct mode: no thinking tokens eating the cap
            max_completion_tokens=600,
            temperature=0.3,
        )
        return _json_from_llm(resp.choices[0].message.content)
    except Exception as e:
        log.warning("Groq Vision analysis failed: %s", e)
        return None


def analyze_viral_videos(force: bool = False) -> dict:
    """
    Analyze curated viral reference videos and update viral_patterns.json.

    Skips if viral_patterns.json was updated in the last 3 days (to avoid
    hitting Groq rate limits on every run). Set force=True to override.

    Returns the loaded patterns dict.
    """
    patterns_path = Path("viral_patterns.json")

    # Load existing patterns
    if patterns_path.exists():
        try:
            existing = json.loads(patterns_path.read_text())
        except Exception:
            existing = {}
    else:
        existing = {}

    # Skip if recently updated
    last_run = existing.get("_last_analyzed", "")
    if not force and last_run:
        try:
            age_days = (datetime.utcnow() -
                        datetime.fromisoformat(last_run)).days
            if age_days < 3:
                log.info("viral_patterns.json is %d days old — skipping analysis", age_days)
                return existing
        except Exception:
            pass

    log.info("Starting viral video analysis…")
    patterns = dict(existing)

    for niche, urls in VIRAL_REFERENCE_VIDEOS.items():
        if not urls:
            continue
        niche_results = []
        for url in urls[:2]:   # max 2 per niche per run (rate limit friendly)
            log.info("Analyzing [%s]: %s", niche, url)
            with tempfile.TemporaryDirectory() as td:
                transcript = _extract_youtube_captions(url, os.path.join(td,"caps.vtt"))
                transcript = transcript or ""
                frames     = _extract_frames(url, td, n_frames=8)
                if not frames:
                    log.warning("No frames extracted for %s", url)
                    continue
                analysis = _analyze_frames_groq(frames, transcript, niche)
                if analysis:
                    analysis["url"]       = url
                    analysis["analyzed_at"] = datetime.utcnow().isoformat()
                    niche_results.append(analysis)
                    log.info("[%s] Analysis: %s", niche,
                             json.dumps(analysis, indent=2)[:300])
                time.sleep(2)   # be gentle on rate limits

        if niche_results:
            patterns[niche] = niche_results

    patterns["_last_analyzed"] = datetime.utcnow().isoformat()
    patterns_path.write_text(json.dumps(patterns, indent=2))
    log.info("viral_patterns.json updated (%d niches)", len(patterns)-1)
    return patterns


def load_viral_patterns() -> dict:
    """Load existing viral patterns, return empty dict if unavailable."""
    try:
        p = Path("viral_patterns.json")
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return {}


def patterns_to_prompt_hints(patterns: dict, niche: str) -> str:
    """
    Convert analyzed viral patterns for a niche into concrete script hints.
    These get injected into the Groq script generation prompt.
    """
    niche_data = patterns.get(niche, [])
    if not niche_data:
        return ""

    # Aggregate across multiple analyzed videos
    cut_speeds  = [d.get("cut_speed", 3) for d in niche_data if "cut_speed" in d]
    hook_styles = [d.get("hook_style","") for d in niche_data if d.get("hook_style")]
    triggers    = [d.get("emotional_trigger","") for d in niche_data if d.get("emotional_trigger")]
    viral_tips  = [d.get("what_makes_it_viral","") for d in niche_data if d.get("what_makes_it_viral")]

    avg_cut = round(sum(cut_speeds)/len(cut_speeds), 1) if cut_speeds else 3.5
    hints = [
        f"VIRAL DATA for {niche} (learned from real viral videos in this niche):",
        f"- Ideal scene length: ~{avg_cut}s per scene",
    ]
    if hook_styles:
        hints.append(f"- Hook style that works: {hook_styles[0]}")
    if triggers:
        unique_triggers = list(dict.fromkeys(triggers))
        hints.append(f"- Emotional triggers that drive retention: {', '.join(unique_triggers[:3])}")
    if viral_tips:
        hints.append(f"- What makes {niche} videos go viral: {viral_tips[0]}")
    return '\n'.join(hints)


def run_pipeline(niche: Optional[str] = None) -> None:
    niche = niche or weighted_niche_choice()
    log.info("═══ VaultMind v4 — niche: %s ═══", niche)

    ensure_font()
    music_path             = ensure_music(niche=niche, duration_s=120)
    sfx_impact             = ensure_sfx()

    # Analyze curated viral videos (free, skips if <3 days old)
    analyze_viral_videos()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # 1. Script
        script    = generate_script(niche)
        scenes    = script["scenes"]
        title     = script["title"]
        full_text = script.get("hook","") + " " + " ".join(s["text"] for s in scenes)
        log.info("Title: %s | %d scenes", title, len(scenes))

        # 2. Voiceover — delivery follows the topic (script's tone, niche default)
        audio_path = str(tmp/"voice.mp3")
        generate_voiceover(full_text, audio_path, niche=niche, tone=script.get("tone"))

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
                sfx_impact_path=sfx_impact,
                title=title,
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
                niche          = niche,
                words_per_line = 1,
                aai_data       = aai_data,
            )

        # 8. Cinematic color grade (teal-orange + unsharp)
        final_video = str(tmp/"final.mp4")
        final_video = apply_color_grade(captioned_video, final_video, niche=niche)

        # 9. Upload
        hashtags  = script.get("hashtags", [])
        tag_line  = " ".join(h if h.startswith("#") else f"#{h}" for h in hashtags) \
                    or f"#shorts #{niche} #viral #fyp"
        desc      = f"{title}\n\n{tag_line}"
        publish_at = publish_target_utc()   # ONE go-live moment for both platforms
        video_url  = upload_youtube(final_video, title, desc, srt_path, tags=hashtags,
                                    publish_at=publish_at)
        tiktok_url = upload_tiktok(final_video, desc, publish_at=publish_at)

        # 10. Google Drive backup
        backup_to_drive(final_video, srt_path, title)

        # 11. Dashboard
        update_dashboard(niche, title, video_url, ok=True,
                          hook_style=script.get("hook_style_used"),
                          tiktok_url=tiktok_url)

    log.info("═══ v4 complete — YouTube: %s | TikTok: %s ═══",
              video_url or "no URL", tiktok_url or "no URL")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--check-performance":
        check_performance()
    else:
        run_pipeline(sys.argv[1] if len(sys.argv) > 1 else None)
