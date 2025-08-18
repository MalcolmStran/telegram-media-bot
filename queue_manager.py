import asyncio
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, List
import json
import os

from config import MAX_QUEUE_SIZE, QUEUE_STATE_FILE

@dataclass(order=True)
class DownloadJob:
    created_at: float = field(init=False, default_factory=time.time, compare=True)
    chat_id: int = field(compare=False)
    query: str = field(compare=False)
    kind: str = field(compare=False)  # 'audio' | 'video'
    user_id: int = field(compare=False)
    request_message_id: Optional[int] = field(default=None, compare=False)

class DownloadQueue:
    def __init__(self):
        self._queue: asyncio.Queue[DownloadJob] = asyncio.Queue()
        self._pending: list[DownloadJob] = []  # mirror for introspection
        self._lock = asyncio.Lock()
        self._loaded = False

    async def load(self):
        if self._loaded:
            return
        self._loaded = True
        if not os.path.exists(QUEUE_STATE_FILE):
            return
        try:
            with open(QUEUE_STATE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for item in data:
                job = DownloadJob(chat_id=item['chat_id'], query=item['query'], kind=item['kind'], user_id=item['user_id'])
                # preserve creation order
                job.created_at = item.get('created_at', job.created_at)
                self._pending.append(job)
                await self._queue.put(job)
        except Exception:
            pass

    def _persist(self):
        try:
            serializable: List[dict] = []
            for j in self._pending:
                d = asdict(j)
                d.pop('request_message_id', None)
                serializable.append(d)
            with open(QUEUE_STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(serializable, f)
        except Exception:
            pass

    def size(self) -> int:
        return len(self._pending)

    def list_jobs(self) -> list[DownloadJob]:
        return list(self._pending)

    async def add(self, job: DownloadJob) -> int:
        async with self._lock:
            if self.size() >= MAX_QUEUE_SIZE:
                raise OverflowError("Queue full")
            self._pending.append(job)
            await self._queue.put(job)
            position = self._pending.index(job) + 1
            self._persist()
            return position

    async def cancel(self, user_id: int, identifier: str) -> Optional[DownloadJob]:
        # identifier can be position (str int) or substring of query
        async with self._lock:
            target: Optional[DownloadJob] = None
            # by position
            if identifier.isdigit():
                idx = int(identifier) - 1
                if 0 <= idx < len(self._pending):
                    j = self._pending[idx]
                    if j.user_id == user_id:
                        target = j
            if not target:
                # search substring
                for j in self._pending:
                    if identifier.lower() in j.query.lower() and j.user_id == user_id:
                        target = j
                        break
            if not target:
                return None
            # Remove from pending; underlying asyncio.Queue still has it; we mark cancelled by substituting sentinel
            self._pending.remove(target)
            # Soft cancel: store a flag
            setattr(target, "_cancelled", True)
            self._persist()
            return target

    async def get(self) -> DownloadJob:
        while True:
            job = await self._queue.get()
            # Skip cancelled jobs
            if getattr(job, "_cancelled", False):
                continue
            return job

    async def mark_done(self, job: DownloadJob):
        async with self._lock:
            if job in self._pending:
                self._pending.remove(job)
                self._persist()

queue = DownloadQueue()
