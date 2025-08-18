import os
import shutil
import stat
import tarfile
import platform
import tempfile
import urllib.request
import logging

logger = logging.getLogger(__name__)

FFMPEG_STATIC_URL = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"

def _is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)

def _which(name: str):
    return shutil.which(name)

def _try_path_dir(dir_path: str) -> bool:
    return _is_executable(os.path.join(dir_path, 'ffmpeg')) and _is_executable(os.path.join(dir_path, 'ffprobe'))

def ensure_ffmpeg(local_dir: str = "ffmpeg_bin") -> str | None:
    """Ensure ffmpeg & ffprobe available; download static build if missing. Returns directory or None."""
    env_loc = os.getenv('FFMPEG_LOCATION')
    if env_loc and _try_path_dir(env_loc):
        return env_loc
    ffmpeg_p = _which('ffmpeg')
    ffprobe_p = _which('ffprobe')
    if ffmpeg_p and ffprobe_p:
        return os.path.dirname(ffmpeg_p)
    if _try_path_dir(local_dir):
        return local_dir
    if platform.system().lower() != 'linux':
        logger.warning("Automatic ffmpeg download skipped (non-Linux platform). Provide ffmpeg manually.")
        return None
    try:
        os.makedirs(local_dir, exist_ok=True)
        logger.info("Downloading static ffmpeg bundle ...")
        with tempfile.TemporaryDirectory() as td:
            archive_path = os.path.join(td, 'ffmpeg.tar.xz')
            with urllib.request.urlopen(FFMPEG_STATIC_URL) as resp, open(archive_path, 'wb') as out:
                shutil.copyfileobj(resp, out)
            with tarfile.open(archive_path, 'r:xz') as tar:
                tar.extractall(td)
            extracted_dir = None
            for root, dirs, files in os.walk(td):
                if 'ffmpeg' in files and 'ffprobe' in files:
                    extracted_dir = root
                    break
            if not extracted_dir:
                raise RuntimeError("ffmpeg binaries not found in archive")
            for name in ('ffmpeg', 'ffprobe'):
                src = os.path.join(extracted_dir, name)
                dest = os.path.join(local_dir, name)
                shutil.copy2(src, dest)
                st = os.stat(dest)
                os.chmod(dest, st.st_mode | stat.S_IEXEC)
        logger.info("Static ffmpeg installed in %s", local_dir)
        if _try_path_dir(local_dir):
            return local_dir
    except Exception as e:
        logger.error(f"Failed to download ffmpeg: {e}")
    return None

FFMPEG_DIR = ensure_ffmpeg()
