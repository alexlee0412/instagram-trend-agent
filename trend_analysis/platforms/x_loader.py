"""Anonymous public X/Twitter post acquisition.

X's public syndication response supplies post text and ordered media details
without a login.  yt-dlp remains the primary downloader for ordinary video
posts; the syndication media URLs cover images, mixed posts, animated GIFs,
and a direct-video fallback when yt-dlp cannot download a public video.
"""

import json
import os
import shutil
from datetime import datetime
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from ..config import YDL_SOCKET_TIMEOUT_SECONDS
from ..media import AcquiredMedia, MediaAcquisitionError, MediaItem
from .ytdlp_utils import download_video, extract_info


PUBLIC_USER_AGENT = "Mozilla/5.0"
MAX_SYNDICATION_BYTES = 4 * 1024 * 1024


def _syndication_url(content_id: str) -> str:
    return (
        "https://cdn.syndication.twimg.com/tweet-result?"
        + urlencode({"id": content_id, "lang": "en", "token": "0"})
    )


def _read_public_post(content_id: str) -> dict:
    request = Request(
        _syndication_url(content_id),
        headers={
            "User-Agent": PUBLIC_USER_AGENT,
            "Accept": "application/json",
            "Referer": "https://platform.twitter.com/",
        },
    )
    try:
        with urlopen(request, timeout=YDL_SOCKET_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_SYNDICATION_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise MediaAcquisitionError(
            f"X public post metadata request failed: {exc}"
        ) from exc

    if len(body) > MAX_SYNDICATION_BYTES:
        raise MediaAcquisitionError("X public post metadata was unexpectedly large")
    try:
        post = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MediaAcquisitionError("X public post metadata was malformed") from exc
    if not isinstance(post, dict) or not post:
        raise MediaAcquisitionError(
            "X post is unavailable, private, deleted, or login-restricted"
        )
    if post.get("__typename") != "Tweet":
        raise MediaAcquisitionError(
            "X post is unavailable, private, deleted, or restricted"
        )
    if str(post.get("id_str") or "") != content_id:
        raise MediaAcquisitionError("X public endpoint returned a different post")
    return post


def _timestamp(value) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _syndication_metadata(post: dict) -> dict:
    user = post.get("user") if isinstance(post.get("user"), dict) else {}
    entities = post.get("entities")
    if not isinstance(entities, dict):
        entities = {}
    hashtags = entities.get("hashtags") or []
    tags = [
        item.get("text")
        for item in hashtags
        if isinstance(item, dict) and item.get("text")
    ]

    duration = None
    for media in post.get("mediaDetails") or []:
        millis = (media.get("video_info") or {}).get("duration_millis")
        if isinstance(millis, (int, float)) and not isinstance(millis, bool):
            duration = millis / 1000
            break

    video = post.get("video") if isinstance(post.get("video"), dict) else {}
    view_count = video.get("viewCount")
    if not isinstance(view_count, (int, float)) or isinstance(view_count, bool):
        view_count = None

    return {
        "id": post.get("id_str"),
        "display_id": post.get("id_str"),
        "description": post.get("text"),
        "channel": user.get("screen_name"),
        "uploader": user.get("name") or user.get("screen_name"),
        "uploader_id": user.get("screen_name"),
        "timestamp": _timestamp(post.get("created_at")),
        "duration": duration,
        "view_count": view_count,
        "like_count": post.get("favorite_count"),
        "comment_count": post.get("conversation_count"),
        "tags": tags,
    }


def _merge_metadata(syndication: dict, ytdlp: Optional[dict], source: str) -> dict:
    merged = dict(syndication)
    for key, value in (ytdlp or {}).items():
        if value not in (None, "", [], {}):
            merged[key] = value
    # Preserve the canonical post ID even when the extractor's media ID is
    # different from the enclosing X status ID.
    merged["display_id"] = syndication.get("display_id")
    merged["acquisition_source"] = source
    return merged


def _with_original_image_size(url: str) -> str:
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["name"] = "orig"
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), "")
    )


def _download_public_asset(
    url: str,
    destination: str,
    expected_type: str,
    referer: str,
) -> None:
    request = Request(
        url,
        headers={"User-Agent": PUBLIC_USER_AGENT, "Referer": referer},
    )
    try:
        with urlopen(request, timeout=YDL_SOCKET_TIMEOUT_SECONDS) as response:
            content_type = response.headers.get_content_type()
            if content_type and not content_type.startswith(expected_type + "/"):
                raise MediaAcquisitionError(
                    "X public media returned unexpected content type: "
                    f"{content_type}"
                )
            with open(destination, "wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
    except MediaAcquisitionError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise MediaAcquisitionError(f"X public media download failed: {exc}") from exc
    if not os.path.isfile(destination) or os.path.getsize(destination) == 0:
        raise MediaAcquisitionError("X public media download was empty")


def _best_mp4_url(media: dict) -> Optional[str]:
    variants = (media.get("video_info") or {}).get("variants") or []
    mp4 = [
        item for item in variants
        if isinstance(item, dict)
        and item.get("content_type") == "video/mp4"
        and item.get("url")
    ]
    if not mp4:
        return None
    return max(mp4, key=lambda item: item.get("bitrate") or 0)["url"]


def _is_x_video_info(info: Optional[dict], content_id: str) -> bool:
    if not isinstance(info, dict):
        return False
    extractor = str(info.get("extractor_key") or info.get("extractor") or "")
    if "twitter" not in extractor.lower():
        return False
    display_id = str(info.get("display_id") or info.get("id") or "")
    if display_id != content_id:
        return False
    return any(
        item.get("vcodec") not in (None, "none")
        for item in (info.get("formats") or [])
        if isinstance(item, dict)
    )


def _download_syndication_items(
    details: list,
    content_url: str,
    work_dir: str,
) -> tuple[list, list]:
    items = []
    reasons = []
    for index, media in enumerate(details):
        media_type = media.get("type")
        try:
            if media_type == "photo" and media.get("media_url_https"):
                path = os.path.join(work_dir, f"x_media_{index}.jpg")
                _download_public_asset(
                    _with_original_image_size(media["media_url_https"]),
                    path,
                    "image",
                    content_url,
                )
                items.append(MediaItem(index=index, media_type="image", path=path))
                continue

            if media_type in ("video", "animated_gif"):
                video_url = _best_mp4_url(media)
                if video_url:
                    path = os.path.join(work_dir, f"x_media_{index}.mp4")
                    _download_public_asset(
                        video_url, path, "video", content_url,
                    )
                    items.append(
                        MediaItem(index=index, media_type="video", path=path)
                    )
                    continue
        except MediaAcquisitionError as exc:
            reasons.append(f"X media item {index} could not be acquired: {exc}")

        items.append(MediaItem(index=index, media_type="unresolved"))
        if not any(reason.startswith(f"X media item {index} ") for reason in reasons):
            reasons.append(f"X media item {index} had no usable public media URL")
    return items, reasons


def load_x_post(
    content_url: str,
    content_id: str,
    work_dir: str,
) -> AcquiredMedia:
    post = _read_public_post(content_id)
    syndication_metadata = _syndication_metadata(post)
    details = [
        item for item in (post.get("mediaDetails") or [])
        if isinstance(item, dict)
        and item.get("type") in ("photo", "video", "animated_gif")
    ]

    # yt-dlp is probed even when syndication omits promoted/ad video media.
    # Reject non-Twitter extractor results so an external link in an image or
    # text post cannot be mistaken for the post's own visual media.
    try:
        video_info = extract_info(content_url, work_dir)
    except MediaAcquisitionError:
        video_info = None
    has_ytdlp_video = _is_x_video_info(video_info, content_id)

    video_details = [
        item for item in details
        if item.get("type") in ("video", "animated_gif")
    ]
    photo_details = [item for item in details if item.get("type") == "photo"]

    if has_ytdlp_video and len(video_details) <= 1 and not photo_details:
        try:
            downloaded_info, video_path = download_video(content_url, work_dir)
            return AcquiredMedia(
                metadata=_merge_metadata(
                    syndication_metadata,
                    downloaded_info,
                    "yt-dlp+x_public_syndication",
                ),
                video_path=video_path,
            )
        except MediaAcquisitionError:
            # Fall through to the public media variant when it exists.
            pass

    if details:
        items, reasons = _download_syndication_items(
            details, content_url, work_dir,
        )
        usable = [item for item in items if item.media_type != "unresolved"]
        if not usable:
            raise MediaAcquisitionError(
                "X post media was identified but could not be downloaded"
            )
        metadata = _merge_metadata(
            syndication_metadata,
            video_info if has_ytdlp_video else None,
            "x_public_syndication",
        )
        if len(items) == 1 and usable[0].media_type == "image":
            return AcquiredMedia(
                metadata=metadata,
                image_path=usable[0].path,
                incomplete_reasons=reasons,
            )
        if len(items) == 1 and usable[0].media_type == "video":
            return AcquiredMedia(
                metadata=metadata,
                video_path=usable[0].path,
                incomplete_reasons=reasons,
            )
        return AcquiredMedia(
            metadata=metadata,
            items=items,
            incomplete_reasons=reasons,
        )

    if has_ytdlp_video:
        downloaded_info, video_path = download_video(content_url, work_dir)
        return AcquiredMedia(
            metadata=_merge_metadata(
                syndication_metadata,
                downloaded_info,
                "yt-dlp+x_public_syndication",
            ),
            video_path=video_path,
        )

    return AcquiredMedia(
        metadata=_merge_metadata(
            syndication_metadata, None, "x_public_syndication",
        ),
        no_visual_reason="No analyzable visual media found in this X post",
    )
