# Copilot Instructions

## Architecture Snapshot
- `telegram_bot.py` is the entry point using python-telegram-bot v20 async `Application`; handlers enqueue jobs while dedicated worker tasks (spawned in `on_start`) drain the queue and interact with Telegram.
- `queue_manager.DownloadQueue` wraps an `asyncio.Queue` plus a mirror list for introspection and persistence to `downloads/queue_state.json`, so enqueue/cancel/report all go through this singleton.
- `downloads.py` centralizes yt-dlp integration, cache decisions, download duration checks, and Telegram upload helpers; worker code only calls `download_audio|download_video` followed by `send_audio|send_video`.
- `recognition.py` performs voice-message song lookup via `Shazam().recognize`, returning a "Title Artist" string that is immediately re-queued as an audio job.

## Queuing & Workers
- Always call `_enqueue` (via handlers) so `RateLimiter`, duplicate suppression, and queue overflow guard run; do not push directly onto `queue`.
- Workers must call `queue.mark_done(job)` in `finally` to keep the persisted queue consistent—follow the existing try/except/finally template when adding new job types.
- Cancelling uses either numeric position or substring match but enforces ownership; cancelled jobs stay in the asyncio queue with a `_cancelled` flag, so new consumers should honor that pattern if manipulating jobs directly.
- Queue metrics (`bot_queue_size`) are updated inside `mark_done`; keep that call when altering completion logic.

## Media Retrieval & Caching
- Cache keys are deterministic (`kind` + query hash) and stored under `downloads/cache`; respect `CACHE_ENABLED` and `CACHE_TTL` when adding new media flows to avoid breaking reuse.
- yt-dlp options are composed via `_yt_base_opts` + format-specific helpers, including `THROTTLED_RATE` and optional `FFMPEG_DIR`; reuse these builders when introducing new formats.
- Downloads occur into `downloads/tmp` and are atomically `os.replace`d into cache; if you add post-processing, keep the temp→final move so cached files remain immutable.
- `send_audio`/`send_video` wrap Telegram uploads in `asyncio.timeout(60)` and translate oversize `BadRequest` errors to user-friendly messages—mirror this structure for new send helpers.

## Recognition & Rate Limiting
- Voice/audio recognition stores a temp file, runs Shazam, and cleans up in a `finally`; any new recognition paths should delete temps manually to avoid disk bloat.
- `rate_limit.RateLimiter` is a simple sliding window keyed by Telegram `user_id`; ensure any new command path still invokes `_rate_check` (or equivalent) before enqueuing work.
- The implicit text-to-audio flow deduplicates back-to-back identical queries per user by inspecting the tail of `queue.list_jobs()`; copy that check if you add alternate enqueue triggers.

## Configuration & Environment
- `config.py` loads `.env` at import, validates `BOT_TOKEN`, and creates `downloads/`, `cache/`, and `tmp/`; reference config constants rather than hard-coding paths or limits.
- Environment knobs control concurrency (`CONCURRENT_WORKERS`), limits (`MAX_*`), caching, and metrics port; document any new tunable there and guard with sane defaults.
- `ffmpeg_setup.ensure_ffmpeg` only auto-downloads static binaries on Linux; on Windows/macOS rely on `FFMPEG_LOCATION`—surface helpful errors instead of assuming availability.

## Developer Workflow
- Typical loop: create/activate venv, `pip install -r requirements.txt`, set `BOT_TOKEN` (and optional knobs) in `.env`, then `python telegram_bot.py`.
- There are no automated tests today; manual verification involves exercising handlers against a Telegram bot plus optional `curl http://localhost:METRICS_PORT/metrics` when Prometheus is enabled.
- Runtime artifacts (`downloads/cache`, `downloads/tmp`, `downloads/queue_state.json`) are gitignored but expected—avoid storing other state elsewhere without updating documentation.

Let me know if any sections need clarification or if you're missing context for another part of the codebase.
