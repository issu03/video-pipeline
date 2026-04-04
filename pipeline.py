"""
🎬 AUTO VIDEO PIPELINE — TikTok + YouTube Shorts
Groq → Pexels → ElevenLabs → ffmpeg → Scheduled Upload
"""

import os, sys, json, time, math, textwrap
import subprocess, shutil, logging
import requests
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

GROQ_KEY       = os.environ.get("GROQ_API_KEY", "")
ELEVEN_KEY     = os.environ.get("ELEVENLABS_KEY", "")
PEXELS_KEY     = os.environ.get("PEXELS_KEY", "")
BUFFER_KEY     = os.environ.get("BUFFER_ACCESS_TOKEN", "")
BUFFER_PROFILE = os.environ.get("BUFFER_TIKTOK_PROFILE_ID", "")
YT_CLIENT_ID   = os.environ.get("YOUTUBE_CLIENT_ID", "")
YT_CLIENT_SEC  = os.environ.get("YOUTUBE_CLIENT_SECRET", "")
VOICE_ID       = "21m00Tcm4TlvDq8ikWAM"
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
import random
NICHE = os.environ.get("NICHE", random.choice(NICHES))
OUTPUT_DIR     = Path("./output_videos")
DASHBOARD_FILE = Path("./dashboard.json")
W, H, FPS      = 1080, 1920, 30

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
    return {"videos": [], "stats": {"generated": 0, "yt": 0, "tt": 0}}

def save_dashboard(data):
    DASHBOARD_FILE.write_text(json.dumps(data, indent=2, default=str))

def add_to_dashboard(entry):
    data = load_dashboard()
    data["videos"].insert(0, entry)
    data["stats"]["generated"] += 1
    save_dashboard(data)

def generate_script():
    log.info("🤖 Generating script via Groq...")
    prompt = f"""You are a viral TikTok/YouTube Shorts scriptwriter for the channel VaultMind.
VaultMind covers: facts, unethical money tips, dating fails, business ideas, what-if scenarios, crazy true stories, psychology, life hacks.
Current niche for this video: {NICHE}

Pick a specific, surprising, scroll-stopping topic within this niche.
Return ONLY valid JSON (no markdown):
{{
  "topic": "1-2 word Pexels search term that matches the video visually",
  "title": "viral video title max 60 chars — use power words, numbers, or curiosity gaps",
  "description": "2-sentence YouTube description with keywords",
  "scenes": [
    {{"text": "on-screen text max 10 words", "voiceover": "spoken 1-2 sentences", "duration": 3.5}}
  ],
  "hashtags": "#vaultmind #facts #shorts #fyp #didyouknow #mindblowing #storytime #money #dating #business"
}}
Rules:
- 5-7 scenes, ~20-25 seconds total
- Scene 1: shocking hook that stops the scroll
- Scene 2-5: deliver the content with energy
- Last scene: Follow VaultMind for more
- Content must be real, specific, and surprising
- Adapt tone to niche: edgy for money/dating, curious for facts, dramatic for stories"""

    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
        json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}],
              "max_tokens": 1000, "temperature": 0.7},
        timeout=30
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    text = text.replace("```json","").replace("```","").strip()
    data = json.loads(text)
    log.info(f"   ✅ Script: '{data['title']}'")
    return data

def download_footage(topic, work_dir):
    log.info(f"🎬 Downloading footage: '{topic}'...")
    for orientation in ["portrait", "landscape"]:
        resp = requests.get("https://api.pexels.com/videos/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": topic, "per_page": 3, "orientation": orientation}, timeout=15)
        videos = resp.json().get("videos", [])
        if videos: break
    files = []
    for i, v in enumerate(videos[:2]):
        best = sorted(v["video_files"], key=lambda x: x.get("width",0), reverse=True)[0]
        path = work_dir / f"footage_{i}.mp4"
        r = requests.get(best["link"], stream=True, timeout=60)
        with open(path, "wb") as f:
            for chunk in r.iter_content(8192): f.write(chunk)
        files.append(path)
        log.info(f"   ✅ Clip {i+1} downloaded")
    return files

def generate_voiceover(scenes, work_dir):
    log.info("🎙️  Generating voiceover...")
    audio_files = []
    for i, scene in enumerate(scenes):
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}",
            headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"},
            json={"text": scene["voiceover"], "model_id": "eleven_monolingual_v1",
                  "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}, timeout=30)
        path = work_dir / f"voice_{i:02d}.mp3"
        path.write_bytes(resp.content)
        audio_files.append(path)
    concat_list = work_dir / "audio_list.txt"
    concat_list.write_text("\n".join(f"file '{p.resolve()}'" for p in audio_files))
    final = work_dir / "final_voice.mp3"
    subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat_list),"-c","copy",str(final)], capture_output=True)
    log.info("   ✅ Voiceover done")
    return final

def get_font(size, bold=True):
    paths = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"] if bold else \
            ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]
    for p in paths:
        if os.path.exists(p): return ImageFont.truetype(p, size)
    return ImageFont.load_default()

PALETTES = [(255,215,0),(138,92,255),(46,213,115),(255,71,87),(30,200,255),(255,165,0)]

def create_overlays(scenes, work_dir):
    log.info("🖼️  Creating overlays...")
    overlays = []
    for si, scene in enumerate(scenes):
        accent = PALETTES[si % len(PALETTES)]
        n_frames = int(scene["duration"] * FPS)
        frames_dir = work_dir / f"sc{si}_frames"
        frames_dir.mkdir()
        font_main = get_font(84); font_label = get_font(50)
        label = "FOLLOW FOR MORE 👆" if si == len(scenes)-1 else "DID YOU KNOW? 🤯" if si == 0 else f"FACT #{si}"
        for f in range(n_frames):
            t = f / max(n_frames-1, 1)
            slide = min(1.0, t*4)**0.4
            img = Image.new("RGBA",(W,H),(0,0,0,0)); draw = ImageDraw.Draw(img)
            bar = Image.new("RGBA",(W,220),(*accent,200)); img.paste(bar,(0,0),bar)
            bot = Image.new("RGBA",(W,650),(5,5,15,190)); img.paste(bot,(0,H-650),bot)
            lb = draw.textbbox((0,0),label,font=font_label)
            lw = lb[2]-lb[0]
            draw.text(((W-lw)//2+2,82),label,font=font_label,fill=(0,0,0,200))
            draw.text(((W-lw)//2,80),label,font=font_label,fill=(10,10,10,255))
            lines = textwrap.fill(scene["text"],width=16).split("\n")
            base_y = H-580+int((1-slide)*80)
            for li, line in enumerate(lines):
                lb2 = draw.textbbox((0,0),line,font=font_main); lw2 = lb2[2]-lb2[0]
                lx = (W-lw2)//2; ly = base_y+li*96
                draw.text((lx+4,ly+4),line,font=font_main,fill=(0,0,0,180))
                draw.text((lx,ly),line,font=font_main,fill=(255,255,255,255))
            lw3 = int(280*slide)
            draw.rectangle([(W//2-lw3,H-235),(W//2+lw3,H-227)],fill=(*accent,255))
            prog = (si+t)/len(scenes); bw = int(W*prog)
            draw.rectangle([0,H-14,bw,H],fill=(*accent,255))
            draw.rectangle([bw,H-14,W,H],fill=(30,30,40,200))
            img.save(frames_dir/f"frame_{f:04d}.png")
        ov = work_dir/f"overlay_{si:02d}.mp4"
        subprocess.run(["ffmpeg","-y","-framerate",str(FPS),"-i",str(frames_dir/"frame_%04d.png"),
            "-c:v","libx264","-preset","fast","-crf","18","-pix_fmt","yuv420p",str(ov)], capture_output=True)
        shutil.rmtree(frames_dir); overlays.append(ov)
    log.info(f"   ✅ {len(overlays)} overlays done")
    return overlays

def assemble_video(script, footage, voiceover, overlays, work_dir, output):
    log.info("🎞️  Assembling video...")
    total_dur = sum(s["duration"] for s in script["scenes"])

    # Step 1: Create background frame image with Pillow
    bg_frame = work_dir / "bg_frame.png"
    Image.new("RGB", (W, H), (8, 12, 20)).save(str(bg_frame))
    log.info(f"   ✅ bg frame created: {bg_frame.exists()}")

    # Step 2: Concat overlays into single video
    ol_list = work_dir / "ol_list.txt"
    ol_list.write_text('\n'.join(f"file '{p.resolve()}'" for p in overlays))
    ol_concat = work_dir / "overlays.mp4"
    r1 = subprocess.run([
        "ffmpeg","-y","-f","concat","-safe","0","-i",str(ol_list),
        "-c:v","libx264","-preset","fast","-crf","18","-pix_fmt","yuv420p",str(ol_concat)
    ], capture_output=True, text=True)
    if r1.returncode != 0:
        log.error(f"   ❌ overlay concat: {r1.stderr[-200:]}")
    else:
        log.info(f"   ✅ overlays concat: {ol_concat.stat().st_size/1024:.0f}KB")

    # Step 3: Final assembly — loop bg image + overlay + audio
    r2 = subprocess.run([
        "ffmpeg","-y",
        "-loop","1","-i",str(bg_frame),
        "-i",str(ol_concat),
        "-i",str(voiceover),
        "-filter_complex",
        f"[0:v]scale={W}:{H},setsar=1[bg];[bg][1:v]overlay=0:0[comp]",
        "-map","[comp]","-map","2:a",
        "-c:v","libx264","-preset","fast","-crf","20","-pix_fmt","yuv420p",
        "-c:a","aac","-b:a","192k",
        "-t",str(total_dur),"-movflags","+faststart",str(output)
    ], capture_output=True, text=True)
    if r2.returncode != 0:
        log.error(f"   ❌ final assembly: {r2.stderr[-400:]}")
    else:
        size = output.stat().st_size/1024/1024 if output.exists() else 0
        log.info(f"   ✅ {output.name} ({size:.1f}MB, {total_dur:.1f}s)")
    return total_dur


def upload_youtube(video_path, title, description, hashtags, publish_at):
    log.info(f"📺 Scheduling YouTube → {publish_at.strftime('%a %d %b %H:%M')}")
    try:
        import pickle, base64
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google.auth.transport.requests import Request

        token_b64 = os.environ.get('YOUTUBE_TOKEN_B64', '')
        if not token_b64:
            log.warning('   ⚠️  YOUTUBE_TOKEN_B64 not set')
            return None

        creds = pickle.loads(base64.b64decode(token_b64))
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())

        yt = build('youtube', 'v3', credentials=creds)
        body = {
            'snippet': {
                'title': title[:100],
                'description': description + '\n\n' + hashtags + '\n\n#Shorts',
#Shorts',
                'tags': [t.replace('#','') for t in hashtags.split()],
                'categoryId': '22',
            },
            'status': {
                'privacyStatus': 'private',
                'publishAt': publish_at.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
                'selfDeclaredMadeForKids': False,
            }
        }
        media = MediaFileUpload(str(video_path), mimetype='video/mp4', resumable=True)
        req = yt.videos().insert(part='snippet,status', body=body, media_body=media)
        response = None
        while response is None:
            _, response = req.next_chunk()
        video_id = response['id']
        log.info(f'   ✅ YouTube scheduled! youtu.be/{video_id}')
        return f'https://youtube.com/shorts/{video_id}'
    except Exception as e:
        log.error(f'   ❌ YouTube error: {e}')
        return None

def run_pipeline(n_videos=1):
    log.info("="*55)
    log.info("🚀 AUTO VIDEO PIPELINE STARTING")
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
        OUTPUT_DIR.mkdir(exist_ok=True)
        output = Path(f"/tmp/video_{ts}.mp4")
        try:
            script    = generate_script()
            footage   = download_footage(script["topic"], work_dir)
            voiceover = generate_voiceover(script["scenes"], work_dir)
            overlays  = create_overlays(script["scenes"], work_dir)
            duration  = assemble_video(script, footage, voiceover, overlays, work_dir, output)
            yt_time = yt_slots[i] if i < len(yt_slots) else datetime.now()+timedelta(hours=1)
            tt_time = tt_slots[i] if i < len(tt_slots) else datetime.now()+timedelta(hours=2)
            yt_url = upload_youtube(output, script['title'], script.get('description',''), script['hashtags'], yt_time)
            entry = {"id": ts, "title": script["title"], "file": str(output), "duration": duration,
                     "created_at": datetime.now().isoformat(),
                     "youtube": {"scheduled": yt_time.isoformat(), "url": yt_url},
                     "tiktok":  {"scheduled": tt_time.isoformat(), "buffer_id": None},
                     "hashtags": script["hashtags"], "status": "scheduled"}
            add_to_dashboard(entry)
            results.append(entry)
            log.info(f"✅ Video {i+1} complete!")
            log.info(f"   📺 YouTube: {yt_time.strftime('%a %d %b %H:%M')}")
            log.info(f"   📱 TikTok:  {tt_time.strftime('%a %d %b %H:%M')}")
        except Exception as e:
            log.error(f"❌ Video {i+1} failed: {e}")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
        if i < n_videos-1: time.sleep(2)
    log.info(f"\n{'='*55}")
    log.info(f"🎉 PIPELINE DONE — {len(results)}/{n_videos} videos scheduled")
    log.info(f"📁 Videos: {OUTPUT_DIR}/")
    log.info(f"📊 Dashboard: {DASHBOARD_FILE}")
    log.info(f"{'='*55}")
    return results

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    run_pipeline(n)
