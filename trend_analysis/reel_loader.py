"""Fetches Instagram content (Reels, and now /p/ posts) via yt-dlp.

Independent of the Playwright collector: works from a bare content URL
only. Never writes into the repository — callers pass a temporary
work_dir and are responsible for its lifecycle (see analyze_reel.py).

The original Reel path (validate_reel_url / ReelMedia / load_reel) is
untouched — it's the same proven mechanism as before. /p/ post support
(validate_content_url / PostMedia / CarouselItem / load_post) is
deliberately additive: it uses a different download mechanism (direct
authenticated fetch of resolved media URLs via yt-dlp's own network
stack) because yt-dlp's own downloader hard-errors on non-video carousel
items (confirmed empirically — see analyze_reel.py's module docstring
for the finding), so it can't safely reuse load_reel's download=True path.
"""

import os
import re
from dataclasses import dataclass
from typing import List, Optional

from yt_dlp import YoutubeDL

from .config import INSTAGRAM_COOKIES_FILE, YDL_SOCKET_TIMEOUT_SECONDS

# Accepts /reel/<id>/ and /reels/<id>/, with an optional leading username
# segment (e.g. instagram.com/someuser/reel/<id>/), matching how Instagram
# actually shapes these URLs.
REEL_URL_PATTERN = re.compile(
    r"^https?://(?:www\.)?instagram\.com/(?:[^/]+/)?reels?/(?P<id>[^/?#]+)/?"
)

# Accepts /reel(s)/<id>/ AND /p/<id>/, with an optional leading username
# segment. Excludes /reels/audio/... (Instagram's audio-browsing sub-path,
# not a Reel shortcode). Query strings (e.g. ?igsh=...) are ignored by
# [^/?#]+ matching up to the first /, ?, or # — so they're naturally
# normalized away.
CONTENT_URL_PATTERN = re.compile(
    r"^https?://(?:www\.)?instagram\.com/(?:[^/]+/)?(?P<kind>reels?(?!/audio/)|p)/(?P<id>[^/?#]+)/?"
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


# ---------------------------------------------------------------------------
# /p/ post support (image / carousel / video) — additive, does not touch the
# Reel path above.
# ---------------------------------------------------------------------------


@dataclass
class CarouselItem:
    index: int
    media_type: str  # "video" | "image" | "unresolved"
    path: Optional[str] = None


@dataclass
class PostMedia:
    content_url: str
    content_id: str
    metadata: dict
    video_path: Optional[str] = None
    image_path: Optional[str] = None
    carousel_items: Optional[List[CarouselItem]] = None


def validate_content_url(url: str):
    """Return (content_kind, shortcode) where content_kind is "reel" or
    "post", or raise ReelLoadError. Superset of validate_reel_url, used
    for the initial Reel-vs-post routing decision in analyze_reel.py.
    """
    if not url or not isinstance(url, str):
        raise ReelLoadError("Content URL is missing or not a string")

    match = CONTENT_URL_PATTERN.match(url.strip())
    if not match:
        raise ReelLoadError(
            f"'{url}' does not look like a supported Instagram URL "
            "(expected .../reel(s)/<ID>/ or .../p/<ID>/)"
        )

    kind = match.group("kind")
    content_kind = "reel" if kind in ("reel", "reels") else "post"
    return content_kind, match.group("id")


def _post_ydl_options(work_dir: str) -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": YDL_SOCKET_TIMEOUT_SECONDS,
        "retries": 1,
        "extractor_retries": 1,
        # Without this, yt-dlp hard-errors ("No video formats found!") on
        # any image-only item — confirmed empirically against a real mixed
        # carousel. Reels never hit this since they're always video.
        "ignore_no_formats_error": True,
        "logger": _SilentLogger(),
    }
    if INSTAGRAM_COOKIES_FILE:
        options["cookiefile"] = INSTAGRAM_COOKIES_FILE
    return options


def _best_thumbnail_url(item: dict) -> Optional[str]:
    thumbnails = item.get("thumbnails")
    if not thumbnails:
        return None
    return thumbnails[-1].get("url")


def _pick_best_format_url(formats: list) -> Optional[str]:
    if not formats:
        return None
    best = max(formats, key=lambda f: (f.get("height") or 0, f.get("filesize") or f.get("filesize_approx") or 0))
    return best.get("url")


def _download_binary(ydl, url: str, dest_path: str) -> bool:
    """Fetch url through yt-dlp's own (cookie-authenticated) network stack
    and write it to dest_path. Used for both images and direct video CDN
    URLs — Instagram media URLs are plain HTTPS links, not manifests.
    """
    try:
        resp = ydl.urlopen(url)
        data = resp.read()
    except Exception:
        return False
    if not data:
        return False
    with open(dest_path, "wb") as f:
        f.write(data)
    return True


def _resolve_carousel_item(ydl, entry: dict, index: int, work_dir: str) -> CarouselItem:
    formats = entry.get("formats")
    if formats:
        format_url = _pick_best_format_url(formats)
        if format_url:
            ext = entry.get("ext") or "mp4"
            dest = os.path.join(work_dir, f"carousel_{index}.{ext}")
            if _download_binary(ydl, format_url, dest):
                return CarouselItem(index=index, media_type="video", path=dest)
        return CarouselItem(index=index, media_type="unresolved", path=None)

    thumb_url = _best_thumbnail_url(entry)
    if thumb_url:
        dest = os.path.join(work_dir, f"carousel_{index}.jpg")
        if _download_binary(ydl, thumb_url, dest):
            return CarouselItem(index=index, media_type="image", path=dest)

    # Confirmed limitation: some carousel image slides currently expose
    # neither formats nor thumbnails through yt-dlp's Instagram extractor
    # (observed on a real mixed image+video carousel). Skip gracefully
    # rather than crash or fabricate — analyze_reel.py logs this.
    return CarouselItem(index=index, media_type="unresolved", path=None)


def load_post(content_url: str, content_id: str, work_dir: str) -> PostMedia:
    """Fetch a /p/ post's metadata and download whatever local media is
    available: a single video, a single image, or (for a carousel) each
    resolvable item. Unresolvable carousel items are left as "unresolved"
    rather than failing the whole post.
    """
    options = _post_ydl_options(work_dir)

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(content_url, download=False)

            if info is None:
                raise ReelLoadError("yt-dlp returned no metadata for this post")

            entries = info.get("entries")
            if entries is not None:
                carousel_items = [
                    _resolve_carousel_item(ydl, entry, i, work_dir)
                    for i, entry in enumerate(list(entries))
                ]
                return PostMedia(
                    content_url=content_url, content_id=content_id,
                    metadata=info, carousel_items=carousel_items,
                )

            if info.get("formats"):
                format_url = _pick_best_format_url(info["formats"])
                if not format_url:
                    raise ReelLoadError("This post's video has no downloadable format")
                ext = info.get("ext") or "mp4"
                video_path = os.path.join(work_dir, f"{content_id}.{ext}")
                if not _download_binary(ydl, format_url, video_path):
                    raise ReelLoadError("Failed to download this post's video")
                return PostMedia(
                    content_url=content_url, content_id=content_id,
                    metadata=info, video_path=video_path,
                )

            thumb_url = _best_thumbnail_url(info)
            if not thumb_url:
                raise ReelLoadError("This post has no video and no usable image was found")
            image_path = os.path.join(work_dir, f"{content_id}.jpg")
            if not _download_binary(ydl, thumb_url, image_path):
                raise ReelLoadError("Failed to download this post's image")
            return PostMedia(
                content_url=content_url, content_id=content_id,
                metadata=info, image_path=image_path,
            )

    except ReelLoadError:
        raise
    except Exception as exc:
        raise ReelLoadError(f"yt-dlp could not fetch this post: {exc}") from exc
