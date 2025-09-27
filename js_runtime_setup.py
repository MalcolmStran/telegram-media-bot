import logging
import os
import platform
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DENO_VERSION = "1.46.3"

_ARCH_MAP = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "arm64": "aarch64",
    "aarch64": "aarch64",
}

_SYSTEM_TARGET = {
    "windows": "pc-windows-msvc",
    "linux": "unknown-linux-gnu",
    "darwin": "apple-darwin",
}

_EXT_PER_SYSTEM = {
    "windows": "zip",
    "darwin": "zip",
    "linux": "tar.xz",
}


def _is_executable(path: str) -> bool:
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def _download(url: str, dest: str) -> None:
    logger.info("Downloading %s", url)
    with urllib.request.urlopen(url) as resp, open(dest, "wb") as out_file:
        shutil.copyfileobj(resp, out_file)


def _extract(archive_path: str, system: str, temp_dir: str) -> str:
    target_names = {"deno.exe"} if system == "windows" else {"deno"}
    if archive_path.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(temp_dir)
    else:
        with tarfile.open(archive_path, "r:xz") as tf:
            tf.extractall(temp_dir)
    for root, _dirs, files in os.walk(temp_dir):
        for name in files:
            if name in target_names:
                return os.path.join(root, name)
    raise FileNotFoundError("Extracted archive did not contain a Deno binary")


def _build_download_url(version: Optional[str], system: str, arch: str) -> tuple[str, str]:
    arch_normalized = _ARCH_MAP.get(arch.lower())
    if not arch_normalized:
        raise RuntimeError(f"Unsupported architecture for Deno auto-install: {arch}")
    target = _SYSTEM_TARGET.get(system)
    if not target:
        raise RuntimeError(f"Unsupported platform for Deno auto-install: {system}")
    version = version or DEFAULT_DENO_VERSION
    tag = version if version.startswith("v") else f"v{version}"
    ext = _EXT_PER_SYSTEM[system]
    filename = f"deno-{arch_normalized}-{target}.{ext}"
    url = f"https://github.com/denoland/deno/releases/download/{tag}/{filename}"
    return url, filename


def ensure_deno(
    *,
    local_dir: str = "deno_bin",
    version: Optional[str] = None,
    explicit_path: Optional[str] = None,
    auto_install: bool = True,
) -> str:
    """Locate (or download) a Deno executable and return its absolute path."""

    system = platform.system().lower()
    machine = platform.machine().lower()

    if explicit_path:
        explicit_path = os.path.abspath(explicit_path)
        if not _is_executable(explicit_path):
            raise FileNotFoundError(f"DENO_PATH is set but not executable: {explicit_path}")
        return explicit_path

    expected_local = os.path.join(local_dir, "deno.exe" if system == "windows" else "deno")
    if _is_executable(expected_local):
        return os.path.abspath(expected_local)

    path_in_path = shutil.which("deno")
    if _is_executable(path_in_path or ""):
        return os.path.abspath(path_in_path)  # type: ignore[arg-type]

    if not auto_install:
        raise RuntimeError(
            "Deno runtime not found on PATH and AUTO_INSTALL_DENO=false. "
            "Install Deno manually or set DENO_PATH."
        )

    os.makedirs(local_dir, exist_ok=True)
    url, filename = _build_download_url(version, system, machine)
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, filename)
        _download(url, archive_path)
        extracted_path = _extract(archive_path, system, temp_dir)
        final_path = os.path.abspath(expected_local)
        shutil.move(extracted_path, final_path)
        current_mode = os.stat(final_path).st_mode
        os.chmod(final_path, current_mode | stat.S_IEXEC)
        logger.info("Installed Deno runtime at %s", final_path)
        return final_path