"""
╔══════════════════════════════════════════════════════════╗
║   🎬 COMPLETE AUTO VIDEO PIPELINE                        ║
║   TikTok + YouTube Shorts — 100% Automatic               ║
║                                                          ║
║   Generates → Schedules → Uploads                        ║
║   Even when your PC is OFF (via Railway/Render cloud)    ║
╚══════════════════════════════════════════════════════════╝

WHAT THIS DOES:
  1. Generates script via Claude API
  2. Downloads stock footage via Pexels API
  3. Creates AI voiceover via ElevenLabs
  4. Assembles 9:16 vertical video with ffmpeg
  5. Schedules YouTube Shorts (official API, auto-uploads)
  6. Schedules TikTok via Buffer API (uploads when PC is OFF)
  7. Logs everything to dashboard.json for the web dashboard

SETUP:
  pip install requests pillow google-auth google-auth-oauthlib google-api-python-client

SET YOUR KEYS in .env file or environment variables.
"""

import os, sys, json, time, math, textwrap
import subprocess, shutil, logging
import requests
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

# ══════════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("pipeline.log")
    ]
)
log = logging.getLogger("pipeline")

# ══════════════════════════════════════════════════════════════
#  CONFIG — load from .env or environment
# ══════════════════════════════════════════════════════════════
def load_env():
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

load_env()

CLAUDE_KEY      = os.environ.get("CLAUDE_API_KEY", "")
ELEVEN_KEY      = os.environ.get("ELEVENLABS_KEY", "")
PEXELS_KEY      = os.environ.get("PEXELS_KEY", "")
BUFFER_KEY      = os.environ.get("BUFFER_ACCESS_TOKEN", "")
BUFFER_PROFILE  = os.environ.get("BUFFER_TIKTOK_PROFILE_ID", "")
YT_CLIENT_ID    = os.environ.get("YOUTUBE_CLIENT_ID", "")
YT_CLIENT_SEC   = os.environ.get("YOUTUBE_CLIENT_SECRET", "")

VOICE_ID        = "21m00Tcm4TlvDq8ikWAM"   # ElevenLabs Rachel
NICHE           = os.environ.get("NICHE", "Interesting Facts")
OUTPUT_DIR      = Path("./output_videos")
DASHBOARD_FILE  = Path("./dashboard.json")
W, H, FPS       = 1080, 1920, 30

# ── Best upload times ─────────────────────────────────────────
BEST_TIMES = {
    "monday":    ["07:00", "12:00", "19:00"],
    "tuesday":   ["07:00", "12:00", "19:00"],
    "wednesday": ["07:00", "14:00", "21:00"],
    "thursday":  ["07:00", "12:00", "19:00"],
    "friday":    ["07:00", "14:00", "20:00"],
    "saturday":  ["09:00", "13:00", "20:00"],
    "sunday":    ["10:00", "15:00", "20:00"],
}

# ══════════════════════════════════════════════════════════════
#  DASHBOARD — track all videos
# ══════════════════════════════════════════════════════════════
def load_dashboard():
    if DASHBOARD_FILE.exists():
        return json.loads(DASHBOARD_FILE.read_text())
    return {"videos": [], "stats": {"generated": 0, "uploaded_yt": 0, "uploaded_tt": 0}}

def save_dashboard(data):
    DASHBOARD_FILE.write_text(json.dumps(data, indent=2, default=str))

def add_to_dashboard(entry):
    data = load_dashboard()
    data["videos"].insert(0, entry)
    data["stats"]["generated"] += 1
    save_dashboard(data)


# ══════════════════════════════════════════════════════════════
#  SCHEDULER
# ══════════════════════════════════════════════════════════════
def get_next_slots(n=14):
    slots = []
    now = datetime.now()
    buffer_time = now + timedelta(minutes=45)
    for day_offset in range(21):
        date = now + timedelta(days=day_offset)
        weekday = date.strftime("%A").lower()
        for t in BEST_TIMES.get(weekday, ["12:00"]):
            h, m = map(int, t.split(":"))
            slot = date.replace(hour=h, minute=m, second=0, microsecond=0)
            if slot > buffer_time:
                slots.append(slot)
            if len(slots) >= n:
                return slots
    return slots


# ══════════════════════════════════════════════════════════════
#  STEP 1 — SCRIPT GENERATION
# ══════════════════════════════════════════════════════════════
def generate_script():
    log.info("🤖 Generating script via Claude...")
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": CLAUDE_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        },
        json={
            "model": "claude-opus-4-5",
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": f"""
You are a viral TikTok/YouTube Shorts scriptwriter. Niche: {NICHE}.
Pick a surprising, counterintuitive, or mind-blowing fact topic.

Return ONLY valid JSON (no markdown):
{{
  "topic": "1-2 word Pexels search term (e.g. 'deep ocean' or 'ancient rome')",
  "title": "viral video title max 60 chars with power word or number",
  "description": "2-sentence YouTube description",
  "scenes": [
    {{
      "text": "on-screen text max 10 words",
      "voiceover": "spoken 1-2 sentences",
      "duration": 3.5
    }}
  ],
  "hashtags": "#facts #didyouknow #mindblowing #shorts #learnontiktok #fyp"
}}

Rules:
- 5-7 scenes, ~20-25 seconds total
- Scene 1: shocking hook question
- Scene 2-5: build the fact with context
- Last scene: "Follow for more" CTA
- Facts must be 100% real and verifiable
"""}]
        }, timeout=30
    )
    resp.raise_for_status()
    text = resp.json()["content"][0]["text"]
    text = text.replace("```json","").replace("```","").strip()
    data = json.loads(text)
    log.info(f"   ✅ Script: '{data['title']}'")
    return data


# ══════════════════════════════════════════════════════════════
#  STEP 2 — FOOTAGE DOWNLOAD
# ══════════════════════════════════════════════════════════════
def download_footage(topic, work_dir):
    log.info(f"🎬 Downloading footage: '{topic}'...")
    for orientation in ["portrait", "landscape"]:
        resp = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": topic, "per_page": 3, "orientation": orientation},
            timeout=15
        )
        videos = resp.json().get("videos", [])
        if videos:
            break

    files = []
    for i, v in enumerate(videos[:2]):
        best = sorted(v["video_files"], key=lambda x: x.get("width", 0), reverse=True)[0]
        path = work_dir / f"footage_{i}.mp4"
        r = requests.get(best["link"], stream=True, timeout=60)
        with open(path, "wb") as f:
            for chunk in r.iter_content(8192): f.write(chunk)
        files.append(path)
        log.info(f"   ✅ Clip {i+1} downloaded")
    return files


# ══════════════════════════════════════════════════════════════
#  STEP 3 — AI VOICEOVER
# ══════════════════════════════════════════════════════════════
def generate_voiceover(scenes, work_dir):
    log.info("🎙️  Generating ElevenLabs voiceover...")
    audio_files = []
    for i, scene in enumerate(scenes):
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}",
            headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"},
            json={
                "text": scene["voiceover"],
                "model_id": "eleven_monolingual_v1",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
            }, timeout=30
        )
        path = work_dir / f"voice_{i:02d}.mp3"
        path.write_bytes(resp.content)
        audio_files.append(path)

    concat_list = work_dir / "audio_list.txt"
    concat_list.write_text("\n".join(f"file '{p.resolve()}'" for p in audio_files))
    final = work_dir / "final_voice.mp3"
    subprocess.run(
        ["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat_list),"-c","copy",str(final)],
        capture_output=True
    )
    log.info("   ✅ Voiceover merged")
    return final


# ══════════════════════════════════════════════════════════════
#  STEP 4 — TEXT OVERLAYS
# ══════════════════════════════════════════════════════════════
def get_font(size, bold=True):
    paths = [
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans-{'Bold' if bold else ''}.ttf",
        f"/usr/share/fonts/truetype/liberation/LiberationSans-{'Bold' if bold else 'Regular'}.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

PALETTES = [
    ((255,215,0),  (8,12,20)),
    ((138,92,255), (10,8,25)),
    ((46,213,115), (5,20,10)),
    ((255,71,87),  (20,5,5)),
    ((30,200,255), (5,10,20)),
    ((255,165,0),  (20,12,0)),
]

def create_overlays(scenes, work_dir):
    log.info("🖼️  Creating text overlays...")
    overlays = []
    for si, scene in enumerate(scenes):
        accent, bg_hint = PALETTES[si % len(PALETTES)]
        n_frames = int(scene["duration"] * FPS)
        frames_dir = work_dir / f"sc{si}_frames"
        frames_dir.mkdir()
        font_main  = get_font(84)
        font_label = get_font(50)
        label = "FOLLOW FOR MORE 👆" if si == len(scenes)-1 else \
                "DID YOU KNOW? 🤯"   if si == 0 else f"FACT #{si}"

        for f in range(n_frames):
            t = f / max(n_frames-1, 1)
            slide = min(1.0, t*4)**0.4
            img  = Image.new("RGBA", (W,H), (0,0,0,0))
            draw = ImageDraw.Draw(img)

            # Top accent bar
            Image.new("RGBA",(W,220),(*accent,200)).paste
            bar = Image.new("RGBA",(W,220),(*accent,200))
            img.paste(bar,(0,0),bar)

            # Bottom dark overlay
            bot = Image.new("RGBA",(W,650),(5,5,15,190))
            img.paste(bot,(0,H-650),bot)

            # Label
            lb = draw.textbbox((0,0),label,font=font_label)
            lw = lb[2]-lb[0]
            draw.text(((W-lw)//2+2,82),label,font=font_label,fill=(0,0,0,200))
            draw.text(((W-lw)//2,80),label,font=font_label,fill=(10,10,10,255))

            # Main text with slide animation
            lines = textwrap.fill(scene["text"], width=16).split("\n")
            base_y = H-580+int((1-slide)*80)
            for li, line in enumerate(lines):
                lb2 = draw.textbbox((0,0),line,font=font_main)
                lw2 = lb2[2]-lb2[0]
                lx = (W-lw2)//2
                ly = base_y + li*96
                draw.text((lx+4,ly+4),line,font=font_main,fill=(0,0,0,180))
                draw.text((lx,ly),line,font=font_main,fill=(255,255,255,255))

            # Accent underline
            lw3 = int(280*slide)
            draw.rectangle([(W//2-lw3,H-235),(W//2+lw3,H-227)],fill=(*accent,255))

            # Progress bar
            prog = (si+t)/len(scenes)
            bw = int(W*prog)
            draw.rectangle([0,H-14,bw,H],fill=(*accent,255))
            draw.rectangle([bw,H-14,W,H],fill=(30,30,40,200))

            img.save(frames_dir/f"frame_{f:04d}.png")

        ov = work_dir/f"overlay_{si:02d}.mp4"
        subprocess.run([
            "ffmpeg","-y","-framerate",str(FPS),
            "-i",str(frames_dir/"frame_%04d.png"),
            "-c:v","libx264","-preset","fast","-crf","18","-pix_fmt","yuva420p",str(ov)
        ], capture_output=True)
        shutil.rmtree(frames_dir)
        overlays.append(ov)
    log.info(f"   ✅ {len(overlays)} overlays done")
    return overlays


# ══════════════════════════════════════════════════════════════
#  STEP 5 — ASSEMBLE VIDEO
# ══════════════════════════════════════════════════════════════
def assemble_video(script, footage, voiceover, overlays, work_dir, output):
    log.info("🎞️  Assembling final video...")
    total_dur = sum(s["duration"] for s in script["scenes"])

    bg = work_dir/"bg.mp4"
    if footage:
        fl = work_dir/"footage_list.txt"
        reps = math.ceil(total_dur/5)+2
        fl.write_text("\n".join(
            f"file '{p.resolve()}'" for _ in range(reps) for p in footage
        ))
        subprocess.run([
            "ffmpeg","-y","-f","concat","-safe","0","-i",str(fl),
            "-t",str(total_dur),
            "-vf",f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setfps={FPS}",
            "-c:v","libx264","-preset","fast","-crf","23","-an",str(bg)
        ], capture_output=True)
    else:
        subprocess.run([
            "ffmpeg","-y","-f","lavfi",
            "-i",f"color=c=0x080C14:size={W}x{H}:rate={FPS}:duration={total_dur}",
            "-c:v","libx264",str(bg)
        ], capture_output=True)

    ol_list = work_dir/"ol_list.txt"
    ol_list.write_text("\n".join(f"file '{p.resolve()}'" for p in overlays))
    ol_concat = work_dir/"overlays.mp4"
    subprocess.run([
        "ffmpeg","-y","-f","concat","-safe","0","-i",str(ol_list),
        "-c:v","libx264","-preset","fast","-crf","18","-pix_fmt","yuva420p",str(ol_concat)
    ], capture_output=True)

    subprocess.run([
        "ffmpeg","-y",
        "-i",str(bg),"-i",str(ol_concat),"-i",str(voiceover),
        "-filter_complex",
        "[0:v]eq=brightness=-0.15:saturation=0.7[bg];[bg][1:v]overlay=0:0[comp]",
        "-map","[comp]","-map","2:a",
        "-c:v","libx264","-preset","fast","-crf","20","-pix_fmt","yuv420p",
        "-c:a","aac","-b:a","192k","-t",str(total_dur),"-movflags","+faststart",
        str(output)
    ], capture_output=True)

    size = output.stat().st_size/1024/1024
    log.info(f"   ✅ {output.name} ({size:.1f}MB, {total_dur:.1f}s)")
    return total_dur


# ══════════════════════════════════════════════════════════════
#  STEP 6 — YOUTUBE SCHEDULED UPLOAD
# ══════════════════════════════════════════════════════════════
def upload_youtube(video_path, title, description, hashtags, publish_at):
    log.info(f"📺 Scheduling YouTube → {publish_at.strftime('%a %d %b %H:%M')}")
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        import pickle

        token_file = Path("youtube_token.pickle")
        creds = None
        if token_file.exists():
            creds = pickle.loads(token_file.read_bytes())
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                secrets = {
                    "installed": {
                        "client_id": YT_CLIENT_ID,
                        "client_secret": YT_CLIENT_SEC,
                        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob"],
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token"
                    }
                }
                Path("client_secrets.json").write_text(json.dumps(secrets))
                flow = InstalledAppFlow.from_client_secrets_file(
                    "client_secrets.json",
                    ["https://www.googleapis.com/auth/youtube.upload"]
                )
                creds = flow.run_local_server(port=0)
            token_file.write_bytes(pickle.dumps(creds))

        yt = build("youtube", "v3", credentials=creds)
        body = {
            "snippet": {
                "title": title[:100],
                "description": f"{description}\n\n{hashtags}\n\n#Shorts",
                "tags": [t.replace("#","") for t in hashtags.split()],
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
        video_id = response["id"]
        log.info(f"   ✅ YouTube scheduled! youtu.be/{video_id}")
        return f"https://youtube.com/shorts/{video_id}"

    except ImportError:
        log.warning("   ⚠️  Install: pip install google-auth google-auth-oauthlib google-api-python-client")
        return None
    except Exception as e:
        log.error(f"   ❌ YouTube error: {e}")
        return None


# ══════════════════════════════════════════════════════════════
#  STEP 7 — TIKTOK VIA BUFFER API (works when PC is OFF)
# ══════════════════════════════════════════════════════════════
def upload_buffer_tiktok(video_path, title, hashtags, publish_at):
    """
    Buffer API — schedules TikTok upload from their cloud servers.
    Your PC does NOT need to be on. Buffer handles the upload.

    Get your Buffer Access Token: buffer.com/app/account/apps
    Get Profile ID via: GET https://api.bufferapp.com/1/profiles.json
    """
    log.info(f"📱 Scheduling TikTok via Buffer → {publish_at.strftime('%a %d %b %H:%M')}")

    if not BUFFER_KEY or not BUFFER_PROFILE:
        log.warning("   ⚠️  BUFFER_ACCESS_TOKEN or BUFFER_TIKTOK_PROFILE_ID not set")
        log.info("   📋 Get token: buffer.com/app/account/apps")
        log.info("   📋 Get profile ID: api.bufferapp.com/1/profiles.json?access_token=YOUR_TOKEN")
        return None

    try:
        # Step 1: Upload video to Buffer
        with open(video_path, "rb") as f:
            upload_resp = requests.post(
                "https://api.bufferapp.com/1/media/upload.json",
                params={"access_token": BUFFER_KEY},
                files={"file": (video_path.name, f, "video/mp4")},
                timeout=120
            )

        if upload_resp.status_code != 200:
            log.error(f"   ❌ Buffer upload failed: {upload_resp.text[:200]}")
            return None

        media_id = upload_resp.json().get("id")
        log.info(f"   ✅ Video uploaded to Buffer (media_id: {media_id})")

        # Step 2: Schedule the post
        caption = f"{title}\n\n{hashtags}"
        scheduled_ts = int(publish_at.timestamp())

        post_resp = requests.post(
            "https://api.bufferapp.com/1/updates/create.json",
            params={"access_token": BUFFER_KEY},
            data={
                "profile_ids[]": BUFFER_PROFILE,
                "text": caption[:2200],
                "media[video]": media_id,
                "scheduled_at": scheduled_ts,
                "shorten": False,
            },
            timeout=30
        )

        if post_resp.status_code == 200:
            update_id = post_resp.json().get("updates", [{}])[0].get("id")
            log.info(f"   ✅ TikTok scheduled via Buffer! (update_id: {update_id})")
            log.info(f"   ⏰ Goes live: {publish_at.strftime('%A %d %b at %H:%M')}")
            return update_id
        else:
            log.error(f"   ❌ Buffer scheduling failed: {post_resp.text[:200]}")
            return None

    except Exception as e:
        log.error(f"   ❌ Buffer error: {e}")
        return None


# ══════════════════════════════════════════════════════════════
#  MAIN PIPELINE
# ══════════════════════════════════════════════════════════════
def run_pipeline(n_videos=1):
    log.info("=" * 55)
    log.info("🚀 AUTO VIDEO PIPELINE STARTING")
    log.info("=" * 55)

    OUTPUT_DIR.mkdir(exist_ok=True)
    slots = get_next_slots(n_videos * 2)
    yt_slots = slots[0::2][:n_videos]
    tt_slots = slots[1::2][:n_videos]

    log.info(f"\n📅 Schedule for {n_videos} video(s):")
    for i in range(n_videos):
        yt = yt_slots[i].strftime('%a %d %b %H:%M') if i < len(yt_slots) else "TBD"
        tt = tt_slots[i].strftime('%a %d %b %H:%M') if i < len(tt_slots) else "TBD"
        log.info(f"  Video {i+1}: YouTube→{yt} | TikTok→{tt}")

    results = []

    for i in range(n_videos):
        log.info(f"\n{'─'*55}")
        log.info(f"  VIDEO {i+1}/{n_videos}")
        log.info(f"{'─'*55}")

        ts = int(time.time())
        work_dir = Path(f"/tmp/pipeline_{ts}_{i}")
        work_dir.mkdir(parents=True)
        output = OUTPUT_DIR / f"video_{ts}.mp4"

        try:
            script    = generate_script()
            footage   = download_footage(script["topic"], work_dir)
            voiceover = generate_voiceover(script["scenes"], work_dir)
            overlays  = create_overlays(script["scenes"], work_dir)
            duration  = assemble_video(script, footage, voiceover, overlays, work_dir, output)

            yt_time = yt_slots[i] if i < len(yt_slots) else datetime.now()+timedelta(hours=1)
            tt_time = tt_slots[i] if i < len(tt_slots) else datetime.now()+timedelta(hours=2)

            yt_url    = upload_youtube(output, script["title"], script["description"],
                                       script["hashtags"], yt_time)
            tt_update = upload_buffer_tiktok(output, script["title"],
                                             script["hashtags"], tt_time)

            entry = {
                "id": ts,
                "title": script["title"],
                "file": str(output),
                "duration": duration,
                "created_at": datetime.now().isoformat(),
                "youtube": {"scheduled": yt_time.isoformat(), "url": yt_url},
                "tiktok":  {"scheduled": tt_time.isoformat(), "buffer_id": tt_update},
                "hashtags": script["hashtags"],
                "status": "scheduled"
            }
            add_to_dashboard(entry)
            results.append(entry)

            log.info(f"\n✅ Video {i+1} complete!")

        except Exception as e:
            log.error(f"❌ Video {i+1} failed: {e}")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

        if i < n_videos - 1:
            time.sleep(2)

    log.info(f"\n{'='*55}")
    log.info(f"🎉 PIPELINE DONE — {len(results)}/{n_videos} videos scheduled")
    log.info(f"📁 Videos: {OUTPUT_DIR}/")
    log.info(f"📊 Dashboard: dashboard.json")
    log.info(f"{'='*55}")
    return results


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    run_pipeline(n)
