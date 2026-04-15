"""
VaultMind Auto Video Pipeline v4
Real gameplay bg + Reddit story format + speaking character + animated captions
"""
import os, sys, json, time, random, textwrap, subprocess, shutil, logging, requests, math
from datetime import datetime, timedelta
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("pipeline.log")])
log = logging.getLogger("pipeline")

def load_env():
    if Path(".env").exists():
        for line in Path(".env").read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
load_env()

GROQ_KEY   = os.environ.get("GROQ_API_KEY", "")
ELEVEN_KEY = os.environ.get("ELEVENLABS_KEY", "")
PEXELS_KEY = os.environ.get("PEXELS_KEY", "")
VOICE_ID   = "21m00Tcm4TlvDq8ikWAM"
W, H, FPS  = 720, 1280, 30
OUTPUT_DIR     = Path("./output_videos")
DASHBOARD_FILE = Path("./dashboard.json")
GAMEPLAY_FILE  = Path(os.environ.get("GAMEPLAY_PATH", "./gameplay_bg.mp4"))

NICHES = [
    {"type": "reddit", "prompt": "Reddit AITA or never again story — dramatic, relatable, real sounding"},
    {"type": "reddit", "prompt": "Reddit relationship fail or revenge story"},
    {"type": "reddit", "prompt": "Reddit workplace drama or entitled boss story"},
    {"type": "fact",   "prompt": "Mind-blowing psychology fact most people don't know"},
    {"type": "fact",   "prompt": "Shocking money or business fact"},
    {"type": "fact",   "prompt": "Insane historical fact that sounds fake but is true"},
    {"type": "money",  "prompt": "Unethical but legal way to earn money online"},
    {"type": "whatif", "prompt": "What if scenario that makes people think"},
]

BEST_TIMES = {
    "monday":    ["07:00","19:00"],
    "tuesday":   ["07:00","19:00"],
    "wednesday": ["07:00","21:00"],
    "thursday":  ["07:00","19:00"],
    "friday":    ["07:00","20:00"],
    "saturday":  ["09:00","20:00"],
    "sunday":    ["10:00","20:00"],
}
MAX_UPLOADS_PER_DAY = 2

ACCENT_COLORS = [
    (255, 215, 0), (255, 60, 80), (46, 213, 115),
    (138, 92, 255), (30, 200, 255),
]

def load_scheduled_slots():
    """Return set of already-scheduled datetime slots from dashboard."""
    data = load_dashboard()
    scheduled = set()
    for v in data.get("videos", []):
        for key in ("youtube", "tiktok"):
            t = v.get(key, {}).get("scheduled")
            if t:
                try:
                    dt = datetime.fromisoformat(str(t))
                    # Normalise to minute precision for collision check
                    scheduled.add(dt.replace(second=0, microsecond=0))
                except Exception:
                    pass
    return scheduled

def get_next_slots(n=14, existing_slots=None):
    """Return n upcoming upload slots, max MAX_UPLOADS_PER_DAY per day,
    skipping any slot already occupied in existing_slots."""
    if existing_slots is None:
        existing_slots = load_scheduled_slots()

    slots, now = [], datetime.now()
    buf = now + timedelta(minutes=45)
    uploads_per_day: dict = {}  # date -> count

    for d in range(60):  # look up to 60 days ahead
        date = now + timedelta(days=d)
        day_key = date.strftime("%Y-%m-%d")
        day_name = date.strftime("%A").lower()
        for t in BEST_TIMES.get(day_name, ["12:00", "20:00"]):
            h, m = map(int, t.split(":"))
            slot = date.replace(hour=h, minute=m, second=0, microsecond=0)
            if slot <= buf:
                continue
            # Skip if already occupied (within 10-min window)
            occupied = any(abs((slot - s).total_seconds()) < 600 for s in existing_slots)
            if occupied:
                continue
            # Max uploads per day
            if uploads_per_day.get(day_key, 0) >= MAX_UPLOADS_PER_DAY:
                continue
            slots.append(slot)
            existing_slots.add(slot)
            uploads_per_day[day_key] = uploads_per_day.get(day_key, 0) + 1
            if len(slots) >= n:
                return slots
    return slots

def load_dashboard():
    if DASHBOARD_FILE.exists(): return json.loads(DASHBOARD_FILE.read_text())
    return {"videos": [], "stats": {"generated": 0}}

def save_dashboard(data): DASHBOARD_FILE.write_text(json.dumps(data, indent=2, default=str))

def add_to_dashboard(entry):
    data = load_dashboard(); data["videos"].insert(0, entry)
    data["stats"]["generated"] += 1; save_dashboard(data)

def get_font(size, bold=True):
    paths = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"] if bold else \
            ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]
    for p in paths:
        if os.path.exists(p): return ImageFont.truetype(p, size)
    return ImageFont.load_default()

# ── STEP 1: Generate script ───────────────────────────────────
def generate_script():
    niche = random.choice(NICHES)
    log.info(f"🤖 Groq script... type: {niche['type']}")

    if niche["type"] == "reddit":
        prompt = f"""You are writing viral TikTok content for VaultMind channel.
Topic: {niche['prompt']}

Create a realistic Reddit-style story video script. Return ONLY valid JSON:
{{
  "type": "reddit",
  "title": "viral title — emotional, specific, max 60 chars. Use numbers or power words. NO generic titles.",
  "description": "2-sentence YouTube description",
  "reddit_title": "realistic Reddit post title (like real Reddit — casual, specific, emotional)",
  "reddit_sub": "AITA",
  "reddit_user": "u/ThrowawayAccount_{random.randint(1000,9999)}",
  "scenes": [
    {{"text": "caption text max 10 words", "voiceover": "narration max 25 words", "duration": 5.0}}
  ],
  "hashtags": "#vaultmind #reddit #storytime #fyp #shorts"
}}
Rules:
- 16-20 scenes, 70-90 seconds total (MUST exceed 60 seconds)
- Scene 1 (5s): Show reddit post title only, voiceover reads it
- Scenes 2-17: Tell the story in chunks, dramatic, engaging
- Last 2 scenes: resolution + CTA follow VaultMind
- Make the story REALISTIC and SPECIFIC (names, places, details)
- High drama, relatable, scroll-stopping"""
    else:
        prompt = f"""You are writing viral TikTok content for VaultMind channel.
Topic: {niche['prompt']}

Return ONLY valid JSON:
{{
  "type": "fact",
  "title": "viral title — emotional, specific, max 60 chars. Use numbers or power words. NO generic titles.",
  "description": "2-sentence YouTube description",
  "reddit_title": "",
  "reddit_sub": "",
  "reddit_user": "",
  "scenes": [
    {{"text": "caption text max 10 words", "voiceover": "narration max 25 words", "duration": 4.5}}
  ],
  "hashtags": "#vaultmind #facts #didyouknow #fyp #shorts"
}}
Rules:
- 16-20 scenes, 70-85 seconds total (MUST exceed 60 seconds)
- Scene 1: shocking hook
- Scenes 2-13: build the content
- Last 2: CTA follow VaultMind
- Real, verifiable, surprising content"""

    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
        json={"model": "llama-3.3-70b-versatile",
              "messages": [{"role": "user", "content": prompt}],
              "max_tokens": 1500, "temperature": 0.8},
        timeout=30)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    raw = raw.replace("```json","").replace("```","").strip()
    s = raw.find("{"); e = raw.rfind("}") + 1
    data = json.loads(raw[s:e])
    log.info(f"   ✅ '{data['title']}' — {len(data['scenes'])} scenes")
    return data

# ── STEP 2: Voiceover ─────────────────────────────────────────
def generate_voiceover(scenes, work_dir):
    log.info("🎙️  Generating voiceover...")
    audio_files = []

    # Priority 1: ElevenLabs (best quality)
    if ELEVEN_KEY:
        for i, scene in enumerate(scenes):
            try:
                resp = requests.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}",
                    headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"},
                    json={"text": scene["voiceover"], "model_id": "eleven_turbo_v2_5",
                          "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
                    timeout=30)
                if resp.status_code == 200 and len(resp.content) > 500:
                    path = work_dir / f"voice_{i:02d}.mp3"
                    path.write_bytes(resp.content)
                    audio_files.append(path)
                else:
                    log.warning(f"   ⚠️ ElevenLabs {resp.status_code} → edge-tts")
                    audio_files = []; break
            except:
                audio_files = []; break

    # Priority 2: edge-tts (Microsoft neural TTS — free, sounds natural)
    if not audio_files:
        try:
            import asyncio, edge_tts
            log.info("   🔄 edge-tts (Microsoft neural TTS)...")
            EDGE_VOICE = "en-US-GuyNeural"

            async def gen_edge(text, path):
                tts = edge_tts.Communicate(text, voice=EDGE_VOICE, rate="+10%")
                await tts.save(str(path))

            for i, scene in enumerate(scenes):
                path = work_dir / f"voice_{i:02d}.mp3"
                asyncio.run(gen_edge(scene["voiceover"], path))
                if path.exists() and path.stat().st_size > 500:
                    audio_files.append(path)
                    log.info(f"   ✅ edge-tts {i}: {path.stat().st_size//1024}KB")
                else:
                    log.warning(f"   ⚠️ edge-tts {i} failed")
                    audio_files = []; break
        except Exception as e:
            log.warning(f"   ⚠️ edge-tts failed: {e} → espeak fallback")
            audio_files = []

    # Priority 3: espeak (offline fallback)
    if not audio_files:
        log.info("   🔄 espeak fallback...")
        for i, scene in enumerate(scenes):
            wav = work_dir / f"v_{i}.wav"
            mp3 = work_dir / f"voice_{i:02d}.mp3"
            subprocess.run(["espeak","-w",str(wav),"-s","140","-p","45","-g","3",
                           scene["voiceover"]], capture_output=True)
            subprocess.run(["ffmpeg","-y","-i",str(wav),"-c:a","libmp3lame","-q:a","2",str(mp3)],
                          capture_output=True)
            wav.unlink(missing_ok=True)
            if mp3.exists(): audio_files.append(mp3)

    if not audio_files: return None

    final = Path("/tmp/final_voice.mp3")
    if len(audio_files) == 1:
        shutil.copy(str(audio_files[0]), str(final))
    else:
        cl = work_dir / "al.txt"
        cl.write_text("\n".join(f"file '{p.resolve()}'" for p in audio_files))
        r = subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(cl),"-c","copy",str(final)],
            capture_output=True, text=True)
        if r.returncode != 0 or not final.exists():
            shutil.copy(str(audio_files[0]), str(final))

    log.info(f"   ✅ Audio: {final.stat().st_size//1024}KB")
    return final

# ── VISUAL: Reddit card ───────────────────────────────────────
def draw_reddit_card(img, script, progress, font_title, font_meta, font_small):
    """Draw realistic Reddit post card at top of screen."""
    if not script.get("reddit_title"): return img

    draw = ImageDraw.Draw(img, 'RGBA')
    card_x, card_y = 20, 85
    card_w, card_h = W - 40, 175
    slide = min(1.0, progress * 4) ** 0.5

    # Card background (Reddit dark mode style)
    alpha = int(230 * slide)
    draw.rectangle([card_x, card_y, card_x+card_w, card_y+card_h],
                   fill=(26, 26, 27, alpha))
    draw.rectangle([card_x, card_y, card_x+card_w, card_y+2],
                   fill=(255, 69, 0, alpha))  # Reddit orange top

    # Subreddit + user
    sub_text = f"r/{script.get('reddit_sub','AskReddit')}  •  {script.get('reddit_user','u/throwaway')}"
    draw.text((card_x+12, card_y+12), sub_text, font=font_small, fill=(129, 132, 135, alpha))

    # Reddit title (wrapped)
    title = script.get("reddit_title", "")
    wrapped = textwrap.fill(title, width=38)
    lines = wrapped.split('\n')
    ty = card_y + 38
    for line in lines[:3]:
        draw.text((card_x+12, ty), line, font=font_title, fill=(215, 218, 220, alpha))
        ty += 38

    # Upvotes + comments (fake but realistic)
    upvotes = f"▲ {random.randint(8,42)}k  💬 {random.randint(200,3000)}"
    draw.text((card_x+12, card_y+card_h-28), upvotes, font=font_small, fill=(129,132,135,alpha))

    return img

# ── VISUAL: TikTok-style captions (2 words, centered) ────────
def draw_caption_tiktok(img, text, word_progress):
    """Viral TikTok caption: 2 words at a time, white+yellow, heavy outline, never out of frame."""
    words = text.split()
    if not words:
        return img
    total = len(words)
    visible_idx = max(1, int(word_progress * total))
    chunk_size = 2
    chunk_start = ((visible_idx - 1) // chunk_size) * chunk_size
    chunk = words[chunk_start:chunk_start + chunk_size]
    if not chunk:
        return img

    draw = ImageDraw.Draw(img, 'RGBA')
    MARGIN = 50

    def outline_text(x, y, txt, font, fill):
        stroke = 7
        for dx in range(-stroke, stroke+1, 2):
            for dy in range(-stroke, stroke+1, 2):
                if dx == 0 and dy == 0:
                    continue
                draw.text((x+dx, y+dy), txt, font=font, fill=(0, 0, 0, 255))
        draw.text((x, y), txt, font=font, fill=fill)

    def text_size(txt, font):
        bb = draw.textbbox((0, 0), txt, font=font)
        return bb[2]-bb[0], bb[3]-bb[1]

    # Auto-size font to fit within frame
    size = 80
    font = get_font(size)
    full_text = ' '.join(chunk)
    tw, th = text_size(full_text, font)
    while tw > W - MARGIN * 2 and size > 32:
        size -= 4
        font = get_font(size)
        tw, th = text_size(full_text, font)

    # Center vertically slightly below middle (TikTok safe zone)
    y_pos = H // 2 - th // 2 + 80

    # Draw word by word, last word = yellow
    x_cursor = (W - tw) // 2
    for wi, word in enumerate(chunk):
        color = (255, 220, 0, 255) if wi == len(chunk)-1 else (255, 255, 255, 255)
        outline_text(x_cursor, y_pos, word, font, color)
        space = word + (' ' if wi < len(chunk)-1 else '')
        ww, _ = text_size(space, font)
        x_cursor += ww

    return img


# ── VISUAL: Speaking character (bottom right) ─────────────────
def draw_character_br(img, frame, mouth_open, accent):
    """Character bottom-right, standing on the platform level."""
    draw = ImageDraw.Draw(img, 'RGBA')
    x = W - 130
    y = H - 310
    bob = int(math.sin(frame * 0.35) * 4)
    y += bob

    def c(r,g,b,a=255): return (r,g,b,a)

    # Shadow
    draw.ellipse([x-45, y+190, x+45, y+205], fill=c(0,0,0,50))

    # Legs
    ls = int(math.sin(frame*0.2)*4)
    draw.rectangle([x-24+ls, y+145, x-7+ls, y+195], fill=c(35,35,170))
    draw.rectangle([x+7-ls,  y+145, x+24-ls, y+195], fill=c(35,35,170))
    draw.ellipse([x-28+ls, y+185, x-3+ls, y+200], fill=c(15,15,15))
    draw.ellipse([x+3-ls,  y+185, x+28-ls, y+200], fill=c(15,15,15))

    # Body
    draw.rectangle([x-30, y+55, x+30, y+150], fill=accent)
    draw.rectangle([x-2, y+55, x+2, y+150], fill=(max(0,accent[0]-50), max(0,accent[1]-50), max(0,accent[2]-50)))

    # Arms
    sw = int(math.sin(frame*0.35)*18)
    draw.line([x-30,y+75, x-60,y+115+sw], fill=accent, width=14)
    draw.ellipse([x-68,y+107+sw, x-50,y+123+sw], fill=c(255,210,170))
    draw.line([x+30,y+75, x+60,y+115-sw], fill=accent, width=14)
    draw.ellipse([x+50,y+107-sw, x+68,y+123-sw], fill=c(255,210,170))

    # Neck + head
    draw.rectangle([x-9,y+40, x+9,y+60], fill=c(255,210,170))
    draw.ellipse([x-34,y-8, x+34,y+48], fill=c(255,210,170))

    # Hair
    draw.ellipse([x-34,y-8, x+34,y+15], fill=c(75,45,18))
    draw.ellipse([x-28,y-20, x+28,y+2], fill=c(85,50,20))

    # Eyes
    blink = (frame % 85 < 3)
    ey = y+8
    if blink:
        draw.line([x-18,ey+7, x-7,ey+7], fill=c(50,35,15), width=3)
        draw.line([x+7,ey+7, x+18,ey+7], fill=c(50,35,15), width=3)
    else:
        draw.ellipse([x-20,ey+1, x-6,ey+17], fill=c(255,255,255))
        draw.ellipse([x+6,ey+1,  x+20,ey+17], fill=c(255,255,255))
        draw.ellipse([x-18,ey+3, x-8,ey+15], fill=c(55,110,195))
        draw.ellipse([x+8,ey+3,  x+18,ey+15], fill=c(55,110,195))
        draw.ellipse([x-16,ey+5, x-10,ey+13], fill=c(5,5,5))
        draw.ellipse([x+10,ey+5, x+16,ey+13], fill=c(5,5,5))
        draw.ellipse([x-15,ey+5, x-13,ey+8], fill=c(255,255,255))
        draw.ellipse([x+13,ey+5, x+15,ey+8], fill=c(255,255,255))

    # Mouth
    if mouth_open:
        draw.ellipse([x-12,y+26, x+12,y+40], fill=c(160,30,30))
        draw.ellipse([x-9, y+28, x+9, y+38], fill=c(200,60,60))
        draw.rectangle([x-8,y+26, x+8,y+31], fill=c(235,225,215))
    else:
        draw.arc([x-11,y+26, x+11,y+40], 10, 170, fill=c(140,55,55), width=2)

    return img

# ── VISUAL: Animated caption (yellow, bottom center) ──────────
def draw_caption_viral(draw, text, word_progress, font_big, font_sm, y_pos):
    """YouTube/TikTok style yellow caption with black outline."""
    words = text.split()
    total = len(words)
    visible = max(1, int(word_progress * total))

    # Show max 5 words at a time
    start = max(0, visible - 5)
    show = words[start:visible]

    # Highlight last word
    if len(show) > 1:
        normal = ' '.join(show[:-1]) + ' '
        highlight = show[-1]
    else:
        normal = ''
        highlight = show[0] if show else ''

    def text_w(t, font):
        bb = draw.textbbox((0,0), t, font=font)
        return bb[2]-bb[0]

    nw = text_w(normal, font_big)
    hw = text_w(highlight, font_big)
    total_w = nw + hw
    start_x = (W - total_w) // 2

    # Outline function
    def outlined(x, y, t, font, fill, outline=(0,0,0)):
        for dx, dy in [(-3,0),(3,0),(0,-3),(0,3),(-2,-2),(2,-2),(-2,2),(2,2)]:
            draw.text((x+dx, y+dy), t, font=font, fill=outline)
        draw.text((x,y), t, font=font, fill=fill)

    if normal:
        outlined(start_x, y_pos, normal, font_big, (255,255,255))
    outlined(start_x + nw, y_pos, highlight, font_big, (255, 220, 0))

# ── STEP 3: Render frames with gameplay bg ────────────────────
def render_video(script, work_dir, output, gameplay_path):
    log.info("🎬 Rendering video with gameplay background...")
    scenes = script["scenes"]
    total_dur = sum(s["duration"] for s in scenes)
    total_frames = int(total_dur * FPS)
    accent = random.choice(ACCENT_COLORS)

    font_caption    = get_font(80)
    font_reddit_title = get_font(32)
    font_reddit_meta  = get_font(24, bold=False)
    font_reddit_sm    = get_font(20, bold=False)

    is_reddit = script.get("type") == "reddit"

    # Extract gameplay frames
    frames_dir = work_dir / "frames"
    frames_dir.mkdir()
    gameplay_frames_dir = work_dir / "gp_frames"
    gameplay_frames_dir.mkdir()

    # Extract gameplay at target FPS (use random start offset)
    max_offset = 60
    gp_offset = random.uniform(0, max_offset)
    log.info(f"   📼 Extracting gameplay frames (offset {gp_offset:.1f}s)...")

    log.info(f"   📁 Gameplay path: {gameplay_path} exists={gameplay_path.exists()}")
    if gameplay_path.exists():
        r = subprocess.run([
            "ffmpeg", "-y",
            "-ss", str(gp_offset),
            "-i", str(gameplay_path),
            "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
            "-r", str(FPS),
            "-t", str(total_dur + 2),
            "-q:v", "5",
            str(gameplay_frames_dir / "frame_%06d.jpg")
        ], capture_output=True, text=True)
        if r.returncode != 0:
            log.error(f"   ❌ ffmpeg gameplay extract: {r.stderr[-200:]}")
        gp_frames = sorted(gameplay_frames_dir.glob("frame_*.jpg"))
        log.info(f"   ✅ {len(gp_frames)} gameplay frames extracted")
    else:
        gp_frames = []
        log.warning("   ⚠️ No gameplay file, using dark background")
        # List /app to debug
        import glob as _g
        log.info(f"   📂 /app contents: {_g.glob('/app/*')}")

    global_frame = 0
    for si, scene in enumerate(scenes):
        n_frames = int(scene["duration"] * FPS)
        for f in range(n_frames):
            t = f / max(n_frames-1, 1)

            # 1. Gameplay background
            gp_idx = min(global_frame, len(gp_frames)-1)
            if gp_frames and gp_idx >= 0:
                bg = Image.open(gp_frames[gp_idx]).convert("RGB")
                if bg.size != (W, H):
                    bg = bg.resize((W, H), Image.BILINEAR)
            else:
                bg = Image.new("RGB", (W, H), (10, 10, 20))

            # 2. Dark overlay for readability
            overlay = Image.new("RGBA", (W, H), (0, 0, 0, 70))
            bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")

            # 3. Reddit card (first 3 scenes)
            if is_reddit and si <= 2:
                card_progress = min(1.0, (si * n_frames + f) / (3 * n_frames))
                bg = draw_reddit_card(bg, script, card_progress,
                                      font_reddit_title, font_reddit_meta, font_reddit_sm)

            # 4. TikTok-style captions (center screen, 2 words)
            word_prog = min(1.0, t * 2.0 + 0.05)
            bg = draw_caption_tiktok(bg, scene["voiceover"], word_prog)

            # 5. Thin progress bar at very bottom
            draw = ImageDraw.Draw(bg, 'RGBA')
            prog = global_frame / max(total_frames-1, 1)
            bw = int(W * prog)
            draw.rectangle([0, H-6, bw, H], fill=(255, 255, 255, 180))
            draw.rectangle([bw, H-6, W, H], fill=(0, 0, 0, 100))

            bg.save(frames_dir / f"frame_{global_frame:06d}.png")
            global_frame += 1

        if si % 3 == 0:
            log.info(f"   🎨 Scene {si+1}/{len(scenes)} done")

    shutil.rmtree(gameplay_frames_dir, ignore_errors=True)
    log.info(f"   ✅ {global_frame} frames rendered")

    # Render frames to video
    r = subprocess.run([
        "ffmpeg", "-y", "-framerate", str(FPS),
        "-i", str(frames_dir/"frame_%06d.png"),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(output)
    ], capture_output=True, text=True)
    shutil.rmtree(frames_dir)

    if r.returncode == 0 and output.exists():
        log.info(f"   ✅ Video: {output.stat().st_size//1024}KB, {total_dur:.1f}s")
        return True
    log.error(f"   ❌ Render failed: {r.stderr[-200:]}")
    return False

def merge_audio(video_path, voice_path, output):
    r = subprocess.run([
        "ffmpeg", "-y", "-i", str(video_path), "-i", str(voice_path),
        "-map", "0:v", "-map", "1:a", "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output)
    ], capture_output=True, text=True)
    if r.returncode == 0 and output.exists():
        log.info(f"   ✅ Final: {output.stat().st_size//1024}KB")
        return True
    log.error(f"   ❌ Merge: {r.stderr[-150:]}")
    return False

def upload_youtube(video_path, title, description, hashtags, publish_at):
    log.info(f"📺 YouTube -> {publish_at.strftime('%a %d %b %H:%M')}")
    try:
        import pickle, base64
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google.auth.transport.requests import Request

        token_b64 = os.environ.get("YOUTUBE_TOKEN_B64", "")
        if not token_b64: return None
        creds = pickle.loads(base64.b64decode(token_b64))
        if creds.expired and creds.refresh_token: creds.refresh(Request())

        # Build without file cache to avoid oauth2client<4.0.0 warning
        import googleapiclient.discovery as gd
        try:
            yt = gd.build("youtube", "v3", credentials=creds, cache_discovery=False)
        except TypeError:
            # Older versions don't support cache_discovery kwarg
            yt = gd.build("youtube", "v3", credentials=creds)

        # Ensure publish_at is UTC-aware ISO format
        if publish_at.tzinfo is None:
            from datetime import timezone
            publish_at = publish_at.replace(tzinfo=timezone.utc)
        publish_str = publish_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        body = {
            "snippet": {
                "title": title[:100],
                "description": description + "\n\n" + hashtags,
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
                log.info(f"   📤 Upload {int(status.progress()*100)}%")
        vid = response["id"]
        log.info(f"   ✅ youtu.be/{vid}")
        return f"https://youtube.com/shorts/{vid}"
    except Exception as e:
        log.error(f"   ❌ YouTube: {e}"); return None

# ── MAIN ──────────────────────────────────────────────────────
def run_pipeline(n_videos=1):
    import shutil as _sh
    if not _sh.which("espeak"):
        log.info("Installing espeak...")
        os.system("apt-get update -qq && apt-get install -y -qq espeak")

    log.info("="*55)
    log.info("🚀 VAULTMIND PIPELINE v4")
    log.info("="*55)
    OUTPUT_DIR.mkdir(exist_ok=True)
    existing_slots = load_scheduled_slots()
    # Each video needs 1 YT slot; TT uses the same slot offset by 30min
    slots = get_next_slots(n_videos, existing_slots)
    yt_slots = slots[:n_videos]
    tt_slots = [s + timedelta(minutes=30) for s in yt_slots]
    results = []

    for i in range(n_videos):
        log.info(f"\n{'─'*55}\n  VIDEO {i+1}/{n_videos}\n{'─'*55}")
        ts = int(time.time())
        work_dir = Path(f"/tmp/pipeline_{ts}_{i}")
        work_dir.mkdir(parents=True)
        raw_video = Path(f"/tmp/raw_{ts}.mp4")
        final_video = Path(f"/tmp/video_{ts}.mp4")
        voice_file = Path("/tmp/final_voice.mp3")

        try:
            script    = generate_script()
            voiceover = generate_voiceover(script["scenes"], work_dir)
            ok        = render_video(script, work_dir, raw_video, GAMEPLAY_FILE)
            if not ok: raise Exception("Render failed")

            if voiceover and voiceover.exists():
                merge_audio(raw_video, voiceover, final_video)
            else:
                shutil.copy(str(raw_video), str(final_video))

            yt_time = yt_slots[i] if i < len(yt_slots) else datetime.now()+timedelta(hours=1)
            tt_time = tt_slots[i] if i < len(tt_slots) else datetime.now()+timedelta(hours=2)

            if final_video.exists() and final_video.stat().st_size > 50000:
                yt_url = upload_youtube(final_video, script["title"],
                                        script.get("description",""),
                                        script["hashtags"], yt_time)
            else:
                log.error("   ❌ Video too small"); yt_url = None

            entry = {"id": ts, "title": script["title"],
                    "type": script.get("type","fact"),
                    "created_at": datetime.now().isoformat(),
                    "youtube": {"scheduled": yt_time.isoformat(), "url": yt_url},
                    "tiktok": {"scheduled": tt_time.isoformat()},
                    "hashtags": script["hashtags"], "status": "scheduled"}
            add_to_dashboard(entry)
            results.append(entry)
            log.info(f"✅ Video {i+1} done! YT: {yt_time.strftime('%a %d %b %H:%M')}")

        except Exception as e:
            import traceback
            log.error(f"❌ Failed: {e}\n{traceback.format_exc()}")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
            for p in [raw_video, voice_file]:
                if p.exists(): p.unlink()
        if i < n_videos-1: time.sleep(3)

    log.info(f"\n🎉 DONE — {len(results)}/{n_videos} videos")
    return results

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    run_pipeline(n)
