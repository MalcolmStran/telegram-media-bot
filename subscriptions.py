import asyncio
import json
import logging
import os
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

import yt_dlp as youtube_dl

from config import SUBSCRIPTIONS_FILE, JS_RUNTIME_CONFIG

logger = logging.getLogger(__name__)


@dataclass
class VideoEntry:
    video_id: str
    video_url: str
    title: str
    timestamp: Optional[float] = None


@dataclass
class ChannelMetadata:
    channel_id: str
    channel_url: str
    channel_title: str
    latest_videos: List[VideoEntry]


@dataclass
class SubscriptionRecord:
    user_id: int
    chat_id: int
    channel_url: str
    channel_id: str
    channel_title: str
    last_video_id: Optional[str] = None
    last_published: Optional[float] = None


class SubscriptionManager:
    def __init__(self, storage_path: str):
        self._storage_path = storage_path
        self._lock = asyncio.Lock()
        self._loaded = False
        self._subscriptions: Dict[int, List[SubscriptionRecord]] = {}

    async def load(self):
        if self._loaded:
            return
        self._loaded = True
        if not self._storage_path or not os.path.exists(self._storage_path):
            return
        try:
            with open(self._storage_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception as exc:  # pragma: no cover - defensive load
            logger.warning("Failed to load subscriptions: %s", exc)
            return
        for user_id_str, entries in raw.items():
            try:
                user_id = int(user_id_str)
            except (TypeError, ValueError):
                continue
            records: List[SubscriptionRecord] = []
            if isinstance(entries, list):
                for item in entries:
                    if not isinstance(item, dict):
                        continue
                    records.append(
                        SubscriptionRecord(
                            user_id=user_id,
                            chat_id=item.get("chat_id", user_id),
                            channel_url=item.get("channel_url", ""),
                            channel_id=item.get("channel_id", ""),
                            channel_title=item.get("channel_title", ""),
                            last_video_id=item.get("last_video_id"),
                            last_published=item.get("last_published"),
                        )
                    )
            if records:
                self._subscriptions[user_id] = records

    async def _save(self):
        try:
            directory = os.path.dirname(self._storage_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            data: Dict[str, List[dict]] = {}
            for user_id, subs in self._subscriptions.items():
                data[str(user_id)] = [asdict(sub) for sub in subs]
            with open(self._storage_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        except Exception as exc:  # pragma: no cover - defensive save
            logger.warning("Failed to persist subscriptions: %s", exc)

    async def add_or_update(self, record: SubscriptionRecord) -> Tuple[bool, SubscriptionRecord]:
        async with self._lock:
            await self.load()
            subs = self._subscriptions.setdefault(record.user_id, [])
            for existing in subs:
                if existing.channel_id == record.channel_id or existing.channel_url.lower() == record.channel_url.lower():
                    existing.chat_id = record.chat_id
                    existing.channel_url = record.channel_url
                    existing.channel_title = record.channel_title
                    if record.last_video_id:
                        existing.last_video_id = record.last_video_id
                        existing.last_published = record.last_published
                    await self._save()
                    return False, existing
            subs.append(record)
            await self._save()
            return True, record

    async def remove(self, user_id: int, identifier: str) -> Optional[SubscriptionRecord]:
        identifier_l = identifier.lower()
        async with self._lock:
            await self.load()
            subs = self._subscriptions.get(user_id)
            if not subs:
                return None
            for existing in list(subs):
                keys = [existing.channel_id.lower(), existing.channel_url.lower(), existing.channel_title.lower()]
                if any(identifier_l in key for key in keys if key):
                    subs.remove(existing)
                    if not subs:
                        self._subscriptions.pop(user_id, None)
                    await self._save()
                    return existing
        return None

    async def list_for_user(self, user_id: int) -> List[SubscriptionRecord]:
        async with self._lock:
            await self.load()
            subs = self._subscriptions.get(user_id, [])
            return list(subs)

    async def all_subscriptions(self) -> List[SubscriptionRecord]:
        async with self._lock:
            await self.load()
            all_items: List[SubscriptionRecord] = []
            for subs in self._subscriptions.values():
                all_items.extend(list(subs))
            return all_items

    async def update_last_video(
        self,
        user_id: int,
        channel_id: str,
        video_id: Optional[str],
        published: Optional[float],
        channel_url: Optional[str] = None,
        channel_title: Optional[str] = None,
    ) -> bool:
        async with self._lock:
            await self.load()
            subs = self._subscriptions.get(user_id)
            if not subs:
                return False
            for existing in subs:
                if existing.channel_id == channel_id or (channel_url and existing.channel_url == channel_url):
                    if video_id:
                        existing.last_video_id = video_id
                    existing.last_published = published
                    if channel_id:
                        existing.channel_id = channel_id
                    if channel_url:
                        existing.channel_url = channel_url
                    if channel_title:
                        existing.channel_title = channel_title
                    await self._save()
                    return True
        return False


def _base_ydl_opts(limit: int) -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "skip_download": True,
        "quiet": True,
        "extract_flat": True,
        "ignoreerrors": True,
        "playlistend": limit,
    }
    if JS_RUNTIME_CONFIG:
        opts["js_runtimes"] = JS_RUNTIME_CONFIG
    return opts


def _normalize_video_entry(entry: dict) -> Optional[VideoEntry]:
    if not isinstance(entry, dict):
        return None
    video_id = entry.get("id") or entry.get("url")
    if not video_id:
        return None
    raw_url = entry.get("url") or ""
    if raw_url.startswith("http"):
        video_url = raw_url
    else:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
    title = entry.get("title") or entry.get("original_url") or "New upload"
    timestamp = entry.get("timestamp") or entry.get("release_timestamp")
    return VideoEntry(video_id=video_id, video_url=video_url, title=title, timestamp=timestamp)


def _extract_channel_metadata_sync(identifier: str, limit: int = 10) -> ChannelMetadata:
    opts = _base_ydl_opts(limit)
    with youtube_dl.YoutubeDL(opts) as ydl:  # type: ignore[arg-type]
        info = ydl.extract_info(identifier, download=False)
    if not isinstance(info, dict):
        raise ValueError("Unable to extract channel information")

    if "entries" not in info or not info.get("entries"):
        uploader_url = info.get("uploader_url") or info.get("channel_url")
        if uploader_url and uploader_url != identifier:
            with youtube_dl.YoutubeDL(opts) as ydl:  # type: ignore[arg-type]
                info = ydl.extract_info(uploader_url, download=False)
        else:
            raise ValueError("Provided link is not a YouTube channel or playlist")

    entries = info.get("entries") or []
    videos: List[VideoEntry] = []
    for entry in entries:
        video = _normalize_video_entry(entry)
        if video:
            videos.append(video)
    channel_id = info.get("channel_id") or info.get("uploader_id") or info.get("id") or identifier
    channel_title = info.get("channel") or info.get("title") or info.get("uploader") or identifier
    channel_url = info.get("webpage_url") or info.get("original_url") or identifier
    return ChannelMetadata(channel_id=str(channel_id), channel_url=str(channel_url), channel_title=str(channel_title), latest_videos=videos)


async def fetch_channel_metadata(identifier: str, limit: int = 10) -> ChannelMetadata:
    return await asyncio.to_thread(_extract_channel_metadata_sync, identifier, limit)


subscriptions = SubscriptionManager(SUBSCRIPTIONS_FILE)

__all__ = [
    "VideoEntry",
    "ChannelMetadata",
    "SubscriptionRecord",
    "SubscriptionManager",
    "subscriptions",
    "fetch_channel_metadata",
]
