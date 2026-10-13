"""Small anonymous yt-dlp helpers shared by non-Instagram adapters."""

import os
from typing import Optional, Tuple

from yt_dlp import YoutubeDL

from ..config import YDL_SOCKET_TIMEOUT_SECONDS
from ..media import MediaAcquisitionError


class SilentLogger:
    def debug(self, _message):
        pass

    def warning(self, _message):
        pass

    def error(self, _message):
        pass


def ytdlp_options(work_dir: str, download: bool) -> dict:
    """Return options that intentionally contain no cookie/auth settings."""
    options = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": YDL_SOCKET_TIMEOUT_SECONDS,
        "retries": 1,
        "extractor_retries": 1,
        "fragment_retries": 1,
        "noplaylist": True,
        "logger": SilentLogger(),
        "outtmpl": os.path.join(work_dir, "%(id)s.%(ext)s"),
    }
    if download:
        # Prefer a directly playable combined MP4 where available.  Falling
        # back to best retains support for extractors without an MP4 choice.
        options["format"] = "best[ext=mp4]/best"
    else:
        options["skip_download"] = True
        options["ignore_no_formats_error"] = True
    return options


def extract_info(url: str, work_dir: str) -> dict:
    try:
        with YoutubeDL(ytdlp_options(work_dir, download=False)) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise MediaAcquisitionError(
            f"Anonymous yt-dlp extraction failed: {exc}"
        ) from exc
    if not isinstance(info, dict):
        raise MediaAcquisitionError("Anonymous yt-dlp returned no metadata")
    return info


def _downloaded_path(ydl: YoutubeDL, info: dict, work_dir: str) -> Optional[str]:
    candidates = []
    requested = info.get("requested_downloads") or []
    for item in requested:
        if isinstance(item, dict):
            candidates.append(item.get("filepath"))
    candidates.extend((info.get("filepath"), info.get("_filename")))
    try:
        candidates.append(ydl.prepare_filename(info))
    except Exception:
        pass

    work_root = os.path.realpath(work_dir) + os.sep
    for candidate in candidates:
        if not candidate:
            continue
        resolved = os.path.realpath(candidate)
        if not resolved.startswith(work_root):
            continue
        if os.path.isfile(resolved) and os.path.getsize(resolved) > 0:
            return resolved

    # Merging/remuxing may change the extension returned by prepare_filename.
    content_id = str(info.get("id") or "")
    if content_id:
        for name in os.listdir(work_dir):
            path = os.path.join(work_dir, name)
            if name.startswith(content_id + ".") and os.path.isfile(path):
                if os.path.getsize(path) > 0:
                    return path
    return None


def download_video(url: str, work_dir: str) -> Tuple[dict, str]:
    try:
        with YoutubeDL(ytdlp_options(work_dir, download=True)) as ydl:
            info = ydl.extract_info(url, download=True)
            if not isinstance(info, dict):
                raise MediaAcquisitionError(
                    "Anonymous yt-dlp returned no video metadata"
                )
            path = _downloaded_path(ydl, info, work_dir)
    except MediaAcquisitionError:
        raise
    except Exception as exc:
        raise MediaAcquisitionError(
            f"Anonymous yt-dlp download failed: {exc}"
        ) from exc

    if not path:
        raise MediaAcquisitionError(
            "Anonymous yt-dlp returned no downloaded video file"
        )
    return info, path
