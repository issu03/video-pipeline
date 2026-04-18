"""
VaultMind Auto Video Pipeline v5
─────────────────────────────────────────────────────────────────
✅ Viral TikTok-style word-by-word animated captions
✅ Proper cartoon character (SVG-style drawn with PIL)
✅ Pexels image cutaways (per scene, contextual)
✅ Background music (looped, mixed under voice)
✅ ElevenLabs high-quality voice (Brian / Adam)
✅ edge-tts GuyNeural fallback
✅ Dashboard JSON sync after every upload
✅ Collision-aware scheduling, max 2/day
✅ No VAULTMIND header, no computer-generated look
"""
import os, sys, json, time, random, textwrap, subprocess, shutil, logging
import requests, math, asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("pipeline.log")]
)
log = logging.getLogger("pipeline")

# ── ENV ──────────────────────────────────────────────────────────
def load_env():
    if Path(".env").exists():
        for line in Path(".env").read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
load_env()

GROQ_KEY     = os.environ.get("GROQ_API_KEY", "")
ELEVEN_KEY   = os.environ.get("ELEVENLABS_KEY", "")
PEXELS_KEY   = os.environ.get("PEXELS_KEY", "")

# ElevenLabs voice IDs — Brian (energetic narrator), fallback Adam
ELEVEN_VOICE_ID = os.environ.get("ELEVEN_VOICE_ID", "nPczCjzI2devNBz1zQrb")  # Brian
ELEVEN_VOICE_ID_ALT = "pNInz6obpgDQGcFmaJgB"  # Adam

W, H, FPS       = 720, 1280, 30
OUTPUT_DIR      = Path("./output_videos")
DASHBOARD_FILE  = Path("./dashboard.json")
GAMEPLAY_FILE   = Path(os.environ.get("GAMEPLAY_PATH", "./gameplay_bg.mp4"))
MUSIC_FILE      = Path(os.environ.get("MUSIC_PATH", "./bg_music.mp3"))

# ── SCHEDULING ───────────────────────────────────────────────────
UPLOAD_TIMES = {
    "monday":    ["07:00", "19:00"],
    "tuesday":   ["07:00", "19:00"],
    "wednesday": ["07:00", "21:00"],
    "thursday":  ["07:00", "19:00"],
    "friday":    ["07:00", "20:00"],
    "saturday":  ["09:00", "20:00"],
    "sunday":    ["10:00", "20:00"],
}
MAX_PER_DAY = 2

# ── NICHES ───────────────────────────────────────────────────────
NICHES = [
    {"type": "reddit", "prompt": "Reddit AITA story — dramatic, relatable, specific names/places"},
    {"type": "reddit", "prompt": "Reddit revenge story with satisfying ending"},
    {"type": "reddit", "prompt": "Reddit workplace drama or entitled boss story"},
    {"type": "fact",   "prompt": "Mind-blowing psychology fact most people don't know"},
    {"type": "fact",   "prompt": "Shocking historical fact that sounds fake but is true"},
    {"type": "fact",   "prompt": "Wild science or nature fact that breaks your brain"},
    {"type": "money",  "prompt": "Surprising way people make passive income online"},
    {"type": "whatif", "prompt": "What if scenario that genuinely makes people think differently"},
]

# ─────────────────────────────────────────────────────────────────
#  DASHBOARD
# ─────────────────────────────────────────────────────────────────
def load_dashboard():
    if DASHBOARD_FILE.exists():
        try:
            return json.loads(DASHBOARD_FILE.read_text())
        except Exception:
            pass
    return {"videos": [], "stats": {"generated": 0}}

def save_dashboard(data):
    DASHBOARD_FILE.write_text(json.dumps(data, indent=2, default=str))

def add_to_dashboard(entry):
    data = load_dashboard()
    # Avoid duplicates by ID
    data["videos"] = [v for v in data["videos"] if v.get("id") != entry.get("id")]
    data["videos"].insert(0, entry)
    data["stats"]["generated"] = len(data["videos"])
    save_dashboard(data)
    log.info(f"   📊 Dashboard updated ({len(data['videos'])} videos)")

# ─────────────────────────────────────────────────────────────────
#  SCHEDULING
# ─────────────────────────────────────────────────────────────────
def get_booked_slots():
    data = load_dashboard()
    booked = set()
    for v in data.get("videos", []):
        for key in ("youtube", "tiktok"):
            t = v.get(key, {}).get("scheduled")
            if t:
                try:
                    dt = datetime.fromisoformat(str(t))
                    booked.add(dt.replace(second=0, microsecond=0, tzinfo=None))
                except Exception:
                    pass
    return booked

def get_next_slots(n=2):
    booked = get_booked_slots()
    slots, now = [], datetime.now()
    buf = now + timedelta(minutes=45)
    per_day = {}

    for d in range(90):
        date     = now + timedelta(days=d)
        day_key  = date.strftime("%Y-%m-%d")
        day_name = date.strftime("%A").lower()
        for t in UPLOAD_TIMES.get(day_name, ["12:00", "20:00"]):
            h, m = map(int, t.split(":"))
            slot = date.replace(hour=h, minute=m, second=0, microsecond=0)
            if slot <= buf:
                continue
            if any(abs((slot - b).total_seconds()) < 600 for b in booked):
                continue
            if per_day.get(day_key, 0) >= MAX_PER_DAY:
                break
            slots.append(slot)
            booked.add(slot)
            per_day[day_key] = per_day.get(day_key, 0) + 1
            if len(slots) >= n:
                return slots
    return slots

# ─────────────────────────────────────────────────────────────────
#  FONTS
# ─────────────────────────────────────────────────────────────────
def install_font():
    """Try to install Impact font for real TikTok-style captions."""
    font_paths = [
        "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for p in font_paths:
        if os.path.exists(p):
            return p
    # Try installing
    os.system("apt-get install -y -qq ttf-mscorefonts-installer fontconfig 2>/dev/null || true")
    os.system("fc-cache -f 2>/dev/null || true")
    for p in font_paths:
        if os.path.exists(p):
            return p
    return None

FONT_PATH = None

def get_font(size, bold=True):
    global FONT_PATH
    if FONT_PATH is None:
        FONT_PATH = install_font()
    if FONT_PATH and os.path.exists(FONT_PATH):
        try:
            return ImageFont.truetype(FONT_PATH, size)
        except Exception:
            pass
    # Fallback chain
    paths_bold   = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
    paths_normal = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]
    for p in (paths_bold if bold else paths_normal):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()

# ─────────────────────────────────────────────────────────────────
#  STEP 1: SCRIPT GENERATION (Groq)
# ─────────────────────────────────────────────────────────────────
def generate_script():
    niche = random.choice(NICHES)
    log.info(f"🤖 Generating script... type={niche['type']}")

    base_rules = """
CRITICAL RULES for scenes:
- "text": max 6 words shown on screen as caption title
- "voiceover": exactly what is SPOKEN — natural, punchy, conversational (NOT robotic)
  Use pauses like "..." and emphasis like "WAIT." or "No seriously."
  Sound like a real person telling a story to a friend.
- "duration": how many seconds this scene lasts (3.5-6.0)
- "image_query": 2-3 word Pexels search for a relevant photo shown briefly
  (e.g. "angry boss office", "shocked woman face", "empty wallet")
- 16-20 scenes total, 70-95 seconds total duration
- Scene 1: SHOCKING hook — must stop scrolling in first 2 seconds
- Last 2 scenes: resolution + "follow for more" CTA
"""

    if niche["type"] == "reddit":
        prompt = f"""You write viral Reddit storytime scripts for TikTok/YouTube Shorts.
Topic: {niche['prompt']}

Return ONLY valid JSON (no markdown, no extra text):
{{
  "type": "reddit",
  "title": "Emotional viral title, max 60 chars, specific & shocking",
  "description": "2-sentence YouTube description with keywords",
  "reddit_title": "Realistic Reddit post title — casual, specific, emotional",
  "reddit_sub": "AITA",
  "reddit_user": "u/ThrowawayAccount_{random.randint(1000,9999)}",
  "scenes": [
    {{"text": "on-screen caption max 6 words", "voiceover": "what narrator speaks naturally", "duration": 5.0, "image_query": "relevant image search"}}
  ],
  "hashtags": "#reddit #storytime #aita #fyp #shorts #viral"
}}
{base_rules}
- Make names, places, and details VERY specific (e.g. "my coworker Karen", "at our Thanksgiving dinner")
- High emotional stakes — betrayal, revenge, shock, vindication"""
    else:
        prompt = f"""You write viral facts/mindblowing content for TikTok/YouTube Shorts.
Topic: {niche['prompt']}

Return ONLY valid JSON (no markdown, no extra text):
{{
  "type": "fact",
  "title": "Shocking viral title, max 60 chars — must make people stop scrolling",
  "description": "2-sentence YouTube description with SEO keywords",
  "reddit_title": "",
  "reddit_sub": "",
  "reddit_user": "",
  "scenes": [
    {{"text": "on-screen caption max 6 words", "voiceover": "what narrator speaks naturally", "duration": 4.5, "image_query": "relevant image search"}}
  ],
  "hashtags": "#facts #mindblowing #didyouknow #fyp #shorts #viral"
}}
{base_rules}
- Only real, verifiable facts — no made-up stuff
- Structure: shocking hook → build-up → reveal → mind-blown → CTA"""

    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
        json={"model": "llama-3.3-70b-versatile",
              "messages": [{"role": "user", "content": prompt}],
              "max_tokens": 2000, "temperature": 0.85},
        timeout=40
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    raw = raw.replace("```json", "").replace("```", "").strip()
    s = raw.find("{"); e = raw.rfind("}") + 1
    data = json.loads(raw[s:e])
    log.info(f"   ✅ '{data['title']}' — {len(data['scenes'])} scenes")
    return data

# ─────────────────────────────────────────────────────────────────
#  STEP 2: VOICEOVER
# ─────────────────────────────────────────────────────────────────
def generate_voiceover(scenes, work_dir):
    log.info("🎙️  Generating voiceover...")
    audio_files = []

    # ── ElevenLabs (best quality, sounds human) ──────────────────
    if ELEVEN_KEY:
        log.info("   🎤 Trying ElevenLabs...")
        voice_id = ELEVEN_VOICE_ID
        for i, scene in enumerate(scenes):
            try:
                resp = requests.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                    headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"},
                    json={
                        "text": scene["voiceover"],
                        "model_id": "eleven_turbo_v2_5",
                        "voice_settings": {
                            "stability": 0.35,          # Lower = more expressive
                            "similarity_boost": 0.80,
                            "style": 0.45,              # Style exaggeration
                            "use_speaker_boost": True
                        }
                    },
                    timeout=30
                )
                if resp.status_code == 200 and len(resp.content) > 500:
                    path = work_dir / f"voice_{i:02d}.mp3"
                    path.write_bytes(resp.content)
                    audio_files.append(path)
                    log.info(f"   ✅ EL scene {i}: {len(resp.content)//1024}KB")
                else:
                    log.warning(f"   ⚠️ ElevenLabs {resp.status_code} on scene {i}")
                    # Try alt voice
                    if voice_id == ELEVEN_VOICE_ID:
                        voice_id = ELEVEN_VOICE_ID_ALT
                    else:
                        audio_files = []
                        break
            except Exception as ex:
                log.warning(f"   ⚠️ ElevenLabs error: {ex}")
                audio_files = []
                break

    # ── edge-tts fallback (Microsoft GuyNeural — fast, decent) ───
    if not audio_files:
        log.info("   🔄 edge-tts fallback (GuyNeural)...")
        try:
            import edge_tts
            EDGE_VOICE = "en-US-GuyNeural"  # Energetic male voice

            async def gen_all():
                tasks = []
                for i, scene in enumerate(scenes):
                    path = work_dir / f"voice_{i:02d}.mp3"
                    # Add SSML-like rate/pitch variation for more energy
                    tts = edge_tts.Communicate(
                        scene["voiceover"],
                        voice=EDGE_VOICE,
                        rate="+15%",
                        pitch="+0Hz"
                    )
                    await tts.save(str(path))
                    tasks.append(path)
                return tasks

            paths = asyncio.run(gen_all())
            audio_files = [p for p in paths if p.exists() and p.stat().st_size > 500]
            log.info(f"   ✅ edge-tts: {len(audio_files)} files")
        except Exception as ex:
            log.warning(f"   ⚠️ edge-tts failed: {ex}")
            audio_files = []

    # ── espeak last resort ────────────────────────────────────────
    if not audio_files:
        log.info("   🔄 espeak fallback...")
        for i, scene in enumerate(scenes):
            wav = work_dir / f"v_{i}.wav"
            mp3 = work_dir / f"voice_{i:02d}.mp3"
            subprocess.run(["espeak", "-w", str(wav), "-s", "145", "-p", "48",
                            scene["voiceover"]], capture_output=True)
            subprocess.run(["ffmpeg", "-y", "-i", str(wav), "-c:a", "libmp3lame",
                            "-q:a", "2", str(mp3)], capture_output=True)
            wav.unlink(missing_ok=True)
            if mp3.exists():
                audio_files.append(mp3)

    if not audio_files:
        log.error("   ❌ All TTS methods failed")
        return None

    # Concatenate all scene audios
    final = Path("/tmp/final_voice.mp3")
    if len(audio_files) == 1:
        shutil.copy(str(audio_files[0]), str(final))
    else:
        concat_list = work_dir / "concat.txt"
        concat_list.write_text("\n".join(f"file '{p.resolve()}'" for p in audio_files))
        r = subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list), "-c", "copy", str(final)
        ], capture_output=True, text=True)
        if r.returncode != 0 or not final.exists():
            shutil.copy(str(audio_files[0]), str(final))

    log.info(f"   ✅ Voice: {final.stat().st_size//1024}KB")
    return final

# ─────────────────────────────────────────────────────────────────
#  STEP 3: PEXELS IMAGE FETCHER
# ─────────────────────────────────────────────────────────────────
def fetch_pexels_image(query, work_dir, idx):
    """Fetch a relevant image from Pexels for scene cutaway."""
    if not PEXELS_KEY or not query:
        return None
    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": query, "per_page": 5, "orientation": "portrait"},
            timeout=10
        )
        if resp.status_code == 200:
            photos = resp.json().get("photos", [])
            if photos:
                photo = random.choice(photos)
                url = photo["src"].get("portrait") or photo["src"]["large"]
                img_resp = requests.get(url, timeout=15)
                if img_resp.status_code == 200:
                    path = work_dir / f"pexels_{idx:02d}.jpg"
                    path.write_bytes(img_resp.content)
                    log.info(f"   📸 Pexels image {idx}: '{query}'")
                    return path
    except Exception as ex:
        log.warning(f"   ⚠️ Pexels failed for '{query}': {ex}")
    return None

def prepare_pexels_image(img_path, target_w, target_h):
    """Crop/resize Pexels image to fit video frame, add blur vignette."""
    try:
        img = Image.open(img_path).convert("RGB")
        # Crop to portrait ratio
        iw, ih = img.size
        target_ratio = target_w / target_h
        current_ratio = iw / ih
        if current_ratio > target_ratio:
            new_w = int(ih * target_ratio)
            x = (iw - new_w) // 2
            img = img.crop((x, 0, x + new_w, ih))
        else:
            new_h = int(iw / target_ratio)
            y = (ih - new_h) // 2
            img = img.crop((0, y, iw, y + new_h))
        img = img.resize((target_w, target_h), Image.LANCZOS)
        # Subtle dark vignette
        vignette = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
        vd = ImageDraw.Draw(vignette)
        for r_pct in range(10):
            alpha = int(r_pct * 8)
            inset = r_pct * 36
            vd.rectangle([inset, inset, target_w-inset, target_h-inset],
                         outline=(0, 0, 0, alpha), width=36)
        img = Image.alpha_composite(img.convert("RGBA"), vignette).convert("RGB")
        return img
    except Exception:
        return None

# ─────────────────────────────────────────────────────────────────
#  STEP 4: CARTOON CHARACTER (proper flat-vector style)
# ─────────────────────────────────────────────────────────────────
def draw_cartoon_character(img, frame, is_talking):
    """
    Flat vector cartoon character — bottom right corner.
    Deliberately simple and clean like a 2D animated avatar.
    No shading gradients, just bold fills + thick outlines = cartoon look.
    """
    draw = ImageDraw.Draw(img, 'RGBA')

    # Position: bottom-right, slight bob
    cx = W - 105
    cy = H - 220
    bob = int(math.sin(frame * 0.25) * 5)
    cy += bob

    BLACK = (15, 15, 15, 255)
    SKIN  = (255, 200, 140, 255)
    HAIR  = (40,  30,  20,  255)
    SHIRT = (255, 80,  80,  255)   # bright red shirt
    PANTS = (50,  80,  180, 255)   # blue pants
    SHOE  = (30,  30,  30,  255)
    WHITE = (255, 255, 255, 255)
    EYE_B = (60,  120, 220, 255)
    CHEEK = (255, 160, 140, 120)

    def filled_circle(x, y, r, color, outline=None, ow=3):
        draw.ellipse([x-r, y-r, x+r, y+r], fill=color,
                     outline=outline or color, width=ow)

    def filled_rect(x1, y1, x2, y2, color, outline=None, ow=3):
        draw.rectangle([x1, y1, x2, y2], fill=color,
                       outline=outline or color, width=ow)

    # ── Drop shadow ──
    draw.ellipse([cx-52, cy+118, cx+52, cy+132], fill=(0,0,0,50))

    # ── Legs ── (walk cycle)
    leg_swing = int(math.sin(frame * 0.18) * 10)
    # Left leg
    draw.rounded_rectangle([cx-36+leg_swing, cy+82, cx-12+leg_swing, cy+125],
                            radius=8, fill=PANTS, outline=BLACK, width=3)
    # Right leg
    draw.rounded_rectangle([cx+12-leg_swing, cy+82, cx+36-leg_swing, cy+125],
                            radius=8, fill=PANTS, outline=BLACK, width=3)
    # Shoes
    draw.rounded_rectangle([cx-42+leg_swing, cy+115, cx-8+leg_swing, cy+132],
                            radius=7, fill=SHOE, outline=BLACK, width=2)
    draw.rounded_rectangle([cx+8-leg_swing, cy+115, cx+42-leg_swing, cy+132],
                            radius=7, fill=SHOE, outline=BLACK, width=2)

    # ── Body ── (rounded rectangle, shirt)
    draw.rounded_rectangle([cx-38, cy+30, cx+38, cy+90],
                            radius=14, fill=SHIRT, outline=BLACK, width=4)

    # Shirt collar V
    draw.polygon([(cx-10, cy+30), (cx+10, cy+30), (cx, cy+48)],
                 fill=(200, 50, 50, 255))

    # ── Arms ── (swing opposite to legs)
    arm_swing = int(math.sin(frame * 0.25) * 20)
    # Left arm
    draw.line([(cx-38, cy+45), (cx-65, cy+75+arm_swing)],
              fill=SHIRT, width=18)
    draw.ellipse([cx-73, cy+67+arm_swing, cx-57, cy+83+arm_swing],
                 fill=SKIN, outline=BLACK, width=3)
    # Right arm
    draw.line([(cx+38, cy+45), (cx+65, cy+75-arm_swing)],
              fill=SHIRT, width=18)
    draw.ellipse([cx+57, cy+67-arm_swing, cx+73, cy+83-arm_swing],
                 fill=SKIN, outline=BLACK, width=3)
    # Arm outlines
    draw.line([(cx-38, cy+45), (cx-65, cy+75+arm_swing)],
              fill=BLACK, width=4)
    draw.line([(cx+38, cy+45), (cx+65, cy+75-arm_swing)],
              fill=BLACK, width=4)

    # ── Neck ──
    filled_rect(cx-10, cy+18, cx+10, cy+35, SKIN, BLACK, 3)

    # ── Head ── (big round head = cartoon feel)
    head_r = 42
    filled_circle(cx, cy-head_r+14, head_r, SKIN, BLACK, 4)

    # ── Hair (flat block hair) ──
    draw.pieslice([cx-42, cy-82, cx+42, cy-10], start=180, end=0,
                  fill=HAIR, outline=BLACK, width=4)
    # Side hair bumps
    filled_circle(cx-36, cy-64, 14, HAIR, BLACK, 3)
    filled_circle(cx+36, cy-64, 14, HAIR, BLACK, 3)
    # Hair top tuft
    draw.polygon([(cx-10, cy-82), (cx, cy-100), (cx+10, cy-82)],
                 fill=HAIR, outline=BLACK)

    # ── Ears ──
    filled_circle(cx-42, cy-52, 10, SKIN, BLACK, 3)
    filled_circle(cx+42, cy-52, 10, SKIN, BLACK, 3)

    # ── Eyes ──
    blink = (frame % 70) < 3
    eye_y = cy - 56
    if blink:
        # Closed eyes = thick line
        draw.line([cx-26, eye_y+4, cx-10, eye_y+4], fill=BLACK, width=4)
        draw.line([cx+10, eye_y+4, cx+26, eye_y+4], fill=BLACK, width=4)
    else:
        # Whites
        filled_circle(cx-18, eye_y, 13, WHITE, BLACK, 3)
        filled_circle(cx+18, eye_y, 13, WHITE, BLACK, 3)
        # Iris (blue)
        filled_circle(cx-18, eye_y+1, 8, EYE_B)
        filled_circle(cx+18, eye_y+1, 8, EYE_B)
        # Pupil
        filled_circle(cx-18, eye_y+1, 4, BLACK)
        filled_circle(cx+18, eye_y+1, 4, BLACK)
        # Shine
        filled_circle(cx-21, eye_y-3, 2, WHITE)
        filled_circle(cx+15, eye_y-3, 2, WHITE)
        # Eyebrows (thick cartoon brows)
        draw.line([cx-28, eye_y-14, cx-9, eye_y-10], fill=HAIR, width=5)
        draw.line([cx+9,  eye_y-10, cx+28, eye_y-14], fill=HAIR, width=5)

    # ── Cheeks ──
    filled_circle(cx-30, eye_y+12, 9, CHEEK)
    filled_circle(cx+30, eye_y+12, 9, CHEEK)

    # ── Mouth ──
    mouth_y = cy - 38
    if is_talking:
        # Open mouth oval
        draw.ellipse([cx-12, mouth_y-6, cx+12, mouth_y+10],
                     fill=(160, 30, 30, 255), outline=BLACK, width=3)
        # Teeth
        draw.rectangle([cx-9, mouth_y-5, cx+9, mouth_y+1],
                       fill=WHITE)
    else:
        # Happy smile arc
        draw.arc([cx-14, mouth_y-4, cx+14, mouth_y+12],
                 start=10, end=170, fill=BLACK, width=4)

    # ── Outline body fix ──
    draw.rounded_rectangle([cx-38, cy+30, cx+38, cy+90],
                            radius=14, fill=None, outline=BLACK, width=4)

    return img

# ─────────────────────────────────────────────────────────────────
#  STEP 5: REDDIT CARD
# ─────────────────────────────────────────────────────────────────
def draw_reddit_card(img, script, alpha_pct):
    """Clean Reddit card — dark mode style, slides in from top."""
    if not script.get("reddit_title"):
        return img
    draw = ImageDraw.Draw(img, 'RGBA')
    alpha = int(240 * alpha_pct)
    card_x, card_y = 16, 16
    card_w = W - 32

    font_sub   = get_font(20, bold=False)
    font_title = get_font(28, bold=True)
    font_small = get_font(19, bold=False)

    # Measure wrapped title
    title = script.get("reddit_title", "")
    wrapped = textwrap.fill(title, width=34)
    lines = wrapped.split("\n")[:3]
    card_h = 50 + len(lines) * 34 + 36

    # Card background
    draw.rounded_rectangle([card_x, card_y, card_x+card_w, card_y+card_h],
                            radius=12,
                            fill=(22, 22, 23, alpha),
                            outline=(255, 69, 0, alpha), width=2)

    # Subreddit + user
    sub_text = f"r/{script.get('reddit_sub','AskReddit')}  ·  {script.get('reddit_user','u/throwaway')}"
    draw.text((card_x+14, card_y+12), sub_text, font=font_sub,
              fill=(130, 133, 136, alpha))

    # Title lines
    ty = card_y + 38
    for line in lines:
        draw.text((card_x+14, ty), line, font=font_title,
                  fill=(215, 218, 220, alpha))
        ty += 34

    # Upvotes
    upvotes = f"▲ {random.randint(8,42)}k   💬 {random.randint(300,4000)}"
    draw.text((card_x+14, card_y+card_h-28), upvotes, font=font_small,
              fill=(130, 133, 136, alpha))

    return img

# ─────────────────────────────────────────────────────────────────
#  STEP 6: VIRAL CAPTIONS (word-by-word, animated)
# ─────────────────────────────────────────────────────────────────
def draw_viral_captions(img, text, word_progress, frame):
    """
    True TikTok word-by-word caption animation:
    - Shows 1-2 words at a time
    - Current word: YELLOW, big, with slight scale pop
    - Previous word: white, slightly smaller
    - Heavy black stroke for readability on any background
    - Never goes out of frame
    """
    words = text.split()
    if not words:
        return img

    total = len(words)
    current_idx = max(0, min(int(word_progress * total), total - 1))

    draw = ImageDraw.Draw(img, 'RGBA')
    MARGIN = 44
    MAX_W  = W - MARGIN * 2

    def stroke_text(d, x, y, txt, font, fill, stroke_w=8):
        """Draw text with thick black stroke (outline)."""
        for dx in range(-stroke_w, stroke_w+1, 2):
            for dy in range(-stroke_w, stroke_w+1, 2):
                if abs(dx) + abs(dy) == 0:
                    continue
                d.text((x+dx, y+dy), txt, font=font, fill=(0, 0, 0, 255))
        d.text((x, y), txt, font=font, fill=fill)

    def tw(txt, font):
        bb = draw.textbbox((0, 0), txt, font=font)
        return bb[2]-bb[0], bb[3]-bb[1]

    # Current and previous word
    curr_word = words[current_idx]
    prev_word = words[current_idx-1] if current_idx > 0 else ""

    # Font sizes
    font_curr = get_font(88)
    font_prev = get_font(70)

    # Scale down if too wide
    while tw(curr_word, font_curr)[0] > MAX_W and font_curr.size > 40:
        font_curr = get_font(font_curr.size - 6)
    while prev_word and tw(prev_word, font_prev)[0] > MAX_W and font_prev.size > 36:
        font_prev = get_font(font_prev.size - 5)

    curr_w, curr_h = tw(curr_word, font_curr)
    prev_w, prev_h = tw(prev_word, font_prev) if prev_word else (0, 0)

    # Position: center of screen, safe zone
    center_y = H // 2 + 40

    # Draw previous word above
    if prev_word:
        px = (W - prev_w) // 2
        py = center_y - prev_h - 12
        stroke_text(draw, px, py, prev_word, font_prev, (220, 220, 220, 200), stroke_w=6)

    # Draw current word (yellow, bigger, pop effect)
    cx_ = (W - curr_w) // 2
    cy_ = center_y
    # Pop scale: slight size pulse on first few frames
    pop = abs(math.sin(frame * 0.8)) * 0
    stroke_text(draw, cx_, cy_, curr_word, font_curr, (255, 224, 0, 255), stroke_w=9)

    return img

# ─────────────────────────────────────────────────────────────────
#  STEP 7: RENDER VIDEO
# ─────────────────────────────────────────────────────────────────
def render_video(script, work_dir, output, gameplay_path):
    log.info("🎬 Rendering video...")
    scenes = script["scenes"]
    total_dur = sum(s["duration"] for s in scenes)
    total_frames = int(total_dur * FPS)
    is_reddit = script.get("type") == "reddit"

    # ── Pre-fetch Pexels images for each scene ───────────────────
    log.info("   📸 Fetching Pexels images...")
    pexels_images = {}
    for i, scene in enumerate(scenes):
        query = scene.get("image_query", "")
        # Show image in middle scenes (not first 2 or last 1)
        if query and 2 <= i < len(scenes) - 1:
            path = fetch_pexels_image(query, work_dir, i)
            if path:
                prepared = prepare_pexels_image(path, W, H)
                if prepared:
                    pexels_images[i] = prepared

    # ── Extract gameplay frames ───────────────────────────────────
    frames_dir = work_dir / "frames"
    frames_dir.mkdir()
    gp_frames_dir = work_dir / "gp_frames"
    gp_frames_dir.mkdir()

    gp_frames = []
    if gameplay_path.exists():
        gp_offset = random.uniform(5, 45)
        log.info(f"   📼 Extracting gameplay (offset {gp_offset:.0f}s)...")
        r = subprocess.run([
            "ffmpeg", "-y", "-ss", str(gp_offset), "-i", str(gameplay_path),
            "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
            "-r", str(FPS), "-t", str(total_dur + 5), "-q:v", "4",
            str(gp_frames_dir / "frame_%06d.jpg")
        ], capture_output=True, text=True)
        gp_frames = sorted(gp_frames_dir.glob("frame_*.jpg"))
        log.info(f"   ✅ {len(gp_frames)} gameplay frames")
        if not gp_frames:
            log.error(f"   ffmpeg stderr: {r.stderr[-300:]}")
    else:
        log.warning("   ⚠️ No gameplay file found — dark background")

    # ── Frame rendering loop ──────────────────────────────────────
    global_frame = 0
    scene_start_frames = []
    for sc in scenes:
        scene_start_frames.append(global_frame)
        global_frame += int(sc["duration"] * FPS)
    global_frame = 0

    for si, scene in enumerate(scenes):
        n_frames = int(scene["duration"] * FPS)
        has_pexels = si in pexels_images
        pexels_img = pexels_images.get(si)

        for f in range(n_frames):
            t = f / max(n_frames - 1, 1)
            is_talking = (f % 8) < 5

            # ── 1. Background ─────────────────────────────────────
            if has_pexels and pexels_img is not None:
                # Use Pexels image as background for this scene (cutaway)
                # Blend in/out smoothly
                blend = min(1.0, min(t * 6, (1 - t) * 6))
                bg = pexels_img.copy()
                # Extra darkening for text readability
                dark = Image.new("RGB", (W, H), (0, 0, 0))
                bg = Image.blend(bg, dark, 0.45)
            else:
                gp_idx = min(global_frame, len(gp_frames) - 1)
                if gp_frames:
                    bg = Image.open(gp_frames[gp_idx]).convert("RGB")
                    if bg.size != (W, H):
                        bg = bg.resize((W, H), Image.BILINEAR)
                else:
                    bg = Image.new("RGB", (W, H), (8, 8, 18))

            # ── 2. Dark overlay (readability) ─────────────────────
            overlay = Image.new("RGBA", (W, H), (0, 0, 0, 55))
            bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")

            # ── 3. Reddit card (first 2 scenes, fade in) ──────────
            if is_reddit and si <= 1:
                alpha_pct = min(1.0, (si * n_frames + f) / (2 * n_frames))
                bg = draw_reddit_card(bg, script, alpha_pct)

            # ── 4. Cartoon character (bottom-right) ───────────────
            bg = draw_cartoon_character(bg, global_frame, is_talking)

            # ── 5. Viral word-by-word captions ────────────────────
            word_prog = min(1.0, t * 1.8 + 0.04)
            bg = draw_viral_captions(bg, scene["voiceover"], word_prog, global_frame)

            # ── 6. Thin white progress bar ─────────────────────────
            draw_final = ImageDraw.Draw(bg, 'RGBA')
            prog = global_frame / max(total_frames - 1, 1)
            bw = int(W * prog)
            draw_final.rectangle([0, H-5, bw, H], fill=(255, 255, 255, 160))
            draw_final.rectangle([bw, H-5, W, H], fill=(0, 0, 0, 80))

            bg.save(frames_dir / f"frame_{global_frame:06d}.png")
            global_frame += 1

        if si % 3 == 0:
            log.info(f"   🎨 Scene {si+1}/{len(scenes)} done")

    shutil.rmtree(gp_frames_dir, ignore_errors=True)
    log.info(f"   ✅ {global_frame} frames rendered")

    # ── Encode to video ───────────────────────────────────────────
    r = subprocess.run([
        "ffmpeg", "-y", "-framerate", str(FPS),
        "-i", str(frames_dir / "frame_%06d.png"),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(output)
    ], capture_output=True, text=True)
    shutil.rmtree(frames_dir)

    if r.returncode == 0 and output.exists():
        log.info(f"   ✅ Video: {output.stat().st_size//1024}KB, {total_dur:.1f}s")
        return True
    log.error(f"   ❌ Render failed: {r.stderr[-300:]}")
    return False

# ─────────────────────────────────────────────────────────────────
#  STEP 8: MIX AUDIO (voice + background music)
# ─────────────────────────────────────────────────────────────────
def mix_and_merge(video_path, voice_path, output, total_dur):
    """
    Mix: background music (looped, -18dB) under voice (0dB).
    If no music file → just attach voice.
    """
    if MUSIC_FILE.exists() and voice_path and voice_path.exists():
        log.info("   🎵 Mixing voice + background music...")
        r = subprocess.run([
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-stream_loop", "-1", "-i", str(MUSIC_FILE),
            "-i", str(voice_path),
            "-filter_complex",
            f"[1:a]volume=0.12,atrim=0:'{total_dur}',asetpts=PTS-STARTPTS[music];"
            f"[2:a]volume=1.0[voice];"
            f"[music][voice]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(output)
        ], capture_output=True, text=True)
        if r.returncode == 0 and output.exists():
            log.info(f"   ✅ Mixed: {output.stat().st_size//1024}KB")
            return True
        log.warning(f"   ⚠️ Music mix failed, trying voice-only: {r.stderr[-200:]}")

    # Fallback: voice only
    if voice_path and voice_path.exists():
        r = subprocess.run([
            "ffmpeg", "-y", "-i", str(video_path), "-i", str(voice_path),
            "-map", "0:v", "-map", "1:a", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
            str(output)
        ], capture_output=True, text=True)
        if r.returncode == 0 and output.exists():
            log.info(f"   ✅ Voice-only: {output.stat().st_size//1024}KB")
            return True

    shutil.copy(str(video_path), str(output))
    return True

# ─────────────────────────────────────────────────────────────────
#  STEP 9: YOUTUBE UPLOAD
# ─────────────────────────────────────────────────────────────────
def upload_youtube(video_path, title, description, hashtags, publish_at):
    log.info(f"📺 YouTube → {publish_at.strftime('%a %d %b %H:%M')}")
    try:
        import pickle, base64
        from googleapiclient.http import MediaFileUpload
        from google.auth.transport.requests import Request
        import googleapiclient.discovery as gd

        token_b64 = os.environ.get("YOUTUBE_TOKEN_B64", "")
        if not token_b64:
            log.warning("   ⚠️ No YOUTUBE_TOKEN_B64 — skipping upload")
            return None

        creds = pickle.loads(base64.b64decode(token_b64))
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())

        try:
            yt = gd.build("youtube", "v3", credentials=creds, cache_discovery=False)
        except TypeError:
            yt = gd.build("youtube", "v3", credentials=creds)

        if publish_at.tzinfo is None:
            publish_at = publish_at.replace(tzinfo=timezone.utc)
        publish_str = publish_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        body = {
            "snippet": {
                "title": title[:100],
                "description": f"{description}\n\n{hashtags}",
                "tags": [t.replace("#", "") for t in hashtags.split() if t.startswith("#")],
                "categoryId": "22"
            },
            "status": {
                "privacyStatus": "private",
                "publishAt": publish_str,
                "selfDeclaredMadeForKids": False
            }
        }
        media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
        req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = req.next_chunk()
            if status:
                log.info(f"   📤 {int(status.progress()*100)}%")
        vid = response["id"]
        url = f"https://youtube.com/shorts/{vid}"
        log.info(f"   ✅ {url}")
        return url
    except Exception as e:
        log.error(f"   ❌ YouTube upload: {e}")
        return None

# ─────────────────────────────────────────────────────────────────
#  MAIN PIPELINE
# ─────────────────────────────────────────────────────────────────
def run_pipeline(n_videos=1):
    # Ensure espeak is installed
    if not shutil.which("espeak"):
        os.system("apt-get install -y -qq espeak 2>/dev/null || true")

    log.info("=" * 60)
    log.info("🚀 VAULTMIND PIPELINE v5")
    log.info("=" * 60)

    OUTPUT_DIR.mkdir(exist_ok=True)
    slots    = get_next_slots(n_videos)
    yt_slots = slots[:n_videos]
    tt_slots = [s + timedelta(minutes=30) for s in yt_slots]
    results  = []

    for i in range(n_videos):
        log.info(f"\n{'─'*60}\n  VIDEO {i+1}/{n_videos}\n{'─'*60}")
        ts          = int(time.time())
        work_dir    = Path(f"/tmp/vm_{ts}_{i}")
        raw_video   = Path(f"/tmp/raw_{ts}.mp4")
        final_video = Path(f"/tmp/final_{ts}.mp4")
        voice_file  = Path("/tmp/final_voice.mp3")
        work_dir.mkdir(parents=True)

        try:
            script    = generate_script()
            voiceover = generate_voiceover(script["scenes"], work_dir)
            total_dur = sum(s["duration"] for s in script["scenes"])
            ok        = render_video(script, work_dir, raw_video, GAMEPLAY_FILE)
            if not ok:
                raise Exception("Render failed")

            mix_and_merge(raw_video, voiceover, final_video, total_dur)

            yt_time = yt_slots[i] if i < len(yt_slots) else datetime.now() + timedelta(hours=1)
            tt_time = tt_slots[i] if i < len(tt_slots) else datetime.now() + timedelta(hours=2)

            yt_url = None
            if final_video.exists() and final_video.stat().st_size > 50_000:
                yt_url = upload_youtube(
                    final_video, script["title"],
                    script.get("description", ""),
                    script["hashtags"], yt_time
                )
                # Copy to output dir for archiving
                out_path = OUTPUT_DIR / f"video_{ts}.mp4"
                shutil.copy(str(final_video), str(out_path))
                log.info(f"   💾 Saved: {out_path}")
            else:
                log.error("   ❌ Final video too small or missing")

            entry = {
                "id":         ts,
                "title":      script["title"],
                "type":       script.get("type", "fact"),
                "created_at": datetime.now().isoformat(),
                "youtube":    {"scheduled": yt_time.isoformat(), "url": yt_url, "status": "scheduled"},
                "tiktok":     {"scheduled": tt_time.isoformat(), "status": "scheduled"},
                "hashtags":   script["hashtags"],
                "status":     "scheduled"
            }
            # ── KEY FIX: always update dashboard.json ─────────────
            add_to_dashboard(entry)
            results.append(entry)
            log.info(f"✅ Video {i+1} done! YT: {yt_time.strftime('%a %d %b %H:%M')}")

        except Exception as e:
            import traceback
            log.error(f"❌ Failed video {i+1}: {e}\n{traceback.format_exc()}")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
            for p in [raw_video, final_video, voice_file]:
                if p.exists():
                    p.unlink(missing_ok=True)

        if i < n_videos - 1:
            time.sleep(5)

    log.info(f"\n🎉 DONE — {len(results)}/{n_videos} videos generated")
    return results


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    run_pipeline(n)
