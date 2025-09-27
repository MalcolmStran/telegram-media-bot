import logging
import os
import shutil
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

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# Limits & settings (env override -> fallback defaults)
MAX_SONG_DURATION = int(os.getenv("MAX_SONG_DURATION", 600))  # seconds
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
MAX_VIDEO_SIZE_MB = int(os.getenv("MAX_VIDEO_SIZE_MB", 50))
if MAX_VIDEO_SIZE_MB < 5:
    MAX_VIDEO_SIZE_MB = 5
MAX_VIDEO_SIZE_BYTES = MAX_VIDEO_SIZE_MB * 1024 * 1024

TRANSCODE_TARGET_SIZE_MB = int(os.getenv("TRANSCODE_TARGET_SIZE_MB", 48))
if TRANSCODE_TARGET_SIZE_MB < 5:
    TRANSCODE_TARGET_SIZE_MB = 5
TRANSCODE_TARGET_SIZE_BYTES = TRANSCODE_TARGET_SIZE_MB * 1024 * 1024

YTDLP_CONCURRENT_FRAGMENTS = int(os.getenv("YTDLP_CONCURRENT_FRAGMENTS", 4))
if YTDLP_CONCURRENT_FRAGMENTS < 1:
    YTDLP_CONCURRENT_FRAGMENTS = 1
YTDLP_HTTP_CHUNK_SIZE = int(os.getenv("YTDLP_HTTP_CHUNK_SIZE", 1048576))
if YTDLP_HTTP_CHUNK_SIZE < 0:
    YTDLP_HTTP_CHUNK_SIZE = 0
YTDLP_SOCKET_TIMEOUT = float(os.getenv("YTDLP_SOCKET_TIMEOUT", 30))
if YTDLP_SOCKET_TIMEOUT < 5:
    YTDLP_SOCKET_TIMEOUT = 5
YTDLP_FORCE_IPV4 = os.getenv("YTDLP_FORCE_IPV4", "true").lower() == "true"
SUBSCRIPTIONS_FILE = os.getenv("SUBSCRIPTIONS_FILE", os.path.join(DOWNLOADS_DIR, "subscriptions.json"))
SUBSCRIPTION_POLL_INTERVAL = int(os.getenv("SUBSCRIPTION_POLL_INTERVAL", 600))

if SUBSCRIPTIONS_FILE:
    subs_dir = os.path.dirname(SUBSCRIPTIONS_FILE)
    if subs_dir:
        os.makedirs(subs_dir, exist_ok=True)

# Misc
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

logger = logging.getLogger(__name__)

# JavaScript runtime (Deno) lookup for yt-dlp YouTube downloads
JS_RUNTIME_ENABLED = os.getenv("ENABLE_JS_RUNTIME", "true").lower() == "true"
JS_RUNTIME_CONFIG = {}
DENO_PATH = None
if JS_RUNTIME_ENABLED:
    candidates = [
        os.getenv("DENO_PATH"),
        os.path.join(PROJECT_ROOT, "deno.exe"),
        os.path.join(PROJECT_ROOT, "deno"),
    ]

    system_deno = shutil.which("deno")
    if system_deno:
        candidates.append(system_deno)

    for candidate in candidates:
        if not candidate:
            continue
        if os.path.isfile(candidate):
            DENO_PATH = candidate
            break
        resolved = shutil.which(candidate)
        if resolved:
            DENO_PATH = resolved
            break

    if DENO_PATH:
        JS_RUNTIME_CONFIG = {"deno": {"path": DENO_PATH}}
    else:
        logger.warning(
            "ENABLE_JS_RUNTIME is true but no Deno executable was found in the project root or system PATH; "
            "YouTube downloads may fail."
        )

# Validation
if CONCURRENT_WORKERS < 1:
    CONCURRENT_WORKERS = 1
if SUBSCRIPTION_POLL_INTERVAL < 60:
    SUBSCRIPTION_POLL_INTERVAL = 60
