import os
from dotenv import load_dotenv

load_dotenv()

# Core bot token
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment or .env file")

# Directories
DOWNLOADS_DIR = os.getenv("DOWNLOADS_DIR", "downloads")
CACHE_DIR = os.path.join(DOWNLOADS_DIR, "cache")
TMP_DIR = os.path.join(DOWNLOADS_DIR, "tmp")
for d in (DOWNLOADS_DIR, CACHE_DIR, TMP_DIR):
    os.makedirs(d, exist_ok=True)

# Limits & settings (env override -> fallback defaults)
MAX_SONG_DURATION = int(os.getenv("MAX_SONG_DURATION", 600))  # seconds
MAX_VIDEO_DURATION = int(os.getenv("MAX_VIDEO_DURATION", 900))  # seconds
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", 50))
AUDIO_BITRATE = os.getenv("AUDIO_BITRATE", "320")
THROTTLED_RATE = os.getenv("THROTTLED_RATE", "")  # e.g. 100K - leave empty to disable
CONCURRENT_WORKERS = int(os.getenv("CONCURRENT_WORKERS", 2))
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", 5))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", 60))  # seconds
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").lower() == "true"
CACHE_TTL = int(os.getenv("CACHE_TTL", 3600))  # seconds
METRICS_PORT = int(os.getenv("METRICS_PORT", 8000))
QUEUE_STATE_FILE = os.getenv("QUEUE_STATE_FILE", os.path.join(DOWNLOADS_DIR, "queue_state.json"))

# Misc
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Validation
if CONCURRENT_WORKERS < 1:
    CONCURRENT_WORKERS = 1
