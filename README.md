# Telegram Media Bot

High‑performance Telegram bot for on‑demand audio/video retrieval (YouTube via yt‑dlp), song recognition, queued & concurrent processing, metrics, and persistent state.

## ✨ Feature Summary
| Area | Capabilities |
|------|--------------|
| Downloads | Audio (mp3) & video (mp4) via yt-dlp with duration limits, caching, concurrency |
| Queue | Persistent across restarts, cancel by position/substring, duplicate suppression |
| Recognition | Voice / audio message song ID via Shazam (shazamio) |
| Rate Limiting | Per-user sliding window to prevent abuse |
| Metrics | Optional Prometheus endpoint (processed, failed, queue size) |
| Progress UX | Chat actions + progress message edit on completion |
| Resilience | Automatic ffmpeg provisioning (static build download) |
| Configurability | All tunables via environment variables |

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

## ⚙️ Configuration
Copy `.env.example` to `.env` and adjust as needed. All options are documented inline.

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

## 🛠 FFmpeg Provisioning
Resolution order:
1. `FFMPEG_LOCATION` directory
2. System PATH
3. Existing `ffmpeg_bin/`
4. Automatic static Linux download into `ffmpeg_bin/`

If none succeed the bot will fail during first post‑processing request.

## ⛑ Troubleshooting
| Symptom | Cause | Fix |
|---------|-------|-----|
| Postprocessing error (ffmpeg not found) | No binary accessible | Set `FFMPEG_LOCATION` or ensure static download succeeded |
| Rate limit exceeded | Too many rapid commands | Wait for window to reset or raise limits |
| Long waits | Queue saturation | Increase `CONCURRENT_WORKERS` or reduce limits |
| Large video rejected | Telegram size limit | Try shorter / lower res video (currently fixed mp4 selection) |

## 🔐 Security Notes
- Input is used in yt-dlp search; yt-dlp handles escaping, but keep the library updated.
- Consider adding a private allowlist for production usage.
- Avoid logging sensitive user content; current logs exclude raw file paths beyond need.

## 🧪 Future Enhancements (Roadmap)
- Test suite (pytest) with mocked yt-dlp & Shazam
- Progress % if/when yt-dlp hook added
- Prometheus histogram for job latency
- Admin command for dynamic reconfig

## 🧾 License
Add your preferred license (e.g. MIT) here.

## 🤝 Contributing
PRs welcome: open issues for non-trivial changes first.

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
| /Hi_Arne | Easter egg image |

---
Happy downloading! 🎶
