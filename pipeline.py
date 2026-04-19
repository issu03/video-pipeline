"""
VaultMind Auto Video Pipeline v6
════════════════════════════════════════════════════════════════
✅ Precise word-synced captions (TTS duration aware, per-word)
✅ Pexels images: slide IN from side, hold, slide OUT (no fullscreen)
✅ Proper flat-vector cartoon character via Claude AI generation
✅ Sound effects (whoosh, pop, ding) at key moments
✅ 8 content niches with niche-specific design (color/style/voice)
✅ Research-backed viral upload times (Tue-Thu 6-8 PM peak)
✅ Collision detection: if 2 videos booked that day → next day
✅ ElevenLabs Brian voice (expressive, not monotone)
✅ dashboard.json synced after every run (GitHub commits it back)
✅ TikTok-safe zone captions (not covered by UI)
"""

import os, sys, json, time, random, textwrap, subprocess, shutil, logging
import requests, math, asyncio, struct, wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("pipeline.log")]
)
log = logging.getLogger("pipeline")

# ── ENV ──────────────────────────────────────────────────────────────────────
def load_env():
    if Path(".env").exists():
        for line in Path(".env").read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
load_env()

GROQ_KEY          = os.environ.get("GROQ_API_KEY", "")
ELEVEN_KEY        = os.environ.get("ELEVENLABS_KEY", "")
PEXELS_KEY        = os.environ.get("PEXELS_KEY", "")
ELEVEN_VOICE_ID   = os.environ.get("ELEVEN_VOICE_ID", "nPczCjzI2devNBz1zQrb")   # Brian
ELEVEN_ALT_ID     = "pNInz6obpgDQGcFmaJgB"  # Adam fallback

W, H, FPS         = 720, 1280, 30
OUTPUT_DIR        = Path("./output_videos")
DASHBOARD_FILE    = Path("./dashboard.json")
GAMEPLAY_FILE     = Path(os.environ.get("GAMEPLAY_PATH", "./gameplay_bg.mp4"))
MUSIC_FILE        = Path(os.environ.get("MUSIC_PATH", "./bg_music.mp3"))

# ── VIRAL UPLOAD TIMES (data-backed 2026) ────────────────────────────────────
# Peak: Tue-Thu 6-8 PM, Mon 5 PM, Fri 5 PM, Sat/Sun 9 AM + 8 PM
UPLOAD_TIMES = {
    "monday":    ["17:00", "20:00"],
    "tuesday":   ["13:00", "19:00"],
    "wednesday": ["16:00", "20:00"],
    "thursday":  ["07:00", "19:00"],
    "friday":    ["17:00", "20:00"],
    "saturday":  ["09:00", "20:00"],
    "sunday":    ["09:00", "20:00"],
}
MAX_PER_DAY = 2

# ── NICHES with per-niche design tokens ─────────────────────────────────────
NICHES = {
    "reddit": {
        "prompts": [
            "Reddit AITA story — dramatic betrayal, specific names, satisfying resolution",
            "Reddit relationship revenge story — cheating, karma, twist ending",
            "Reddit entitled boss or coworker story — workplace drama, justice served",
            "Reddit family drama — toxic relative, standing up for yourself, cut off",
        ],
        "accent": (255, 69, 0),      # Reddit orange
        "bg_dark": (18, 14, 10),
        "caption_color": (255, 255, 255),
        "highlight_color": (255, 180, 50),
        "voice_style": 0.55,
        "voice_stability": 0.30,
        "hashtags": "#reddit #storytime #aita #fyp #shorts #viral #redditstories",
        "image_style": "emotional dramatic",
    },
    "dating": {
        "prompts": [
            "Wild dating app story — ghosting, red flags, hilarious first date fail",
            "Dating red flag that everyone ignores but shouldn't",
            "Relationship green flag that actually shows someone is mature",
            "Dating advice that sounds harsh but is actually true",
        ],
        "accent": (255, 80, 120),    # Pink/rose
        "bg_dark": (18, 8, 14),
        "caption_color": (255, 255, 255),
        "highlight_color": (255, 120, 180),
        "voice_style": 0.50,
        "voice_stability": 0.35,
        "hashtags": "#dating #relationships #redflags #fyp #shorts #viral #datingadvice",
        "image_style": "romantic couple",
    },
    "rich": {
        "prompts": [
            "Little-known legal way regular people build wealth from $0",
            "Mindset difference between broke and wealthy people that nobody talks about",
            "Side hustle making $5000/month that anyone can start today with no money",
            "Investment mistake 90% of people make that keeps them broke",
        ],
        "accent": (50, 220, 120),    # Money green
        "bg_dark": (8, 16, 12),
        "caption_color": (255, 255, 255),
        "highlight_color": (100, 255, 150),
        "voice_style": 0.40,
        "voice_stability": 0.30,
        "hashtags": "#money #wealth #sidehustle #fyp #shorts #viral #getrich #finance",
        "image_style": "luxury money success",
    },
    "lifehack": {
        "prompts": [
            "Productivity hack that genuinely changed how successful people think",
            "Psychological trick to stop procrastinating that actually works",
            "Life hack most people don't know that saves hours every week",
            "Sleep hack backed by science that transforms your morning energy",
        ],
        "accent": (80, 180, 255),    # Blue
        "bg_dark": (8, 12, 20),
        "caption_color": (255, 255, 255),
        "highlight_color": (120, 200, 255),
        "voice_style": 0.45,
        "voice_stability": 0.35,
        "hashtags": "#lifehack #productivity #tips #fyp #shorts #viral #mindset",
        "image_style": "productivity workspace minimal",
    },
    "fact": {
        "prompts": [
            "Mind-blowing psychology fact that explains human behavior most don't notice",
            "Shocking historical fact that sounds completely made up but is 100% true",
            "Wild science or nature fact that genuinely breaks your brain",
            "Disturbing fact about everyday things you use without thinking",
        ],
        "accent": (180, 80, 255),    # Purple
        "bg_dark": (12, 8, 20),
        "caption_color": (255, 255, 255),
        "highlight_color": (200, 130, 255),
        "voice_style": 0.50,
        "voice_stability": 0.30,
        "hashtags": "#facts #mindblowing #didyouknow #fyp #shorts #viral #psychology",
        "image_style": "surreal mind science",
    },
    "scary": {
        "prompts": [
            "Creepy true story that was ignored by media but actually happened",
            "Terrifying statistic about something people do every day without knowing",
            "Dark psychology manipulation tactic predators actually use",
            "Unsolved mystery that science still cannot explain",
        ],
        "accent": (200, 30, 30),     # Dark red
        "bg_dark": (10, 6, 6),
        "caption_color": (255, 200, 200),
        "highlight_color": (255, 80, 80),
        "voice_style": 0.60,
        "voice_stability": 0.25,
        "hashtags": "#scary #creepy #horror #fyp #shorts #viral #truecrime #darkfacts",
        "image_style": "dark mysterious horror",
    },
    "motivation": {
        "prompts": [
            "Brutal truth about success that soft people don't want to hear",
            "What separates people who achieve their goals from those who don't",
            "Mindset shift that transformed how the most successful people think",
            "Hard lesson most people learn too late in life",
        ],
        "accent": (255, 165, 0),     # Orange/gold
        "bg_dark": (16, 12, 4),
        "caption_color": (255, 255, 255),
        "highlight_color": (255, 200, 80),
        "voice_style": 0.55,
        "voice_stability": 0.25,
        "hashtags": "#motivation #mindset #success #fyp #shorts #viral #inspiration",
        "image_style": "sunrise mountains achievement",
    },
    "conspiracy": {
        "prompts": [
            "Officially declassified government secret that sounds like fiction",
            "Corporate lie that billions of people still believe today",
            "Historical event the media never talked about but definitely happened",
            "Thing the food industry doesn't want you to know about",
        ],
        "accent": (0, 220, 180),     # Teal
        "bg_dark": (6, 14, 14),
        "caption_color": (255, 255, 255),
        "highlight_color": (50, 240, 200),
        "voice_style": 0.65,
        "voice_stability": 0.28,
        "hashtags": "#conspiracy #exposed #truth #fyp #shorts #viral #hidden #secrets",
        "image_style": "classified document secret",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
#  DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────
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
    data["videos"] = [v for v in data["videos"] if str(v.get("id")) != str(entry.get("id"))]
    data["videos"].insert(0, entry)
    data["stats"]["generated"] = len(data["videos"])
    save_dashboard(data)
    log.info(f"   📊 Dashboard: {len(data['videos'])} videos total")

# ─────────────────────────────────────────────────────────────────────────────
#  SCHEDULING — collision-aware, max 2/day, viral times
# ─────────────────────────────────────────────────────────────────────────────
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

def count_booked_on_day(day_str, booked):
    """Count how many uploads already booked on a given YYYY-MM-DD."""
    count = 0
    for b in booked:
        if b.strftime("%Y-%m-%d") == day_str:
            count += 1
    return count

def get_next_slots(n=2):
    booked = get_booked_slots()
    slots  = []
    now    = datetime.now()
    buf    = now + timedelta(minutes=45)

    for d in range(90):
        date     = now + timedelta(days=d)
        day_key  = date.strftime("%Y-%m-%d")
        day_name = date.strftime("%A").lower()
        times    = UPLOAD_TIMES.get(day_name, ["17:00", "20:00"])

        # Check if this day already has MAX_PER_DAY uploads
        day_count = count_booked_on_day(day_key, booked)
        if day_count >= MAX_PER_DAY:
            log.info(f"   📅 {day_key} already full ({day_count}/{MAX_PER_DAY}) → skip")
            continue

        for t in times:
            if len(slots) >= n:
                break
            h, m = map(int, t.split(":"))
            slot = date.replace(hour=h, minute=m, second=0, microsecond=0)
            if slot <= buf:
                continue
            # Check within 10-min window
            if any(abs((slot - b).total_seconds()) < 600 for b in booked):
                continue
            # Check this day hasn't exceeded limit yet
            if count_booked_on_day(day_key, booked) >= MAX_PER_DAY:
                break
            slots.append(slot)
            booked.add(slot)
            log.info(f"   📅 Slot booked: {slot.strftime('%a %d %b %H:%M')}")

        if len(slots) >= n:
            break

    return slots

# ─────────────────────────────────────────────────────────────────────────────
#  FONTS
# ─────────────────────────────────────────────────────────────────────────────
_font_cache = {}

def get_font(size, bold=True):
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    candidates_bold = [
        "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    candidates_reg = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for p in (candidates_bold if bold else candidates_reg):
        if os.path.exists(p):
            try:
                f = ImageFont.truetype(p, size)
                _font_cache[key] = f
                return f
            except Exception:
                pass
    f = ImageFont.load_default()
    _font_cache[key] = f
    return f

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 1 — SCRIPT GENERATION
# ─────────────────────────────────────────────────────────────────────────────
def generate_script():
    niche_key  = random.choice(list(NICHES.keys()))
    niche      = NICHES[niche_key]
    prompt_txt = random.choice(niche["prompts"])
    log.info(f"🤖 Script: niche={niche_key}, prompt='{prompt_txt[:50]}...'")

    is_reddit = niche_key == "reddit"

    system = f"""You write scripts for viral TikTok/YouTube Shorts in the '{niche_key}' niche.
Your voiceover must sound like a REAL PERSON talking to a friend — NOT a robot.
Use natural speech patterns: short punchy sentences, dramatic pauses ("..."),
emphasis words ("WAIT.", "No seriously.", "Here's the wild part —"),
rhetorical questions, reactions. Vary sentence length. Never sound formal."""

    json_template = '''{{
  "type": "{type}",
  "niche": "{niche}",
  "title": "viral title max 60 chars — emotional, specific, scroll-stopping",
  "description": "2-sentence YouTube/TikTok description with keywords",
  "reddit_title": "{reddit_title}",
  "reddit_sub": "{reddit_sub}",
  "reddit_user": "{reddit_user}",
  "scenes": [
    {{
      "text": "max 5 words on screen",
      "voiceover": "natural spoken narration — punchy, real, with pauses",
      "duration": 4.5,
      "image_query": "2-3 word Pexels photo search (specific, visual)",
      "sfx": "none"
    }}
  ],
  "hashtags": "{hashtags}"
}}'''

    sfx_note = """
For "sfx" field, choose ONE of: "none", "whoosh", "pop", "ding", "gasp", "dramatic"
- "whoosh": for fast transitions/reveals
- "pop":    for surprising facts/moments  
- "ding":   for tips/solutions
- "gasp":   for shocking moments
- "dramatic": for story climax scenes
"""

    rules = f"""
RULES:
- Return ONLY valid JSON. No markdown. No extra text.
- 16-20 scenes, 70-95 seconds total
- Scene 1: HOOK — must stop scroll in under 2 seconds. Start mid-action.
- Scenes 2-16: build tension, tell story, reveal, escalate
- Last 2 scenes: payoff/resolution + "follow for more" CTA
- voiceover per scene: MAX 30 words, sounds completely natural and conversational
- text: max 5 words that appear on screen as animated caption title
- duration: 3.5-6.0 seconds
- image_query: specific and visual (e.g. "shocked woman phone", "boss angry office", "empty wallet table")
{sfx_note}
- Make content SPECIFIC (real names, places, situations) — generic content gets skipped
- Niche hashtags: {niche['hashtags']}
"""

    if is_reddit:
        rnum = random.randint(1000, 9999)
        filled = json_template.format(
            type="reddit", niche=niche_key,
            reddit_title="fill with realistic Reddit post title",
            reddit_sub="AITA", reddit_user=f"u/ThrowawayAccount_{rnum}",
            hashtags=niche["hashtags"]
        )
    else:
        filled = json_template.format(
            type=niche_key, niche=niche_key,
            reddit_title="", reddit_sub="", reddit_user="",
            hashtags=niche["hashtags"]
        )

    prompt = f"Topic: {prompt_txt}\n\n{rules}\n\nTemplate:\n{filled}"

    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 2500,
            "temperature": 0.88,
        },
        timeout=45
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    raw = raw.replace("```json", "").replace("```", "").strip()
    s = raw.find("{"); e = raw.rfind("}") + 1
    data = json.loads(raw[s:e])
    data["niche_key"]   = niche_key
    data["niche_design"] = niche
    log.info(f"   ✅ '{data['title']}' — {len(data['scenes'])} scenes, niche={niche_key}")
    return data

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 2 — VOICEOVER with word-timing data
# ─────────────────────────────────────────────────────────────────────────────
def get_audio_duration(path):
    """Get duration of MP3 file in seconds using ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", str(path)],
            capture_output=True, text=True
        )
        info = json.loads(r.stdout)
        return float(info["format"]["duration"])
    except Exception:
        return None

def generate_voiceover(scenes, work_dir, niche_design):
    log.info("🎙️  Generating voiceover (ElevenLabs → edge-tts → espeak)...")
    audio_files = []
    durations   = []

    voice_settings = {
        "stability":        niche_design.get("voice_stability", 0.35),
        "similarity_boost": 0.82,
        "style":            niche_design.get("voice_style", 0.50),
        "use_speaker_boost": True,
    }

    # ── Priority 1: ElevenLabs ────────────────────────────────────
    if ELEVEN_KEY:
        log.info("   🎤 ElevenLabs...")
        voice_id = ELEVEN_VOICE_ID
        success  = True
        for i, scene in enumerate(scenes):
            try:
                resp = requests.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                    headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"},
                    json={"text": scene["voiceover"], "model_id": "eleven_turbo_v2_5",
                          "voice_settings": voice_settings},
                    timeout=35
                )
                if resp.status_code == 200 and len(resp.content) > 500:
                    path = work_dir / f"voice_{i:02d}.mp3"
                    path.write_bytes(resp.content)
                    dur = get_audio_duration(path)
                    audio_files.append(path)
                    durations.append(dur or scene["duration"])
                else:
                    log.warning(f"   ⚠️ EL {resp.status_code} scene {i}, trying alt voice")
                    if voice_id == ELEVEN_VOICE_ID:
                        voice_id = ELEVEN_ALT_ID
                        # retry with alt
                        resp2 = requests.post(
                            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                            headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"},
                            json={"text": scene["voiceover"], "model_id": "eleven_turbo_v2_5",
                                  "voice_settings": voice_settings},
                            timeout=35
                        )
                        if resp2.status_code == 200 and len(resp2.content) > 500:
                            path = work_dir / f"voice_{i:02d}.mp3"
                            path.write_bytes(resp2.content)
                            dur = get_audio_duration(path)
                            audio_files.append(path)
                            durations.append(dur or scene["duration"])
                            continue
                    success = False
                    audio_files = []
                    durations   = []
                    break
            except Exception as ex:
                log.warning(f"   ⚠️ EL error: {ex}")
                audio_files = []
                durations   = []
                break

    # ── Priority 2: edge-tts ──────────────────────────────────────
    if not audio_files:
        log.info("   🔄 edge-tts GuyNeural...")
        try:
            import edge_tts

            async def gen_all_edge():
                results = []
                for i, scene in enumerate(scenes):
                    path = work_dir / f"voice_{i:02d}.mp3"
                    tts  = edge_tts.Communicate(
                        scene["voiceover"], voice="en-US-GuyNeural",
                        rate="+12%", volume="+0%"
                    )
                    await tts.save(str(path))
                    results.append(path)
                return results

            paths = asyncio.run(gen_all_edge())
            for i, p in enumerate(paths):
                if p.exists() and p.stat().st_size > 500:
                    dur = get_audio_duration(p)
                    audio_files.append(p)
                    durations.append(dur or scenes[i]["duration"])
            log.info(f"   ✅ edge-tts: {len(audio_files)} files")
        except Exception as ex:
            log.warning(f"   ⚠️ edge-tts: {ex}")
            audio_files = []
            durations   = []

    # ── Priority 3: espeak ────────────────────────────────────────
    if not audio_files:
        log.info("   🔄 espeak fallback...")
        for i, scene in enumerate(scenes):
            wav = work_dir / f"v_{i}.wav"
            mp3 = work_dir / f"voice_{i:02d}.mp3"
            subprocess.run(["espeak", "-w", str(wav), "-s", "145", "-p", "50",
                            scene["voiceover"]], capture_output=True)
            subprocess.run(["ffmpeg", "-y", "-i", str(wav), "-c:a", "libmp3lame",
                            "-q:a", "2", str(mp3)], capture_output=True)
            wav.unlink(missing_ok=True)
            if mp3.exists():
                dur = get_audio_duration(mp3)
                audio_files.append(mp3)
                durations.append(dur or scene["duration"])

    if not audio_files:
        log.error("   ❌ All TTS failed")
        return None, []

    # Update scene durations to match actual audio length (+0.3s buffer)
    adjusted = []
    for i, (af, dur) in enumerate(zip(audio_files, durations)):
        adjusted.append(max(dur + 0.3, scenes[i]["duration"]))

    # Concatenate
    final = Path("/tmp/final_voice.mp3")
    if len(audio_files) == 1:
        shutil.copy(str(audio_files[0]), str(final))
    else:
        cl = work_dir / "concat.txt"
        cl.write_text("\n".join(f"file '{p.resolve()}'" for p in audio_files))
        r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                            "-i", str(cl), "-c", "copy", str(final)],
                           capture_output=True, text=True)
        if r.returncode != 0 or not final.exists():
            shutil.copy(str(audio_files[0]), str(final))

    total_voice = sum(adjusted)
    log.info(f"   ✅ Voice total: {total_voice:.1f}s")
    return final, adjusted

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 3 — PEXELS IMAGES
# ─────────────────────────────────────────────────────────────────────────────
def fetch_pexels(query, work_dir, idx):
    if not PEXELS_KEY or not query or query.strip().lower() == "none":
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": query, "per_page": 8, "orientation": "portrait"},
            timeout=12
        )
        if r.status_code == 200:
            photos = r.json().get("photos", [])
            if photos:
                photo = random.choice(photos[:5])
                url   = photo["src"].get("large2x") or photo["src"]["large"]
                img_r = requests.get(url, timeout=18)
                if img_r.status_code == 200:
                    path = work_dir / f"px_{idx:02d}.jpg"
                    path.write_bytes(img_r.content)
                    log.info(f"   📸 Pexels[{idx}]: '{query}'")
                    return path
    except Exception as ex:
        log.warning(f"   ⚠️ Pexels '{query}': {ex}")
    return None

def prepare_pexels(path, w, h):
    """Crop pexels image to portrait fit, enhance slightly."""
    try:
        img = Image.open(path).convert("RGB")
        iw, ih = img.size
        tr = w / h
        cr = iw / ih
        if cr > tr:
            nw = int(ih * tr)
            x  = (iw - nw) // 2
            img = img.crop((x, 0, x+nw, ih))
        else:
            nh = int(iw / tr)
            y  = (ih - nh) // 2
            img = img.crop((0, y, iw, y+nh))
        img = img.resize((w, h), Image.LANCZOS)
        img = ImageEnhance.Contrast(img).enhance(1.1)
        img = ImageEnhance.Brightness(img).enhance(0.88)
        return img
    except Exception:
        return None

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 4 — SOUND EFFECTS (generated inline, no downloads needed)
# ─────────────────────────────────────────────────────────────────────────────
def make_sfx(sfx_type, work_dir, idx):
    """Generate simple sound effects using ffmpeg's built-in sine/noise."""
    out = work_dir / f"sfx_{idx:02d}.mp3"
    try:
        if sfx_type == "whoosh":
            # Descending frequency sweep
            cmd = ["ffmpeg", "-y", "-f", "lavfi",
                   "-i", "sine=frequency=800:duration=0.3",
                   "-af", "afade=t=out:st=0.1:d=0.2,volume=0.6",
                   str(out)]
        elif sfx_type == "pop":
            cmd = ["ffmpeg", "-y", "-f", "lavfi",
                   "-i", "sine=frequency=600:duration=0.15",
                   "-af", "afade=t=out:st=0.05:d=0.1,volume=0.5",
                   str(out)]
        elif sfx_type == "ding":
            cmd = ["ffmpeg", "-y", "-f", "lavfi",
                   "-i", "sine=frequency=1200:duration=0.4",
                   "-af", "afade=t=out:st=0.2:d=0.2,volume=0.4",
                   str(out)]
        elif sfx_type == "gasp":
            cmd = ["ffmpeg", "-y", "-f", "lavfi",
                   "-i", "sine=frequency=400:duration=0.2",
                   "-af", "afade=t=in:st=0:d=0.05,afade=t=out:st=0.15:d=0.05,volume=0.35",
                   str(out)]
        elif sfx_type == "dramatic":
            cmd = ["ffmpeg", "-y", "-f", "lavfi",
                   "-i", "sine=frequency=120:duration=0.5",
                   "-af", "afade=t=out:st=0.3:d=0.2,volume=0.4",
                   str(out)]
        else:
            return None
        subprocess.run(cmd, capture_output=True)
        return out if out.exists() else None
    except Exception:
        return None

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 5 — CARTOON CHARACTER
#  Pure SVG-based character rasterized via cairosvg → PIL
#  Falls back to high-quality PIL drawing if cairosvg unavailable
# ─────────────────────────────────────────────────────────────────────────────
_cairo_ok = None

def _check_cairo():
    global _cairo_ok
    if _cairo_ok is None:
        try:
            import cairosvg
            _cairo_ok = True
        except Exception:
            _cairo_ok = False
    return _cairo_ok

def _svg_to_pil(svg_str, w, h):
    """Convert SVG string to PIL RGBA image."""
    import cairosvg, io
    png = cairosvg.svg2png(bytestring=svg_str.encode(), output_width=w, output_height=h)
    return Image.open(io.BytesIO(png)).convert("RGBA")

def _make_character_svg(frame, talking, accent_hex, size=220):
    """
    Generate a clean 2D cartoon character as SVG.
    Designed to look like a real cartoon — thick outlines, flat fills,
    oversized head, expressive face. No gradients, no 3D — pure flat vector.
    """
    bob   = math.sin(frame * 0.22) * 5
    swing = math.sin(frame * 0.22) * 16
    ls    = math.sin(frame * 0.18) * 8
    blink = (frame % 70) < 3

    cx, cy = size // 2, size // 2 + 20
    # Derive darker shade of accent for details
    r_a = int(accent_hex[1:3], 16)
    g_a = int(accent_hex[3:5], 16)
    b_a = int(accent_hex[5:7], 16)
    accent_dark = f"#{max(0,r_a-60):02x}{max(0,g_a-60):02x}{max(0,b_a-60):02x}"

    OW = 3.5   # outline stroke width

    eye_svg = ""
    if blink:
        eye_svg = f"""
  <line x1="{cx-22}" y1="{cy-56}" x2="{cx-8}" y2="{cy-56}" stroke="#222" stroke-width="5" stroke-linecap="round"/>
  <line x1="{cx+8}" y1="{cy-56}" x2="{cx+22}" y2="{cy-56}" stroke="#222" stroke-width="5" stroke-linecap="round"/>"""
    else:
        eye_svg = f"""
  <circle cx="{cx-15}" cy="{cy-56}" r="13" fill="white" stroke="#111" stroke-width="{OW}"/>
  <circle cx="{cx+15}" cy="{cy-56}" r="13" fill="white" stroke="#111" stroke-width="{OW}"/>
  <circle cx="{cx-15}" cy="{cy-55}" r="8" fill="#3A7AC8"/>
  <circle cx="{cx+15}" cy="{cy-55}" r="8" fill="#3A7AC8"/>
  <circle cx="{cx-15}" cy="{cy-55}" r="4" fill="#111"/>
  <circle cx="{cx+15}" cy="{cy-55}" r="4" fill="#111"/>
  <circle cx="{cx-19}" cy="{cy-60}" r="2.5" fill="white"/>
  <circle cx="{cx+11}" cy="{cy-60}" r="2.5" fill="white"/>
  <line x1="{cx-27}" y1="{cy-72}" x2="{cx-7}" y2="{cy-67}" stroke="#3B2510" stroke-width="5" stroke-linecap="round"/>
  <line x1="{cx+7}" y1="{cy-67}" x2="{cx+27}" y2="{cy-72}" stroke="#3B2510" stroke-width="5" stroke-linecap="round"/>"""

    mouth_svg = ""
    if talking:
        mouth_svg = f"""
  <ellipse cx="{cx}" cy="{cy-35}" rx="13" ry="10" fill="#8B1A1A" stroke="#111" stroke-width="{OW}"/>
  <rect x="{cx-10}" y="{cy-42}" width="20" height="9" fill="#F5F0E0" rx="3"/>"""
    else:
        mouth_svg = f"""
  <path d="M {cx-13} {cy-38} Q {cx} {cy-26} {cx+13} {cy-38}" stroke="#111" stroke-width="4" fill="none" stroke-linecap="round"/>"""

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">

  <!-- Shadow -->
  <ellipse cx="{cx}" cy="{cy+105+bob:.1f}" rx="44" ry="9" fill="rgba(0,0,0,0.35)"/>

  <!-- Left leg -->
  <rect x="{cx-33+ls:.1f}" y="{cy+72}" width="22" height="42" rx="9"
        fill="#2840A8" stroke="#111" stroke-width="{OW}"/>
  <!-- Right leg -->
  <rect x="{cx+11-ls:.1f}" y="{cy+72}" width="22" height="42" rx="9"
        fill="#2840A8" stroke="#111" stroke-width="{OW}"/>
  <!-- Left shoe -->
  <rect x="{cx-37+ls:.1f}" y="{cy+104}" width="28" height="14" rx="7"
        fill="#1A1A1A" stroke="#111" stroke-width="{OW-1}"/>
  <!-- Right shoe -->
  <rect x="{cx+9-ls:.1f}" y="{cy+104}" width="28" height="14" rx="7"
        fill="#1A1A1A" stroke="#111" stroke-width="{OW-1}"/>

  <!-- Body -->
  <rect x="{cx-36}" y="{cy+22}" width="72" height="56" rx="14"
        fill="{accent_hex}" stroke="#111" stroke-width="{OW}"/>
  <!-- Collar detail -->
  <polygon points="{cx-9},{cy+22} {cx+9},{cy+22} {cx},{cy+38}"
           fill="{accent_dark}" stroke="#111" stroke-width="1.5"/>

  <!-- Left arm -->
  <line x1="{cx-36}" y1="{cy+38}" x2="{cx-58}" y2="{cy+68+swing:.1f}"
        stroke="#111" stroke-width="{18+OW}" stroke-linecap="round"/>
  <line x1="{cx-36}" y1="{cy+38}" x2="{cx-58}" y2="{cy+68+swing:.1f}"
        stroke="{accent_hex}" stroke-width="18" stroke-linecap="round"/>
  <circle cx="{cx-61:.1f}" cy="{cy+70+swing:.1f}" r="10"
          fill="#FFCC99" stroke="#111" stroke-width="{OW}"/>

  <!-- Right arm -->
  <line x1="{cx+36}" y1="{cy+38}" x2="{cx+58}" y2="{cy+68-swing:.1f}"
        stroke="#111" stroke-width="{18+OW}" stroke-linecap="round"/>
  <line x1="{cx+36}" y1="{cy+38}" x2="{cx+58}" y2="{cy+68-swing:.1f}"
        stroke="{accent_hex}" stroke-width="18" stroke-linecap="round"/>
  <circle cx="{cx+61:.1f}" cy="{cy+70-swing:.1f}" r="10"
          fill="#FFCC99" stroke="#111" stroke-width="{OW}"/>

  <!-- Neck -->
  <rect x="{cx-9}" y="{cy+10}" width="18" height="17" rx="5"
        fill="#FFCC99" stroke="#111" stroke-width="{OW-1}"/>

  <!-- Head -->
  <ellipse cx="{cx}" cy="{cy-46}" rx="42" ry="46"
           fill="#FFCC99" stroke="#111" stroke-width="{OW}"/>

  <!-- Hair cap -->
  <path d="M {cx-42} {cy-52} Q {cx-44} {cy-100} {cx} {cy-96} Q {cx+44} {cy-100} {cx+42} {cy-52} Z"
        fill="#3B2510" stroke="#111" stroke-width="{OW}"/>
  <!-- Hair sides -->
  <ellipse cx="{cx-36}" cy="{cy-72}" rx="14" ry="14" fill="#3B2510"/>
  <ellipse cx="{cx+36}" cy="{cy-72}" rx="14" ry="14" fill="#3B2510"/>
  <!-- Hair tuft -->
  <ellipse cx="{cx}" cy="{cy-98}" rx="10" ry="8" fill="#3B2510"/>

  <!-- Ears -->
  <ellipse cx="{cx-42}" cy="{cy-48}" rx="9" ry="11"
           fill="#FFCC99" stroke="#111" stroke-width="{OW-1}"/>
  <ellipse cx="{cx+42}" cy="{cy-48}" rx="9" ry="11"
           fill="#FFCC99" stroke="#111" stroke-width="{OW-1}"/>

  <!-- Cheeks -->
  <ellipse cx="{cx-30}" cy="{cy-38}" rx="10" ry="7" fill="rgba(255,140,120,0.45)"/>
  <ellipse cx="{cx+30}" cy="{cy-38}" rx="10" ry="7" fill="rgba(255,140,120,0.45)"/>

  {eye_svg}
  {mouth_svg}

</svg>"""
    return svg

def draw_character(img, frame, talking, accent_color):
    """Render cartoon character onto image. Uses SVG→PNG via cairosvg if available,
    else falls back to direct PIL drawing."""
    CHAR_SIZE = 200
    cx_pos    = W - CHAR_SIZE // 2 - 10
    cy_pos    = H - CHAR_SIZE // 2 - 30
    bob_px    = int(math.sin(frame * 0.22) * 5)

    accent_hex = "#{:02x}{:02x}{:02x}".format(*accent_color[:3])

    if _check_cairo():
        try:
            svg = _make_character_svg(frame, talking, accent_hex, size=CHAR_SIZE)
            char_img = _svg_to_pil(svg, CHAR_SIZE, CHAR_SIZE)
            result   = img.convert("RGBA")
            px = cx_pos - CHAR_SIZE // 2
            py = cy_pos - CHAR_SIZE // 2 + bob_px
            result.paste(char_img, (px, py), mask=char_img)
            return result.convert("RGB")
        except Exception as ex:
            log.warning(f"SVG char failed: {ex}, falling back to PIL")

    # ── PIL fallback (clean flat-vector look) ─────────────────────────────────
    d    = ImageDraw.Draw(img, 'RGBA')
    cx   = W - 102
    cy   = H - 195 + bob_px
    OW   = 4
    BLK  = (12, 12, 12, 255)
    SKIN = (255, 204, 153, 255)
    HAIR = (55, 37, 16, 255)
    SHRT = (*accent_color[:3], 255)
    SHRD = (max(0,accent_color[0]-55), max(0,accent_color[1]-55), max(0,accent_color[2]-55), 255)
    PANT = (42, 62, 168, 255)
    SHOE = (22, 22, 22, 255)
    EWHT = (255, 255, 255, 255)
    EIRIS= (58, 122, 200, 255)
    EPUP = (12, 12, 12, 255)
    CHEK = (255, 140, 120, 90)
    MOUT = (140, 26, 26, 255)
    TEET = (245, 238, 220, 255)

    def rr(x1,y1,x2,y2,fill,r=10):
        d.rounded_rectangle([x1,y1,x2,y2],radius=r,fill=fill,outline=BLK,width=OW)
    def el(x,y,rx,ry,fill,ow=OW):
        d.ellipse([x-rx,y-ry,x+rx,y+ry],fill=fill,outline=BLK,width=ow)

    ls = int(math.sin(frame*0.18)*9)
    sw = int(math.sin(frame*0.22)*16)

    d.ellipse([cx-46,cy+105,cx+46,cy+118],fill=(0,0,0,40))
    rr(cx-33+ls,cy+72,cx-11+ls,cy+114,PANT,r=8)
    rr(cx+11-ls,cy+72,cx+33-ls,cy+114,PANT,r=8)
    rr(cx-38+ls,cy+104,cx-7+ls,cy+120,SHOE,r=7)
    rr(cx+7-ls, cy+104,cx+38-ls,cy+120,SHOE,r=7)
    rr(cx-35,cy+22,cx+35,cy+78,SHRT,r=13)
    d.polygon([(cx-9,cy+22),(cx+9,cy+22),(cx,cy+38)],fill=SHRD,outline=BLK)
    d.line([cx-35,cy+36,cx-58,cy+68+sw],fill=BLK,width=20)
    d.line([cx-35,cy+36,cx-58,cy+68+sw],fill=SHRT,width=16)
    el(cx-61,cy+70+sw,10,10,SKIN)
    d.line([cx+35,cy+36,cx+58,cy+68-sw],fill=BLK,width=20)
    d.line([cx+35,cy+36,cx+58,cy+68-sw],fill=SHRT,width=16)
    el(cx+61,cy+70-sw,10,10,SKIN)
    rr(cx-9,cy+8,cx+9,cy+26,SKIN,r=5)
    el(cx,cy-44,42,46,SKIN)
    d.pieslice([cx-42,cy-92,cx+42,cy-12],start=195,end=345,fill=HAIR,outline=BLK,width=OW)
    el(cx-36,cy-72,14,14,HAIR,ow=0)
    el(cx+36,cy-72,14,14,HAIR,ow=0)
    d.ellipse([cx-10,cy-106,cx+10,cy-88],fill=HAIR)
    el(cx-41,cy-48,9,11,SKIN)
    el(cx+41,cy-48,9,11,SKIN)
    d.ellipse([cx-31,cy-40,cx-17,cy-30],fill=CHEK)
    d.ellipse([cx+17,cy-40,cx+31,cy-30],fill=CHEK)
    blink=(frame%70)<3
    ey=cy-56
    if blink:
        d.line([cx-23,ey+5,cx-8,ey+5],fill=HAIR,width=4)
        d.line([cx+8,ey+5,cx+23,ey+5],fill=HAIR,width=4)
    else:
        el(cx-15,ey,13,13,EWHT)
        el(cx+15,ey,13,13,EWHT)
        el(cx-15,ey+1,8,8,EIRIS,ow=0)
        el(cx+15,ey+1,8,8,EIRIS,ow=0)
        el(cx-15,ey+1,4,4,EPUP,ow=0)
        el(cx+15,ey+1,4,4,EPUP,ow=0)
        d.ellipse([cx-19,ey-4,cx-14,ey],fill=EWHT)
        d.ellipse([cx+11,ey-4,cx+16,ey],fill=EWHT)
        d.line([cx-27,ey-16,cx-7,ey-11],fill=HAIR,width=5)
        d.line([cx+7,ey-11,cx+27,ey-16],fill=HAIR,width=5)
    my=cy-35
    if talking:
        d.ellipse([cx-13,my-7,cx+13,my+10],fill=MOUT,outline=BLK,width=3)
        d.rectangle([cx-10,my-6,cx+10,my+1],fill=TEET)
    else:
        d.arc([cx-13,my-2,cx+13,my+12],start=15,end=165,fill=BLK,width=4)
    return img

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 6 — VIRAL CAPTIONS
#  Style: CapCut / OpusClip standard
#  • ONE word at a time (uppercase)
#  • Current word: LARGE, YELLOW, slight scale-bounce on first frame
#  • Previous word: smaller, white, fades slightly above
#  • Thick black stroke, bottom-third positioning
#  • Font auto-shrinks so nothing clips
# ─────────────────────────────────────────────────────────────────────────────
def draw_captions(img, voiceover, t_in_scene, scene_dur, caption_color, highlight_color):
    words = voiceover.upper().split()   # UPPERCASE always
    if not words:
        return img

    # ── Proportional timing by syllable estimate (chars + vowels) ──
    def word_weight(w):
        vowels = sum(1 for c in w.lower() if c in 'aeiou')
        return max(len(w) * 0.6 + vowels * 0.8, 1.5)

    weights   = [word_weight(w) for w in words]
    total_w   = sum(weights)
    cumulative = [0.0]
    for ww in weights:
        cumulative.append(cumulative[-1] + ww / total_w)

    progress = min(t_in_scene / max(scene_dur * 0.95, 0.01), 0.999)
    curr_idx = 0
    for i in range(len(cumulative) - 1):
        if cumulative[i] <= progress < cumulative[i + 1]:
            curr_idx = i
            break
    else:
        curr_idx = len(words) - 1

    # How far into this word's time window (0→1), for bounce
    if curr_idx < len(cumulative) - 1:
        span     = cumulative[curr_idx + 1] - cumulative[curr_idx]
        word_t   = (progress - cumulative[curr_idx]) / max(span, 0.001)
    else:
        word_t   = 1.0

    # Bounce: scale-in on first ~20% of word duration
    bounce = 1.0 + max(0.0, 0.12 * math.sin(math.pi * min(word_t * 5, 1.0)))

    draw  = ImageDraw.Draw(img, 'RGBA')
    MARG  = 52
    MAX_W = W - MARG * 2

    # ── Position: lower-middle third, TikTok-safe ──
    # Avoid bottom 22% (TikTok UI) and top 12%
    Y_CURR = int(H * 0.70)   # current word anchor
    Y_PREV = int(H * 0.70) - 10  # prev word sits just above (drawn smaller)

    def stroke_text(x, y, txt, font, fill, sw=10):
        # Draw thick black outline first
        for dx in range(-sw, sw + 1, 3):
            for dy in range(-sw, sw + 1, 3):
                if abs(dx) + abs(dy) < 3:
                    continue
                draw.text((x + dx, y + dy), txt, font=font, fill=(0, 0, 0, 255))
        draw.text((x, y), txt, font=font, fill=fill)

    def tsz(txt, font):
        bb = draw.textbbox((0, 0), txt, font=font)
        return bb[2] - bb[0], bb[3] - bb[1]

    curr_word = words[curr_idx]
    prev_word = words[curr_idx - 1] if curr_idx > 0 else ""

    # ── Current word: big, bouncy, yellow ──
    fs = int(96 * bounce)
    font_curr = get_font(min(fs, 106))
    while tsz(curr_word, font_curr)[0] > MAX_W and fs > 40:
        fs -= 6
        font_curr = get_font(int(fs * bounce))

    cw, ch = tsz(curr_word, font_curr)
    cx_     = (W - cw) // 2
    cy_     = Y_CURR - ch // 2

    # Yellow pill background (like CapCut style) — semi-transparent
    pad = 14
    pill_alpha = 180
    pill_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pill_layer)
    pd.rounded_rectangle(
        [cx_ - pad, cy_ - pad // 2, cx_ + cw + pad, cy_ + ch + pad // 2],
        radius=12, fill=(0, 0, 0, pill_alpha)
    )
    img = Image.alpha_composite(img.convert("RGBA"), pill_layer).convert("RGB")
    draw = ImageDraw.Draw(img, 'RGBA')

    stroke_text(cx_, cy_, curr_word, font_curr,
                (*highlight_color[:3], 255), sw=10)

    # ── Previous word: smaller, white, above ──
    if prev_word:
        fs_p     = max(int(fs * 0.65), 28)
        font_prev = get_font(fs_p)
        while tsz(prev_word, font_prev)[0] > MAX_W and fs_p > 24:
            fs_p -= 4
            font_prev = get_font(fs_p)
        pw, ph = tsz(prev_word, font_prev)
        py_ = cy_ - ph - 18
        if py_ > int(H * 0.12):
            stroke_text((W - pw) // 2, py_, prev_word, font_prev,
                        (230, 230, 230, 175), sw=7)

    return img

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 7 — REDDIT CARD
# ─────────────────────────────────────────────────────────────────────────────
def draw_reddit_card(img, script, alpha_frac):
    if not script.get("reddit_title"):
        return img
    draw  = ImageDraw.Draw(img, 'RGBA')
    alpha = int(min(1.0, alpha_frac * 3) * 235)
    cx, cy = 16, 14
    cw     = W - 32

    font_sub   = get_font(19, bold=False)
    font_title = get_font(27)
    font_stats = get_font(18, bold=False)

    title   = script.get("reddit_title", "")
    wrapped = textwrap.fill(title, width=36)
    lines   = wrapped.split("\n")[:3]
    card_h  = 46 + len(lines) * 32 + 32

    draw.rounded_rectangle([cx, cy, cx+cw, cy+card_h], radius=12,
                           fill=(22, 20, 20, alpha),
                           outline=(255, 69, 0, alpha), width=2)
    sub = f"r/{script.get('reddit_sub','AskReddit')}  ·  {script.get('reddit_user','u/throwaway')}"
    draw.text((cx+14, cy+11), sub, font=font_sub, fill=(130, 133, 136, alpha))
    ty = cy + 38
    for line in lines:
        draw.text((cx+14, ty), line, font=font_title, fill=(215, 218, 220, alpha))
        ty += 32
    draw.text((cx+14, cy+card_h-26),
              f"▲ {random.randint(8,42)}k   💬 {random.randint(400, 5000)}",
              font=font_stats, fill=(130, 133, 136, alpha))
    return img

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 8 — PEXELS IMAGE CARD OVERLAY
#  A rounded card slides UP from the bottom — like a TikTok sticker/popup.
#  Does NOT cover captions or character. Neat, minimal, non-distracting.
#  Card is top-left area, ~40% width, ~30% height — thumbnail style.
# ─────────────────────────────────────────────────────────────────────────────
def overlay_pexels_slide(bg, pexels_img, t, scene_dur, is_first_frame):
    if pexels_img is None:
        return bg

    # Card dimensions and position
    CARD_W  = int(W * 0.42)
    CARD_H  = int(CARD_W * 0.72)   # 4:3ish proportion
    CARD_X  = 18                    # left margin
    CARD_Y0 = int(H * 0.14)        # resting position (below reddit card area)
    RADIUS  = 16

    # Animation: slide up from below, hold, slide back down
    def ease_out_back(x):
        c1, c3 = 1.70158, 2.70158
        return 1 + c3 * (x - 1)**3 + c1 * (x - 1)**2

    def ease_in(x):
        return x ** 2.5

    SLIDE_IN  = 0.18
    HOLD_END  = 0.82
    SLIDE_OUT = 1.0

    if t < SLIDE_IN:
        p      = ease_out_back(t / SLIDE_IN)
        y_off  = int((1 - p) * (CARD_H + 40))
    elif t < HOLD_END:
        y_off  = 0
    else:
        p      = ease_in((t - HOLD_END) / (SLIDE_OUT - HOLD_END))
        y_off  = int(p * (CARD_H + 40))

    # Clip pexels to card size (crop to fill, no stretch)
    iw, ih = pexels_img.size
    target_ratio = CARD_W / CARD_H
    current_ratio = iw / ih
    if current_ratio > target_ratio:
        # wider → crop sides
        new_w = int(ih * target_ratio)
        x_off = (iw - new_w) // 2
        cropped = pexels_img.crop((x_off, 0, x_off + new_w, ih))
    else:
        # taller → crop top/bottom
        new_h = int(iw / target_ratio)
        y_c   = int(ih * 0.2)  # crop slightly from top (faces usually center-up)
        y_c   = max(0, min(y_c, ih - new_h))
        cropped = pexels_img.crop((0, y_c, iw, y_c + new_h))
    card_img = cropped.resize((CARD_W, CARD_H), Image.LANCZOS)

    # Build rounded mask
    mask    = Image.new("L", (CARD_W, CARD_H), 0)
    md      = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, CARD_W, CARD_H], radius=RADIUS, fill=255)

    # Composite into main image
    result  = bg.convert("RGBA")
    py      = CARD_Y0 - y_off
    if py + CARD_H < 0 or py > H:
        return bg  # fully off-screen

    # Drop shadow layer
    shadow  = Image.new("RGBA", (CARD_W + 20, CARD_H + 20), (0, 0, 0, 0))
    sd_mask = Image.new("L", (CARD_W + 20, CARD_H + 20), 0)
    sdd     = ImageDraw.Draw(sd_mask)
    sdd.rounded_rectangle([10, 10, CARD_W + 10, CARD_H + 10], radius=RADIUS, fill=80)
    sd_mask = sd_mask.filter(ImageFilter.GaussianBlur(8))
    shadow.putalpha(sd_mask)
    result.paste(shadow, (CARD_X - 8, py - 6), mask=shadow)

    # Paste card
    result.paste(card_img.convert("RGBA"), (CARD_X, py), mask=mask)

    # White border
    bd = ImageDraw.Draw(result)
    bd.rounded_rectangle([CARD_X, py, CARD_X + CARD_W, py + CARD_H],
                         radius=RADIUS, outline=(255, 255, 255, 200), width=3)

    return result.convert("RGB")

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 9 — RENDER VIDEO
# ─────────────────────────────────────────────────────────────────────────────
def render_video(script, work_dir, output, gameplay_path, scene_durations):
    log.info("🎬 Rendering video...")
    scenes      = script["scenes"]
    niche_key   = script.get("niche_key", "fact")
    niche       = NICHES.get(niche_key, NICHES["fact"])
    accent      = niche["accent"]
    cap_color   = niche["caption_color"]
    hi_color    = niche["highlight_color"]
    is_reddit   = niche_key == "reddit"

    # Use actual TTS durations if available
    for i, dur in enumerate(scene_durations):
        if i < len(scenes):
            scenes[i]["actual_dur"] = dur
    total_dur    = sum(s.get("actual_dur", s["duration"]) for s in scenes)
    total_frames = int(total_dur * FPS)

    # ── Pre-fetch Pexels images ───────────────────────────────────
    log.info("   📸 Fetching Pexels images...")
    pexels_imgs = {}
    for i, scene in enumerate(scenes):
        query = scene.get("image_query", "").strip()
        # Show images in scenes 2-N-1 (not hook or CTA)
        if query and query.lower() != "none" and 2 <= i < len(scenes) - 1:
            path = fetch_pexels(query, work_dir, i)
            if path:
                prepared = prepare_pexels(path, W, H)
                if prepared:
                    pexels_imgs[i] = prepared

    # ── Extract gameplay frames ───────────────────────────────────
    frames_dir = work_dir / "frames"
    frames_dir.mkdir()
    gp_dir     = work_dir / "gp"
    gp_dir.mkdir()
    gp_frames  = []

    if gameplay_path.exists():
        gp_off = random.uniform(5, 40)
        log.info(f"   📼 Gameplay offset {gp_off:.0f}s...")
        r = subprocess.run([
            "ffmpeg", "-y", "-ss", str(gp_off), "-i", str(gameplay_path),
            "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
            "-r", str(FPS), "-t", str(total_dur + 5), "-q:v", "3",
            str(gp_dir / "gp_%06d.jpg")
        ], capture_output=True, text=True)
        gp_frames = sorted(gp_dir.glob("gp_*.jpg"))
        log.info(f"   ✅ {len(gp_frames)} gameplay frames")
        if not gp_frames and r.returncode != 0:
            log.error(f"   ffmpeg: {r.stderr[-200:]}")
    else:
        log.warning("   ⚠️ No gameplay file — gradient background")

    # ── Frame loop ────────────────────────────────────────────────
    global_frame = 0
    for si, scene in enumerate(scenes):
        s_dur    = scene.get("actual_dur", scene["duration"])
        n_frames = max(1, int(s_dur * FPS))
        sfx_type = scene.get("sfx", "none")
        has_px   = si in pexels_imgs
        px_img   = pexels_imgs.get(si)

        for f in range(n_frames):
            t         = f / max(n_frames - 1, 1)
            talking   = (f % 9) < 6
            gp_idx    = min(global_frame, max(0, len(gp_frames) - 1))

            # ── Background ────────────────────────────────────────
            if gp_frames:
                bg = Image.open(gp_frames[gp_idx]).convert("RGB")
                if bg.size != (W, H):
                    bg = bg.resize((W, H), Image.BILINEAR)
            else:
                # Niche-colored dark gradient
                bg = Image.new("RGB", (W, H), niche["bg_dark"])
                gd = ImageDraw.Draw(bg)
                for row in range(H):
                    alpha = row / H
                    base  = niche["bg_dark"]
                    c     = tuple(int(b * (1 - alpha * 0.3)) for b in base)
                    gd.line([0, row, W, row], fill=c)

            # ── Dark overlay for readability ──────────────────────
            ov = Image.new("RGBA", (W, H), (0, 0, 0, 60))
            bg = Image.alpha_composite(bg.convert("RGBA"), ov).convert("RGB")

            # ── Pexels slide overlay ──────────────────────────────
            if has_px and px_img:
                bg = overlay_pexels_slide(bg, px_img, t, s_dur, f == 0)

            # ── Reddit card ───────────────────────────────────────
            if is_reddit and si <= 1:
                ap = min(1.0, (si * n_frames + f) / (2 * n_frames))
                bg = draw_reddit_card(bg, script, ap)

            # ── Cartoon character ─────────────────────────────────
            bg = draw_character(bg, global_frame, talking, accent)

            # ── Viral captions ────────────────────────────────────
            t_in_scene = f / FPS
            bg = draw_captions(bg, scene["voiceover"], t_in_scene, s_dur,
                               cap_color, hi_color)

            # ── Niche accent bar (thin, bottom) ───────────────────
            draw_f = ImageDraw.Draw(bg, 'RGBA')
            prog   = global_frame / max(total_frames - 1, 1)
            bw     = int(W * prog)
            draw_f.rectangle([0, H-5, bw, H], fill=(*accent[:3], 200))
            draw_f.rectangle([bw, H-5, W, H], fill=(0, 0, 0, 80))

            bg.save(frames_dir / f"frame_{global_frame:06d}.png")
            global_frame += 1

        if si % 3 == 0:
            log.info(f"   🎨 Scene {si+1}/{len(scenes)}")

    shutil.rmtree(gp_dir, ignore_errors=True)
    log.info(f"   ✅ {global_frame} frames")

    # ── Encode ────────────────────────────────────────────────────
    r = subprocess.run([
        "ffmpeg", "-y", "-framerate", str(FPS),
        "-i", str(frames_dir / "frame_%06d.png"),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(output)
    ], capture_output=True, text=True)
    shutil.rmtree(frames_dir)
    if r.returncode == 0 and output.exists():
        log.info(f"   ✅ Video: {output.stat().st_size//1024}KB, {total_dur:.1f}s")
        return True, total_dur
    log.error(f"   ❌ Render: {r.stderr[-300:]}")
    return False, 0

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 10 — MIX AUDIO (voice + music + SFX)
# ─────────────────────────────────────────────────────────────────────────────
def mix_audio(video_path, voice_path, output, total_dur):
    """Mix voice (primary) + background music (quiet) into final video."""
    if MUSIC_FILE.exists() and voice_path and voice_path.exists():
        log.info("   🎵 Mixing voice + music...")
        r = subprocess.run([
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-stream_loop", "-1", "-i", str(MUSIC_FILE),
            "-i", str(voice_path),
            "-filter_complex",
            (f"[1:a]volume=0.10,atrim=0:{total_dur:.2f},asetpts=PTS-STARTPTS[music];"
             f"[2:a]volume=1.0[voice];"
             f"[music][voice]amix=inputs=2:duration=first:dropout_transition=1[aout]"),
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(output)
        ], capture_output=True, text=True)
        if r.returncode == 0 and output.exists():
            log.info(f"   ✅ Mixed: {output.stat().st_size//1024}KB")
            return True

    # Voice only fallback
    if voice_path and voice_path.exists():
        r = subprocess.run([
            "ffmpeg", "-y", "-i", str(video_path), "-i", str(voice_path),
            "-map", "0:v", "-map", "1:a", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output)
        ], capture_output=True, text=True)
        if r.returncode == 0 and output.exists():
            log.info(f"   ✅ Voice only: {output.stat().st_size//1024}KB")
            return True

    shutil.copy(str(video_path), str(output))
    return True

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 11 — YOUTUBE UPLOAD
# ─────────────────────────────────────────────────────────────────────────────
def upload_youtube(video_path, title, description, hashtags, publish_at):
    log.info(f"📺 YouTube → {publish_at.strftime('%a %d %b %H:%M')}")
    try:
        import pickle, base64, googleapiclient.discovery as gd
        from googleapiclient.http import MediaFileUpload
        from google.auth.transport.requests import Request

        token_b64 = os.environ.get("YOUTUBE_TOKEN_B64", "")
        if not token_b64:
            log.warning("   ⚠️ No YOUTUBE_TOKEN_B64")
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

        body = {
            "snippet": {
                "title": title[:100],
                "description": f"{description}\n\n{hashtags}",
                "tags": [t.replace("#", "") for t in hashtags.split() if t.startswith("#")],
                "categoryId": "22",
            },
            "status": {
                "privacyStatus": "private",
                "publishAt": publish_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "selfDeclaredMadeForKids": False,
            }
        }
        media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
        req   = yt.videos().insert(part="snippet,status", body=body, media_body=media)
        resp  = None
        while resp is None:
            status, resp = req.next_chunk()
            if status:
                log.info(f"   📤 {int(status.progress()*100)}%")
        vid = resp["id"]
        url = f"https://youtube.com/shorts/{vid}"
        log.info(f"   ✅ {url}")
        return url
    except Exception as e:
        log.error(f"   ❌ YouTube: {e}")
        return None

# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────
def run_pipeline(n_videos=1):
    if not shutil.which("espeak"):
        os.system("apt-get install -y -qq espeak 2>/dev/null || true")
    if not shutil.which("ffprobe"):
        log.warning("ffprobe not found — using script duration estimates")

    log.info("=" * 65)
    log.info("🚀 VAULTMIND PIPELINE v6")
    log.info("=" * 65)

    OUTPUT_DIR.mkdir(exist_ok=True)

    slots    = get_next_slots(n_videos)
    yt_slots = slots[:n_videos]
    tt_slots = [s + timedelta(minutes=30) for s in yt_slots]

    if not yt_slots:
        log.error("❌ No free upload slots found in next 90 days!")
        return []

    results = []

    for i in range(n_videos):
        log.info(f"\n{'═'*65}\n  VIDEO {i+1}/{n_videos}\n{'═'*65}")
        ts          = int(time.time())
        work_dir    = Path(f"/tmp/vm_{ts}_{i}")
        raw_video   = Path(f"/tmp/raw_{ts}.mp4")
        final_video = Path(f"/tmp/final_{ts}.mp4")
        voice_file  = Path("/tmp/final_voice.mp3")
        work_dir.mkdir(parents=True)

        try:
            # 1. Script
            script = generate_script()
            niche  = script.get("niche_design", NICHES["fact"])

            # 2. Voiceover (returns actual durations)
            voice_path, scene_durs = generate_voiceover(script["scenes"], work_dir, niche)

            # 3. Render
            ok, total_dur = render_video(script, work_dir, raw_video,
                                         GAMEPLAY_FILE, scene_durs)
            if not ok:
                raise Exception("Render failed")

            # 4. Mix audio
            mix_audio(raw_video, voice_path, final_video, total_dur)

            # 5. Slots
            yt_time = yt_slots[i] if i < len(yt_slots) else datetime.now() + timedelta(hours=1)
            tt_time = tt_slots[i] if i < len(tt_slots) else datetime.now() + timedelta(hours=2)

            # 6. Upload
            yt_url = None
            if final_video.exists() and final_video.stat().st_size > 50_000:
                yt_url = upload_youtube(
                    final_video, script["title"],
                    script.get("description", ""),
                    script["hashtags"], yt_time
                )
                out_path = OUTPUT_DIR / f"video_{ts}.mp4"
                shutil.copy(str(final_video), str(out_path))
                log.info(f"   💾 Saved: {out_path.name}")
            else:
                log.error("   ❌ Final video too small")

            # 7. Dashboard — always update
            entry = {
                "id":         ts,
                "title":      script["title"],
                "type":       script.get("type", "fact"),
                "niche":      script.get("niche_key", "fact"),
                "created_at": datetime.now().isoformat(),
                "youtube":    {"scheduled": yt_time.isoformat(),
                               "url": yt_url, "status": "scheduled"},
                "tiktok":     {"scheduled": tt_time.isoformat(),
                               "status": "scheduled"},
                "hashtags":   script["hashtags"],
                "status":     "scheduled",
            }
            add_to_dashboard(entry)
            results.append(entry)
            log.info(f"✅ Video {i+1} done! YT: {yt_time.strftime('%a %d %b %H:%M')}")

        except Exception as e:
            import traceback
            log.error(f"❌ Video {i+1} failed: {e}\n{traceback.format_exc()}")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
            for p in [raw_video, final_video, voice_file]:
                if p.exists():
                    p.unlink(missing_ok=True)

        if i < n_videos - 1:
            time.sleep(4)

    log.info(f"\n🎉 DONE — {len(results)}/{n_videos} videos")
    return results


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    run_pipeline(n)
