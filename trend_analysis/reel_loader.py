"""Fetches a Reel's metadata and a temporary local video copy via yt-dlp.

Independent of the Playwright collector: works from a bare Reel URL only.
Never writes into the repository — callers pass a temporary work_dir and
are responsible for its lifecycle (see analyze_reel.py).
"""

import os
import re
from dataclasses import dataclass
from typing import Optional

from yt_dlp import YoutubeDL

from .config import INSTAGRAM_COOKIES_FILE, YDL_SOCKET_TIMEOUT_SECONDS

# Accepts /reel/<id>/ and /reels/<id>/, with an optional leading username
# segment (e.g. instagram.com/someuser/reel/<id>/), matching how Instagram
# actually shapes these URLs.
REEL_URL_PATTERN = re.compile(
    r"^https?://(?:www\.)?instagram\.com/(?:[^/]+/)?reels?/(?P<id>[^/?#]+)/?"
)


class ReelLoadError(Exception):
    """Raised when a Reel URL is invalid or yt-dlp can't fetch it."""


@dataclass
class ReelMedia:
    reel_url: str
    reel_id: str
    video_path: Optional[str]
    metadata: dict


class _SilentLogger:
    def debug(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def validate_reel_url(url: str) -> str:
    """Return the Reel's shortcode/id if url looks like a valid Instagram
    Reel URL, otherwise raise ReelLoadError.
    """
    if not url or not isinstance(url, str):
        raise ReelLoadError("Reel URL is missing or not a string")

    match = REEL_URL_PATTERN.match(url.strip())
    if not match:
        raise ReelLoadError(
            f"'{url}' does not look like an Instagram Reel URL "
            "(expected https://www.instagram.com/reel(s)/<ID>/)"
        )

    return match.group("id")


def _build_ydl_options(work_dir: str) -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": YDL_SOCKET_TIMEOUT_SECONDS,
        "retries": 1,
        "extractor_retries": 1,
        "fragment_retries": 1,
        "outtmpl": os.path.join(work_dir, "%(id)s.%(ext)s"),
        "format": "best",
        "logger": _SilentLogger(),
    }
    if INSTAGRAM_COOKIES_FILE:
        options["cookiefile"] = INSTAGRAM_COOKIES_FILE
    return options


def load_reel(reel_url: str, work_dir: str, download_video: bool = True) -> ReelMedia:
    """Validate the URL, then fetch metadata (and, unless download_video is
    False, a temporary local video copy for frame extraction) via yt-dlp.
    """
    reel_id = validate_reel_url(reel_url)
    options = _build_ydl_options(work_dir)

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(reel_url, download=download_video)

            video_path = None
            if download_video and info is not None:
                try:
                    video_path = ydl.prepare_filename(info)
                except Exception:
                    video_path = None
    except Exception as exc:
        raise ReelLoadError(f"yt-dlp could not fetch this Reel: {exc}") from exc

    if info is None:
        raise ReelLoadError("yt-dlp returned no metadata for this Reel")

    if video_path and not os.path.exists(video_path):
        video_path = None

    return ReelMedia(reel_url=reel_url, reel_id=reel_id, video_path=video_path, metadata=info)
