# Telegram Media Bot

Telegram bot for on‑demand audio/video retrieval (YouTube via yt‑dlp), song recognition, queued & concurrent processing, metrics, and persistent state.

## ✨ Feature Summary
| Area | Capabilities |
|------|--------------|
| Downloads | Audio (mp3) & video (mp4) via yt-dlp with smart format selection, 50 MB Telegram cap, caching, concurrency |
| Queue | Persistent across restarts, cancel by position/substring, duplicate suppression |
| Recognition | Voice / audio message song ID via Shazam (shazamio) |
| Rate Limiting | Per-user sliding window to prevent abuse |
| Metrics | Optional Prometheus endpoint (processed, failed, queue size) |
| Progress UX | Chat actions + progress message edit on completion |
| Resilience | Automatic ffmpeg provisioning (static build download) |
| Configurability | All tunables via environment variables |
| Subscriptions | Follow channels and auto-queue new uploads every 10 minutes |

## 🗂 Project Structure
```
telegram_bot.py        # Entry point & handlers
downloads.py           # yt-dlp integration, caching, send helpers
queue_manager.py       # Async queue + persistence
recognition.py         # Shazam-based recognition utility
rate_limit.py          # Simple per-user rate limiter
ffmpeg_setup.py        # Static ffmpeg acquisition (Linux)
config.py              # Configuration & env loading
downloads/             # Runtime cache + tmp + queue state
```

## 🔧 Requirements
- Python 3.10+
- Network access to YouTube and (optionally) johnvansickle.com for first ffmpeg fetch
- Telegram Bot Token from @BotFather
- Deno JavaScript runtime (place `deno`/`deno.exe` in the project root or ensure it's on PATH)

## ⚙️ Configuration
Copy `.env.example` to `.env` and adjust as needed. All options are documented inline.

### JavaScript runtime for YouTube extraction
Recent YouTube changes require yt-dlp to run certain scripts with an external JavaScript runtime. This project assumes the Deno executable lives in the repository root (`./deno` or `./deno.exe`) or is accessible on your system `PATH`.

If Deno is elsewhere, set `DENO_PATH` in your `.env` to point at the executable. When no runtime is found, the bot logs a warning and YouTube downloads will likely fail until Deno is installed from [https://deno.land](https://deno.land).

### Channel subscriptions
Users can follow YouTube channels with `/subscribe <channel>` (URL or `@handle`). The bot polls every 10 minutes by default and queues new uploads as video jobs in the chat where the subscription was created. Configure:

- `SUBSCRIPTION_POLL_INTERVAL` (seconds, default `600`) to change the polling cadence.
- `SUBSCRIPTIONS_FILE` to relocate the persisted subscription store (defaults to `downloads/subscriptions.json`).
- `MAX_VIDEO_SIZE_MB` caps the size of delivered videos (default `50` MB to honor Telegram's bot limit). Oversized downloads are recompressed with two-pass H.265/Opus to fit.
- `TRANSCODE_TARGET_SIZE_MB` sets the approximate size goal for recompressed videos (default `48` MB to leave headroom).
- `YTDLP_CONCURRENT_FRAGMENTS`, `YTDLP_HTTP_CHUNK_SIZE`, and `YTDLP_SOCKET_TIMEOUT` tune yt-dlp download concurrency and chunking if you encounter slow YouTube transfers. Set `YTDLP_FORCE_IPV4=false` to allow IPv6 if it performs better in your region.
	The bot prefers native MP4 formats that already sit under `TRANSCODE_TARGET_SIZE_MB` before falling back to re-encoding.

## 🚀 Quick Start
```bash
python -m venv .venv
. .venv/bin/activate  # (Windows PowerShell: .venv\Scripts\Activate.ps1)
pip install -r requirements.txt
python telegram_bot.py
```

Send `/commands` to the bot chat to see available commands.

## 🧵 Queue & Concurrency
- Jobs are FIFO.
- Each worker pulls next available job; cancelled jobs are skipped.
- Position displayed when enqueued; progress message edited to "Done" or failure.

## 🎵 Recognition Flow
1. User sends voice/audio.
2. File saved temporarily.
3. Shazam recognition attempted.
4. On success, recognized "Title Artist" enqueued as audio job.

## 📦 Caching
Media stored in `downloads/cache` keyed by SHA-256 of (kind, query). Cache hit returns instantly without redownloading. Expired entries (beyond `CACHE_TTL`) trigger refresh.

## 📊 Metrics (Optional)
If `prometheus-client` installed (it is in requirements) a server starts on `METRICS_PORT`.
Metrics exposed:
- `bot_jobs_processed_total{kind}`
- `bot_jobs_failed_total`
- `bot_queue_size`

Scrape example:
```
curl http://localhost:8000/metrics
```

## ⛑ Troubleshooting
| Symptom | Cause | Fix |
|---------|-------|-----|
| Postprocessing error (ffmpeg not found) | No binary accessible | Set `FFMPEG_LOCATION` or ensure static download succeeded |
| Rate limit exceeded | Too many rapid commands | Wait for window to reset or raise limits |
| Long waits | Queue saturation | Increase `CONCURRENT_WORKERS` or reduce limits |
| Large video rejected | Telegram size limit | Try shorter / lower res video (currently fixed mp4 selection) |

## 🧾 License
MIT License - see LICENSE file for details.

## 🙏 Credits
This project uses the following open-source libraries:
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) - Telegram Bot API wrapper
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - Media download backend
- [shazamio](https://github.com/dotX12/ShazamIO) - Audio recognition via Shazam
- [prometheus-client](https://github.com/prometheus/client_python) - Metrics collection
- [python-dotenv](https://github.com/theskumar/python-dotenv) - Environment variable loading

Static FFmpeg binaries courtesy of [John Van Sickle](https://johnvansickle.com/ffmpeg/).

## 📝 Command Reference
| Command | Description |
|---------|-------------|
| /start | Greeting |
| /commands | Help text |
| /mp3 <query|url> | Queue audio download |
| /mp4 <query|url> | Queue video download |
| (plain text) | Queue audio (implicit) |
| /queue | Show pending items |
| /cancel <pos|term> | Cancel your job |
| /stats | Show processing counters |
| /subscribe <channel> | Follow a YouTube channel for auto-uploads |
| /unsubscribe <channel> | Stop following a channel |
| /subscriptions | List your followed channels |

---
