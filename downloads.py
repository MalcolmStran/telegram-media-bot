import os
import re
import asyncio
import logging
import time
import hashlib
from typing import Optional, Tuple, Any, Dict, cast

import yt_dlp as youtube_dl
from telegram.error import BadRequest

from config import (
    DOWNLOADS_DIR,
    TMP_DIR,
    CACHE_DIR,
    AUDIO_BITRATE,
    MAX_SONG_DURATION,
    MAX_VIDEO_DURATION,
    THROTTLED_RATE,
    CACHE_ENABLED,
    CACHE_TTL,
)
try:
    from ffmpeg_setup import FFMPEG_DIR
except Exception:
    FFMPEG_DIR = None

logger = logging.getLogger(__name__)

SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize(name: str) -> str:
    name = name.strip()[:150]
    return SAFE_FILENAME_RE.sub("_", name)


def _build_cache_key(kind: str, query: str) -> str:
    h = hashlib.sha256(f"{kind}:{query.lower()}".encode()).hexdigest()[:16]
    return f"{kind}-{h}.mp3" if kind == "audio" else f"{kind}-{h}.mp4"


def _yt_base_opts():
    base = {
        'noplaylist': True,
        'force_ipv4': True,
        'restrictfilenames': True,
        'quiet': True,
        'outtmpl': os.path.join(TMP_DIR, '%(id)s.%(ext)s'),
    }
    if THROTTLED_RATE:
        base['throttled_rate'] = THROTTLED_RATE
    if FFMPEG_DIR:
        base['ffmpeg_location'] = FFMPEG_DIR
    return base


def _audio_opts():
    o = _yt_base_opts()
    o.update({
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': AUDIO_BITRATE,
        }]
    })
    return o


def _video_opts():
    o = _yt_base_opts()
    o.update({
        'format': 'mp4',
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    })
    return o

async def search_and_resolve(query: str, ydl: youtube_dl.YoutubeDL) -> Optional[Dict[str, Any]]:
    # Accept full URLs directly
    if query.startswith('http://') or query.startswith('https://'):
        try:
            info_raw = ydl.extract_info(query, download=False)
            if isinstance(info_raw, dict) and 'duration' in info_raw:
                return info_raw
        except Exception as e:
            logger.warning(f"Direct URL failed, fallback to search: {e}")
    try:
        search_raw = ydl.extract_info(f"ytsearch:{query}", download=False)
        if isinstance(search_raw, dict) and 'entries' in search_raw:
            entries = search_raw.get('entries')
            if isinstance(entries, list) and entries:
                first = entries[0]
                if isinstance(first, dict):
                    return first
    except Exception as e:
        logger.error(f"Search failed: {e}")
    return None

async def download_audio(query: str) -> Tuple[str, str]:
    """Return (filepath, title)."""
    cache_file = _build_cache_key('audio', query)
    cache_path = os.path.join(CACHE_DIR, cache_file)
    if CACHE_ENABLED and os.path.exists(cache_path):
        if time.time() - os.path.getmtime(cache_path) < CACHE_TTL:
            return cache_path, os.path.splitext(os.path.basename(cache_path))[0]
    opts = _audio_opts()
    with youtube_dl.YoutubeDL(opts) as ydl:
        info = await search_and_resolve(query, ydl)
        if not info:
            raise ValueError("Could not find audio")
        duration = info.get('duration') if isinstance(info, dict) else None
        if not duration or duration > MAX_SONG_DURATION:
            raise ValueError("Audio duration exceeds limit or unknown")
        info_dl = ydl.extract_info(info['webpage_url'], download=True)
        if not isinstance(info_dl, dict):
            raise ValueError("Unexpected download metadata format")
        file_name = ydl.prepare_filename(info_dl)
        base, _ = os.path.splitext(file_name)
        mp3_file = base + '.mp3'
        if not os.path.exists(mp3_file):
            raise FileNotFoundError("mp3 output missing")
        # Move to cache with sanitized name
        final_path = cache_path if CACHE_ENABLED else os.path.join(DOWNLOADS_DIR, os.path.basename(mp3_file))
        os.replace(mp3_file, final_path)
        title = info_dl.get('title') if isinstance(info_dl, dict) else None
        return final_path, (title or query)

async def download_video(query: str) -> Tuple[str, str]:
    cache_file = _build_cache_key('video', query)
    cache_path = os.path.join(CACHE_DIR, cache_file)
    if CACHE_ENABLED and os.path.exists(cache_path):
        if time.time() - os.path.getmtime(cache_path) < CACHE_TTL:
            return cache_path, os.path.splitext(os.path.basename(cache_path))[0]
    opts = _video_opts()
    with youtube_dl.YoutubeDL(opts) as ydl:
        info = await search_and_resolve(query, ydl)
        if not info:
            raise ValueError("Could not find video")
        duration = info.get('duration') if isinstance(info, dict) else None
        if not duration or duration > MAX_VIDEO_DURATION:
            raise ValueError("Video duration exceeds limit or unknown")
        info_dl = ydl.extract_info(info['webpage_url'], download=True)
        if not isinstance(info_dl, dict):
            raise ValueError("Unexpected download metadata format")
        file_name = ydl.prepare_filename(info_dl)
        base, _ = os.path.splitext(file_name)
        mp4_file = base + '.mp4'
        if not os.path.exists(mp4_file):
            raise FileNotFoundError("mp4 output missing")
        final_path = cache_path if CACHE_ENABLED else os.path.join(DOWNLOADS_DIR, os.path.basename(mp4_file))
        os.replace(mp4_file, final_path)
        title = info_dl.get('title') if isinstance(info_dl, dict) else None
        return final_path, (title or query)

async def send_audio(bot, chat_id: int, path: str, title: str):
    try:
        async with asyncio.timeout(60):
            with open(path, 'rb') as f:
                await bot.send_audio(chat_id=chat_id, audio=f, title=title)
    except Exception as e:
        logger.error(f"Failed to send audio: {e}")
        raise

async def send_video(bot, chat_id: int, path: str, title: str):
    try:
        async with asyncio.timeout(60):
            with open(path, 'rb') as f:
                await bot.send_video(chat_id=chat_id, video=f, supports_streaming=True)
    except BadRequest as e:
        if "Request Entity Too Large" in str(e):
            raise ValueError("Video file too large to send")
        raise
    except Exception as e:
        logger.error(f"Failed to send video: {e}")
        raise
