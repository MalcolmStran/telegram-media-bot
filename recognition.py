import os
import uuid
import logging
from shazamio import Shazam
from config import TMP_DIR

logger = logging.getLogger(__name__)

async def recognize_audio_file(file_obj, suffix: str = '.ogg') -> str | None:
    """Download telegram File object to temp, run recognition, return 'Title Artist' or None."""
    tmp_name = f"rec-{uuid.uuid4().hex}{suffix}"
    tmp_path = os.path.join(TMP_DIR, tmp_name)
    try:
        await file_obj.download_to_drive(tmp_path)
        shazam = Shazam()
        out = await shazam.recognize(tmp_path)
        if out and 'track' in out:
            t = out['track']
            return f"{t['title']} {t['subtitle']}"
        return None
    except Exception as e:
        logger.error(f"Recognition failed: {e}")
        return None
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
