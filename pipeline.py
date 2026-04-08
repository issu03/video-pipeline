"""
VaultMind Auto Video Pipeline
Groq -> Pexels Photos -> ElevenLabs -> ffmpeg -> YouTube Scheduled Upload
"""
import os, sys, json, time, random, textwrap, subprocess, shutil, logging, requests
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

def get_next_slots(n=14):
    slots, now = [], datetime.now()
    buffer = now + timedelta(minutes=45)
    for d in range(21):
        date = now + timedelta(days=d)
        day = date.strftime("%A").lower()
        for t in BEST_TIMES.get(day, ["12:00"]):
            h, m = map(int, t.split(":"))
            slot = date.replace(hour=h, minute=m, second=0, microsecond=0)
            if slot > buffer:
                slots.append(slot)
            if len(slots) >= n:
                return slots
    return slots

def load_dashboard():
    if DASHBOARD_FILE.exists():
        return json.loads(DASHBOARD_FILE.read_text())
    return {"videos": [], "stats": {"generated": 0}}

def save_dashboard(data):
    DASHBOARD_FILE.write_text(json.dumps(data, indent=2, default=str))

def add_to_dashboard(entry):
    data = load_dashboard()
    data["videos"].insert(0, entry)
    data["stats"]["generated"] += 1
    save_dashboard(data)

def get_font(size, bold=True):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ] if bold else [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

PALETTES = [
    (255, 215,   0),
    (138,  92, 255),
    ( 46, 213, 115),
    (255,  71,  87),
    ( 30, 200, 255),
    (255, 165,   0),
]

# ── STEP 1: Generate script ───────────────────────────────────
def generate_script():
    niche = random.choice(NICHES)
    log.info(f"🤖 Groq script... niche: {niche}")
    prompt = f"""You are a viral TikTok/Shorts scriptwriter for VaultMind.
Niche: {niche}

Return ONLY valid JSON, no markdown:
{{
  "topic": "2 word Pexels photo search (e.g. 'night city' or 'ancient temple')",
  "title": "viral title max 60 chars",
  "description": "2-sentence YouTube description",
  "scenes": [
    {{"text": "max 8 words on screen", "voiceover": "max 25 words spoken", "duration": 3.5}}
  ],
  "hashtags": "#vaultmind #facts #shorts #fyp #didyouknow"
}}
Rules: 5-6 scenes, hook first, CTA last, real facts only."""

    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
        json={"model": "llama-3.3-70b-versatile",
              "messages": [{"role": "user", "content": prompt}],
              "max_tokens": 800, "temperature": 0.7},
        timeout=30)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    raw = raw.replace("```json", "").replace("```", "").strip()
    s = raw.find("{")
    e = raw.rfind("}") + 1
    data = json.loads(raw[s:e])
    log.info(f"   ✅ '{data['title']}'")
    return data

# ── STEP 2: Download Pexels PHOTOS ────────────────────────────
def download_photos(topic, n_scenes, work_dir):
    """Download one photo per scene from Pexels."""
    log.info(f"📸 Downloading {n_scenes} photos for '{topic}'...")
    photos = []

    # Search Pexels Photos API (not videos)
    resp = requests.get(
        "https://api.pexels.com/v1/search",
        headers={"Authorization": PEXELS_KEY},
        params={"query": topic, "per_page": min(n_scenes + 3, 15),
                "orientation": "portrait"},
        timeout=15)

    results = resp.json().get("photos", [])

    # Also try landscape if not enough
    if len(results) < n_scenes:
        resp2 = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": topic, "per_page": 15},
            timeout=15)
        results += resp2.json().get("photos", [])

    if not results:
        log.warning(f"   ⚠️ No photos found for '{topic}', using fallback")
        return []

    for i in range(n_scenes):
        photo = results[i % len(results)]
        # Use medium size (fit for mobile)
        url = photo["src"].get("large", photo["src"]["original"])
        path = work_dir / f"photo_{i:02d}.jpg"
        r = requests.get(url, stream=True, timeout=30)
        if r.status_code == 200:
            with open(path, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            log.info(f"   ✅ Photo {i}: {path.stat().st_size//1024}KB")
            photos.append(path)
        else:
            log.warning(f"   ⚠️ Photo {i} download failed: {r.status_code}")
            photos.append(None)

    return photos

# ── STEP 3: Generate voiceover (espeak — free, offline) ──────
def generate_voiceover(scenes, work_dir):
    log.info("🎙️  Generating voiceover with espeak...")

    # Try ElevenLabs first, fall back to espeak
    eleven_key = os.environ.get("ELEVENLABS_KEY", "")
    audio_files = []

    if eleven_key:
        voice_id = "21m00Tcm4TlvDq8ikWAM"
        for i, scene in enumerate(scenes):
            try:
                resp = requests.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                    headers={"xi-api-key": eleven_key, "Content-Type": "application/json"},
                    json={"text": scene["voiceover"], "model_id": "eleven_turbo_v2_5",
                          "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
                    timeout=30)
                if resp.status_code == 200 and len(resp.content) > 500:
                    path = work_dir / f"voice_{i:02d}.mp3"
                    path.write_bytes(resp.content)
                    audio_files.append(path)
                    log.info(f"   ✅ ElevenLabs voice {i}: {len(resp.content)//1024}KB")
                else:
                    log.warning(f"   ⚠️ ElevenLabs {resp.status_code}, switching to espeak")
                    audio_files = []
                    break
            except Exception as e:
                log.warning(f"   ⚠️ ElevenLabs error: {e}, switching to espeak")
                audio_files = []
                break

    # espeak fallback (or primary if no ElevenLabs key)
    if not audio_files:
        log.info("   🔄 Using espeak (free offline TTS)...")
        for i, scene in enumerate(scenes):
            wav_path = work_dir / f"voice_{i:02d}.wav"
            mp3_path = work_dir / f"voice_{i:02d}.mp3"
            # Generate WAV with espeak
            r1 = subprocess.run(
                ["espeak", "-w", str(wav_path), "-s", "145", "-p", "50", scene["voiceover"]],
                capture_output=True, text=True)
            if r1.returncode != 0 or not wav_path.exists():
                log.error(f"   ❌ espeak scene {i} failed: {r1.stderr[:100]}")
                continue
            # Convert WAV to MP3
            r2 = subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav_path),
                 "-c:a", "libmp3lame", "-q:a", "4", str(mp3_path)],
                capture_output=True, text=True)
            wav_path.unlink(missing_ok=True)
            if r2.returncode == 0 and mp3_path.exists():
                audio_files.append(mp3_path)
                log.info(f"   ✅ espeak voice {i}: {mp3_path.stat().st_size//1024}KB")
            else:
                log.error(f"   ❌ espeak mp3 {i} failed")

    if not audio_files:
        log.error("   ❌ No audio generated!")
        return None

    final = Path("/tmp/final_voice.mp3")
    if len(audio_files) == 1:
        shutil.copy(str(audio_files[0]), str(final))
    else:
        concat_list = work_dir / "audio_list.txt"
        concat_list.write_text("\n".join(f"file '{p.resolve()}'" for p in audio_files))
        r = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat_list), "-c", "copy", str(final)],
            capture_output=True, text=True)
        if r.returncode != 0 or not final.exists():
            log.error(f"   ❌ concat failed, using first")
            shutil.copy(str(audio_files[0]), str(final))

    log.info(f"   ✅ Voiceover ready: {final.stat().st_size//1024}KB")
    return final

# ── STEP 4: Create scene videos (photo + text overlay) ────────
def create_scene_video(scene_idx, scene, photo_path, work_dir):
    """Create one scene: photo background + animated text overlay."""
    accent = PALETTES[scene_idx % len(PALETTES)]
    n_frames = int(scene["duration"] * FPS)
    frames_dir = work_dir / f"sc{scene_idx}_frames"
    frames_dir.mkdir()

    font_main  = get_font(68)
    font_label = get_font(40)

    # Load and resize photo
    if photo_path and photo_path.exists():
        try:
            bg_img = Image.open(photo_path).convert("RGB")
            # Crop to 9:16
            orig_w, orig_h = bg_img.size
            target_ratio = W / H
            orig_ratio = orig_w / orig_h
            if orig_ratio > target_ratio:
                new_w = int(orig_h * target_ratio)
                offset = (orig_w - new_w) // 2
                bg_img = bg_img.crop((offset, 0, offset + new_w, orig_h))
            else:
                new_h = int(orig_w / target_ratio)
                offset = (orig_h - new_h) // 2
                bg_img = bg_img.crop((0, offset, orig_w, offset + new_h))
            bg_img = bg_img.resize((W, H), Image.LANCZOS)
        except Exception as e:
            log.warning(f"   ⚠️ Photo load failed: {e}, using dark bg")
            bg_img = Image.new("RGB", (W, H), (8, 12, 20))
    else:
        bg_img = Image.new("RGB", (W, H), (8, 12, 20))

    label = "FOLLOW FOR MORE" if scene_idx == -1 else \
            "DID YOU KNOW?" if scene_idx == 0 else f"FACT #{scene_idx}"

    for f in range(n_frames):
        t = f / max(n_frames - 1, 1)
        slide = min(1.0, t * 4) ** 0.4

        img = bg_img.copy()
        draw = ImageDraw.Draw(img)

        # Dark gradient overlay top
        for y in range(200):
            alpha = int(180 * (1 - y / 200))
            r_col = max(0, 8 - alpha // 20)
            draw.line([(0, y), (W, y)], fill=(r_col, r_col + 4, r_col + 12))

        # Accent top bar
        draw.rectangle([0, 0, W, 10], fill=accent)

        # Label
        lb = draw.textbbox((0, 0), label, font=font_label)
        lw = lb[2] - lb[0]
        draw.text(((W - lw) // 2 + 2, 25), label, font=font_label, fill=(0, 0, 0))
        draw.text(((W - lw) // 2, 23), label, font=font_label, fill=accent)

        # Dark bottom overlay for text
        for y in range(H - 520, H):
            alpha = min(220, int(220 * (y - (H - 520)) / 520))
            draw.line([(0, y), (W, y)], fill=(5, 5, 15))

        # Main text with slide-up animation
        wrapped = textwrap.fill(scene["text"], width=14)
        lines = wrapped.split("\n")
        base_y = H - 440 + int((1 - slide) * 60)

        for li, line in enumerate(lines):
            lb2 = draw.textbbox((0, 0), line, font=font_main)
            lw2 = lb2[2] - lb2[0]
            lx = (W - lw2) // 2
            ly = base_y + li * 82
            # Shadow
            draw.text((lx + 3, ly + 3), line, font=font_main, fill=(0, 0, 0))
            draw.text((lx, ly), line, font=font_main, fill=(255, 255, 255))

        # Accent underline
        line_w = int(220 * slide)
        draw.rectangle([(W // 2 - line_w, H - 170),
                         (W // 2 + line_w, H - 163)], fill=accent)

        # Progress bar
        prog = (scene_idx + t) / max(6, scene_idx + 1)
        bw = int(W * min(prog, 1.0))
        draw.rectangle([0, H - 10, bw, H], fill=accent)
        draw.rectangle([bw, H - 10, W, H], fill=(25, 25, 35))

        img.save(frames_dir / f"frame_{f:04d}.png")

    # Render scene to mp4
    out_path = work_dir / f"scene_{scene_idx:02d}.mp4"
    r = subprocess.run([
        "ffmpeg", "-y", "-framerate", str(FPS),
        "-i", str(frames_dir / "frame_%04d.png"),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(out_path)
    ], capture_output=True, text=True)

    shutil.rmtree(frames_dir)

    if r.returncode == 0 and out_path.exists():
        log.info(f"   ✅ scene {scene_idx}: {out_path.stat().st_size // 1024}KB")
        return out_path
    else:
        log.error(f"   ❌ scene {scene_idx} failed: {r.stderr[-150:]}")
        return None

# ── STEP 5: Assemble final video ──────────────────────────────
def assemble_video(script, photos, voiceover, work_dir, output):
    log.info("🎞️  Assembling video...")
    total_dur = sum(s["duration"] for s in script["scenes"])

    if voiceover is None or not voiceover.exists():
        log.error("   ❌ No voiceover!")
        return total_dur

    # Create each scene video
    scene_videos = []
    for si, scene in enumerate(script["scenes"]):
        photo = photos[si] if si < len(photos) else None
        sv = create_scene_video(si, scene, photo, work_dir)
        if sv:
            scene_videos.append(sv)

    if not scene_videos:
        log.error("   ❌ No scene videos!")
        return total_dur

    log.info(f"   ✅ {len(scene_videos)} scene videos created")

    # Concat all scenes
    concat_list = work_dir / "scenes_list.txt"
    concat_list.write_text("\n".join(f"file '{p.resolve()}'" for p in scene_videos))
    concat_video = work_dir / "concat.mp4"

    r1 = subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p", str(concat_video)
    ], capture_output=True, text=True)

    if r1.returncode != 0 or not concat_video.exists():
        log.error(f"   ❌ scene concat failed: {r1.stderr[-200:]}")
        return total_dur

    log.info(f"   ✅ concat: {concat_video.stat().st_size // 1024}KB")

    # Add audio
    r2 = subprocess.run([
        "ffmpeg", "-y",
        "-i", str(concat_video),
        "-i", str(voiceover),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k",
        "-t", str(total_dur),
        "-movflags", "+faststart",
        str(output)
    ], capture_output=True, text=True)

    if r2.returncode != 0:
        log.error(f"   ❌ audio merge failed: {r2.stderr[-200:]}")
    else:
        size = output.stat().st_size / 1024 / 1024 if output.exists() else 0
        log.info(f"   ✅ {output.name} ({size:.1f}MB, {total_dur:.1f}s)")

    return total_dur

# ── STEP 6: YouTube upload ────────────────────────────────────
def upload_youtube(video_path, title, description, hashtags, publish_at):
    log.info(f"📺 YouTube -> {publish_at.strftime('%a %d %b %H:%M')}")
    try:
        import pickle, base64
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google.auth.transport.requests import Request

        token_b64 = os.environ.get("YOUTUBE_TOKEN_B64", "")
        if not token_b64:
            log.warning("   ⚠️ YOUTUBE_TOKEN_B64 not set")
            return None

        creds = pickle.loads(base64.b64decode(token_b64))
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())

        yt = build("youtube", "v3", credentials=creds)
        body = {
            "snippet": {
                "title": title[:100],
                "description": description + "\n\n" + hashtags + "\n\n#Shorts",
                "tags": [t.replace("#", "") for t in hashtags.split()],
                "categoryId": "22",
            },
            "status": {
                "privacyStatus": "private",
                "publishAt": publish_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "selfDeclaredMadeForKids": False,
            }
        }
        media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
        req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            _, response = req.next_chunk()
        vid = response["id"]
        log.info(f"   ✅ Uploaded! youtu.be/{vid}")
        return f"https://youtube.com/shorts/{vid}"
    except Exception as e:
        log.error(f"   ❌ YouTube: {e}")
        return None

# ── MAIN ──────────────────────────────────────────────────────
def run_pipeline(n_videos=1):
    log.info("=" * 55)
    log.info("🚀 VAULTMIND PIPELINE")
    log.info("=" * 55)
    OUTPUT_DIR.mkdir(exist_ok=True)
    slots = get_next_slots(n_videos * 2)
    yt_slots = slots[0::2][:n_videos]
    tt_slots = slots[1::2][:n_videos]
    results = []

    for i in range(n_videos):
        log.info(f"\n{'─'*55}\n  VIDEO {i+1}/{n_videos}\n{'─'*55}")
        ts = int(time.time())
        work_dir = Path(f"/tmp/pipeline_{ts}_{i}")
        work_dir.mkdir(parents=True)
        output = Path(f"/tmp/video_{ts}.mp4")
        voice_file = Path("/tmp/final_voice.mp3")

        try:
            script    = generate_script()
            n_scenes  = len(script["scenes"])
            photos    = download_photos(script["topic"], n_scenes, work_dir)
            voiceover = generate_voiceover(script["scenes"], work_dir)
            duration  = assemble_video(script, photos, voiceover, work_dir, output)

            yt_time = yt_slots[i] if i < len(yt_slots) else datetime.now() + timedelta(hours=1)
            tt_time = tt_slots[i] if i < len(tt_slots) else datetime.now() + timedelta(hours=2)

            if output.exists() and output.stat().st_size > 10000:
                yt_url = upload_youtube(output, script["title"],
                                        script.get("description", ""),
                                        script["hashtags"], yt_time)
            else:
                log.error("   ❌ Video missing/too small, skipping upload")
                yt_url = None

            entry = {
                "id": ts, "title": script["title"], "duration": duration,
                "created_at": datetime.now().isoformat(),
                "youtube": {"scheduled": yt_time.isoformat(), "url": yt_url},
                "tiktok":  {"scheduled": tt_time.isoformat()},
                "hashtags": script["hashtags"], "status": "scheduled",
            }
            add_to_dashboard(entry)
            results.append(entry)
            log.info(f"✅ Video {i+1} done! YT: {yt_time.strftime('%a %d %b %H:%M')}")

        except Exception as e:
            import traceback
            log.error(f"❌ Video {i+1} failed: {e}")
            log.error(traceback.format_exc())
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
            if voice_file.exists():
                voice_file.unlink()

        if i < n_videos - 1:
            time.sleep(3)

    log.info(f"\n{'='*55}")
    log.info(f"🎉 DONE — {len(results)}/{n_videos} videos scheduled")
    log.info(f"{'='*55}")
    return results

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    run_pipeline(n)
