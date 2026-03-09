#!/bin/bash
# ╔══════════════════════════════════════════════════════════╗
# ║   🚀 AUTO SETUP SCRIPT                                  ║
# ║   Run this once to set everything up                    ║
# ╚══════════════════════════════════════════════════════════╝

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   🎬 VIDEO PIPELINE SETUP               ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# 1. Install Python dependencies
echo "📦 Installing Python dependencies..."
pip install requests pillow google-auth google-auth-oauthlib google-api-python-client --quiet
echo "   ✅ Done"

# 2. Check ffmpeg
echo ""
echo "🔧 Checking ffmpeg..."
if command -v ffmpeg &> /dev/null; then
    echo "   ✅ ffmpeg found"
else
    echo "   ⚠️  ffmpeg not found. Installing..."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        brew install ffmpeg
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        sudo apt-get install -y ffmpeg
    else
        echo "   Please install ffmpeg manually: ffmpeg.org/download.html"
    fi
fi

# 3. Create .env if not exists
echo ""
echo "🔑 Setting up .env file..."
if [ ! -f .env ]; then
    cat > .env << 'EOF'
# ══════════════════════════════════════════
#  API KEYS — Fill in your keys here
# ══════════════════════════════════════════

# Claude API → console.anthropic.com
CLAUDE_API_KEY=YOUR_CLAUDE_KEY_HERE

# ElevenLabs → elevenlabs.io (Profile → API Key)
ELEVENLABS_KEY=YOUR_ELEVENLABS_KEY_HERE

# Pexels → pexels.com/api (free, unlimited)
PEXELS_KEY=YOUR_PEXELS_KEY_HERE

# Buffer → buffer.com/app/account/apps
# (Connects TikTok and schedules from cloud — PC can be OFF)
BUFFER_ACCESS_TOKEN=YOUR_BUFFER_TOKEN_HERE
BUFFER_TIKTOK_PROFILE_ID=YOUR_BUFFER_PROFILE_ID_HERE

# YouTube → console.cloud.google.com
# (Enable YouTube Data API v3, create OAuth2 credentials)
YOUTUBE_CLIENT_ID=YOUR_YT_CLIENT_ID_HERE
YOUTUBE_CLIENT_SECRET=YOUR_YT_CLIENT_SECRET_HERE

# Settings
NICHE=Interesting Facts
EOF
    echo "   ✅ .env created — fill in your API keys!"
else
    echo "   ✅ .env already exists"
fi

# 4. Create output folder
mkdir -p output_videos
echo ""
echo "📁 Output folder created: ./output_videos"

# 5. Print next steps
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   ✅ SETUP COMPLETE                     ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "NEXT STEPS:"
echo ""
echo "  1. Fill in your API keys in .env"
echo "     nano .env"
echo ""
echo "  2. Connect Buffer to TikTok:"
echo "     → buffer.com → Connect TikTok account"
echo "     → Get your profile ID:"
echo "     curl 'https://api.bufferapp.com/1/profiles.json?access_token=YOUR_TOKEN'"
echo ""
echo "  3. Generate your first video:"
echo "     python pipeline.py 1"
echo ""
echo "  4. Generate a week of content:"
echo "     python pipeline.py 7"
echo ""
echo "  5. Open dashboard to track everything:"
echo "     Open dashboard.html in your browser"
echo ""
echo "  ☁️  To run in cloud (PC off):"
echo "     → railway.app → New Project → Deploy from GitHub"
echo "     → Add your .env keys as environment variables"
echo "     → Done! Runs 24/7 without your PC"
echo ""
