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

# JavaScript runtime (Deno) provisioning for yt-dlp YouTube downloads
JS_RUNTIME_ENABLED = os.getenv("ENABLE_JS_RUNTIME", "true").lower() == "true"
JS_RUNTIME_CONFIG = {}
DENO_PATH = None
if JS_RUNTIME_ENABLED:
    AUTO_INSTALL_DENO = os.getenv("AUTO_INSTALL_DENO", "true").lower() == "true"
    deno_install_dir = os.getenv("DENO_INSTALL_DIR", "deno_bin")
    deno_version = os.getenv("DENO_VERSION") or None
    deno_explicit = os.getenv("DENO_PATH") or None
    try:
        from js_runtime_setup import ensure_deno

        DENO_PATH = ensure_deno(
            local_dir=deno_install_dir,
            version=deno_version,
            explicit_path=deno_explicit,
            auto_install=AUTO_INSTALL_DENO,
        )
    except Exception as exc:  # pragma: no cover - safety net for startup
        raise RuntimeError(
            "Unable to provision the required Deno JavaScript runtime. "
            "Install Deno manually, set DENO_PATH, or disable YouTube downloads temporarily."
        ) from exc

    if not DENO_PATH or not os.path.isfile(DENO_PATH):
        raise RuntimeError(
            "Deno runtime is required for YouTube downloads but could not be located. "
            "Set DENO_PATH or allow AUTO_INSTALL_DENO."
        )
    JS_RUNTIME_CONFIG = {"deno": {"path": DENO_PATH}}

# Validation
if CONCURRENT_WORKERS < 1:
    CONCURRENT_WORKERS = 1
