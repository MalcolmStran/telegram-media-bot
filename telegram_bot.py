import logging
from typing import Dict
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

from config import BOT_TOKEN, LOG_LEVEL, CONCURRENT_WORKERS, METRICS_PORT, SUBSCRIPTION_POLL_INTERVAL
from downloads import download_audio, download_video, send_audio, send_video
from queue_manager import queue, DownloadJob
from recognition import recognize_audio_file
from rate_limit import rate_limiter
from subscriptions import (
    subscriptions,
    SubscriptionRecord,
    fetch_channel_metadata,
    ChannelMetadata,
)

logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger(__name__)

stats: Dict[str, int] = {"processed": 0, "audio": 0, "video": 0, "failed": 0}

# Prometheus metrics (lazy import so optional)
try:
    from prometheus_client import Counter, Gauge, start_http_server
    METRICS_ENABLED = True
    m_jobs_processed = Counter('bot_jobs_processed_total', 'Jobs processed', ['kind'])
    m_jobs_failed = Counter('bot_jobs_failed_total', 'Jobs failed')
    m_queue_size = Gauge('bot_queue_size', 'Current queue size')
except Exception:
    METRICS_ENABLED = False
    Counter = Gauge = start_http_server = None  # type: ignore

HELP_TEXT = (
    "Type a song name to enqueue an mp3.\n"
    "/mp3 <query|url> - Explicitly queue audio.\n"
    "/mp4 <query|url> - Queue video.\n"
    "Send a voice/audio message for recognition.\n"
    "/queue - Show pending items. /cancel <pos|term> - cancel your own.\n"
    "/subscribe <channel> - Follow a YouTube channel for new uploads here.\n"
    "/unsubscribe <channel> - Stop following a channel.\n"
    "/subscriptions - List your followed channels.\n"
    "/stats - Show processing stats."
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    await msg.reply_text("Hi! Use /commands for help.")

async def commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    await msg.reply_text(HELP_TEXT)

async def hi_arne(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    with open('hiarne.png', 'rb') as f:
        await msg.reply_photo(photo=f)

def _get_ids(update: Update):
    user = getattr(update, 'effective_user', None)
    chat = getattr(update, 'effective_chat', None)
    if not user or not chat:
        return None, None
    return user.id, chat.id

def _rate_check(update: Update) -> bool:
    user_id, _ = _get_ids(update)
    if user_id is None:
        return False
    return rate_limiter.allow(user_id)

async def _enqueue(update: Update, query: str, kind: str):
    if not update.effective_user or not update.effective_chat or not update.message:
        return
    user_id = update.effective_user.id  # type: ignore[assignment]
    if not _rate_check(update):
        await update.message.reply_text("Rate limit exceeded. Try again shortly.")  # type: ignore[arg-type]
        return
    job = DownloadJob(chat_id=update.effective_chat.id, query=query, kind=kind, user_id=user_id, request_message_id=update.message.message_id)
    try:
        position = await queue.add(job)
        await update.message.reply_text(f"Queued {kind}: '{query}' (position {position})")  # type: ignore[arg-type]
    except OverflowError:
        await update.message.reply_text("Queue is full. Please wait and try later.")  # type: ignore[arg-type]

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    text_val = update.message.text
    if not isinstance(text_val, str):
        return
    query = text_val.strip()
    # Prevent duplicates back-to-back from same user
    jobs = queue.list_jobs()
    if update.effective_user and jobs and jobs[-1].user_id == update.effective_user.id and jobs[-1].query.lower() == query.lower():
        await update.message.reply_text("Already queued.")
        return
    await _enqueue(update, query, 'audio')

async def mp3_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if not context.args:
        await update.message.reply_text("Usage: /mp3 <query|url>")
        return
    await _enqueue(update, " ".join(context.args), 'audio')

async def mp4_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if not context.args:
        await update.message.reply_text("Usage: /mp4 <query|url>")
        return
    await _enqueue(update, " ".join(context.args), 'video')


async def subscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return
    if not context.args:
        await message.reply_text("Usage: /subscribe <YouTube channel URL or @handle>")
        return
    identifier = " ".join(context.args).strip()
    try:
        metadata = await fetch_channel_metadata(identifier)
    except ValueError as exc:
        await message.reply_text(str(exc))
        return
    except Exception as exc:  # pragma: no cover - network errors
        logger.warning("Channel lookup failed for %s: %s", identifier, exc)
        await message.reply_text("Couldn't resolve that channel. Please provide a valid channel URL or @handle.")
        return

    latest = metadata.latest_videos[0] if metadata.latest_videos else None
    channel_label = metadata.channel_title or metadata.channel_url
    record = SubscriptionRecord(
        user_id=user.id,  # type: ignore[arg-type]
        chat_id=chat.id,
        channel_url=metadata.channel_url,
        channel_id=metadata.channel_id,
        channel_title=metadata.channel_title,
        last_video_id=latest.video_id if latest else None,
        last_published=latest.timestamp if latest else None,
    )
    created, existing = await subscriptions.add_or_update(record)
    if created:
        suffix = " I'll keep an eye out for new uploads." if latest else " I'll watch for the first upload."
        await message.reply_text(f"Subscribed to {channel_label}.{suffix}")
    else:
        existing_label = existing.channel_title or existing.channel_url
        await message.reply_text(
            f"You're already subscribed to {existing_label}. Updated the delivery chat just in case."
        )


async def unsubscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user
    if not message or not user:
        return
    if not context.args:
        await message.reply_text("Usage: /unsubscribe <channel name|url|id>")
        return
    identifier = " ".join(context.args).strip()
    removed = await subscriptions.remove(user.id, identifier)  # type: ignore[arg-type]
    if removed:
        title = removed.channel_title or removed.channel_url
        await message.reply_text(f"Unsubscribed from {title}.")
    else:
        await message.reply_text("No matching subscription found.")


async def subscriptions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user
    if not message or not user:
        return
    subs = await subscriptions.list_for_user(user.id)  # type: ignore[arg-type]
    if not subs:
        await message.reply_text("You have no channel subscriptions. Use /subscribe <channel> to add one.")
        return
    lines = []
    for idx, sub in enumerate(subs, start=1):
        lines.append(f"{idx}. {sub.channel_title or sub.channel_url}\n   {sub.channel_url}")
    await message.reply_text("Subscribed channels:\n" + "\n".join(lines[:30]))

async def queue_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    jobs = queue.list_jobs()
    if not jobs:
        await update.message.reply_text("Queue empty")
        return
    lines = []
    for i, j in enumerate(jobs, 1):
        owner = 'you' if (update.effective_user and j.user_id == update.effective_user.id) else 'other'
        lines.append(f"{i}. [{j.kind}] {j.query} ({owner})")
    await update.message.reply_text("Pending:\n" + "\n".join(lines[:30]))

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if not context.args:
        await update.message.reply_text("Usage: /cancel <position|term>")
        return
    identifier = " ".join(context.args)
    if not update.effective_user:
        return
    removed = await queue.cancel(update.effective_user.id, identifier)
    if removed:
        await update.message.reply_text("Cancelled.")
    else:
        await update.message.reply_text("Nothing matched or not owned by you.")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(
            f"Processed: {stats['processed']} (audio {stats['audio']}, video {stats['video']}), failed {stats['failed']}"
        )


async def poll_subscriptions(context: ContextTypes.DEFAULT_TYPE):
    await subscriptions.load()
    all_subs = await subscriptions.all_subscriptions()
    if not all_subs:
        return

    metadata_cache: Dict[str, ChannelMetadata] = {}
    for sub in all_subs:
        channel_key = sub.channel_id or sub.channel_url
        metadata = metadata_cache.get(channel_key)
        if metadata is None:
            try:
                metadata = await fetch_channel_metadata(sub.channel_url)
            except Exception as exc:  # pragma: no cover - network errors
                logger.warning(
                    "Subscription poll failed for user %s channel %s: %s",
                    sub.user_id,
                    sub.channel_url,
                    exc,
                )
                continue
            cache_key = metadata.channel_id or channel_key
            metadata_cache[cache_key] = metadata
            metadata_cache[channel_key] = metadata

        await subscriptions.update_last_video(
            sub.user_id,
            metadata.channel_id or sub.channel_id,
            None,
            sub.last_published,
            channel_url=metadata.channel_url,
            channel_title=metadata.channel_title,
        )

        videos = metadata.latest_videos
        if not videos:
            continue

        if not sub.last_video_id:
            latest = videos[0]
            await subscriptions.update_last_video(
                sub.user_id,
                metadata.channel_id or sub.channel_id,
                latest.video_id,
                latest.timestamp,
                channel_url=metadata.channel_url,
                channel_title=metadata.channel_title,
            )
            continue

        new_videos = []
        for video in videos:
            if video.video_id == sub.last_video_id:
                break
            new_videos.append(video)

        if not new_videos:
            continue

        new_videos.reverse()
        last_processed = None
        for video in new_videos:
            job = DownloadJob(
                chat_id=sub.chat_id,
                query=video.video_url,
                kind='video',
                user_id=sub.user_id,
                request_message_id=None,
            )
            try:
                position = await queue.add(job)
                last_processed = video
                await context.bot.send_message(
                    chat_id=sub.chat_id,
                    text=(
                        f"New upload from {metadata.channel_title}: {video.title}\n"
                        f"{video.video_url}\nQueued (position {position})."
                    ),
                )
                if METRICS_ENABLED:
                    m_queue_size.set(queue.size())
            except OverflowError:
                await context.bot.send_message(
                    chat_id=sub.chat_id,
                    text=(
                        f"Queue full - couldn't queue '{video.title}' from {metadata.channel_title}. "
                        "I'll try again soon."
                    ),
                )
                break
            except Exception as exc:  # pragma: no cover - network errors
                logger.error(
                    "Failed to queue subscription video for user %s: %s",
                    sub.user_id,
                    exc,
                )
                break

        if last_processed:
            await subscriptions.update_last_video(
                sub.user_id,
                metadata.channel_id or sub.channel_id,
                last_processed.video_id,
                last_processed.timestamp,
                channel_url=metadata.channel_url,
                channel_title=metadata.channel_title,
            )

async def audio_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    voice = msg.voice
    audio = msg.audio
    file_id = voice.file_id if voice else (audio.file_id if audio else None)
    if not file_id:
        return
    tg_file = await context.bot.get_file(file_id)
    await msg.reply_text("Recognizing...")
    song = await recognize_audio_file(tg_file)
    if song:
        await msg.reply_text(f"Identified: {song}. Queued.")
        await _enqueue(update, song, 'audio')
    else:
        await msg.reply_text("Could not identify song.")

async def worker(app: Application, worker_id: int):
    while True:
        job = await queue.get()
        chat_id = job.chat_id
        progress_message = None
        last_progress_text = None
        display_kind = 'Audio' if job.kind == 'audio' else 'Video'
        query_preview = job.query if len(job.query) <= 180 else job.query[:177] + '...'

        async def update_progress(status: str) -> None:
            nonlocal progress_message, last_progress_text
            if not progress_message:
                return
            new_text = f"{display_kind} '{query_preview}': {status}"
            if new_text == last_progress_text:
                return
            try:
                edited = await app.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_message.message_id,
                    text=new_text,
                )
                progress_message = edited
                last_progress_text = new_text
            except BadRequest as exc:
                if 'message is not modified' in str(exc).lower():
                    return
                logger.debug("Progress edit failed: %s", exc)
            except Exception as exc:
                logger.debug("Progress edit failed: %s", exc)
        try:
            # send chat action
            await app.bot.send_chat_action(chat_id=chat_id, action='upload_audio' if job.kind=='audio' else 'upload_video')
            initial_text = f"{display_kind} '{query_preview}': downloading..."
            progress_message = await app.bot.send_message(chat_id=chat_id, text=initial_text)
            last_progress_text = initial_text
            if job.kind == 'audio':
                path, title = await download_audio(job.query)
                await update_progress("Uploading...")
                await app.bot.send_chat_action(chat_id=chat_id, action='upload_audio')
                await send_audio(app.bot, chat_id, path, title)
                stats['audio'] += 1
                if METRICS_ENABLED:
                    m_jobs_processed.labels('audio').inc()
            else:
                path, title = await download_video(job.query, update_progress)
                await update_progress("Uploading...")
                await app.bot.send_chat_action(chat_id=chat_id, action='upload_video')
                await send_video(app.bot, chat_id, path, title)
                stats['video'] += 1
                if METRICS_ENABLED:
                    m_jobs_processed.labels('video').inc()
            stats['processed'] += 1
            await update_progress("Done.")
        except Exception as e:
            stats['failed'] += 1
            logger.error(f"Job failed: {e}")
            try:
                await app.bot.send_message(chat_id=chat_id, text=f"Failed: {e}")
            except Exception:
                pass
            if METRICS_ENABLED:
                m_jobs_failed.inc()
        finally:
            await queue.mark_done(job)
            if METRICS_ENABLED:
                m_queue_size.set(queue.size())

async def on_start(app: Application):
    await queue.load()
    await subscriptions.load()
    for i in range(CONCURRENT_WORKERS):
        app.create_task(worker(app, i))
    if METRICS_ENABLED and callable(start_http_server):  # type: ignore
        try:
            start_http_server(METRICS_PORT)  # type: ignore
            logger.info(f"Metrics server on :{METRICS_PORT}")
        except Exception as e:
            logger.warning(f"Metrics server failed to start: {e}")
    logger.info("Workers started; queue size %d", queue.size())
    if app.job_queue:
        app.job_queue.run_repeating(
            poll_subscriptions,
            interval=SUBSCRIPTION_POLL_INTERVAL,
            first=30,
            name="subscription-poll",
        )

def main():
    # BOT_TOKEN validated non-empty in config
    application = Application.builder().token(str(BOT_TOKEN)).build()
    application.post_init = on_start

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('commands', commands))
    application.add_handler(CommandHandler('Hi_Arne', hi_arne))
    application.add_handler(CommandHandler('mp3', mp3_cmd))
    application.add_handler(CommandHandler('mp4', mp4_cmd))
    application.add_handler(CommandHandler('queue', queue_cmd))
    application.add_handler(CommandHandler('cancel', cancel_cmd))
    application.add_handler(CommandHandler('stats', stats_cmd))
    application.add_handler(CommandHandler('subscribe', subscribe_cmd))
    application.add_handler(CommandHandler('unsubscribe', unsubscribe_cmd))
    application.add_handler(CommandHandler('subscriptions', subscriptions_cmd))

    application.add_handler(MessageHandler(filters.AUDIO | filters.VOICE, audio_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    application.run_polling(close_loop=False)

if __name__ == '__main__':
    main()