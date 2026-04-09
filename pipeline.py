"""
VaultMind Auto Video Pipeline v3
Groq -> Pexels Photos -> espeak/ElevenLabs -> ffmpeg
Style: Minecraft bg + speaking character + animated captions + photo inserts
Duration: 60-90 seconds
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

NICHES = [
    "Interesting Facts",
    "Unethical ways to earn money (legal but edgy)",
    "Dating fail stories",
    "Business ideas and money hacks",
    "What if scenarios",
    "Crazy true stories",
    "Psychology facts",
    "Life hacks nobody talks about",
]

BEST_TIMES = {
    "monday":    ["07:00","12:00","19:00"],
    "tuesday":   ["07:00","12:00","19:00"],
    "wednesday": ["07:00","14:00","21:00"],
    "thursday":  ["07:00","12:00","19:00"],
    "friday":    ["07:00","14:00","20:00"],
    "saturday":  ["09:00","13:00","20:00"],
    "sunday":    ["10:00","15:00","20:00"],
}

ACCENT_COLORS = [
    (255, 60, 80), (255, 215, 0), (46, 213, 115),
    (138, 92, 255), (30, 200, 255), (255, 140, 0),
]

def get_next_slots(n=14):
    slots, now = [], datetime.now()
    buf = now + timedelta(minutes=45)
    for d in range(21):
        date = now + timedelta(days=d)
        day = date.strftime("%A").lower()
        for t in BEST_TIMES.get(day, ["12:00"]):
            h, m = map(int, t.split(":"))
            slot = date.replace(hour=h, minute=m, second=0, microsecond=0)
            if slot > buf: slots.append(slot)
            if len(slots) >= n: return slots
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
            ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for p in paths:
        if os.path.exists(p): return ImageFont.truetype(p, size)
    return ImageFont.load_default()

# ── STEP 1: Generate script ───────────────────────────────────
def generate_script():
    niche = random.choice(NICHES)
    log.info(f"🤖 Groq script... niche: {niche}")
    prompt = f"""You are a viral TikTok/YouTube Shorts scriptwriter for VaultMind.
Niche: {niche}

Create a 60-75 second video script. Return ONLY valid JSON:
{{
  "topic": "2 word Pexels photo search term",
  "title": "viral title max 60 chars",
  "description": "2-sentence YouTube description",
  "hook": "shocking 1-sentence hook (shown first, 4 seconds)",
  "scenes": [
    {{"text": "max 8 words on screen", "voiceover": "max 20 words spoken", "duration": 4.0, "show_photo": true}}
  ],
  "hashtags": "#vaultmind #facts #shorts #fyp #didyouknow"
}}
Rules:
- 12-16 scenes for 60-75 seconds total
- Scene 1: use the hook text
- Alternate show_photo true/false
- Last 2 scenes: CTA to follow VaultMind
- Keep voiceover short per scene (max 20 words)
- Real facts only, high energy"""

    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
        json={"model": "llama-3.3-70b-versatile",
              "messages": [{"role": "user", "content": prompt}],
              "max_tokens": 1200, "temperature": 0.7},
        timeout=30)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    raw = raw.replace("```json","").replace("```","").strip()
    s = raw.find("{"); e = raw.rfind("}") + 1
    data = json.loads(raw[s:e])
    log.info(f"   ✅ '{data['title']}' — {len(data['scenes'])} scenes")
    return data

# ── STEP 2: Download Pexels photos ────────────────────────────
def download_photos(topic, count, work_dir):
    log.info(f"📸 Downloading {count} photos for '{topic}'...")
    photos = []
    for orientation in ["portrait", "landscape"]:
        resp = requests.get("https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": topic, "per_page": min(count+5, 15),
                    "orientation": orientation}, timeout=15)
        results = resp.json().get("photos", [])
        if len(results) >= count: break

    for i in range(count):
        if i >= len(results):
            photos.append(None); continue
        url = results[i]["src"].get("large2x", results[i]["src"]["original"])
        path = work_dir / f"photo_{i:02d}.jpg"
        r = requests.get(url, stream=True, timeout=30)
        if r.status_code == 200:
            with open(path, "wb") as f:
                for chunk in r.iter_content(8192): f.write(chunk)
            log.info(f"   ✅ Photo {i}: {path.stat().st_size//1024}KB")
            photos.append(path)
        else:
            photos.append(None)
    return photos

# ── STEP 3: Generate voiceover ────────────────────────────────
def generate_voiceover(scenes, work_dir):
    log.info("🎙️  Generating voiceover...")
    audio_files = []

    # Try ElevenLabs first
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
                    log.info(f"   ✅ ElevenLabs {i}: {len(resp.content)//1024}KB")
                else:
                    log.warning(f"   ⚠️ ElevenLabs {resp.status_code} → espeak fallback")
                    audio_files = []; break
            except Exception as e:
                log.warning(f"   ⚠️ ElevenLabs error → espeak fallback")
                audio_files = []; break

    # espeak fallback
    if not audio_files:
        log.info("   🔄 Using espeak...")
        for i, scene in enumerate(scenes):
            wav = work_dir / f"voice_{i:02d}.wav"
            mp3 = work_dir / f"voice_{i:02d}.mp3"
            r1 = subprocess.run(
                ["espeak", "-w", str(wav), "-s", "148", "-p", "52", scene["voiceover"]],
                capture_output=True, text=True)
            if r1.returncode != 0: continue
            r2 = subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav), "-c:a", "libmp3lame", "-q:a", "4", str(mp3)],
                capture_output=True, text=True)
            wav.unlink(missing_ok=True)
            if r2.returncode == 0 and mp3.exists():
                audio_files.append(mp3)
                log.info(f"   ✅ espeak {i}: {mp3.stat().st_size//1024}KB")

    if not audio_files: return None

    final = Path("/tmp/final_voice.mp3")
    if len(audio_files) == 1:
        shutil.copy(str(audio_files[0]), str(final))
    else:
        cl = work_dir / "audio_list.txt"
        cl.write_text("\n".join(f"file '{p.resolve()}'" for p in audio_files))
        r = subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(cl),"-c","copy",str(final)],
            capture_output=True, text=True)
        if r.returncode != 0 or not final.exists():
            shutil.copy(str(audio_files[0]), str(final))

    log.info(f"   ✅ Voiceover: {final.stat().st_size//1024}KB")
    return final

# ── VISUAL HELPERS ────────────────────────────────────────────
def make_minecraft_bg(frame_num):
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    scroll = frame_num * 2

    # Sky gradient
    for y in range(int(H * 0.52)):
        t = y / (H * 0.52)
        draw.line([(0,y),(W,y)], fill=(int(80+t*80), int(140+t*60), int(200+t*30)))

    # Sun
    draw.ellipse([W-110, 45, W-55, 105], fill=(255, 220, 50))
    # Sun glow
    draw.ellipse([W-125, 30, W-40, 120], fill=(255, 240, 100, 40) if hasattr(Image, 'RGBA') else (255,240,100))

    # Clouds
    for i in range(6):
        cx = int((i*160 - scroll*0.25) % (W+180)) - 60
        cy = 50 + (i%4)*45
        for dx,dy,r in [(0,0,32),(28,4,26),(-26,4,23),(14,-16,20),(-14,-16,18)]:
            draw.ellipse([cx+dx-r,cy+dy-r,cx+dx+r,cy+dy+r], fill=(235,240,255))

    gl = int(H * 0.52)

    # Grass top
    for bx in range(-1, W//32+2):
        x = bx*32-(scroll%32)
        draw.rectangle([x,gl,x+31,gl+18], fill=(55,160,55))
        draw.rectangle([x,gl+18,x+31,gl+50], fill=(130,85,40))
        for row in range(2,16):
            sh = max(15, 130-row*8)
            draw.rectangle([x,gl+18+row*32,x+31,gl+18+(row+1)*32], fill=(sh,max(0,sh-45),max(0,sh-80)))

    # Trees
    for i in range(5):
        tx = int((i*220+60-scroll)%(W+250))-100
        draw.rectangle([tx-5,gl-95,tx+5,gl], fill=(90,55,25))
        draw.rectangle([tx-34,gl-160,tx+34,gl-75], fill=(28,108,28))
        draw.rectangle([tx-24,gl-195,tx+24,gl-135], fill=(32,128,32))

    # Underground (visible at bottom)
    draw.rectangle([0,gl+480,W,H], fill=(35,18,5))
    # Stone layer
    for bx in range(-1, W//32+2):
        x = bx*32-(scroll%64)
        if (bx+int(scroll/64))%3==0:
            draw.rectangle([x,gl+490,x+31,gl+521], fill=(100,100,110))

    return img

def crop_photo_to_916(photo_path):
    try:
        img = Image.open(photo_path).convert("RGB")
        ow, oh = img.size
        tr = W/H
        if ow/oh > tr:
            nw = int(oh*tr); off=(ow-nw)//2
            img = img.crop((off,0,off+nw,oh))
        else:
            nh = int(ow/tr); off=(oh-nh)//2
            img = img.crop((0,off,ow,off+nh))
        return img.resize((W,H), Image.LANCZOS)
    except:
        return None

def draw_photo_insert(img, photo_path, progress, slide_dir="right"):
    """Animate a photo sliding in from side with rounded corners."""
    if not photo_path or not photo_path.exists(): return img
    photo = crop_photo_to_916(photo_path)
    if photo is None: return img

    # Photo shown in top 55% of screen
    ph_h = int(H * 0.52)
    photo_resized = photo.resize((W, ph_h), Image.LANCZOS)

    # Slide animation
    ease = min(1.0, progress * 2) ** 0.5
    if slide_dir == "right":
        offset_x = int((1-ease) * W)
    elif slide_dir == "left":
        offset_x = -int((1-ease) * W)
    else:
        offset_x = 0

    # Dark overlay on photo
    overlay = Image.new("RGBA", (W, ph_h), (0,0,20,int(120*(1-ease*0.3))))
    photo_resized_rgba = photo_resized.convert("RGBA")
    photo_final = Image.alpha_composite(photo_resized_rgba, overlay).convert("RGB")

    img.paste(photo_final, (offset_x, 0))
    return img

def draw_speaking_character(img, frame, mouth_open=False, accent=(255,60,80)):
    draw = ImageDraw.Draw(img, 'RGBA')
    x, y = 155, H - 370
    bob = int(math.sin(frame * 0.35) * 5)
    y += bob

    # Shadow
    draw.ellipse([x-50,y+195,x+50,y+212], fill=(0,0,0,50))

    # Legs
    lleg_x = int(math.sin(frame*0.2)*5)
    draw.rectangle([x-26+lleg_x, y+148, x-8+lleg_x, y+200], fill=(40,40,180))
    draw.rectangle([x+8-lleg_x, y+148, x+26-lleg_x, y+200], fill=(40,40,180))
    # Shoes
    draw.ellipse([x-30+lleg_x, y+190, x-2+lleg_x, y+208], fill=(20,20,20))
    draw.ellipse([x+2-lleg_x, y+190, x+30-lleg_x, y+208], fill=(20,20,20))

    # Body
    draw.rectangle([x-34, y+58, x+34, y+152], fill=accent)
    # Shirt detail
    draw.rectangle([x-2, y+58, x+2, y+152], fill=(max(0,accent[0]-40), max(0,accent[1]-40), max(0,accent[2]-40)))

    # Arms animated
    arm_swing = math.sin(frame*0.35)*20
    # Left arm
    draw.line([x-34,y+80,x-68,y+120+int(arm_swing)], fill=accent, width=16)
    draw.ellipse([x-76,y+112+int(arm_swing),x-56,y+132+int(arm_swing)], fill=(255,210,170))
    # Right arm
    draw.line([x+34,y+80,x+68,y+120-int(arm_swing)], fill=accent, width=16)
    draw.ellipse([x+56,y+112-int(arm_swing),x+76,y+132-int(arm_swing)], fill=(255,210,170))

    # Neck
    draw.rectangle([x-10,y+42,x+10,y+64], fill=(255,210,170))

    # Head
    draw.ellipse([x-38,y-10,x+38,y+52], fill=(255,210,170))

    # Hair
    draw.ellipse([x-38,y-10,x+38,y+15], fill=(80,50,20))
    draw.ellipse([x-30,y-22,x+30,y+5], fill=(90,55,22))

    # Eyes (blink every ~3 sec)
    blink = (frame % 90 < 4)
    ey = y+10
    if blink:
        draw.line([x-20,ey+8,x-8,ey+8], fill=(60,40,20), width=3)
        draw.line([x+8,ey+8,x+20,ey+8], fill=(60,40,20), width=3)
    else:
        # Whites
        draw.ellipse([x-22,ey,x-6,ey+18], fill=(255,255,255))
        draw.ellipse([x+6,ey,x+22,ey+18], fill=(255,255,255))
        # Iris
        draw.ellipse([x-20,ey+2,x-8,ey+16], fill=(60,120,200))
        draw.ellipse([x+8,ey+2,x+20,ey+16], fill=(60,120,200))
        # Pupil
        draw.ellipse([x-17,ey+5,x-11,ey+13], fill=(10,10,10))
        draw.ellipse([x+11,ey+5,x+17,ey+13], fill=(10,10,10))
        # Shine
        draw.ellipse([x-15,ey+5,x-13,ey+8], fill=(255,255,255))
        draw.ellipse([x+13,ey+5,x+15,ey+8], fill=(255,255,255))

    # Eyebrows
    draw.line([x-22,ey-3,x-8,ey], fill=(70,45,15), width=3)
    draw.line([x+8,ey-3,x+22,ey], fill=(70,45,15), width=3)

    # Mouth
    if mouth_open:
        draw.ellipse([x-14,y+28,x+14,y+44], fill=(160,30,30))
        draw.ellipse([x-11,y+30,x+11,y+42], fill=(200,60,60))
        draw.rectangle([x-10,y+28,x+10,y+33], fill=(235,225,215))
    else:
        draw.arc([x-12,y+28,x+12,y+44], 10, 170, fill=(140,60,60), width=3)

    return img

def draw_animated_caption(draw, text, word_progress, font_big, font_med, accent, y_pos):
    """Word-by-word caption with highlight on current word."""
    words = text.split()
    total = len(words)
    visible = max(1, int(word_progress * total))

    # Show last 6 words max at a time
    start = max(0, visible-6)
    show_words = words[start:visible]
    display = ' '.join(show_words)

    wrapped = textwrap.fill(display, width=22)
    lines_text = wrapped.split('\n')
    lh = 58
    total_h = len(lines_text)*lh + 24

    # Caption background
    pad = 20
    draw.rectangle([pad, y_pos-total_h//2-12, W-pad, y_pos+total_h//2+12],
                   fill=(0,0,0,210))
    # Accent line top
    draw.rectangle([pad, y_pos-total_h//2-12, W-pad, y_pos-total_h//2-6], fill=accent)

    for li, line in enumerate(lines_text):
        lb = draw.textbbox((0,0), line, font=font_big)
        lw = lb[2]-lb[0]
        lx = (W-lw)//2
        ly = y_pos - total_h//2 + li*lh + 4
        # Shadow
        draw.text((lx+2,ly+2), line, font=font_big, fill=(0,0,0))
        # Main text
        draw.text((lx,ly), line, font=font_big, fill=(255,255,255))

def draw_top_bar(draw, channel_name, font_sm, accent):
    draw.rectangle([0, 0, W, 72], fill=(0,0,0,180))
    draw.rectangle([0, 0, W, 6], fill=accent)
    # Channel badge
    draw.rectangle([16, 14, 220, 56], fill=accent)
    lb = draw.textbbox((0,0), channel_name, font=font_sm)
    lx = 16 + (204-(lb[2]-lb[0]))//2
    draw.text((lx, 18), channel_name, font=font_sm, fill=(0,0,8))

# ── STEP 4: Render full video ─────────────────────────────────
def render_video(script, photos, work_dir, output):
    log.info("🎬 Rendering video...")
    scenes = script["scenes"]
    total_dur = sum(s["duration"] for s in scenes)
    total_frames = int(total_dur * FPS)
    accent = random.choice(ACCENT_COLORS)

    font_caption = get_font(52)
    font_label   = get_font(34)
    font_channel = get_font(28)

    frames_dir = work_dir / "frames"
    frames_dir.mkdir()

    # Photo pool
    photo_pool = [p for p in photos if p and p.exists()]
    photo_idx = 0

    global_frame = 0
    for si, scene in enumerate(scenes):
        n_frames = int(scene["duration"] * FPS)
        show_photo = scene.get("show_photo", si % 2 == 0) and photo_pool
        current_photo = photo_pool[photo_idx % len(photo_pool)] if show_photo else None
        if show_photo: photo_idx += 1
        slide_dir = "right" if si % 2 == 0 else "left"

        for f in range(n_frames):
            t = f / max(n_frames-1, 1)
            mouth_open = (f % 8) < 5

            # 1. Minecraft background
            bg = make_minecraft_bg(global_frame)

            # 2. Photo insert (top area)
            if current_photo:
                photo_progress = min(1.0, t * 3)
                bg = draw_photo_insert(bg, current_photo, photo_progress, slide_dir)
            else:
                # Dark overlay on bg top
                ov = Image.new("RGBA", (W, int(H*0.52)), (0,0,0,120))
                bg.paste(Image.new("RGB",(W,int(H*0.52)),(0,0,0)), (0,0),
                         Image.new("L",(W,int(H*0.52)), 120))

            # 3. Speaking character
            bg = draw_speaking_character(bg, global_frame, mouth_open, accent)

            # 4. Overlays
            draw = ImageDraw.Draw(bg, 'RGBA')

            # Top bar
            draw_top_bar(draw, "VAULTMIND", font_channel, accent)

            # Caption
            caption_text = scene["voiceover"]
            word_prog = min(1.0, t * 2.0 + 0.1)
            draw_animated_caption(draw, caption_text, word_prog,
                                  font_caption, font_label, accent,
                                  H - 290)

            # Scene label (small, top right)
            scene_label = f"{si+1}/{len(scenes)}"
            draw.rectangle([W-80, 14, W-14, 54], fill=(0,0,0,160))
            lb = draw.textbbox((0,0), scene_label, font=font_label)
            draw.text((W-80+(66-(lb[2]-lb[0]))//2, 20), scene_label, font=font_label, fill=accent)

            # Progress bar
            prog = global_frame / max(total_frames-1, 1)
            bw = int(W*prog)
            draw.rectangle([0, H-10, bw, H], fill=accent)
            draw.rectangle([bw, H-10, W, H], fill=(15,15,15,220))

            bg.save(frames_dir / f"frame_{global_frame:06d}.png")
            global_frame += 1

        if global_frame % 50 == 0:
            log.info(f"   🎨 Frame {global_frame}/{total_frames}")

    log.info(f"   ✅ {global_frame} frames rendered")

    # ffmpeg: frames → video
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
    else:
        log.error(f"   ❌ render failed: {r.stderr[-200:]}")
        return False

# ── STEP 5: Merge audio + video ───────────────────────────────
def merge_audio(video_path, voice_path, output):
    total_dur = None
    # Get video duration
    r = subprocess.run(["ffprobe","-v","quiet","-print_format","json",
                        "-show_format",str(video_path)], capture_output=True, text=True)
    try: total_dur = float(json.loads(r.stdout)["format"]["duration"])
    except: pass

    cmd = ["ffmpeg","-y","-i",str(video_path),"-i",str(voice_path),
           "-map","0:v","-map","1:a","-c:v","copy",
           "-c:a","aac","-b:a","128k","-movflags","+faststart"]
    if total_dur: cmd += ["-t", str(total_dur)]
    cmd.append(str(output))

    r2 = subprocess.run(cmd, capture_output=True, text=True)
    if r2.returncode == 0 and output.exists():
        log.info(f"   ✅ Final: {output.stat().st_size//1024}KB")
        return True
    else:
        log.error(f"   ❌ merge failed: {r2.stderr[-150:]}")
        return False

# ── STEP 6: YouTube upload ────────────────────────────────────
def upload_youtube(video_path, title, description, hashtags, publish_at):
    log.info(f"📺 YouTube -> {publish_at.strftime('%a %d %b %H:%M')}")
    try:
        import pickle, base64
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google.auth.transport.requests import Request
        token_b64 = os.environ.get("YOUTUBE_TOKEN_B64", "")
        if not token_b64: log.warning("   ⚠️ No YouTube token"); return None
        creds = pickle.loads(base64.b64decode(token_b64))
        if creds.expired and creds.refresh_token: creds.refresh(Request())
        yt = build("youtube","v3",credentials=creds)
        body = {
            "snippet": {"title": title[:100],
                       "description": description+"\n\n"+hashtags+"\n\n#Shorts",
                       "tags": [t.replace("#","") for t in hashtags.split()],
                       "categoryId": "22"},
            "status": {"privacyStatus": "private",
                      "publishAt": publish_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                      "selfDeclaredMadeForKids": False}
        }
        media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
        req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None: _, response = req.next_chunk()
        vid = response["id"]
        log.info(f"   ✅ youtu.be/{vid}")
        return f"https://youtube.com/shorts/{vid}"
    except Exception as e:
        log.error(f"   ❌ YouTube: {e}"); return None

# ── MAIN ──────────────────────────────────────────────────────
def run_pipeline(n_videos=1):
    # Auto-install espeak if missing
    import shutil as _sh
    if not _sh.which("espeak"):
        log.info("Installing espeak...")
        os.system("apt-get update -qq && apt-get install -y -qq espeak")

    log.info("="*55)
    log.info("🚀 VAULTMIND PIPELINE v3")
    log.info("="*55)
    OUTPUT_DIR.mkdir(exist_ok=True)
    slots = get_next_slots(n_videos*2)
    yt_slots = slots[0::2][:n_videos]
    tt_slots = slots[1::2][:n_videos]
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
            n_photos  = sum(1 for s in script["scenes"] if s.get("show_photo", True))
            photos    = download_photos(script["topic"], n_photos, work_dir)
            voiceover = generate_voiceover(script["scenes"], work_dir)

            ok = render_video(script, photos, work_dir, raw_video)
            if not ok: raise Exception("Video render failed")

            if voiceover and voiceover.exists():
                ok2 = merge_audio(raw_video, voiceover, final_video)
                if not ok2:
                    shutil.copy(str(raw_video), str(final_video))
            else:
                shutil.copy(str(raw_video), str(final_video))

            yt_time = yt_slots[i] if i < len(yt_slots) else datetime.now()+timedelta(hours=1)
            tt_time = tt_slots[i] if i < len(tt_slots) else datetime.now()+timedelta(hours=2)

            if final_video.exists() and final_video.stat().st_size > 50000:
                yt_url = upload_youtube(final_video, script["title"],
                                        script.get("description",""),
                                        script["hashtags"], yt_time)
            else:
                log.error("   ❌ Video too small, skipping upload")
                yt_url = None

            entry = {"id": ts, "title": script["title"],
                    "duration": sum(s["duration"] for s in script["scenes"]),
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
