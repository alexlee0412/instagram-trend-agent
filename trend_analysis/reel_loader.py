"""Acquire public Instagram Reels and ``/p/`` posts.

Independent of the Playwright collector: works from a bare content URL
only. Never writes into the repository — callers pass a temporary
work_dir and are responsible for its lifecycle (see analyze_url.py).

Reels use anonymous yt-dlp first. Instagram frequently gates that path even
for public content, so Reel acquisition falls back to Instagram's public
``/embed/captioned/`` representation. The embed contains a short-lived CDN
video URL and a small metadata object and does not require an Instagram
session. This is intentionally isolated here so callers do not need to know
which acquisition mechanism succeeded.

/p/ post support
(validate_content_url / PostMedia / CarouselItem / load_post) is
deliberately additive: it resolves each media item with current yt-dlp and
streams direct media URLs through yt-dlp's network stack. If yt-dlp cannot
resolve a post or every carousel item, the public embed is used as a fallback.
"""

import json
import os
import re
import shutil
from dataclasses import dataclass
from typing import List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from yt_dlp import YoutubeDL

from .config import INSTAGRAM_COOKIES_FILE, YDL_SOCKET_TIMEOUT_SECONDS

# Instagram currently serves the server-rendered public embed (including its
# media context) to this generic user agent. A full desktop Chrome signature
# is routed to the client-rendered shell instead, which has no media payload.
PUBLIC_USER_AGENT = "Mozilla/5.0"
MAX_EMBED_HTML_BYTES = 4 * 1024 * 1024

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
    r"^https?://(?:www\.)?instagram\.com/(?:[^/]+/)?"
    r"(?P<kind>reels?(?!/audio/)|p)/(?P<id>[^/?#]+)/?"
)


class ReelLoadError(Exception):
    """Raised when supported Instagram content cannot be acquired."""


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


def _public_embed_url(reel_id: str) -> str:
    return f"https://www.instagram.com/reel/{reel_id}/embed/captioned/"


def _public_content_embed_url(content_kind: str, content_id: str) -> str:
    path_kind = "reel" if content_kind == "reel" else "p"
    return (
        f"https://www.instagram.com/{path_kind}/{content_id}/embed/captioned/"
    )


def _read_public_embed_content(content_kind: str, content_id: str) -> dict:
    """Return the media object embedded in Instagram's public page.

    The page serializes a JSON string under ``contextJSON``. Parse the JSON
    layers rather than scraping presentation markup, then verify that the
    payload belongs to the shortcode the caller requested.
    """
    request = Request(
        _public_content_embed_url(content_kind, content_id),
        headers={
            "User-Agent": PUBLIC_USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urlopen(request, timeout=YDL_SOCKET_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_EMBED_HTML_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ReelLoadError(f"Instagram public embed request failed: {exc}") from exc

    if len(body) > MAX_EMBED_HTML_BYTES:
        raise ReelLoadError("Instagram public embed response was unexpectedly large")

    html = body.decode("utf-8", errors="replace")
    marker = '"contextJSON":'
    marker_index = html.find(marker)
    if marker_index < 0:
        raise ReelLoadError("Instagram public embed did not contain media metadata")

    serialized = html[marker_index + len(marker):]
    try:
        context_json, _ = json.JSONDecoder().raw_decode(serialized)
        context = json.loads(context_json)
        media = context["gql_data"]["shortcode_media"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ReelLoadError("Instagram public embed metadata was malformed") from exc

    if not isinstance(media, dict):
        raise ReelLoadError("Instagram public embed metadata was malformed")
    if media.get("shortcode") != content_id:
        raise ReelLoadError("Instagram public embed returned different content")
    return media


def _read_public_embed(reel_id: str) -> dict:
    """Backward-compatible Reel-specific public embed reader."""
    media = _read_public_embed_content("reel", reel_id)
    if not media.get("is_video") or not media.get("video_url"):
        raise ReelLoadError("Instagram public embed did not expose Reel video media")
    return media


def _edge_count(media: dict, key: str) -> Optional[int]:
    edge = media.get(key)
    if not isinstance(edge, dict):
        return None
    count = edge.get("count")
    return count if isinstance(count, int) else None


def _embed_caption(media: dict) -> Optional[str]:
    caption_edge = media.get("edge_media_to_caption")
    if not isinstance(caption_edge, dict):
        return None
    edges = caption_edge.get("edges") or []
    if not edges:
        return None
    text = edges[0].get("node", {}).get("text")
    return text if isinstance(text, str) else None


def _embed_metadata(media: dict) -> dict:
    """Map the public embed payload onto fields used by the shared model."""
    owner = media.get("owner") if isinstance(media.get("owner"), dict) else {}
    return {
        "id": media.get("id"),
        "display_id": media.get("shortcode"),
        "description": _embed_caption(media),
        "channel": owner.get("username"),
        "uploader": owner.get("username"),
        "duration": media.get("video_duration"),
        "view_count": media.get("video_view_count"),
        "like_count": _edge_count(media, "edge_liked_by"),
        "comment_count": _edge_count(media, "edge_media_to_comment"),
        "timestamp": None,
        "upload_date": None,
        "audio": media.get("clips_music_attribution_info"),
        "acquisition_source": "instagram_public_embed",
    }


def _download_public_asset(
    url: str,
    dest_path: str,
    referer: str,
    expected_type: str,
) -> None:
    """Stream a public CDN object to disk without buffering it in memory."""
    request = Request(
        url,
        headers={
            "User-Agent": PUBLIC_USER_AGENT,
            "Referer": referer,
        },
    )
    try:
        with urlopen(request, timeout=YDL_SOCKET_TIMEOUT_SECONDS) as response:
            content_type = response.headers.get_content_type()
            if content_type and not content_type.startswith(f"{expected_type}/"):
                raise ReelLoadError(
                    f"Instagram public media returned unexpected content type: {content_type}"
                )
            with open(dest_path, "wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
    except ReelLoadError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ReelLoadError(f"Instagram public media download failed: {exc}") from exc

    if not os.path.exists(dest_path) or os.path.getsize(dest_path) == 0:
        raise ReelLoadError("Instagram public media download was empty")


def _download_public_media(url: str, dest_path: str, referer: str) -> None:
    """Backward-compatible wrapper for downloading public Reel video."""
    _download_public_asset(url, dest_path, referer, expected_type="video")


def _load_reel_from_public_embed(
    reel_url: str,
    reel_id: str,
    work_dir: str,
    download_video: bool,
) -> ReelMedia:
    media = _read_public_embed(reel_id)
    video_path = None
    if download_video:
        video_path = os.path.join(work_dir, f"{reel_id}.mp4")
        _download_public_media(media["video_url"], video_path, _public_embed_url(reel_id))
    return ReelMedia(
        reel_url=reel_url,
        reel_id=reel_id,
        video_path=video_path,
        metadata=_embed_metadata(media),
    )


def load_reel(reel_url: str, work_dir: str, download_video: bool = True) -> ReelMedia:
    """Fetch a public Reel, using yt-dlp first and the public embed as fallback."""
    reel_id = validate_reel_url(reel_url)
    options = _build_ydl_options(work_dir)
    ytdlp_error = None

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
        info = None
        video_path = None
        ytdlp_error = exc

    if info is not None:
        if video_path and not os.path.exists(video_path):
            video_path = None
        if not download_video or video_path:
            return ReelMedia(
                reel_url=reel_url,
                reel_id=reel_id,
                video_path=video_path,
                metadata=info,
            )

    try:
        return _load_reel_from_public_embed(
            reel_url, reel_id, work_dir, download_video=download_video,
        )
    except ReelLoadError as embed_error:
        if ytdlp_error is not None:
            raise ReelLoadError(
                "Anonymous yt-dlp acquisition failed and Instagram's public "
                f"embed fallback also failed: {embed_error}"
            ) from embed_error
        raise ReelLoadError(
            "yt-dlp returned no usable Reel media and Instagram's public "
            f"embed fallback also failed: {embed_error}"
        ) from embed_error


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
    incomplete_reasons: Optional[List[str]] = None


def validate_content_url(url: str):
    """Return (content_kind, shortcode) where content_kind is "reel" or
    "post", or raise ReelLoadError. Superset of validate_reel_url, used
    for compatibility with the legacy Instagram-specific entry point.
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
    best = max(
        formats,
        key=lambda item: (
            item.get("height") or 0,
            item.get("filesize") or item.get("filesize_approx") or 0,
        ),
    )
    return best.get("url")


def _video_formats(info: dict) -> list:
    formats = info.get("formats") or []
    explicit = [
        item for item in formats
        if item.get("vcodec") not in (None, "none")
    ]
    if explicit:
        return explicit

    video_extensions = {"mp4", "mov", "m4v", "webm"}
    if info.get("is_video") or info.get("ext") in video_extensions:
        return [
            item for item in formats
            if item.get("ext") in video_extensions
            and item.get("acodec") in (None, "none")
        ]
    return []


def _download_binary(ydl, url: str, dest_path: str) -> bool:
    """Fetch url through yt-dlp's network stack
    and write it to dest_path. Used for both images and direct video CDN
    URLs — Instagram media URLs are plain HTTPS links, not manifests.
    """
    try:
        resp = ydl.urlopen(url)
        with resp, open(dest_path, "wb") as output:
            shutil.copyfileobj(resp, output, length=1024 * 1024)
    except Exception:
        return False
    return os.path.exists(dest_path) and os.path.getsize(dest_path) > 0


def _resolve_carousel_item(ydl, entry: dict, index: int, work_dir: str) -> CarouselItem:
    formats = _video_formats(entry)
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

    # Leave unavailable entries unresolved so the public-embed fallback can
    # attempt to fill the complete carousel without fabricating media.
    return CarouselItem(index=index, media_type="unresolved", path=None)


def _merge_metadata(primary: dict, fallback: dict, source: str) -> dict:
    merged = dict(primary or {})
    for key, value in (fallback or {}).items():
        if merged.get(key) in (None, "", [], {}):
            merged[key] = value
    merged["acquisition_source"] = source
    return merged


def _usable_post_media_count(post: PostMedia) -> int:
    if post.video_path or post.image_path:
        return 1
    return sum(
        item.media_type != "unresolved"
        for item in (post.carousel_items or [])
    )


def _load_post_from_public_embed(
    content_url: str,
    content_id: str,
    work_dir: str,
) -> PostMedia:
    media = _read_public_embed_content("post", content_id)
    referer = _public_content_embed_url("post", content_id)
    metadata = _embed_metadata(media)
    metadata["acquisition_source"] = "instagram_public_embed"
    typename = media.get("__typename")

    sidecar = media.get("edge_sidecar_to_children")
    if isinstance(sidecar, dict):
        children = sidecar.get("edges") or []
    else:
        children = []
    if typename == "GraphSidecar" or children:
        if not children:
            raise ReelLoadError(
                "Instagram public embed exposed no carousel items"
            )
        items = []
        reasons = []
        for index, edge in enumerate(children):
            node = edge.get("node") if isinstance(edge, dict) else None
            if not isinstance(node, dict):
                items.append(CarouselItem(index=index, media_type="unresolved"))
                reasons.append(f"carousel item {index} metadata was unavailable")
                continue

            if node.get("is_video") and node.get("video_url"):
                path = os.path.join(work_dir, f"carousel_{index}.mp4")
                try:
                    _download_public_asset(
                        node["video_url"], path, referer, expected_type="video",
                    )
                    items.append(
                        CarouselItem(index=index, media_type="video", path=path)
                    )
                    continue
                except ReelLoadError:
                    reasons.append(
                        f"carousel video item {index} could not be downloaded"
                    )

            display_url = node.get("display_url")
            if display_url:
                path = os.path.join(work_dir, f"carousel_{index}.jpg")
                try:
                    _download_public_asset(
                        display_url, path, referer, expected_type="image",
                    )
                    items.append(
                        CarouselItem(index=index, media_type="image", path=path)
                    )
                    if node.get("is_video"):
                        reasons.append(
                            f"carousel video item {index} used its cover image"
                        )
                    continue
                except ReelLoadError:
                    pass

            items.append(CarouselItem(index=index, media_type="unresolved"))

        return PostMedia(
            content_url=content_url,
            content_id=content_id,
            metadata=metadata,
            carousel_items=items,
            incomplete_reasons=reasons,
        )

    if media.get("is_video"):
        video_url = media.get("video_url")
        if video_url:
            video_path = os.path.join(work_dir, f"{content_id}.mp4")
            _download_public_asset(
                video_url, video_path, referer, expected_type="video",
            )
            return PostMedia(
                content_url=content_url,
                content_id=content_id,
                metadata=metadata,
                video_path=video_path,
            )

        display_url = media.get("display_url")
        if display_url:
            image_path = os.path.join(work_dir, f"{content_id}.jpg")
            _download_public_asset(
                display_url, image_path, referer, expected_type="image",
            )
            return PostMedia(
                content_url=content_url,
                content_id=content_id,
                metadata=metadata,
                image_path=image_path,
                incomplete_reasons=[
                    "post video was unavailable; analysis used its cover image"
                ],
            )

    display_url = media.get("display_url")
    if display_url:
        image_path = os.path.join(work_dir, f"{content_id}.jpg")
        _download_public_asset(
            display_url, image_path, referer, expected_type="image",
        )
        return PostMedia(
            content_url=content_url,
            content_id=content_id,
            metadata=metadata,
            image_path=image_path,
        )

    raise ReelLoadError("Instagram public embed exposed no usable post media")


def _load_post_with_ytdlp(
    content_url: str,
    content_id: str,
    work_dir: str,
) -> PostMedia:
    options = _post_ydl_options(work_dir)

    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(content_url, download=False)

        if info is None:
            raise ReelLoadError("yt-dlp returned no metadata for this post")
        info = dict(info)
        info["acquisition_source"] = "yt-dlp"

        entries = info.get("entries")
        if entries is not None:
            carousel_items = [
                _resolve_carousel_item(ydl, entry, index, work_dir)
                for index, entry in enumerate(list(entries))
            ]
            return PostMedia(
                content_url=content_url,
                content_id=content_id,
                metadata=info,
                carousel_items=carousel_items,
            )

        formats = _video_formats(info)
        if formats:
            format_url = _pick_best_format_url(formats)
            if not format_url:
                raise ReelLoadError("This post's video has no downloadable format")
            ext = info.get("ext") or "mp4"
            video_path = os.path.join(work_dir, f"{content_id}.{ext}")
            if not _download_binary(ydl, format_url, video_path):
                raise ReelLoadError("Failed to download this post's video")
            return PostMedia(
                content_url=content_url,
                content_id=content_id,
                metadata=info,
                video_path=video_path,
            )

        thumb_url = _best_thumbnail_url(info)
        if not thumb_url:
            raise ReelLoadError("This post has no video and no usable image was found")
        image_path = os.path.join(work_dir, f"{content_id}.jpg")
        if not _download_binary(ydl, thumb_url, image_path):
            raise ReelLoadError("Failed to download this post's image")
        return PostMedia(
            content_url=content_url,
            content_id=content_id,
            metadata=info,
            image_path=image_path,
        )


def load_post(content_url: str, content_id: str, work_dir: str) -> PostMedia:
    """Fetch a /p/ post's metadata and download whatever local media is
    available: a single video, a single image, or (for a carousel) each
    resolvable item. Unresolvable carousel items are left as "unresolved"
    rather than failing the whole post.
    """
    ytdlp_post = None
    ytdlp_error = None
    try:
        ytdlp_post = _load_post_with_ytdlp(content_url, content_id, work_dir)
        if not ytdlp_post.carousel_items or all(
            item.media_type != "unresolved"
            for item in ytdlp_post.carousel_items
        ):
            return ytdlp_post
    except Exception as exc:
        ytdlp_error = exc

    try:
        embed_post = _load_post_from_public_embed(
            content_url, content_id, work_dir,
        )
        if ytdlp_post is not None:
            if _usable_post_media_count(ytdlp_post) >= _usable_post_media_count(
                embed_post
            ):
                return ytdlp_post
            embed_post.metadata = _merge_metadata(
                ytdlp_post.metadata,
                embed_post.metadata,
                "yt-dlp+instagram_public_embed",
            )
        return embed_post
    except ReelLoadError as embed_error:
        if ytdlp_post is not None:
            return ytdlp_post
        if ytdlp_error is not None:
            raise ReelLoadError(
                "yt-dlp post acquisition failed and Instagram's public "
                f"embed fallback also failed: {embed_error}"
            ) from embed_error
        raise
