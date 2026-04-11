FROM python:3.11-slim

RUN apt-get update -qq && apt-get install -y -qq ffmpeg espeak && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt --no-cache-dir

COPY pipeline.py .
COPY gameplay_bg.mp4 .

CMD ["python", "pipeline.py", "1"]
