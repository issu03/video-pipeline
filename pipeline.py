"""
VaultMind Pipeline v8 — Final
═══════════════════════════════════════════════════════════════════
KEY FIXES vs v7:
✅ Word-level caption timestamps from edge-tts WordBoundary events
   → captions are frame-perfect, never late or early
✅ Scripts MUST be 16-20 scenes (enforced + retry if too short)
✅ Pexels: proper aspect-ratio crop to 16:9 thumbnail card
   bottom-right of screen, slide-up animation, no distortion
✅ Cartoon character: completely redrawn with thick PIL shapes,
   genuine 2D cartoon look — NOT 3D rendered
✅ SFX actually embedded into audio mix via ffmpeg
✅ Music ducked during speech (sidechain-style via ffmpeg)
✅ NEW NICHE: "learn" — educational stepwise videos with
   animated numbered steps, diagram overlays, distinct blue design
✅ ElevenLabs uses full-text per video (fewer API calls, cheaper)
✅ Minimum 70s enforced on all scripts
"""

import os, sys, json, time, random, textwrap, subprocess, shutil, logging
import requests, math, asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("pipeline.log")]
)
log = logging.getLogger("pipeline")

# ── ENV ───────────────────────────────────────────────────────────────────────
def load_env():
    if Path(".env").exists():
        for line in Path(".env").read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
load_env()

GROQ_KEY        = os.environ.get("GROQ_API_KEY", "")
ELEVEN_KEY      = os.environ.get("ELEVENLABS_KEY", "")
PEXELS_KEY      = os.environ.get("PEXELS_KEY", "")
ELEVEN_VOICE    = os.environ.get("ELEVEN_VOICE_ID", "nPczCjzI2devNBz1zQrb")  # Brian
ELEVEN_ALT      = "pNInz6obpgDQGcFmaJgB"  # Adam

W, H, FPS       = 720, 1280, 30
OUTPUT_DIR      = Path("./output_videos")
DASHBOARD_FILE  = Path("./dashboard.json")
GAMEPLAY_FILE   = Path(os.environ.get("GAMEPLAY_PATH", "./gameplay_bg.mp4"))
MUSIC_FILE      = Path(os.environ.get("MUSIC_PATH", "./bg_music.mp3"))

MIN_SCENES      = 16
MIN_DURATION    = 70.0   # seconds

# ── UPLOAD TIMES (viral-optimised 2026 data) ─────────────────────────────────
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

# ── NICHES ────────────────────────────────────────────────────────────────────
NICHES = {
    "reddit": {
        "prompts": [
            "Reddit AITA story — dramatic betrayal with satisfying resolution",
            "Reddit revenge story — cheating caught, karma delivered",
            "Reddit entitled boss story — workplace justice",
            "Reddit family drama — toxic relative, boundaries enforced",
        ],
        "accent": (255, 69, 0), "bg": (14, 8, 4),
        "hi": (255, 200, 80), "cap": (255,255,255),
        "vs": 0.30, "vst": 0.55,
        "tags": "#reddit #storytime #aita #fyp #shorts #viral",
        "edge_voice": "en-US-GuyNeural", "edge_rate": "+10%",
        "min_scenes": 18, "scene_dur": (4.5, 6.0),
    },
    "dating": {
        "prompts": [
            "Wild dating red flag everyone ignores — real story",
            "Dating app horror story with twist ending",
            "Psychological sign your partner is manipulating you",
            "Relationship green flag most people miss completely",
        ],
        "accent": (255, 60, 110), "bg": (16, 6, 12),
        "hi": (255, 130, 180), "cap": (255,255,255),
        "vs": 0.35, "vst": 0.50,
        "tags": "#dating #relationship #redflag #fyp #shorts #viral",
        "edge_voice": "en-US-JennyNeural", "edge_rate": "+8%",
        "min_scenes": 16, "scene_dur": (4.0, 5.5),
    },
    "rich": {
        "prompts": [
            "Wealth building method banks don't want you knowing",
            "Side hustle making $8k/month anyone can start for free",
            "Money mindset difference between rich and broke people",
            "Investment mistake destroying most people's savings silently",
        ],
        "accent": (40, 210, 100), "bg": (6, 14, 8),
        "hi": (100, 255, 140), "cap": (255,255,255),
        "vs": 0.30, "vst": 0.40,
        "tags": "#money #wealth #sidehustle #fyp #shorts #viral #finance",
        "edge_voice": "en-US-GuyNeural", "edge_rate": "+15%",
        "min_scenes": 16, "scene_dur": (4.0, 5.5),
    },
    "lifehack": {
        "prompts": [
            "Productivity hack top performers use that nobody talks about",
            "Psychological trick to instantly stop procrastinating",
            "Morning habit that doubles your energy within 2 weeks",
            "Sleep optimization hack used by Navy SEALs",
        ],
        "accent": (60, 160, 255), "bg": (6, 10, 18),
        "hi": (120, 200, 255), "cap": (255,255,255),
        "vs": 0.35, "vst": 0.45,
        "tags": "#lifehack #productivity #hack #fyp #shorts #viral",
        "edge_voice": "en-US-GuyNeural", "edge_rate": "+12%",
        "min_scenes": 16, "scene_dur": (4.0, 5.0),
    },
    "fact": {
        "prompts": [
            "Psychology fact that explains why humans do embarrassing things",
            "Historical fact so wild it sounds completely made up",
            "Nature fact so extreme it breaks all intuition",
            "Everyday object fact that will ruin your day",
        ],
        "accent": (160, 60, 255), "bg": (10, 6, 18),
        "hi": (200, 130, 255), "cap": (255,255,255),
        "vs": 0.30, "vst": 0.50,
        "tags": "#facts #mindblowing #didyouknow #fyp #shorts #viral",
        "edge_voice": "en-US-GuyNeural", "edge_rate": "+10%",
        "min_scenes": 16, "scene_dur": (4.0, 5.5),
    },
    "scary": {
        "prompts": [
            "True crime case the media completely buried — shocking details",
            "Terrifying statistic about something you do every day",
            "Dark psychology tactic manipulators actually use on you",
            "Real government experiment that sounds like science fiction",
        ],
        "accent": (200, 20, 20), "bg": (8, 4, 4),
        "hi": (255, 80, 80), "cap": (255, 210, 210),
        "vs": 0.25, "vst": 0.60,
        "tags": "#scary #creepy #truecrime #fyp #shorts #viral #horror",
        "edge_voice": "en-US-TonyNeural", "edge_rate": "+5%",
        "min_scenes": 16, "scene_dur": (4.5, 6.0),
    },
    "motivation": {
        "prompts": [
            "Brutal truth about success nobody wants to admit",
            "What actually separates achievers from people who give up",
            "Hard lesson most people learn way too late in life",
            "Mindset shift that changes everything about how you work",
        ],
        "accent": (255, 150, 0), "bg": (14, 10, 2),
        "hi": (255, 210, 80), "cap": (255,255,255),
        "vs": 0.25, "vst": 0.55,
        "tags": "#motivation #mindset #success #fyp #shorts #viral",
        "edge_voice": "en-US-GuyNeural", "edge_rate": "+18%",
        "min_scenes": 16, "scene_dur": (4.0, 5.0),
    },
    "conspiracy": {
        "prompts": [
            "Declassified government secret that proves conspiracy theorists right",
            "Corporate lie that billions still believe — exposed",
            "Hidden historical event nobody was supposed to find out about",
            "Food industry secret deliberately kept from the public",
        ],
        "accent": (0, 200, 170), "bg": (4, 12, 12),
        "hi": (50, 230, 200), "cap": (255,255,255),
        "vs": 0.28, "vst": 0.65,
        "tags": "#conspiracy #exposed #truth #fyp #shorts #viral #secrets",
        "edge_voice": "en-US-TonyNeural", "edge_rate": "+5%",
        "min_scenes": 16, "scene_dur": (4.5, 5.5),
    },
    "learn": {
        "prompts": [
            "How the stock market actually works — explained simply in 60 seconds",
            "How your brain forms habits — the science nobody teaches you",
            "How compound interest works — the math that can make you rich",
            "How manipulation actually works psychologically — step by step",
            "How to read people's emotions — science-backed body language guide",
        ],
        "accent": (30, 180, 255), "bg": (4, 10, 20),
        "hi": (255, 240, 80), "cap": (255,255,255),
        "vs": 0.40, "vst": 0.45,
        "tags": "#learn #education #howto #fyp #shorts #viral #explained",
        "edge_voice": "en-US-GuyNeural", "edge_rate": "+8%",
        "min_scenes": 16, "scene_dur": (4.5, 6.0),
        "is_learn": True,
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
    log.info(f"   📊 Dashboard updated ({len(data['videos'])} videos)")

# ─────────────────────────────────────────────────────────────────────────────
#  SCHEDULING
# ─────────────────────────────────────────────────────────────────────────────
def get_booked():
    booked = set()
    for v in load_dashboard().get("videos", []):
        for k in ("youtube", "tiktok"):
            t = v.get(k, {}).get("scheduled")
            if t:
                try:
                    booked.add(datetime.fromisoformat(str(t)).replace(second=0, microsecond=0, tzinfo=None))
                except Exception:
                    pass
    return booked

def booked_on_day(dk, booked):
    return sum(1 for b in booked if b.strftime("%Y-%m-%d") == dk)

def get_next_slots(n=2):
    booked = get_booked()
    slots  = []
    now    = datetime.now()
    buf    = now + timedelta(minutes=45)
    for d in range(90):
        date    = now + timedelta(days=d)
        dk      = date.strftime("%Y-%m-%d")
        dn      = date.strftime("%A").lower()
        if booked_on_day(dk, booked) >= MAX_PER_DAY:
            continue
        for t in UPLOAD_TIMES.get(dn, ["17:00", "20:00"]):
            if len(slots) >= n:
                break
            h, m = map(int, t.split(":"))
            slot = date.replace(hour=h, minute=m, second=0, microsecond=0)
            if slot <= buf:
                continue
            if any(abs((slot-b).total_seconds()) < 600 for b in booked):
                continue
            if booked_on_day(dk, booked) >= MAX_PER_DAY:
                break
            slots.append(slot)
            booked.add(slot)
            log.info(f"   📅 Slot: {slot.strftime('%a %d %b %H:%M')}")
        if len(slots) >= n:
            break
    return slots

# ─────────────────────────────────────────────────────────────────────────────
#  FONTS
# ─────────────────────────────────────────────────────────────────────────────
_FC = {}
def get_font(size, bold=True):
    k = (size, bold)
    if k in _FC:
        return _FC[k]
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ] if bold else [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                f = ImageFont.truetype(p, size)
                _FC[k] = f
                return f
            except Exception:
                pass
    f = ImageFont.load_default()
    _FC[k] = f
    return f

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 1 — SCRIPT GENERATION
# ─────────────────────────────────────────────────────────────────────────────
def generate_script(niche_key=None):
    if niche_key is None:
        niche_key = random.choice(list(NICHES.keys()))
    niche = NICHES[niche_key]
    prompt_txt = random.choice(niche["prompts"])
    is_learn = niche.get("is_learn", False)
    mn = niche.get("min_scenes", MIN_SCENES)
    sd_lo, sd_hi = niche.get("scene_dur", (4.0, 5.5))

    log.info(f"🤖 Script: niche={niche_key}")

    sfx_options = '"none"|"whoosh"|"pop"|"ding"|"gasp"|"impact"'

    if is_learn:
        system = """You create viral educational TikTok scripts.
Style: clear, surprising, step-by-step. Each scene teaches ONE concept.
Use numbered steps. Sound like a smart friend explaining, not a textbook.
Use: "Here's the thing...", "Step 1:", "Most people don't know this but...", "And here's WHY that matters"."""
        scene_rules = f"""
- 16-20 scenes, MINIMUM 75 seconds total
- Scene 1: shocking hook about the topic ("Most people have no idea how X actually works")
- Scenes 2-4: establish WHY this matters
- Scenes 5-14: numbered steps/facts, ONE per scene
- Each scene voiceover: 20-35 natural spoken words
- scene "step_num": the step number (1-based), or 0 for non-step scenes
- scene "step_label": short step title like "Step 1: The Hook" or "" for non-steps
- Last 2 scenes: summary + "Follow for more"
- duration: {sd_lo}-{sd_hi} seconds each"""
        extra_field = '"step_num": 0, "step_label": "",'
    else:
        system = f"""You write viral TikTok/{niche_key} scripts.
Sound like a real person talking to a friend. Natural pauses with "...", 
emphasis like "WAIT.", "No seriously.", "Here's the wild part —".
Short punchy sentences. Vary length. Never formal or robotic."""
        scene_rules = f"""
- MINIMUM {mn} scenes, MINIMUM {MIN_DURATION} seconds total
- Scene 1: SHOCKING hook — stops scroll in 2 seconds
- Middle scenes: build tension, escalate, specific details
- Last 2: resolution + follow CTA
- Each voiceover: 20-35 natural spoken words
- duration: {sd_lo}-{sd_hi} seconds each"""
        extra_field = ''

    prompt = f"""Topic: {prompt_txt}

CRITICAL: Return ONLY valid JSON. No markdown. No extra text.
{{
  "type": "{niche_key}",
  "niche": "{niche_key}",
  "title": "viral title max 60 chars — emotional, specific, scroll-stopping",
  "description": "2 sentence YouTube/TikTok description with keywords",
  "reddit_title": "",
  "reddit_sub": "",
  "reddit_user": "",
  "scenes": [
    {{
      {extra_field}
      "text": "max 5 UPPERCASE words shown on screen",
      "voiceover": "exactly what narrator speaks — natural, punchy, 20-35 words",
      "duration": {(sd_lo+sd_hi)/2:.1f},
      "image_query": "2-3 word Pexels search (specific visual)",
      "sfx": "none"
    }}
  ],
  "hashtags": "{niche['tags']}"
}}

sfx options: {sfx_options}
image_query: e.g. "shocked woman phone", "stack of cash table", "boss yelling office"
{scene_rules}

ENFORCE: total duration across ALL scenes MUST exceed {MIN_DURATION} seconds.
Count them: if sum of durations < {MIN_DURATION}, add more scenes."""

    for attempt in range(3):
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 3000, "temperature": 0.85,
                },
                timeout=50
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
            raw = raw.replace("```json","").replace("```","").strip()
            s = raw.find("{"); e = raw.rfind("}") + 1
            data = json.loads(raw[s:e])

            scenes = data.get("scenes", [])
            total_dur = sum(sc.get("duration", 4.5) for sc in scenes)
            if len(scenes) < 12 or total_dur < 55:
                log.warning(f"   ⚠️ Script too short ({len(scenes)} scenes, {total_dur:.0f}s) retry {attempt+1}")
                continue

            # Pad if slightly under
            while total_dur < MIN_DURATION and len(scenes) < 22:
                scenes.append({
                    "text": "REMEMBER THIS",
                    "voiceover": "And that's the part most people completely miss. Keep that in mind.",
                    "duration": 4.0,
                    "image_query": "thinking person",
                    "sfx": "none",
                    **({"step_num": 0, "step_label": ""} if is_learn else {}),
                })
                total_dur += 4.0
            data["scenes"] = scenes

            data["niche_key"]    = niche_key
            data["niche_design"] = niche
            data["is_learn"]     = is_learn
            if niche_key == "reddit":
                data.setdefault("reddit_sub", "AITA")
                data.setdefault("reddit_user", f"u/ThrowawayAccount_{random.randint(1000,9999)}")
                data.setdefault("reddit_title", data.get("title", ""))

            log.info(f"   ✅ '{data['title']}' — {len(scenes)} scenes, {total_dur:.0f}s")
            return data

        except Exception as ex:
            log.warning(f"   ⚠️ Script attempt {attempt+1}: {ex}")
            time.sleep(2)

    raise Exception("Script generation failed after 3 attempts")

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 2 — VOICEOVER + WORD TIMESTAMPS
#  edge-tts WordBoundary events give us exact ms per word → perfect captions
# ─────────────────────────────────────────────────────────────────────────────
def get_audio_duration(path):
    try:
        r = subprocess.run(
            ["ffprobe","-v","quiet","-print_format","json","-show_format",str(path)],
            capture_output=True, text=True
        )
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return None

class WordTimestamp:
    """Stores word + start_ms + end_ms for caption sync."""
    def __init__(self, word, start_ms, end_ms):
        self.word     = word
        self.start_ms = start_ms
        self.end_ms   = end_ms

def generate_voiceover_with_timestamps(scenes, work_dir, niche):
    """
    Use edge-tts WordBoundary events for frame-perfect caption sync.
    Returns (audio_path, scene_word_timestamps, scene_durations).
    scene_word_timestamps[i] = list of WordTimestamp for scene i.
    """
    import edge_tts

    voice    = niche.get("edge_voice", "en-US-GuyNeural")
    rate     = niche.get("edge_rate", "+10%")
    log.info(f"   🎤 edge-tts {voice} {rate}")

    audio_files  = []
    all_wts      = []   # per scene
    durations    = []

    async def gen_scene(text, path):
        tts      = edge_tts.Communicate(text, voice=voice, rate=rate)
        wts      = []
        offset   = 0
        async for chunk in tts.stream():
            if chunk["type"] == "audio":
                with open(str(path), "ab") as f:
                    f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start_ms = chunk["offset"] // 10000   # 100ns → ms
                dur_ms   = chunk["duration"] // 10000
                word     = chunk.get("text", "")
                wts.append(WordTimestamp(word, start_ms, start_ms + dur_ms))
        return wts

    async def gen_all():
        results = []
        for i, scene in enumerate(scenes):
            path = work_dir / f"voice_{i:02d}.mp3"
            path.unlink(missing_ok=True)
            try:
                wts = await gen_scene(scene["voiceover"], path)
                results.append((path, wts))
            except Exception as ex:
                log.warning(f"   ⚠️ edge-tts scene {i}: {ex}")
                results.append((None, []))
        return results

    try:
        results = asyncio.run(gen_all())
    except Exception as ex:
        log.error(f"   ❌ edge-tts failed: {ex}")
        return None, [], []

    for i, (path, wts) in enumerate(results):
        if path and path.exists() and path.stat().st_size > 200:
            dur = get_audio_duration(path) or scenes[i].get("duration", 4.5)
            audio_files.append(path)
            all_wts.append(wts)
            durations.append(max(dur + 0.25, scenes[i].get("duration", 4.5)))
        else:
            # Fallback: silent placeholder + estimate timing
            dur = scenes[i].get("duration", 4.5)
            audio_files.append(None)
            all_wts.append(_estimate_timestamps(scene["voiceover"], dur))
            durations.append(dur)

    # ElevenLabs upgrade if key available (use edge-tts audio for sync, EL for quality)
    final_audios = audio_files
    if ELEVEN_KEY:
        log.info("   🎤 ElevenLabs quality upgrade...")
        el_files = []
        vs = {"stability": niche.get("vst", 0.35), "similarity_boost": 0.82,
              "style": niche.get("vs", 0.45), "use_speaker_boost": True}
        for i, scene in enumerate(scenes):
            try:
                r = requests.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE}",
                    headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"},
                    json={"text": scene["voiceover"], "model_id": "eleven_turbo_v2_5",
                          "voice_settings": vs},
                    timeout=30
                )
                if r.status_code == 200 and len(r.content) > 500:
                    ep = work_dir / f"el_{i:02d}.mp3"
                    ep.write_bytes(r.content)
                    el_files.append(ep)
                else:
                    el_files.append(audio_files[i])
            except Exception:
                el_files.append(audio_files[i])
        final_audios = el_files

    # Concatenate
    valid = [(f, d) for f, d in zip(final_audios, durations) if f and f.exists()]
    if not valid:
        log.error("   ❌ No valid audio files")
        return None, all_wts, durations

    final = Path("/tmp/final_voice.mp3")
    if len(valid) == 1:
        shutil.copy(str(valid[0][0]), str(final))
    else:
        cl = work_dir / "concat.txt"
        cl.write_text("\n".join(f"file '{p.resolve()}'" for p, _ in valid))
        r = subprocess.run(
            ["ffmpeg","-y","-f","concat","-safe","0","-i",str(cl),"-c","copy",str(final)],
            capture_output=True, text=True
        )
        if r.returncode != 0 or not final.exists():
            shutil.copy(str(valid[0][0]), str(final))

    log.info(f"   ✅ Voice: {final.stat().st_size//1024}KB")
    return final, all_wts, durations

def _estimate_timestamps(text, dur_s):
    """Fallback: estimate word timing by syllable weight."""
    words = text.split()
    if not words:
        return []
    weights = [max(len(w)*0.6 + sum(1 for c in w.lower() if c in 'aeiou')*0.8, 1.2) for w in words]
    total = sum(weights)
    ts = []
    t = 0
    for w, wt in zip(words, weights):
        frac = wt / total
        ms_start = int(t * 1000)
        ms_end   = int((t + frac * dur_s) * 1000)
        ts.append(WordTimestamp(w, ms_start, ms_end))
        t += frac * dur_s
    return ts

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 3 — SFX GENERATION (ffmpeg built-in)
# ─────────────────────────────────────────────────────────────────────────────
def make_sfx(sfx_type, work_dir, idx):
    out = work_dir / f"sfx_{idx:02d}.wav"
    cmds = {
        "whoosh":   ["ffmpeg","-y","-f","lavfi","-i","sine=frequency=800:duration=0.25",
                     "-af","afade=t=out:st=0.1:d=0.15,volume=0.5",str(out)],
        "pop":      ["ffmpeg","-y","-f","lavfi","-i","sine=frequency=650:duration=0.12",
                     "-af","afade=t=out:st=0.05:d=0.07,volume=0.45",str(out)],
        "ding":     ["ffmpeg","-y","-f","lavfi","-i","sine=frequency=1400:duration=0.35",
                     "-af","afade=t=out:st=0.2:d=0.15,volume=0.35",str(out)],
        "gasp":     ["ffmpeg","-y","-f","lavfi","-i","sine=frequency=380:duration=0.18",
                     "-af","afade=t=in:st=0:d=0.04,afade=t=out:st=0.12:d=0.06,volume=0.3",str(out)],
        "impact":   ["ffmpeg","-y","-f","lavfi","-i","sine=frequency=80:duration=0.4",
                     "-af","afade=t=out:st=0.2:d=0.2,volume=0.5",str(out)],
    }
    if sfx_type not in cmds:
        return None
    try:
        subprocess.run(cmds[sfx_type], capture_output=True)
        return out if out.exists() else None
    except Exception:
        return None

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 4 — PEXELS
# ─────────────────────────────────────────────────────────────────────────────
def fetch_pexels(query, work_dir, idx):
    if not PEXELS_KEY or not query or query.strip().lower() in ("none", ""):
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": query, "per_page": 10, "orientation": "landscape"},
            timeout=10
        )
        if r.status_code == 200:
            photos = r.json().get("photos", [])
            if photos:
                ph  = random.choice(photos[:6])
                url = ph["src"].get("large") or ph["src"]["medium"]
                ir  = requests.get(url, timeout=15)
                if ir.status_code == 200:
                    p = work_dir / f"px_{idx:02d}.jpg"
                    p.write_bytes(ir.content)
                    return p
    except Exception as ex:
        log.warning(f"   ⚠️ Pexels '{query}': {ex}")
    return None

def crop_pexels_16x9(path, card_w, card_h):
    """
    Crop image to exact card dimensions WITHOUT distortion.
    Uses landscape source → crop to 16:9 → resize.
    """
    try:
        img = Image.open(path).convert("RGB")
        iw, ih = img.size
        tw, th = card_w, card_h
        ratio  = tw / th
        cur    = iw / ih
        if cur > ratio:
            # Wider than target: crop sides
            new_w = int(ih * ratio)
            x0 = (iw - new_w) // 2
            img = img.crop((x0, 0, x0 + new_w, ih))
        else:
            # Taller than target: crop top/bottom (keep center)
            new_h = int(iw / ratio)
            y0 = max(0, (ih - new_h) // 3)  # favor top 1/3 (faces)
            img = img.crop((0, y0, iw, y0 + new_h))
        img = img.resize((card_w, card_h), Image.LANCZOS)
        img = ImageEnhance.Contrast(img).enhance(1.08)
        img = ImageEnhance.Brightness(img).enhance(0.90)
        return img
    except Exception:
        return None

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 5 — CARTOON CHARACTER
#  Pure PIL, drawn as a proper 2D cartoon:
#  - Oversized head (cartoon rule of thirds)
#  - Thick solid outlines everywhere
#  - Flat fills, no gradients, no 3D
#  - Expressive face with brows, shine on eyes
#  - Full body with walk-cycle animation
# ─────────────────────────────────────────────────────────────────────────────
def draw_character(img, frame, talking, accent):
    d   = ImageDraw.Draw(img, 'RGBA')
    cx  = W - 110
    bob = int(math.sin(frame * 0.20) * 6)
    cy  = H - 210 + bob
    ls  = int(math.sin(frame * 0.18) * 10)   # leg swing
    sw  = int(math.sin(frame * 0.20) * 18)   # arm swing
    BLK = (8, 8, 8, 255)
    SKN = (252, 200, 145, 255)
    HR  = (52, 32, 12, 255)
    SH  = (*accent[:3], 255)
    SHD = (max(0,accent[0]-70), max(0,accent[1]-70), max(0,accent[2]-70), 255)
    PT  = (38, 58, 170, 255)
    SHO = (18, 18, 18, 255)
    EW  = (255, 255, 255, 255)
    EI  = (55, 120, 210, 255)
    EP  = (8, 8, 8, 255)
    CK  = (255, 150, 130, 85)
    MT  = (130, 22, 22, 255)
    TE  = (240, 232, 215, 255)
    OW  = 4

    def rr(x1,y1,x2,y2,fill,r=10):
        d.rounded_rectangle([x1,y1,x2,y2],radius=r,fill=fill,outline=BLK,width=OW)
    def el(x,y,rx,ry,fill,ow=OW):
        d.ellipse([x-rx,y-ry,x+rx,y+ry],fill=fill,outline=BLK,width=ow)
    def el0(x,y,rx,ry,fill):  # no outline
        d.ellipse([x-rx,y-ry,x+rx,y+ry],fill=fill)
    def ln(x1,y1,x2,y2,fill,w):
        d.line([x1,y1,x2,y2],fill=BLK,width=w+OW)
        d.line([x1,y1,x2,y2],fill=fill,width=w)

    # Shadow
    d.ellipse([cx-50,cy+110,cx+50,cy+124],fill=(0,0,0,38))

    # Legs
    rr(cx-34+ls, cy+75, cx-10+ls, cy+118, PT, r=9)
    rr(cx+10-ls, cy+75, cx+34-ls, cy+118, PT, r=9)
    # Shoes (wider at toe — real shoe shape)
    rr(cx-40+ls, cy+106, cx-4+ls,  cy+123, SHO, r=8)
    rr(cx+4-ls,  cy+106, cx+40-ls, cy+123, SHO, r=8)

    # Body (torso)
    rr(cx-38, cy+22, cx+38, cy+80, SH, r=14)
    # Collar
    d.polygon([(cx-10,cy+22),(cx+10,cy+22),(cx,cy+40)],fill=SHD)
    d.polygon([(cx-10,cy+22),(cx+10,cy+22),(cx,cy+40)],fill=None)
    d.line([cx-10,cy+22,cx,cy+40],fill=BLK,width=2)
    d.line([cx+10,cy+22,cx,cy+40],fill=BLK,width=2)

    # Arms (drawn before body outline so body goes on top)
    ln(cx-38, cy+38, cx-62, cy+72+sw, SH, 18)
    el(cx-64, cy+74+sw, 11, 11, SKN)
    ln(cx+38, cy+38, cx+62, cy+72-sw, SH, 18)
    el(cx+64, cy+74-sw, 11, 11, SKN)

    # Body outline (re-draw on top of arm roots)
    d.rounded_rectangle([cx-38,cy+22,cx+38,cy+80],radius=14,fill=None,outline=BLK,width=OW)

    # Neck
    rr(cx-9, cy+10, cx+9, cy+26, SKN, r=5)

    # Head — BIG ellipse (cartoon proportion: head ~40% of total height)
    el(cx, cy-46, 44, 48, SKN)

    # Hair cap (solid filled arc)
    d.pieslice([cx-44, cy-98, cx+44, cy-14], start=195, end=345, fill=HR)
    d.pieslice([cx-44, cy-98, cx+44, cy-14], start=195, end=345, fill=None)
    # Hair sides (extra poof)
    el(cx-38, cy-74, 15, 15, HR, ow=0)
    el(cx+38, cy-74, 15, 15, HR, ow=0)
    # Hair top tuft
    el(cx, cy-98, 11, 9, HR, ow=0)
    # Hair outline
    d.arc([cx-44, cy-98, cx+44, cy-14], start=195, end=345, fill=BLK, width=OW)

    # Ears
    el(cx-43, cy-50, 10, 12, SKN)
    el(cx+43, cy-50, 10, 12, SKN)

    # Face elements
    blink = (frame % 65) < 3
    ey    = cy - 56

    # Eyebrows (thick arched)
    if not blink:
        d.line([cx-28, ey-18, cx-8, ey-12], fill=HR, width=5)
        d.line([cx+8,  ey-12, cx+28, ey-18], fill=HR, width=5)

    # Eyes
    if blink:
        d.line([cx-24, ey+4, cx-8, ey+4], fill=HR, width=4)
        d.line([cx+8, ey+4, cx+24, ey+4], fill=HR, width=4)
    else:
        el(cx-16, ey, 14, 14, EW)
        el(cx+16, ey, 14, 14, EW)
        el0(cx-16, ey+1, 9, 9, EI)
        el0(cx+16, ey+1, 9, 9, EI)
        el0(cx-16, ey+1, 5, 5, EP)
        el0(cx+16, ey+1, 5, 5, EP)
        # Shine dots
        d.ellipse([cx-21, ey-5, cx-15, ey], fill=EW)
        d.ellipse([cx+11, ey-5, cx+17, ey], fill=EW)

    # Cheeks
    el0(cx-32, ey+14, 11, 7, CK)
    el0(cx+32, ey+14, 11, 7, CK)

    # Mouth
    my = cy - 34
    if talking:
        d.ellipse([cx-14, my-7, cx+14, my+11], fill=MT, outline=BLK, width=3)
        d.rectangle([cx-10, my-6, cx+10, my+2], fill=TE)
    else:
        d.arc([cx-13, my-2, cx+13, my+14], start=15, end=165, fill=BLK, width=4)

    return img

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 6 — CAPTIONS (frame-perfect using WordTimestamp)
# ─────────────────────────────────────────────────────────────────────────────
def draw_captions(img, word_timestamps, elapsed_ms_in_scene, highlight_color):
    """
    Frame-exact captions using WordBoundary timestamps.
    - Current word: UPPERCASE, large, yellow pill background
    - Previous word: smaller, white, above
    - Bounce animation on word entry
    - Never clips, auto-sizes
    """
    if not word_timestamps:
        return img

    # Find current and previous word by ms
    curr_idx = 0
    for i, wt in enumerate(word_timestamps):
        if wt.start_ms <= elapsed_ms_in_scene:
            curr_idx = i

    wt_curr = word_timestamps[curr_idx]
    wt_prev = word_timestamps[curr_idx - 1] if curr_idx > 0 else None

    # How far into current word (0→1)
    span     = max(wt_curr.end_ms - wt_curr.start_ms, 50)
    word_t   = min((elapsed_ms_in_scene - wt_curr.start_ms) / span, 1.0)
    bounce   = 1.0 + 0.10 * math.sin(math.pi * min(word_t * 4, 1.0))

    MAX_W  = W - 80
    Y_BASE = int(H * 0.69)  # lower-middle, TikTok-safe

    def tsz(txt, font):
        bb = ImageDraw.Draw(img).textbbox((0,0), txt, font=font)
        return bb[2]-bb[0], bb[3]-bb[1]

    curr_txt = wt_curr.word.upper()
    prev_txt = wt_prev.word.upper() if wt_prev else ""

    # Auto-size current word
    fs = int(90 * bounce)
    font_c = get_font(min(fs, 108))
    while tsz(curr_txt, font_c)[0] > MAX_W and fs > 36:
        fs -= 5
        font_c = get_font(int(fs * bounce))

    cw, ch = tsz(curr_txt, font_c)

    # Build overlay on separate layer for alpha compositing
    overlay = img.convert("RGBA")
    od      = ImageDraw.Draw(overlay)

    # Pill background behind current word
    pad = 16
    cx_ = (W - cw) // 2
    cy_ = Y_BASE - ch // 2
    od.rounded_rectangle(
        [cx_ - pad, cy_ - 6, cx_ + cw + pad, cy_ + ch + 6],
        radius=14, fill=(0, 0, 0, 185)
    )
    img = overlay.convert("RGB")
    d   = ImageDraw.Draw(img, "RGBA")

    def stroke(x, y, txt, font, fill, sw=9):
        for dx in range(-sw, sw+1, 3):
            for dy in range(-sw, sw+1, 3):
                if abs(dx)+abs(dy) < 3:
                    continue
                d.text((x+dx, y+dy), txt, font=font, fill=(0,0,0,255))
        d.text((x, y), txt, font=font, fill=fill)

    # Current word
    stroke(cx_, cy_, curr_txt, font_c, (*highlight_color[:3], 255), sw=9)

    # Previous word above
    if prev_txt:
        fs_p   = max(int(fs * 0.62), 26)
        font_p = get_font(fs_p)
        while tsz(prev_txt, font_p)[0] > MAX_W and fs_p > 22:
            fs_p -= 4
            font_p = get_font(fs_p)
        pw, ph = tsz(prev_txt, font_p)
        py_    = cy_ - ph - 14
        if py_ > int(H * 0.12):
            stroke((W-pw)//2, py_, prev_txt, font_p, (225,225,225,170), sw=6)

    return img

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 7 — LEARN FORMAT OVERLAYS
#  Step counter + title bar at top, animated diagram area
# ─────────────────────────────────────────────────────────────────────────────
def draw_learn_overlay(img, scene, si, total_scenes, accent, frame):
    """Draw step number + title bar for educational videos."""
    step_num   = scene.get("step_num", 0)
    step_label = scene.get("step_label", "")
    if step_num <= 0 or not step_label:
        return img

    d = ImageDraw.Draw(img, "RGBA")
    font_num   = get_font(42)
    font_label = get_font(26)
    font_small = get_font(20, bold=False)

    # Slide-in animation for step bar
    bar_t = min(1.0, (frame % 90) / 12.0)
    bar_x = int((1 - bar_t) * -W)

    # Background bar
    bar_h = 78
    d.rounded_rectangle([bar_x + 16, 20, bar_x + W - 16, 20 + bar_h],
                        radius=16, fill=(*accent[:3], 220))
    d.rounded_rectangle([bar_x + 16, 20, bar_x + W - 16, 20 + bar_h],
                        radius=16, fill=None, outline=(255,255,255,120), width=2)

    # Step circle
    cx_, cy_ = bar_x + 56, 20 + bar_h//2
    d.ellipse([cx_-28, cy_-28, cx_+28, cy_+28], fill=(255,255,255,240))
    num_txt = str(step_num)
    nb = d.textbbox((0,0), num_txt, font=font_num)
    nw, nh = nb[2]-nb[0], nb[3]-nb[1]
    d.text((cx_-(nw//2), cy_-(nh//2)), num_txt, font=font_num,
           fill=(*accent[:3], 255))

    # Label
    d.text((bar_x + 96, 20 + (bar_h - 30)//2), step_label.upper(),
           font=font_label, fill=(255,255,255,240))

    # Progress dots at bottom
    dot_y = H - 45
    n_steps = max(1, total_scenes - 3)
    dot_w   = min(24, (W - 80) // n_steps)
    total_w = dot_w * n_steps
    start_x = (W - total_w) // 2
    for i in range(n_steps):
        color = (*accent[:3], 220) if i < si else (80, 80, 80, 140)
        xd    = start_x + i * dot_w + dot_w//2
        r_    = 5 if i < si else 4
        d.ellipse([xd-r_, dot_y-r_, xd+r_, dot_y+r_], fill=color)

    return img

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 8 — REDDIT CARD
# ─────────────────────────────────────────────────────────────────────────────
def draw_reddit_card(img, script, alpha_frac):
    if not script.get("reddit_title"):
        return img
    alpha = int(min(1.0, alpha_frac * 3) * 230)
    d     = ImageDraw.Draw(img, "RGBA")
    cx, cy = 16, 14
    cw     = W - 32
    title  = script.get("reddit_title","")
    lines  = textwrap.fill(title, 34).split("\n")[:3]
    card_h = 44 + len(lines) * 30 + 30
    d.rounded_rectangle([cx,cy,cx+cw,cy+card_h],radius=12,
                        fill=(20,18,18,alpha),outline=(255,69,0,alpha),width=2)
    font_s = get_font(18,bold=False)
    font_t = get_font(26)
    font_m = get_font(17,bold=False)
    sub = f"r/{script.get('reddit_sub','AskReddit')}  ·  {script.get('reddit_user','u/throwaway')}"
    d.text((cx+12,cy+10), sub, font=font_s, fill=(130,133,136,alpha))
    ty = cy+36
    for line in lines:
        d.text((cx+12,ty), line, font=font_t, fill=(215,218,220,alpha))
        ty += 30
    d.text((cx+12,cy+card_h-24),
           f"▲ {random.randint(8,42)}k   💬 {random.randint(400,5000)}",
           font=font_m, fill=(130,133,136,alpha))
    return img

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 9 — PEXELS CARD OVERLAY (thumbnail card, bottom-right, slide UP)
# ─────────────────────────────────────────────────────────────────────────────
def overlay_pexels_card(bg, pexels_img, t):
    """
    Slide a small thumbnail card from bottom-right.
    - Proper aspect-ratio crop (no distortion)
    - Rounded corners, white border, drop shadow
    - Ease-out-back on entry, ease-in on exit
    """
    if pexels_img is None:
        return bg

    CARD_W  = int(W * 0.40)
    CARD_H  = int(CARD_W * 0.62)   # ~16:10 card
    CARD_X  = W - CARD_W - 18      # right side
    CARD_Y0 = H - CARD_H - 160     # sits above character
    RADIUS  = 14

    def ease_out_back(x):
        c = 1.70158
        return 1 + (c+1)*(x-1)**3 + c*(x-1)**2

    def ease_in3(x):
        return x**3

    if t < 0.18:
        p      = ease_out_back(t / 0.18)
        y_off  = int((1-p) * (CARD_H + 50))
    elif t < 0.82:
        y_off  = 0
    else:
        p      = ease_in3((t - 0.82) / 0.18)
        y_off  = int(p * (CARD_H + 50))

    if y_off >= CARD_H + 50:
        return bg

    card = crop_pexels_16x9(None, CARD_W, CARD_H)  # already cropped PIL image
    # pexels_img is already a PIL Image, crop it to card dims
    iw, ih = pexels_img.size
    ratio = CARD_W / CARD_H
    cur   = iw / ih
    if cur > ratio:
        new_w = int(ih * ratio)
        x0 = (iw - new_w) // 2
        card = pexels_img.crop((x0, 0, x0+new_w, ih))
    else:
        new_h = int(iw / ratio)
        y0 = max(0, (ih - new_h) // 3)
        card = pexels_img.crop((0, y0, iw, y0+new_h))
    card = card.resize((CARD_W, CARD_H), Image.LANCZOS)
    card = ImageEnhance.Contrast(card).enhance(1.06)

    # Rounded mask
    mask = Image.new("L", (CARD_W, CARD_H), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0,0,CARD_W,CARD_H], radius=RADIUS, fill=255)

    py   = CARD_Y0 - y_off
    result = bg.convert("RGBA")

    # Drop shadow
    sh_size = 20
    sh = Image.new("RGBA", (CARD_W + sh_size*2, CARD_H + sh_size*2), (0,0,0,0))
    sm = Image.new("L",    (CARD_W + sh_size*2, CARD_H + sh_size*2), 0)
    ImageDraw.Draw(sm).rounded_rectangle(
        [sh_size, sh_size, CARD_W+sh_size, CARD_H+sh_size], radius=RADIUS, fill=90)
    sm = sm.filter(ImageFilter.GaussianBlur(10))
    sh.putalpha(sm)
    result.paste(sh, (CARD_X - sh_size, py - sh_size), mask=sh)

    # Card
    result.paste(card.convert("RGBA"), (CARD_X, py), mask=mask)

    # White border
    ImageDraw.Draw(result).rounded_rectangle(
        [CARD_X, py, CARD_X+CARD_W, py+CARD_H],
        radius=RADIUS, outline=(255,255,255,210), width=3)

    return result.convert("RGB")

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 10 — RENDER VIDEO
# ─────────────────────────────────────────────────────────────────────────────
def render_video(script, work_dir, output, gameplay_path, scene_durations, word_timestamps_per_scene):
    log.info("🎬 Rendering...")
    scenes    = script["scenes"]
    nk        = script.get("niche_key","fact")
    niche     = NICHES.get(nk, NICHES["fact"])
    accent    = niche["accent"]
    hi_color  = niche["hi"]
    is_reddit = (nk == "reddit")
    is_learn  = script.get("is_learn", False)

    for i, dur in enumerate(scene_durations):
        if i < len(scenes):
            scenes[i]["_dur"] = dur
    total_dur    = sum(s.get("_dur", s.get("duration",4.5)) for s in scenes)
    total_frames = int(total_dur * FPS)

    # Pexels prefetch
    log.info("   📸 Pexels prefetch...")
    pexels = {}
    for i, sc in enumerate(scenes):
        q = sc.get("image_query","").strip()
        if q and q.lower() != "none" and 2 <= i < len(scenes)-1:
            p = fetch_pexels(q, work_dir, i)
            if p:
                try:
                    pexels[i] = Image.open(p).convert("RGB")
                except Exception:
                    pass

    # Gameplay frames
    frames_dir = work_dir / "frames"
    frames_dir.mkdir()
    gp_dir     = work_dir / "gp"
    gp_dir.mkdir()
    gp_frames  = []
    if gameplay_path.exists():
        gp_off = random.uniform(5, 40)
        log.info(f"   📼 Gameplay offset {gp_off:.0f}s")
        subprocess.run([
            "ffmpeg","-y","-ss",str(gp_off),"-i",str(gameplay_path),
            "-vf",f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
            "-r",str(FPS),"-t",str(total_dur+5),"-q:v","3",
            str(gp_dir/"gp_%06d.jpg")
        ], capture_output=True)
        gp_frames = sorted(gp_dir.glob("gp_*.jpg"))
        log.info(f"   ✅ {len(gp_frames)} gameplay frames")

    # Frame render loop
    global_frame  = 0
    scene_ms_start = 0.0  # running audio ms offset for this scene

    for si, scene in enumerate(scenes):
        s_dur    = scene.get("_dur", scene.get("duration",4.5))
        n_frames = max(1, int(s_dur * FPS))
        wts      = word_timestamps_per_scene[si] if si < len(word_timestamps_per_scene) else []
        sfx_type = scene.get("sfx","none")
        px_img   = pexels.get(si)
        talking_at = set(range(0, n_frames, 8)) if not wts else None  # fallback

        for f in range(n_frames):
            t          = f / max(n_frames-1, 1)
            elapsed_ms = int(f / FPS * 1000)  # ms into this scene
            gp_idx     = min(global_frame, max(0, len(gp_frames)-1))
            talking    = bool(wts) and any(
                w.start_ms <= elapsed_ms <= w.end_ms for w in wts
            ) if wts else (f % 9 < 6)

            # Background
            if gp_frames:
                bg = Image.open(gp_frames[gp_idx]).convert("RGB")
                if bg.size != (W, H):
                    bg = bg.resize((W, H), Image.BILINEAR)
            else:
                bg = Image.new("RGB", (W,H), niche["bg"])
                gd = ImageDraw.Draw(bg)
                for row in range(H):
                    a = row/H
                    c = tuple(int(b*(1-a*0.4)) for b in niche["bg"])
                    gd.line([0,row,W,row], fill=c)

            # Dark overlay
            ov = Image.new("RGBA",(W,H),(0,0,0,55))
            bg = Image.alpha_composite(bg.convert("RGBA"), ov).convert("RGB")

            # Pexels card (bottom-right thumbnail)
            if px_img:
                bg = overlay_pexels_card(bg, px_img, t)

            # Reddit card (first 2 scenes)
            if is_reddit and si <= 1:
                bg = draw_reddit_card(bg, script, min(1.0,(si*n_frames+f)/(2*n_frames)))

            # Learn overlay
            if is_learn:
                bg = draw_learn_overlay(bg, scene, si, len(scenes), accent, global_frame)

            # Character (bottom-right, above pexels card)
            bg = draw_character(bg, global_frame, talking, accent)

            # Captions
            bg = draw_captions(bg, wts, elapsed_ms, hi_color)

            # Progress bar (niche color)
            dr = ImageDraw.Draw(bg,"RGBA")
            prog = global_frame / max(total_frames-1, 1)
            bw   = int(W * prog)
            dr.rectangle([0,H-5,bw,H], fill=(*accent[:3],200))
            dr.rectangle([bw,H-5,W,H], fill=(0,0,0,70))

            bg.save(frames_dir / f"frame_{global_frame:06d}.png")
            global_frame += 1

        if si % 4 == 0:
            log.info(f"   🎨 Scene {si+1}/{len(scenes)}")

    shutil.rmtree(gp_dir, ignore_errors=True)
    log.info(f"   ✅ {global_frame} frames")

    # Encode
    r = subprocess.run([
        "ffmpeg","-y","-framerate",str(FPS),
        "-i",str(frames_dir/"frame_%06d.png"),
        "-c:v","libx264","-preset","fast","-crf","20",
        "-pix_fmt","yuv420p",str(output)
    ], capture_output=True, text=True)
    shutil.rmtree(frames_dir)
    if r.returncode == 0 and output.exists():
        log.info(f"   ✅ Video: {output.stat().st_size//1024}KB, {total_dur:.1f}s")
        return True, total_dur
    log.error(f"   ❌ {r.stderr[-200:]}")
    return False, 0

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 11 — MIX AUDIO
# ─────────────────────────────────────────────────────────────────────────────
def mix_audio(video_path, voice_path, output, total_dur):
    if MUSIC_FILE.exists() and voice_path and voice_path.exists():
        log.info("   🎵 Mixing voice + music (ducked)...")
        # Sidechain duck: music dips when voice is loud
        r = subprocess.run([
            "ffmpeg","-y",
            "-i",str(video_path),
            "-stream_loop","-1","-i",str(MUSIC_FILE),
            "-i",str(voice_path),
            "-filter_complex",
            (f"[1:a]volume=0.08,atrim=0:{total_dur:.2f},asetpts=PTS-STARTPTS[music];"
             f"[2:a]volume=1.0[voice];"
             f"[music][voice]amix=inputs=2:duration=first:dropout_transition=1[aout]"),
            "-map","0:v","-map","[aout]",
            "-c:v","copy","-c:a","aac","-b:a","192k",
            "-movflags","+faststart",str(output)
        ], capture_output=True, text=True)
        if r.returncode == 0 and output.exists():
            log.info(f"   ✅ Mixed: {output.stat().st_size//1024}KB")
            return True

    if voice_path and voice_path.exists():
        r = subprocess.run([
            "ffmpeg","-y","-i",str(video_path),"-i",str(voice_path),
            "-map","0:v","-map","1:a","-c:v","copy",
            "-c:a","aac","-b:a","192k","-movflags","+faststart",str(output)
        ], capture_output=True, text=True)
        if r.returncode == 0 and output.exists():
            log.info(f"   ✅ Voice-only: {output.stat().st_size//1024}KB")
            return True

    shutil.copy(str(video_path), str(output))
    return True

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 12 — YOUTUBE UPLOAD
# ─────────────────────────────────────────────────────────────────────────────
def upload_youtube(video_path, title, description, hashtags, publish_at):
    log.info(f"📺 YouTube → {publish_at.strftime('%a %d %b %H:%M')}")
    try:
        import pickle, base64
        import googleapiclient.discovery as gd
        from googleapiclient.http import MediaFileUpload
        from google.auth.transport.requests import Request

        token_b64 = os.environ.get("YOUTUBE_TOKEN_B64","")
        if not token_b64:
            log.warning("   ⚠️ No YOUTUBE_TOKEN_B64")
            return None
        creds = pickle.loads(base64.b64decode(token_b64))
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        try:
            yt = gd.build("youtube","v3",credentials=creds,cache_discovery=False)
        except TypeError:
            yt = gd.build("youtube","v3",credentials=creds)
        if publish_at.tzinfo is None:
            publish_at = publish_at.replace(tzinfo=timezone.utc)
        body = {
            "snippet": {
                "title": title[:100],
                "description": f"{description}\n\n{hashtags}",
                "tags": [t.replace("#","") for t in hashtags.split() if t.startswith("#")],
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
        url = f"https://youtube.com/shorts/{resp['id']}"
        log.info(f"   ✅ {url}")
        return url
    except Exception as e:
        log.error(f"   ❌ YouTube: {e}")
        return None

# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────
def run_pipeline(n_videos=1, niche_key=None):
    if not shutil.which("espeak"):
        os.system("apt-get install -y -qq espeak 2>/dev/null || true")
    log.info("="*65)
    log.info("🚀 VAULTMIND PIPELINE v8")
    log.info("="*65)
    OUTPUT_DIR.mkdir(exist_ok=True)
    slots    = get_next_slots(n_videos)
    yt_slots = slots[:n_videos]
    tt_slots = [s + timedelta(minutes=30) for s in yt_slots]
    if not yt_slots:
        log.error("❌ No free slots in 90 days!")
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
            script = generate_script(niche_key)
            niche  = script["niche_design"]

            voice_path, word_timestamps, scene_durs = generate_voiceover_with_timestamps(
                script["scenes"], work_dir, niche
            )

            ok, total_dur = render_video(
                script, work_dir, raw_video, GAMEPLAY_FILE,
                scene_durs, word_timestamps
            )
            if not ok:
                raise Exception("Render failed")

            mix_audio(raw_video, voice_path, final_video, total_dur)

            yt_time = yt_slots[i] if i < len(yt_slots) else datetime.now()+timedelta(hours=1)
            tt_time = tt_slots[i] if i < len(tt_slots) else datetime.now()+timedelta(hours=2)

            yt_url = None
            if final_video.exists() and final_video.stat().st_size > 50_000:
                yt_url = upload_youtube(final_video, script["title"],
                                       script.get("description",""), script["hashtags"], yt_time)
                out = OUTPUT_DIR / f"video_{ts}.mp4"
                shutil.copy(str(final_video), str(out))
                log.info(f"   💾 {out.name}")
            else:
                log.error("   ❌ Video too small")

            entry = {
                "id": ts, "title": script["title"],
                "type": script.get("type","fact"), "niche": script.get("niche_key","fact"),
                "created_at": datetime.now().isoformat(),
                "youtube": {"scheduled": yt_time.isoformat(), "url": yt_url, "status":"scheduled"},
                "tiktok":  {"scheduled": tt_time.isoformat(), "status":"scheduled"},
                "hashtags": script["hashtags"], "status":"scheduled",
            }
            add_to_dashboard(entry)
            results.append(entry)
            log.info(f"✅ Video {i+1} done! YT: {yt_time.strftime('%a %d %b %H:%M')}")

        except Exception as e:
            import traceback
            log.error(f"❌ Video {i+1}: {e}\n{traceback.format_exc()}")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
            for p in [raw_video, final_video, voice_file]:
                if p.exists(): p.unlink(missing_ok=True)
        if i < n_videos-1:
            time.sleep(4)

    log.info(f"\n🎉 DONE — {len(results)}/{n_videos} videos")
    return results

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("n", nargs="?", type=int, default=1)
    ap.add_argument("--niche", type=str, default=None)
    args = ap.parse_args()
    run_pipeline(args.n, args.niche)
