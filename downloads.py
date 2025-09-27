import os
import re
import asyncio
import logging
import time
import hashlib
import uuid
import shutil
from typing import Optional, Tuple, Any, Dict, cast, Callable, Awaitable

import yt_dlp as youtube_dl
from telegram.error import BadRequest

from config import (
    DOWNLOADS_DIR,
    TMP_DIR,
    CACHE_DIR,
    AUDIO_BITRATE,
    MAX_SONG_DURATION,
    THROTTLED_RATE,
    CACHE_ENABLED,
    CACHE_TTL,
    JS_RUNTIME_CONFIG,
    MAX_VIDEO_SIZE_BYTES,
    TRANSCODE_TARGET_SIZE_BYTES,
    YTDLP_CONCURRENT_FRAGMENTS,
    YTDLP_HTTP_CHUNK_SIZE,
    YTDLP_SOCKET_TIMEOUT,
    YTDLP_FORCE_IPV4,
)
try:
    from ffmpeg_setup import FFMPEG_DIR
except Exception:
    FFMPEG_DIR = None

logger = logging.getLogger(__name__)

SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_FFMPEG_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")

MAX_VIDEO_SIZE_LIMIT = MAX_VIDEO_SIZE_BYTES
DEFAULT_OPUS_BITRATE = 128_000  # bits per second
TRANSCODE_SIZE_LIMIT = min(MAX_VIDEO_SIZE_LIMIT, TRANSCODE_TARGET_SIZE_BYTES)


async def _emit_progress(
    callback: Optional[Callable[[str], Awaitable[None]]],
    message: str,
) -> None:
    if not callback:
        return
    try:
        await callback(message)
    except Exception:
        logger.debug("Progress callback raised", exc_info=True)


def _ffmpeg_binary() -> str:
    if FFMPEG_DIR:
        executable = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        candidate = os.path.join(FFMPEG_DIR, executable)
        if os.path.isfile(candidate):
            return candidate
    return "ffmpeg"


def _format_bitrate(bits_per_second: int) -> str:
    if bits_per_second <= 0:
        bits_per_second = 1
    return f"{max(bits_per_second // 1000, 1)}k"


async def _run_ffmpeg_command(
    cmd: list[str],
    *,
    progress_cb: Optional[Callable[[str], Awaitable[None]]] = None,
    duration: Optional[float] = None,
    pass_label: Optional[str] = None,
    base_progress: float = 0.0,
    progress_span: float = 1.0,
) -> None:
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stderr_lines: list[str] = []
    last_percent_reported: Optional[int] = None
    last_emit_ts: float = time.monotonic()

    async def _pump_stdout() -> None:
        nonlocal last_percent_reported, last_emit_ts
        if not process.stdout:
            return
        if not progress_cb or not pass_label:
            # Drain stdout to avoid blocking if progress disabled
            while True:
                chunk = await process.stdout.read(4096)
                if not chunk:
                    break
            return

        buffer: Dict[str, str] = {}
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="ignore").strip()
            if not decoded or "=" not in decoded:
                continue
            key, value = decoded.split("=", 1)
            buffer[key] = value
            if key != "progress":
                continue

            status = value.lower()
            percent: Optional[int] = None
            if duration and duration > 0 and "out_time_ms" in buffer:
                try:
                    elapsed = float(buffer["out_time_ms"]) / 1_000_000.0
                except ValueError:
                    elapsed = None
                if elapsed is not None:
                    pass_completion = max(0.0, min(1.0, elapsed / duration))
                    overall = base_progress + progress_span * pass_completion
                    overall = max(0.0, min(overall, 1.0))
                    percent = int(overall * 100)

            if percent is not None:
                now = time.monotonic()
                should_emit = False
                if last_percent_reported is None or percent != last_percent_reported:
                    should_emit = True
                elif now - last_emit_ts >= 10.0:
                    should_emit = True

                if should_emit:
                    last_percent_reported = percent
                    last_emit_ts = now
                    await _emit_progress(progress_cb, f"{pass_label} {percent}%")

            if status == "end" and percent is not None:
                # Ensure we emit final progress for this pass
                await _emit_progress(progress_cb, f"{pass_label} {percent}%")

            buffer.clear()

    async def _pump_stderr() -> None:
        if not process.stderr:
            return
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            stderr_lines.append(line.decode("utf-8", errors="ignore"))

    stdout_task = asyncio.create_task(_pump_stdout())
    stderr_task = asyncio.create_task(_pump_stderr())

    await process.wait()
    await stdout_task
    await stderr_task

    if process.returncode != 0:
        stderr_text = "".join(stderr_lines)
        raise RuntimeError(f"ffmpeg failed with code {process.returncode}: {stderr_text}")


def _cleanup_pass_logs(passlog_base: str) -> None:
    if not passlog_base:
        return
    patterns = [
        passlog_base,
        f"{passlog_base}-0.log",
        f"{passlog_base}-0.log.mbtree",
        f"{passlog_base}.log",
        f"{passlog_base}.log.mbtree",
    ]
    for path in patterns:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def _select_progressive_format_within_size(info: Dict[str, Any], size_limit: int) -> Optional[Dict[str, Any]]:
    formats = info.get("formats")
    if not isinstance(formats, list):
        return None
    candidates: list[tuple[int, float, float, int, Dict[str, Any]]] = []
    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        if fmt.get("acodec") in (None, "none"):
            continue
        if fmt.get("vcodec") in (None, "none"):
            continue
        ext = (fmt.get("ext") or "").lower()
        if ext != "mp4":
            continue
        size = fmt.get("filesize") or fmt.get("filesize_approx")
        if not size or size > size_limit:
            continue
        height = int(fmt.get("height") or 0)
        fps = float(fmt.get("fps") or 0.0)
        tbr = float(fmt.get("tbr") or 0.0)
        preference = int(fmt.get("preference") or 0)
        candidates.append((height, fps, tbr, preference, fmt))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    return candidates[-1][4]


def _select_progressive_format_by_height(info: Dict[str, Any], max_height: int) -> Optional[Dict[str, Any]]:
    formats = info.get("formats")
    if not isinstance(formats, list):
        return None
    candidates: list[tuple[int, float, Dict[str, Any]]] = []
    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        if fmt.get("acodec") in (None, "none"):
            continue
        if fmt.get("vcodec") in (None, "none"):
            continue
        if (fmt.get("ext") or "").lower() != "mp4":
            continue
        height = int(fmt.get("height") or 0)
        if height <= 0 or height > max_height:
            continue
        fps = float(fmt.get("fps") or 0.0)
        candidates.append((height, fps, fmt))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates[-1][2]


async def _transcode_to_target_size(
    input_path: str,
    duration: Optional[float],
    target_size_bytes: int,
    progress_cb: Optional[Callable[[str], Awaitable[None]]] = None,
) -> str:
    ffmpeg_bin = _ffmpeg_binary()
    audio_bitrate = DEFAULT_OPUS_BITRATE
    if not duration or duration <= 0:
        duration = None

    if duration:
        total_bits = target_size_bytes * 8
        audio_bits = audio_bitrate * duration
        video_bits = max(total_bits - audio_bits, total_bits * 0.2)
        video_bitrate = max(int(video_bits / duration), 1)
    else:
        video_bitrate = 1_200_000

    output_path = os.path.splitext(input_path)[0] + "_h265.mp4"
    passlog_base = os.path.join(TMP_DIR, f"ffmpeg-pass-{uuid.uuid4().hex}")
    attempts = 0
    size_goal_mb = target_size_bytes / (1024 * 1024)
    await _emit_progress(
        progress_cb,
        (
            f"Compression required – targeting ≤ {size_goal_mb:.1f} MB"
        ),
    )

    try:
        while attempts < 3:
            attempts += 1
            bitrate_str = _format_bitrate(int(video_bitrate))
            audio_bitrate_str = _format_bitrate(audio_bitrate)
            attempt_prefix = f"Attempt {attempts}:"

            await _emit_progress(
                progress_cb,
                (
                    f"{attempt_prefix} encoding started (0%)"
                    f" – target video {int(video_bitrate / 1000)} kbps"
                ),
            )

            first_pass_cmd = [
                ffmpeg_bin,
                "-y",
                "-i",
                input_path,
                "-c:v",
                "libx265",
                "-b:v",
                bitrate_str,
                "-preset",
                "ultrafast",
                "-pass",
                "1",
                "-passlogfile",
                passlog_base,
                "-an",
                "-f",
                "mp4",
                "-progress",
                "pipe:1",
                "-nostats",
                os.devnull,
            ]
            await _run_ffmpeg_command(
                first_pass_cmd,
                progress_cb=progress_cb,
                duration=duration,
                pass_label=f"{attempt_prefix} pass 1/2",
                base_progress=0.0,
                progress_span=0.50,
            )

            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass

            await _emit_progress(progress_cb, f"{attempt_prefix} pass 2/2 starting (~50%)")

            second_pass_cmd = [
                ffmpeg_bin,
                "-y",
                "-i",
                input_path,
                "-c:v",
                "libx265",
                "-b:v",
                bitrate_str,
                "-preset",
                "ultrafast",
                "-pass",
                "2",
                "-passlogfile",
                passlog_base,
                "-c:a",
                "libopus",
                "-b:a",
                audio_bitrate_str,
                "-movflags",
                "+faststart",
                "-pix_fmt",
                "yuv420p",
                "-progress",
                "pipe:1",
                "-nostats",
                output_path,
            ]
            await _run_ffmpeg_command(
                second_pass_cmd,
                progress_cb=progress_cb,
                duration=duration,
                pass_label=f"{attempt_prefix} pass 2/2",
                base_progress=0.50,
                progress_span=0.48,
            )

            if os.path.exists(output_path):
                new_size = os.path.getsize(output_path)
                if new_size <= target_size_bytes:
                    await _emit_progress(
                        progress_cb,
                        f"{attempt_prefix} complete 100% ({new_size / (1024 * 1024):.1f} MB)"
                    )
                    return output_path
                # adjust bitrate based on achieved size
                ratio = target_size_bytes / new_size
                video_bitrate = max(int(video_bitrate * ratio * 0.9), 1)
                await _emit_progress(
                    progress_cb,
                    (
                        f"{attempt_prefix} output {new_size / (1024 * 1024):.1f} MB"
                        " – retrying with lower bitrate"
                    ),
                )
            else:
                raise RuntimeError("ffmpeg did not produce an output file")

        raise ValueError("Unable to compress video below the size limit after multiple attempts")
    finally:
        _cleanup_pass_logs(passlog_base)


async def _ensure_video_within_size(
    input_path: str,
    duration: Optional[float],
    max_size_bytes: int,
    progress_cb: Optional[Callable[[str], Awaitable[None]]] = None,
) -> str:
    current_size = os.path.getsize(input_path)
    if current_size <= max_size_bytes:
        return input_path

    await _emit_progress(
        progress_cb,
        (
            f"Downloaded size {current_size / (1024 * 1024):.1f} MB exceeds limit"
            f" ({max_size_bytes / (1024 * 1024):.1f} MB)"
        ),
    )

    target_size_bytes = min(max_size_bytes, TRANSCODE_TARGET_SIZE_BYTES)
    compressed_path = await _transcode_to_target_size(
        input_path,
        duration,
        target_size_bytes,
        progress_cb,
    )
    try:
        os.remove(input_path)
    except OSError:
        pass
    return compressed_path


def _sanitize(name: str) -> str:
    name = name.strip()[:150]
    return SAFE_FILENAME_RE.sub("_", name)


def _build_cache_key(kind: str, query: str) -> str:
    h = hashlib.sha256(f"{kind}:{query.lower()}".encode()).hexdigest()[:16]
    return f"{kind}-{h}.mp3" if kind == "audio" else f"{kind}-{h}.mp4"


def _yt_base_opts() -> Dict[str, Any]:
    base: Dict[str, Any] = {
        'noplaylist': True,
        'force_ipv4': YTDLP_FORCE_IPV4,
        'restrictfilenames': True,
        'quiet': True,
        'outtmpl': os.path.join(TMP_DIR, '%(id)s.%(ext)s'),
    }
    if THROTTLED_RATE:
        base['throttled_rate'] = THROTTLED_RATE
    if FFMPEG_DIR:
        base['ffmpeg_location'] = FFMPEG_DIR
    if JS_RUNTIME_CONFIG:
        base['js_runtimes'] = JS_RUNTIME_CONFIG
    if YTDLP_CONCURRENT_FRAGMENTS:
        base['concurrent_fragment_downloads'] = YTDLP_CONCURRENT_FRAGMENTS
    if YTDLP_HTTP_CHUNK_SIZE:
        base['http_chunk_size'] = YTDLP_HTTP_CHUNK_SIZE
    if YTDLP_SOCKET_TIMEOUT:
        base['socket_timeout'] = YTDLP_SOCKET_TIMEOUT
    return base


def _audio_opts() -> Dict[str, Any]:
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


def _video_opts() -> Dict[str, Any]:
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
                return cast(Dict[str, Any], info_raw)
        except Exception as e:
            logger.warning(f"Direct URL failed, fallback to search: {e}")
    try:
        search_raw = ydl.extract_info(f"ytsearch:{query}", download=False)
        if isinstance(search_raw, dict) and 'entries' in search_raw:
            entries = search_raw.get('entries')
            if isinstance(entries, list) and entries:
                first = entries[0]
                if isinstance(first, dict):
                    return cast(Dict[str, Any], first)
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
    opts: Dict[str, Any] = _audio_opts()
    session_token = uuid.uuid4().hex
    job_tmp_dir = os.path.join(TMP_DIR, session_token)
    os.makedirs(job_tmp_dir, exist_ok=True)
    opts['outtmpl'] = os.path.join(job_tmp_dir, '%(id)s.%(ext)s')
    try:
        with youtube_dl.YoutubeDL(cast(Any, opts)) as ydl:
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
    finally:
        shutil.rmtree(job_tmp_dir, ignore_errors=True)

async def download_video(
    query: str,
    progress_cb: Optional[Callable[[str], Awaitable[None]]] = None,
) -> Tuple[str, str]:
    cache_file = _build_cache_key('video', query)
    cache_path = os.path.join(CACHE_DIR, cache_file)
    if CACHE_ENABLED and os.path.exists(cache_path):
        if time.time() - os.path.getmtime(cache_path) < CACHE_TTL:
            return cache_path, os.path.splitext(os.path.basename(cache_path))[0]

    base_opts: Dict[str, Any] = _video_opts()
    download_opts: Dict[str, Any] = dict(base_opts)
    session_token = uuid.uuid4().hex
    job_tmp_dir = os.path.join(TMP_DIR, session_token)
    os.makedirs(job_tmp_dir, exist_ok=True)
    unique_outtmpl = os.path.join(job_tmp_dir, '%(id)s.%(ext)s')
    base_opts['outtmpl'] = unique_outtmpl
    download_opts['outtmpl'] = unique_outtmpl

    duration: Optional[float] = None
    video_url: Optional[str] = None

    with youtube_dl.YoutubeDL(cast(Any, base_opts)) as metadata_ydl:
        info = await search_and_resolve(query, metadata_ydl)
        if not info:
            raise ValueError("Could not find video")
        duration = info.get('duration') if isinstance(info, dict) else None
        video_url = info.get('webpage_url') or info.get('url')
        if not isinstance(video_url, str):
            raise ValueError("Unable to resolve video URL")

        detailed_info = metadata_ydl.extract_info(video_url, download=False)
        if isinstance(detailed_info, dict):
            selected_format = _select_progressive_format_within_size(
                cast(Dict[str, Any], detailed_info), TRANSCODE_SIZE_LIMIT
            )
            if selected_format:
                fmt_id = str(selected_format.get('format_id') or "")
                if fmt_id:
                    download_opts['format'] = fmt_id
                    approx_size = selected_format.get('filesize') or selected_format.get('filesize_approx')
                    human_title = detailed_info.get('title') or query
                    if approx_size:
                        logger.info(
                            "Using pre-sized format %s (~%.1f MB) for %s",
                            fmt_id,
                            approx_size / (1024 * 1024),
                            human_title,
                        )
                    else:
                        logger.info("Using pre-sized format %s for %s", fmt_id, human_title)
            else:
                fallback_format = _select_progressive_format_by_height(
                    cast(Dict[str, Any], detailed_info),
                    480,
                )
                if fallback_format:
                    fmt_id = str(fallback_format.get('format_id') or "")
                    if fmt_id:
                        download_opts['format'] = fmt_id
                        human_title = detailed_info.get('title') or query
                        fmt_height = fallback_format.get('height')
                        fmt_note = fallback_format.get('format_note') or ''
                        logger.info(
                            "Falling back to %sp format %s (%s) for %s prior to compression",
                            fmt_height or '480',
                            fmt_id,
                            fmt_note,
                            human_title,
                        )

    if not video_url:
        raise ValueError("No video URL found for download")

    with youtube_dl.YoutubeDL(cast(Any, download_opts)) as download_ydl:
        info_dl = download_ydl.extract_info(video_url, download=True)
        if not isinstance(info_dl, dict):
            raise ValueError("Unexpected download metadata format")
        file_name = download_ydl.prepare_filename(info_dl)

    try:
        base, _ = os.path.splitext(file_name)
        mp4_file = base + '.mp4'
        if not os.path.exists(mp4_file):
            raise FileNotFoundError("mp4 output missing")

        duration = info_dl.get('duration', duration)
        size_threshold = TRANSCODE_SIZE_LIMIT
        current_size = os.path.getsize(mp4_file)
        if current_size <= size_threshold:
            source_path = mp4_file
        else:
            try:
                source_path = await _ensure_video_within_size(
                    mp4_file,
                    duration,
                    MAX_VIDEO_SIZE_LIMIT,
                    progress_cb,
                )
            except Exception as exc:
                raise ValueError(f"Failed to compress video under size limit: {exc}") from exc

        final_path = cache_path if CACHE_ENABLED else os.path.join(DOWNLOADS_DIR, os.path.basename(source_path))
        os.replace(source_path, final_path)
        title = info_dl.get('title') if isinstance(info_dl, dict) else None
        return final_path, (title or query)
    finally:
        shutil.rmtree(job_tmp_dir, ignore_errors=True)

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
