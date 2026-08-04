"""
VaultMind Ball Fight — automated daily TikTok pipeline
────────────────────────────────────────────────────────
Simulates a short physics "ball fight" (pymunk), writes short bursts of
AI commentary (Groq) tied to real in-sim events, mixes them with an
always-epic synthesized music bed + collision SFX, appends a "<COLOR>
WINS!" end card with a follow/like call-to-action, color-grades the
result, and Direct-Posts it to TikTok via the Content Posting API.

No burned-in captions — the video speaks (literally, sparingly) for
itself.
"""

import os, json, time, random, logging, tempfile
import subprocess, shutil, math
from pathlib import Path
from typing import Optional

import pymunk

import requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pydub import AudioSegment

from groq import Groq
import edge_tts, asyncio
import colorsys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("vaultmind")

# ══════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════
VIDEO_W, VIDEO_H   = 1080, 1920
FPS                = 30
MUSIC_DUCK_DB      = -16
FONT_PATH          = "font_bold.ttf"

# Visual identity + ffmpeg color-grade curve for the Ball Fight niche.
NICHES = {
    "ballfight": {
        "color": "#39FF14", "glow": "#8CFF66", "bg": "#04050A",
        "color_grade": (
            "curves=r='0/0 0.4/0.42 0.8/0.85 1/1.0':"
            "g='0/0 0.4/0.50 0.8/0.92 1/1.0':"
            "b='0/0 0.4/0.35 0.8/0.70 1/0.90',"  # punchy neon arcade look
            "unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount=0.8"
        ),
    },
}


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

def _elevenlabs_tts(text: str, path: str) -> bool:
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

    try:
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": key, "Content-Type": "application/json"},
            json={"text": text, "model_id": "eleven_turbo_v2_5",
                  "voice_settings": {"stability": 0.30, "similarity_boost": 0.82,
                                     "style": 0.55, "use_speaker_boost": True}},
            timeout=90,
        )
    except Exception as e:
        log.warning("ElevenLabs: request error: %s", e)
        return False

    if r.status_code == 200:
        Path(path).write_bytes(r.content)
        log.info("ElevenLabs TTS OK (%d bytes)", len(r.content))
        return True
    # Surface the actual reason — quota, invalid key, etc.
    log.warning("ElevenLabs failed (%d): %s", r.status_code, r.text[:200])
    return False


def _openai_tts(text: str, path: str) -> bool:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        log.info("OpenAI TTS: no key set, skipping")
        return False
    try:
        r = requests.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": "tts-1-hd", "voice": "onyx", "input": text, "speed": 1.08},
            timeout=90,
        )
    except Exception as e:
        log.warning("OpenAI TTS: request error: %s", e)
        return False

    if r.status_code == 200:
        Path(path).write_bytes(r.content)
        log.info("OpenAI TTS OK (%d bytes)", len(r.content))
        return True
    log.warning("OpenAI TTS failed (%d): %s", r.status_code, r.text[:200])
    return False


def _edgetts_tts(text: str, path: str) -> bool:
    """
    edge-tts fallback. Uses a standard (non-multilingual) voice, since
    multilingual variants are more prone to 'NoAudioReceived' errors in
    CI environments. Retries up to 3 times with backoff — Microsoft's
    endpoint occasionally rate-limits/blocks datacenter IPs (GitHub Actions).
    """
    voices_to_try = ["en-US-GuyNeural", "en-US-ChristopherNeural", "en-US-EricNeural"]

    for voice in voices_to_try:
        for attempt in range(3):
            try:
                async def _run():
                    comm = edge_tts.Communicate(text, voice, rate="+8%", volume="+10%")
                    await comm.save(path)
                asyncio.run(_run())
                if Path(path).exists() and Path(path).stat().st_size > 1000:
                    log.info("edge-tts OK (voice=%s, attempt=%d)", voice, attempt + 1)
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


def generate_voiceover(text: str, path: str) -> None:
    """
    Voice fallback chain (first success wins):
    ElevenLabs → OpenAI TTS → edge-tts (3 voices × 3 retries) → gTTS
    Raises RuntimeError only if every single option fails.
    """
    if _elevenlabs_tts(text, path):
        return
    if _openai_tts(text, path):
        return
    if _edgetts_tts(text, path):
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

def _hex(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0,2,4))


# ══════════════════════════════════════════════════════════════════════
# STEP 11 — YOUTUBE UPLOAD
# ══════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════
# STEP 11b — TIKTOK UPLOAD (Content Posting API v2, Direct Post)
#
#  Flow: refresh access token → query creator_info (allowed privacy
#  levels) → POST /video/init/ (FILE_UPLOAD) → PUT raw video bytes to
#  the returned upload_url → poll /status/fetch/ until PUBLISH_COMPLETE.
#
#  IMPORTANT CAVEAT (real TikTok platform constraint, not a bug here):
#  until this TikTok developer app has passed TikTok's content-posting
#  audit, every video posted via the API is forced to SELF_ONLY privacy
#  (visible only to the account owner) regardless of what's requested.
#  Once the app is audited/approved, PUBLIC_TO_EVERYONE becomes an
#  available option and this code will pick it up automatically (it
#  reads the allowed levels fresh from creator_info on every run).
# ══════════════════════════════════════════════════════════════════════

TIKTOK_API = "https://open.tiktokapis.com/v2"

def _tiktok_refresh_token() -> Optional[str]:
    """Exchange the long-lived TIKTOK_REFRESH_TOKEN for a fresh access token."""
    client_key    = os.getenv("TIKTOK_CLIENT_KEY")
    client_secret = os.getenv("TIKTOK_CLIENT_SECRET")
    refresh_token = os.getenv("TIKTOK_REFRESH_TOKEN")
    if not (client_key and client_secret and refresh_token):
        log.warning("TikTok: missing TIKTOK_CLIENT_KEY/SECRET/REFRESH_TOKEN — skipping upload")
        return None
    try:
        r = requests.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"client_key": client_key, "client_secret": client_secret,
                  "grant_type": "refresh_token", "refresh_token": refresh_token},
            timeout=20,
        )
        data = r.json()
        if "access_token" in data:
            log.info("TikTok: access token refreshed (expires in %ss)", data.get("expires_in"))
            return data["access_token"]
        log.warning("TikTok: token refresh failed: %s", data)
    except Exception as e:
        log.warning("TikTok: token refresh error: %s", e)
    return None


def _tiktok_creator_info(token: str) -> dict:
    try:
        r = requests.post(f"{TIKTOK_API}/post/publish/creator_info/query/",
                          headers={"Authorization": f"Bearer {token}"}, timeout=20)
        body = r.json()
        log.info("TikTok creator_info raw response [%s]: %s", r.status_code, body)
        return body.get("data", {})
    except Exception as e:
        log.warning("TikTok: creator_info query failed: %s", e)
        return {}


def upload_tiktok(video_path: str, title: str, hashtags: Optional[list[str]] = None) -> Optional[str]:
    """
    Direct-Post a video to TikTok via the Content Posting API (FILE_UPLOAD).
    Returns a status string (publish_id) since TikTok's API does not hand
    back a public post URL synchronously — check the account for the live
    post once PUBLISH_COMPLETE is reached.
    """
    token = _tiktok_refresh_token()
    if not token:
        return None

    info          = _tiktok_creator_info(token)
    allowed_priv  = info.get("privacy_level_options") or ["SELF_ONLY"]
    # Prefer public if the app's audit status allows it; otherwise SELF_ONLY
    # is the only privacy_level unaudited clients are actually permitted to
    # post with — don't just grab allowed_priv[0], since a private account's
    # options can list FOLLOWER_OF_CREATOR/MUTUAL_FOLLOW_FRIENDS first, and
    # TikTok rejects those for unaudited apps even though they're "valid"
    # options for the account in general.
    if "PUBLIC_TO_EVERYONE" in allowed_priv:
        privacy = "PUBLIC_TO_EVERYONE"
    elif "SELF_ONLY" in allowed_priv:
        privacy = "SELF_ONLY"
    else:
        privacy = allowed_priv[0]
    if privacy == "SELF_ONLY":
        log.warning("TikTok: app not yet audited for public posting — "
                    "video will upload as private (visible only to the account owner)")
    log.info("TikTok: creator=%s privacy_level_options=%s -> using privacy=%s",
              info.get("creator_username"), allowed_priv, privacy)

    caption = title[:150]
    if hashtags:
        tag_line = " ".join(h if h.startswith("#") else f"#{h}" for h in hashtags)
        caption  = f"{caption} {tag_line}"[:2200]

    size = Path(video_path).stat().st_size
    try:
        init = requests.post(
            f"{TIKTOK_API}/post/publish/video/init/",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "post_info": {
                    "title": caption,
                    "privacy_level": privacy,
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                    "video_cover_timestamp_ms": 1000,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": size,
                    "chunk_size": size,
                    "total_chunk_count": 1,
                },
            },
            timeout=30,
        ).json()
    except Exception as e:
        log.warning("TikTok: init request failed: %s", e)
        return None

    data = init.get("data", {})
    upload_url, publish_id = data.get("upload_url"), data.get("publish_id")
    if not upload_url:
        log.warning("TikTok: init response missing upload_url: %s", init)
        return None

    try:
        with open(video_path, "rb") as f:
            put = requests.put(
                upload_url,
                headers={"Content-Type": "video/mp4",
                         "Content-Range": f"bytes 0-{size-1}/{size}"},
                data=f, timeout=180,
            )
        if put.status_code not in (200, 201):
            log.warning("TikTok: video PUT failed (%d): %s", put.status_code, put.text[:200])
            return None
    except Exception as e:
        log.warning("TikTok: video upload error: %s", e)
        return None

    # Poll publish status (async on TikTok's side)
    for _ in range(20):
        time.sleep(3)
        try:
            status = requests.post(
                f"{TIKTOK_API}/post/publish/status/fetch/",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"publish_id": publish_id}, timeout=15,
            ).json().get("data", {})
        except Exception as e:
            log.warning("TikTok: status poll error: %s", e)
            break
        st = status.get("status")
        if st == "PUBLISH_COMPLETE":
            log.info("TikTok upload complete: %s (privacy=%s)", publish_id, privacy)
            return publish_id
        if st == "FAILED":
            log.warning("TikTok: publish failed: %s", status)
            return None
    log.info("TikTok: upload accepted, still processing (publish_id=%s)", publish_id)
    return publish_id


# ══════════════════════════════════════════════════════════════════════
# STEP 12 — DASHBOARD
# ══════════════════════════════════════════════════════════════════════

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

BF_W, BF_H, BF_FPS = VIDEO_W, VIDEO_H, FPS
BF_MODES = ["battle_royale", "color_infection", "gravity_race"]
BF_PATTERNS = ["solid", "stripes", "dots", "eyes"]


def _bf_vivid_color(h: float, s: float = 0.85, v: float = 0.98) -> tuple:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def _bf_make_sprite(diameter: int, color: tuple, pattern: str, rng: random.Random) -> Image.Image:
    """Pre-render one ball as an antialiased RGBA sprite (supersampled 4x)."""
    ss = 4
    d = diameter
    big = Image.new("RGBA", (d * ss, d * ss), (0, 0, 0, 0))
    dr = ImageDraw.Draw(big)
    pad = 2 * ss
    dr.ellipse([pad, pad, d * ss - pad, d * ss - pad], fill=color + (255,))
    # subtle highlight for a "bouncy toy" look
    hl_r = d * ss * 0.18
    dr.ellipse([d * ss * 0.28 - hl_r, d * ss * 0.26 - hl_r,
                d * ss * 0.28 + hl_r, d * ss * 0.26 + hl_r], fill=(255, 255, 255, 90))
    if pattern == "stripes":
        for i in range(-d, d, max(6, d // 4)):
            dr.line([(i * ss, 0), ((i + d) * ss, d * ss)], fill=(255, 255, 255, 80), width=ss * 2)
    elif pattern == "dots":
        for _ in range(5):
            cx = rng.uniform(0.25, 0.75) * d * ss
            cy = rng.uniform(0.25, 0.75) * d * ss
            rr = rng.uniform(0.05, 0.09) * d * ss
            dr.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(255, 255, 255, 110))
    elif pattern == "eyes":
        ex, ey, er = d * ss * 0.5, d * ss * 0.42, d * ss * 0.11
        for sign in (-1, 1):
            cx = ex + sign * er * 1.2
            dr.ellipse([cx - er, ey - er, cx + er, ey + er], fill=(255, 255, 255, 235))
            dr.ellipse([cx - er * 0.45, ey - er * 0.3, cx + er * 0.45, ey + er * 0.7],
                       fill=(20, 20, 20, 255))
        dr.arc([ex - er * 1.6, ey + er * 0.5, ex + er * 1.6, ey + er * 2.4],
               start=15, end=165, fill=(30, 30, 30, 255), width=max(2, int(ss * 1.1)))
    return big.resize((d, d), Image.LANCZOS)


def _bf_ffmpeg_writer(path: str, w: int, h: int, fps: int) -> subprocess.Popen:
    return subprocess.Popen(
        ["ffmpeg", "-y", "-f", "rawvideo", "-pixel_format", "rgb24",
         "-video_size", f"{w}x{h}", "-framerate", str(fps),
         "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
         "-pix_fmt", "yuv420p", path],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _bf_background(w: int, h: int, bg_hex: str, accent: tuple) -> Image.Image:
    base = _hex(bg_hex)
    img = Image.new("RGB", (w, h), base)
    dr = ImageDraw.Draw(img)
    cx, cy = w / 2, h * 0.42
    for i, r in enumerate(range(int(w * 0.75), 0, -40)):
        fade = i / (w * 0.75 / 40)
        col = tuple(int(base[k] + (accent[k] - base[k]) * 0.05 * (1 - fade)) for k in range(3))
        dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    return img


def _bf_hud_text(img: Image.Image, xy: tuple, text: str, size: int,
                 color: tuple = (255, 255, 255), anchor: str = "mm") -> None:
    dr = ImageDraw.Draw(img)
    font = load_font(size)
    # cheap outline for legibility over busy footage
    x, y = xy
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
        dr.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0), anchor=anchor)
    dr.text(xy, text, font=font, fill=color, anchor=anchor)


def _bf_gen_balls(n: int, radius_range: tuple, rng: random.Random,
                  palette: Optional[list] = None) -> list:
    balls = []
    for i in range(n):
        r = rng.uniform(*radius_range)
        color = palette[i % len(palette)] if palette else _bf_vivid_color(rng.random())
        pattern = rng.choice(BF_PATTERNS)
        sprite = _bf_make_sprite(int(r * 2), color, pattern, rng)
        balls.append({"id": i, "r": r, "color": color, "pattern": pattern, "sprite": sprite})
    return balls


def _bf_paste_sprite(img: Image.Image, sprite: Image.Image, pos: tuple, angle_rad: float) -> None:
    rotated = sprite.rotate(-math.degrees(angle_rad), resample=Image.BICUBIC, expand=False)
    x, y = pos
    img.paste(rotated, (int(x - sprite.width / 2), int(y - sprite.height / 2)), rotated)


# ─────────────────────────────────────────────────────────────────────
# MODE 1 — BATTLE ROYALE (shrinking circular arena, elimination)
# ─────────────────────────────────────────────────────────────────────

def simulate_battle_royale(target_dur: float, out_path: str, rng: random.Random) -> tuple:
    space = pymunk.Space(); space.gravity = (0, 0)
    center = (BF_W / 2, BF_H * 0.44)
    r0 = BF_W * 0.46
    r1 = BF_W * 0.10
    grace = target_dur * 0.18
    n = rng.randint(8, 14)
    accent = NICHES["ballfight"]["glow"]

    balls_meta = _bf_gen_balls(n, (32, 46), rng)
    live = []
    for bm in balls_meta:
        mass = (bm["r"] ** 2) * 0.02
        body = pymunk.Body(mass, pymunk.moment_for_circle(mass, 0, bm["r"]))
        ang = rng.uniform(0, 2 * math.pi); dist = rng.uniform(0, r0 * 0.55)
        body.position = (center[0] + math.cos(ang) * dist, center[1] + math.sin(ang) * dist)
        speed = rng.uniform(140, 260)
        vang = rng.uniform(0, 2 * math.pi)
        body.velocity = (math.cos(vang) * speed, math.sin(vang) * speed)
        shape = pymunk.Circle(body, bm["r"]); shape.elasticity = 0.97; shape.friction = 0.05
        space.add(body, shape)
        live.append({**bm, "body": body, "shape": shape, "alive": True})

    def radius_at(t):
        sudden_death = target_dur - 3.0
        if t < grace:
            return r0
        if t < sudden_death:
            frac = (t - grace) / max(0.01, sudden_death - grace)
            return r0 - (r0 - r1) * frac
        return r1  # hold tight for forced finish

    events = [{"t": 0.0, "type": "start", "n": n}]
    touching, winner = set(), None
    proc = _bf_ffmpeg_writer(out_path, BF_W, BF_H, BF_FPS)
    bg = _bf_background(BF_W, BF_H, NICHES["ballfight"]["bg"], _hex(accent))
    n_frames = int(target_dur * BF_FPS)

    for f in range(n_frames):
        t = f / BF_FPS
        for _ in range(2):
            space.step(1 / (BF_FPS * 2))
        R = radius_at(t)
        alive_balls = [b for b in live if b["alive"]]
        # boundary handling
        for b in alive_balls:
            p = b["body"].position
            d = math.hypot(p[0] - center[0], p[1] - center[1])
            if d + b["r"] > R:
                if d + b["r"] > R + b["r"] * 0.7 and len(alive_balls) > 1:
                    b["alive"] = False
                    space.remove(b["body"], b["shape"])
                    events.append({"t": round(t, 2), "type": "eliminated",
                                    "id": b["id"], "color": b["color"], "left": len(alive_balls) - 1})
                else:
                    nx, ny = (p[0] - center[0]) / max(d, 1e-6), (p[1] - center[1]) / max(d, 1e-6)
                    vx, vy = b["body"].velocity
                    dot = vx * nx + vy * ny
                    if dot > 0:
                        b["body"].velocity = (vx - 2 * dot * nx, vy - 2 * dot * ny)
        # collision logging (for SFX)
        alive_balls = [b for b in live if b["alive"]]
        for i in range(len(alive_balls)):
            for j in range(i + 1, len(alive_balls)):
                a, b = alive_balls[i], alive_balls[j]
                pa, pb = a["body"].position, b["body"].position
                dist = math.hypot(pa[0] - pb[0], pa[1] - pb[1])
                key = (a["id"], b["id"])
                if dist < a["r"] + b["r"]:
                    if key not in touching:
                        speed = math.hypot(*a["body"].velocity) + math.hypot(*b["body"].velocity)
                        events.append({"t": round(t, 2), "type": "collision", "impact": min(1.0, speed / 800)})
                    touching.add(key)
                else:
                    touching.discard(key)

        if len(alive_balls) == 1 and winner is None:
            winner = alive_balls[0]
            events.append({"t": round(t, 2), "type": "winner", "id": winner["id"], "color": winner["color"]})

        img = bg.copy()
        dr = ImageDraw.Draw(img)
        dr.ellipse([center[0] - R - 6, center[1] - R - 6, center[0] + R + 6, center[1] + R + 6],
                   outline=_hex(accent), width=10)
        dr.ellipse([center[0] - R, center[1] - R, center[0] + R, center[1] + R],
                   outline=(255, 255, 255), width=3)
        for b in alive_balls:
            _bf_paste_sprite(img, b["sprite"], b["body"].position, b["body"].angle)
        left_n = len(alive_balls) if winner is None else 1
        _bf_hud_text(img, (BF_W / 2, 90), f"{left_n} LEFT" if winner is None else "WINNER!", 78, _hex(accent))
        proc.stdin.write(np.array(img).tobytes())

    proc.stdin.close(); proc.wait()
    if winner is None and live:
        winner = max((b for b in live), key=lambda b: b["alive"])
        events.append({"t": target_dur, "type": "winner", "id": winner["id"], "color": winner["color"]})
    return events, {"mode": "battle_royale", "winner_color": winner["color"] if winner else None,
                     "n_balls": n, "duration": target_dur}


# ─────────────────────────────────────────────────────────────────────
# MODE 2 — COLOR INFECTION (team paint-tag, non-elimination)
# ─────────────────────────────────────────────────────────────────────

def simulate_color_infection(target_dur: float, out_path: str, rng: random.Random) -> tuple:
    space = pymunk.Space(); space.gravity = (0, 0)
    center = (BF_W / 2, BF_H * 0.42)
    R = BF_W * 0.44
    n_teams = rng.choice([3, 4, 5])
    per_team = rng.randint(3, 5)
    team_colors = [_bf_vivid_color(i / n_teams) for i in range(n_teams)]
    accent = NICHES["ballfight"]["glow"]

    balls = []
    bid = 0
    for tcol in team_colors:
        for _ in range(per_team):
            r = 30
            body = pymunk.Body(1.0, pymunk.moment_for_circle(1.0, 0, r))
            ang = rng.uniform(0, 2 * math.pi); dist = rng.uniform(0, R * 0.7)
            body.position = (center[0] + math.cos(ang) * dist, center[1] + math.sin(ang) * dist)
            speed = rng.uniform(120, 220); vang = rng.uniform(0, 2 * math.pi)
            body.velocity = (math.cos(vang) * speed, math.sin(vang) * speed)
            shape = pymunk.Circle(body, r); shape.elasticity = 0.95; shape.friction = 0.05
            space.add(body, shape)
            balls.append({"id": bid, "r": r, "color": list(tcol), "body": body, "shape": shape})
            bid += 1

    total = len(balls)
    events = [{"t": 0.0, "type": "start", "teams": n_teams, "n": total}]
    touching = set()
    proc = _bf_ffmpeg_writer(out_path, BF_W, BF_H, BF_FPS)
    bg = _bf_background(BF_W, BF_H, NICHES["ballfight"]["bg"], _hex(accent))
    n_frames = int(target_dur * BF_FPS)
    sudden_death = target_dur - 4.0

    for f in range(n_frames):
        t = f / BF_FPS
        for _ in range(2):
            space.step(1 / (BF_FPS * 2))
        for b in balls:
            p = b["body"].position
            d = math.hypot(p[0] - center[0], p[1] - center[1])
            if d + b["r"] > R:
                nx, ny = (p[0] - center[0]) / max(d, 1e-6), (p[1] - center[1]) / max(d, 1e-6)
                vx, vy = b["body"].velocity
                dot = vx * nx + vy * ny
                if dot > 0:
                    b["body"].velocity = (vx - 2 * dot * nx, vy - 2 * dot * ny)
        for i in range(len(balls)):
            for j in range(i + 1, len(balls)):
                a, b = balls[i], balls[j]
                pa, pb = a["body"].position, b["body"].position
                dist = math.hypot(pa[0] - pb[0], pa[1] - pb[1])
                key = (i, j)
                if dist < a["r"] + b["r"]:
                    if key not in touching and a["color"] != b["color"]:
                        sa, sb = math.hypot(*a["body"].velocity), math.hypot(*b["body"].velocity)
                        w, l = (a, b) if sa >= sb else (b, a)
                        l["color"] = list(w["color"])
                        events.append({"t": round(t, 2), "type": "convert", "color": w["color"]})
                    touching.add(key)
                else:
                    touching.discard(key)
        # sudden-death: force convergence to majority color near the end
        if t >= sudden_death:
            from collections import Counter
            counts = Counter(tuple(b["color"]) for b in balls)
            majority = counts.most_common(1)[0][0]
            for b in balls:
                b["color"] = list(majority)

        img = bg.copy()
        dr = ImageDraw.Draw(img)
        dr.ellipse([center[0] - R - 6, center[1] - R - 6, center[0] + R + 6, center[1] + R + 6],
                   outline=_hex(accent), width=10)
        for b in balls:
            p = b["body"].position; r = b["r"]
            dr.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=tuple(b["color"]),
                       outline=(255, 255, 255), width=2)
        # territory bar
        from collections import Counter
        counts = Counter(tuple(b["color"]) for b in balls)
        bx = 40
        for col, cnt in counts.items():
            bw = (BF_W - 80) * (cnt / total)
            dr.rectangle([bx, 40, bx + bw, 70], fill=col)
            bx += bw
        proc.stdin.write(np.array(img).tobytes())

    proc.stdin.close(); proc.wait()
    from collections import Counter
    final_counts = Counter(tuple(b["color"]) for b in balls)
    win_color = final_counts.most_common(1)[0][0]
    events.append({"t": target_dur, "type": "winner", "color": list(win_color)})
    return events, {"mode": "color_infection", "winner_color": list(win_color),
                     "n_balls": total, "n_teams": n_teams, "duration": target_dur}


# ─────────────────────────────────────────────────────────────────────
# MODE 3 — GRAVITY RACE (pachinko drop race)
# ─────────────────────────────────────────────────────────────────────

def simulate_gravity_race(target_dur: float, out_path: str, rng: random.Random) -> tuple:
    space = pymunk.Space(); space.gravity = (0, 1500)
    margin = 60
    accent = NICHES["ballfight"]["glow"]
    walls = [pymunk.Segment(space.static_body, (margin, 0), (margin, BF_H), 4),
             pymunk.Segment(space.static_body, (BF_W - margin, 0), (BF_W - margin, BF_H), 4)]
    for w_ in walls:
        w_.elasticity = 0.4; w_.friction = 0.3
    space.add(*walls)

    peg_positions = []
    rows = rng.randint(11, 15)
    row_gap = (BF_H * 0.62) / rows
    for row in range(rows):
        y = BF_H * 0.14 + row * row_gap
        offset = 34 if row % 2 == 0 else 0
        col_gap = 78
        col = 0
        while True:
            x = margin + 40 + offset + col * col_gap
            if x > BF_W - margin - 40:
                break
            peg_positions.append((x, y))
            shape = pymunk.Circle(space.static_body, 9, (x, y))
            shape.elasticity = 0.55; shape.friction = 0.15
            space.add(shape)
            col += 1

    finish_y = BF_H * 0.86
    n = rng.randint(6, 10)
    palette = [_bf_vivid_color(i / n) for i in range(n)]
    balls_meta = _bf_gen_balls(n, (26, 26), rng, palette=palette)
    balls = []
    for i, bm in enumerate(balls_meta):
        body = pymunk.Body(1.0, pymunk.moment_for_circle(1.0, 0, bm["r"]))
        body.position = (BF_W / 2 + rng.uniform(-70, 70), -80)
        shape = pymunk.Circle(body, bm["r"]); shape.elasticity = 0.5; shape.friction = 0.2
        space.add(body, shape)
        balls.append({**bm, "body": body, "shape": shape,
                      "dropped": False, "finished": False, "drop_t": i * 0.18})

    events = [{"t": 0.0, "type": "start", "n": n}]
    proc = _bf_ffmpeg_writer(out_path, BF_W, BF_H, BF_FPS)
    bg = _bf_background(BF_W, BF_H, NICHES["ballfight"]["bg"], _hex(accent))
    n_frames = int(target_dur * BF_FPS)
    winner = None
    finish_hold = None

    for f in range(n_frames):
        t = f / BF_FPS
        for b in balls:
            if not b["dropped"] and t >= b["drop_t"]:
                b["dropped"] = True
                b["body"].position = (BF_W / 2 + random.uniform(-70, 70), -40)
        for _ in range(2):
            space.step(1 / (BF_FPS * 2))
        for b in balls:
            if not b["dropped"]:
                continue
            if not b["finished"] and b["body"].position[1] >= finish_y:
                b["finished"] = True
                events.append({"t": round(t, 2), "type": "finish", "id": b["id"], "color": b["color"]})
                if winner is None:
                    winner = b
                    events.append({"t": round(t, 2), "type": "winner", "id": b["id"], "color": b["color"]})
                    finish_hold = t + 2.5

        img = bg.copy()
        dr = ImageDraw.Draw(img)
        for (x, y) in peg_positions:
            dr.ellipse([x - 9, y - 9, x + 9, y + 9], fill=(130, 130, 150))
        dr.line([(margin, finish_y), (BF_W - margin, finish_y)], fill=_hex(accent), width=6)
        _bf_hud_text(img, (BF_W / 2, finish_y - 30), "FINISH", 42, _hex(accent))
        for b in balls:
            if not b["dropped"]:
                continue
            _bf_paste_sprite(img, b["sprite"], b["body"].position, b["body"].angle)
        if winner:
            _bf_hud_text(img, (BF_W / 2, 90), "WINNER!", 78, _hex(accent))
        proc.stdin.write(np.array(img).tobytes())

        if finish_hold and t >= finish_hold:
            break

    proc.stdin.close(); proc.wait()
    return events, {"mode": "gravity_race", "winner_color": winner["color"] if winner else None,
                     "n_balls": n, "duration": (finish_hold or target_dur)}


BF_SIM_FUNCS = {"battle_royale": simulate_battle_royale,
                "color_infection": simulate_color_infection,
                "gravity_race": simulate_gravity_race}


# ─────────────────────────────────────────────────────────────────────
# MUSIC — 4 distinct synthesized moods, one picked at random per video
# (epic is always part of the pool)
# ─────────────────────────────────────────────────────────────────────

def _bf_synth_arcade_chiptune(path: str, dur_s: float, rng: random.Random) -> None:
    sr = 44100; n = int(sr * dur_s); t = np.linspace(0, dur_s, n)
    root = rng.choice([220, 246.94, 261.63, 293.66])
    notes = [root, root * 1.125, root * 1.25, root * 1.5]
    bpm = rng.choice([128, 140, 150])
    beat = 60 / bpm
    wave = np.zeros(n)
    for i in range(int(dur_s / beat)):
        f = notes[i % len(notes)]
        s0, s1 = int(i * beat * sr), int(min(dur_s, (i + 1) * beat) * sr)
        seg_t = t[s0:s1] - t[s0]
        square = np.sign(np.sin(2 * np.pi * f * seg_t))
        env = np.exp(-6 * seg_t)
        wave[s0:s1] += square * env * 0.5
    wave = wave / (np.max(np.abs(wave)) + 1e-9) * 20000
    seg = AudioSegment(wave.astype(np.int16).tobytes(), frame_rate=sr, sample_width=2, channels=1)
    seg.fade_in(200).fade_out(600).export(path, format="mp3")


def _bf_synth_epic_hype(path: str, dur_s: float, rng: random.Random) -> None:
    sr = 44100; n = int(sr * dur_s); t = np.linspace(0, dur_s, n)
    root = rng.choice([65.4, 73.4, 82.4, 87.3, 98.0, 110.0])
    pulse = 0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 2.2 * t))
    wave = (0.35 * np.sin(2 * np.pi * root * t) + 0.2 * np.sin(2 * np.pi * root * 2 * t)) * pulse
    wave += 0.12 * np.sin(2 * np.pi * root * 4 * t)
    swell = np.clip(t / (dur_s * 0.3), 0, 1)
    wave *= (0.5 + 0.5 * swell)
    wave = wave / (np.max(np.abs(wave)) + 1e-9) * 21000
    seg = AudioSegment(wave.astype(np.int16).tobytes(), frame_rate=sr, sample_width=2, channels=1)
    seg.fade_in(400).fade_out(800).export(path, format="mp3")


def _bf_synth_comedy_bounce(path: str, dur_s: float, rng: random.Random) -> None:
    sr = 44100; n = int(sr * dur_s); t = np.linspace(0, dur_s, n)
    root = rng.choice([329.63, 349.23, 392.0])
    seq = [1, 1.2, 1.5, 1.2]
    bpm = rng.choice([110, 120, 130]); beat = 60 / bpm
    wave = np.zeros(n)
    for i in range(int(dur_s / beat)):
        f = root * seq[i % len(seq)]
        s0, s1 = int(i * beat * sr), int(min(dur_s, (i + 1) * beat) * sr)
        seg_t = t[s0:s1] - t[s0]
        tone = np.sin(2 * np.pi * f * seg_t) * np.exp(-8 * seg_t)
        wave[s0:s1] += tone * 0.6
    wave = wave / (np.max(np.abs(wave)) + 1e-9) * 19000
    seg = AudioSegment(wave.astype(np.int16).tobytes(), frame_rate=sr, sample_width=2, channels=1)
    seg.fade_in(150).fade_out(500).export(path, format="mp3")


def _bf_synth_retro_synth(path: str, dur_s: float, rng: random.Random) -> None:
    sr = 44100; n = int(sr * dur_s); t = np.linspace(0, dur_s, n)
    root = rng.choice([146.83, 164.81, 174.61])
    wave = (0.3 * np.sin(2 * np.pi * root * t) + 0.22 * np.sin(2 * np.pi * root * 1.5 * t) +
           0.15 * np.sin(2 * np.pi * root * 2 * t))
    lfo = 0.7 + 0.3 * np.sin(2 * np.pi * 0.5 * t)
    wave *= lfo
    wave = wave / (np.max(np.abs(wave)) + 1e-9) * 20000
    seg = AudioSegment(wave.astype(np.int16).tobytes(), frame_rate=sr, sample_width=2, channels=1)
    seg.fade_in(300).fade_out(700).export(path, format="mp3")


BF_MOODS = {
    "arcade": _bf_synth_arcade_chiptune,
    "epic": _bf_synth_epic_hype,
    "comedy": _bf_synth_comedy_bounce,
    "retro": _bf_synth_retro_synth,
}


def ensure_ballfight_music(dur_s: float, out_path: str, rng: random.Random) -> str:
    mood = rng.choice(list(BF_MOODS.keys()))
    BF_MOODS[mood](out_path, dur_s + 5, rng)
    log.info("Ballfight music: mood='%s'", mood)
    return out_path


def _bf_make_blip_segment(freq: float, dur: float = 0.09) -> AudioSegment:
    """Build a short synth blip directly in memory — no file/ffmpeg round-trip,
    which is both faster and avoids occasional corrupt-file failures when
    spawning many ffmpeg subprocesses back-to-back for dozens of events."""
    sr = 44100
    n = max(1, int(sr * dur))
    t = np.linspace(0, dur, n, endpoint=False)
    env = np.exp(-14 * t)
    wave = (np.sin(2 * np.pi * freq * t) * env * 24000).astype(np.int16)
    return AudioSegment(wave.tobytes(), frame_rate=sr, sample_width=2, channels=1)


def mix_ballfight_audio(events: list, total_dur: float, voice_lines: list,
                        music_path: str, tmp_dir: Path) -> str:
    """Layer synthesized collision/elimination blips + a looped ducked epic
    music bed, then overlay each short commentary burst at its resolved
    timestamp (bursts are sparse — most of the runtime stays music+SFX only,
    not continuously narrated)."""
    bed = AudioSegment.silent(duration=int(total_dur * 1000) + 500)

    music = AudioSegment.from_file(music_path)
    music = (music * (int(len(bed) / len(music)) + 1))[:len(bed)]
    music = music + MUSIC_DUCK_DB
    bed = bed.overlay(music)

    rng = random.Random()
    for ev in events:
        if ev["type"] not in ("collision", "eliminated", "convert", "finish", "winner"):
            continue
        base_freq = {"collision": 520, "eliminated": 220, "convert": 700,
                     "finish": 380, "winner": 300}[ev["type"]]
        freq = base_freq * rng.uniform(0.85, 1.25)
        dur = 0.22 if ev["type"] in ("eliminated", "winner") else 0.09
        blip = _bf_make_blip_segment(freq, dur)
        impact = ev.get("impact", 0.7)
        blip = blip + (6 * impact - 6)  # louder on harder impacts
        pos_ms = int(ev["t"] * 1000)
        if pos_ms < len(bed):
            bed = bed.overlay(blip, position=pos_ms)

    for line in voice_lines:
        voice = AudioSegment.from_file(line["path"])
        # duck the music a bit further under each spoken burst so it's clear
        pos_ms = int(line["start_s"] * 1000)
        if pos_ms < len(bed):
            bed = bed.overlay(voice, position=pos_ms)

    out_path = str(tmp_dir / "ballfight_audio.mp3")
    bed.export(out_path, format="mp3")
    return out_path


# ─────────────────────────────────────────────────────────────────────
# COMMENTARY — a handful of short reaction bursts tied to real events,
# plus one closing line for the winner/CTA end card
# ─────────────────────────────────────────────────────────────────────

def generate_ballfight_commentary(mode: str, events: list, meta: dict, target_dur: float) -> dict:
    client = Groq(api_key=os.environ["GROQ_API_KEY"])

    key_events = [e for e in events if e["type"] in
                  ("start", "eliminated", "convert", "finish", "winner")][:24]
    event_summary = "\n".join(
        f"- t={e['t']}s: {e['type']}" + (f" (ball/color {e.get('color') or e.get('id')})" if e.get('color') or 'id' in e else "")
        for e in key_events
    )
    mode_desc = {
        "battle_royale": "a shrinking-arena ball battle royale — balls get eliminated as the ring closes in, last ball wins",
        "color_infection": "a color-infection game — balls 'infect' each other on contact, one team color takes over the whole arena",
        "gravity_race": "a gravity pachinko drop-race — balls fall through pegs and race to a finish line",
    }[mode]
    winner_name = _bf_color_name(meta.get("winner_color"))

    system = (
        "You are a hyped sports commentator for physics-simulation TikTok videos "
        "(satisfying marble-race / ball-battle content). Return ONLY valid JSON, no markdown: "
        "{title, hashtags: [string], lines: [{t: number, text: string}], winner_line: string}. "
        "Pick 4 to 6 of the most exciting moments from the real event list and write ONE short, "
        "punchy reaction line for each (5-12 words, playful/hyped/a little absurd — like a caster "
        "who is way too invested in some bouncing balls). The 't' value for each line MUST be "
        "copied EXACTLY from one of the given event timestamps — never invent a new number. "
        "These lines are short bursts spoken over an otherwise mostly-instrumental video, NOT "
        "continuous narration — so keep every line short and punchy, not a play-by-play. "
        "Also write 'winner_line': one hyped line (8-15 words) announcing the winner and telling "
        f"viewers to follow and like — it plays over a '{winner_name} WINS' end card, no timestamp. "
        "title: short catchy TikTok title. hashtags: 6-10 tags mixing #ballfight #satisfying "
        "#physics #fyp with a couple specific to the game mode."
    )
    prompt = (
        f"Game mode: {mode_desc}\n"
        f"Real events from this exact simulation, in order:\n{event_summary}\n"
        f"Winner: {winner_name}\n"
        "Comment on only a handful of the punchiest moments, spaced out across the video."
    )

    for attempt in range(4):
        try:
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": prompt}],
                temperature=1.0, max_tokens=800,
            )
            raw = resp.choices[0].message.content.strip().strip("```json").strip("```").strip()
            data = json.loads(raw)
            lines = [l for l in data.get("lines", [])
                     if isinstance(l, dict) and l.get("text") and isinstance(l.get("t"), (int, float))]
            if lines and data.get("winner_line"):
                data["lines"] = lines
                return data
        except Exception as e:
            log.warning("Ballfight commentary attempt %d: %s", attempt + 1, e)
        time.sleep(2 ** attempt)
    # Fallback if Groq fails entirely — still produces a postable video
    mid_t = key_events[len(key_events)//2]["t"] if key_events else target_dur/2
    return {"title": f"Ball {mode.replace('_',' ').title()}!",
            "hashtags": ["ballfight", "satisfying", "physics", "fyp", "foryou"],
            "lines": [{"t": 0.3, "text": "Here we go!"},
                      {"t": mid_t, "text": "This is getting intense!"}],
            "winner_line": f"{winner_name} takes it! Follow for more, and drop a like!"}


# ─────────────────────────────────────────────────────────────────────
# END CARD — "<COLOR> WINS!" + follow/like CTA, held for a few seconds
# ─────────────────────────────────────────────────────────────────────

_BF_COLOR_NAMES = [
    (15, "RED"), (45, "ORANGE"), (65, "YELLOW"), (150, "GREEN"),
    (195, "CYAN"), (250, "BLUE"), (280, "PURPLE"), (325, "PINK"), (360, "RED"),
]

def _bf_color_name(rgb: Optional[tuple]) -> str:
    if not rgb:
        return "EVERYONE"
    r, g, b = (rgb[0]/255, rgb[1]/255, rgb[2]/255)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s < 0.15:
        return "WHITE" if v > 0.7 else "GRAY"
    deg = h * 360
    for hi, name in _BF_COLOR_NAMES:
        if deg < hi:
            return name
    return "RED"


def _bf_fit_font_size(text: str, max_width: int, start_size: int, min_size: int = 44) -> int:
    """Largest font size (down to min_size) at which `text` still fits
    within max_width, so longer color names don't overflow the canvas."""
    size = start_size
    dummy = Image.new("RGB", (1, 1))
    dr = ImageDraw.Draw(dummy)
    while size > min_size:
        font = load_font(size)
        w = dr.textbbox((0, 0), text, font=font)[2]
        if w <= max_width:
            break
        size -= 6
    return size


def _bf_render_outro_card(winner_color: Optional[tuple], dur_s: float,
                          out_mp4_path: str, tmp_dir: Path) -> str:
    accent = tuple(winner_color) if winner_color else _hex(NICHES["ballfight"]["glow"])
    bg = _bf_background(BF_W, BF_H, NICHES["ballfight"]["bg"], accent)
    dr = ImageDraw.Draw(bg)
    cx, cy, r = BF_W / 2, BF_H * 0.36, 150
    dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=accent, outline=(255, 255, 255), width=8)
    name = _bf_color_name(winner_color)
    title_text = "IT'S A DRAW!" if name == "EVERYONE" else f"{name} WINS!"
    title_size = _bf_fit_font_size(title_text, int(BF_W * 0.9), 145)
    _bf_hud_text(bg, (cx, cy + r + 125), title_text, title_size, color=accent)
    _bf_hud_text(bg, (cx, cy + r + 250), "FOLLOW + LIKE FOR MORE", 62, color=(255, 255, 255))
    png_path = str(tmp_dir / "outro.png")
    bg.save(png_path)
    subprocess.run(
        ["ffmpeg", "-y", "-loop", "1", "-i", png_path, "-t", f"{dur_s:.2f}",
         "-r", str(BF_FPS), "-pix_fmt", "yuv420p", "-vf", f"scale={BF_W}:{BF_H}",
         out_mp4_path],
        check=True, capture_output=True,
    )
    return out_mp4_path


def _bf_concat_videos(video_paths: list, out_path: str) -> str:
    args = ["ffmpeg", "-y"]
    for p in video_paths:
        args += ["-i", p]
    n = len(video_paths)
    filt = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[outv]"
    args += ["-filter_complex", filt, "-map", "[outv]",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
              "-pix_fmt", "yuv420p", out_path]
    subprocess.run(args, check=True, capture_output=True)
    return out_path


# ─────────────────────────────────────────────────────────────────────
# ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────

def run_ballfight_pipeline() -> None:
    mode = random.choice(BF_MODES)
    target_dur = random.uniform(24, 36)
    rng = random.Random()
    log.info("═══ VaultMind Ballfight — mode: %s (~%.0fs) ═══", mode, target_dur)

    ensure_font()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # 1. Simulate + render silent physics footage
        raw_video = str(tmp / "raw.mp4")
        events, meta = BF_SIM_FUNCS[mode](target_dur, raw_video, rng)
        real_dur = meta.get("duration", target_dur)
        winner_color = meta.get("winner_color")
        log.info("Sim done: %d events, winner=%s", len(events), winner_color)

        # 2. Short commentary bursts tied to real events + one winner/CTA line
        script    = generate_ballfight_commentary(mode, events, meta, real_dur)
        title     = script.get("title", "Ball Fight!")
        hashtags  = script.get("hashtags", ["ballfight", "satisfying", "fyp"])
        raw_lines = script["lines"]
        winner_text = script["winner_line"]

        # Snap each line's timestamp to the nearest real event (defends
        # against the model drifting off the given numbers) and clamp it
        # inside the sim's actual duration.
        valid_ts = sorted(e["t"] for e in events)
        def _snap(t):
            return min(valid_ts, key=lambda v: abs(v - t)) if valid_ts else 0.0

        # 3. Synth each burst, resolve non-overlapping start times
        voice_lines, cur_end = [], 0.0
        for raw in sorted(raw_lines, key=lambda l: l["t"]):
            t = min(_snap(raw["t"]), max(0.0, real_dur - 0.6))
            start = max(t, cur_end + 0.4) if cur_end else t
            if start >= real_dur - 0.3:
                continue  # ran out of room before the sim ends — drop it
            path = str(tmp / f"line_{len(voice_lines)}.mp3")
            generate_voiceover(raw["text"], path)
            dur = len(AudioSegment.from_file(path)) / 1000
            voice_lines.append({"start_s": start, "path": path})
            cur_end = start + dur

        # 4. Winner line — synth first so we know how long the end card
        #    needs to stay on screen.
        winner_path = str(tmp / "winner_line.mp3")
        generate_voiceover(winner_text, winner_path)
        winner_dur = len(AudioSegment.from_file(winner_path)) / 1000
        outro_dur  = max(2.6, winner_dur + 1.2)
        winner_start = real_dur + 0.3
        voice_lines.append({"start_s": winner_start, "path": winner_path})

        # 5. Build the "<COLOR> WINS!" + follow/like end card and append it
        outro_video = str(tmp / "outro.mp4")
        _bf_render_outro_card(winner_color, outro_dur, outro_video, tmp)
        combined_video = str(tmp / "combined.mp4")
        _bf_concat_videos([raw_video, outro_video], combined_video)
        total_dur = real_dur + outro_dur

        # 6. Always-epic music bed + collision/elimination SFX + the sparse
        #    commentary bursts (incl. winner line), mixed to total_dur.
        music_path = str(tmp / "music.mp3")
        ensure_ballfight_music(total_dur, music_path, rng)
        audio_path = mix_ballfight_audio(events, total_dur, voice_lines, music_path, tmp)

        # 7. Mux video (sim + end card) with the mixed audio bed
        muxed = str(tmp / "muxed.mp4")
        subprocess.run(["ffmpeg", "-y", "-i", combined_video, "-i", audio_path,
                        "-c:v", "copy", "-c:a", "aac", "-shortest", muxed],
                       check=True, capture_output=True)

        # 8. Color grade — no captions burned in anymore
        final_video = apply_color_grade(muxed, str(tmp / "final.mp4"), niche="ballfight")

        # 9. Upload to TikTok
        tiktok_id = upload_tiktok(final_video, title, hashtags)

    log.info("═══ Ballfight complete: %s ═══", tiktok_id or "upload failed/skipped")


if __name__ == "__main__":
    run_ballfight_pipeline()
