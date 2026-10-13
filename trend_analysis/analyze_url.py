"""Root URL router and shared one-item trend-analysis pipeline.

URL classification is pure and network-free. Only implemented routes are
allowed to enter acquisition, and all acquired media is normalized to ordered
image assets before reaching the existing multimodal trend identifier.

stdout is reserved for one JSON result. Operational logging goes to stderr.
"""

import argparse
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from typing import Optional, Tuple

from .frame_extractor import FrameExtractionError
from .media import AcquiredMedia, MediaAcquisitionError, MediaItem
from .platforms.tiktok_loader import load_tiktok_video
from .platforms.x_loader import load_x_post
from .reel_loader import ReelLoadError, load_post, load_reel
from .trend_identifier import ModelNotConfiguredError, ProviderError, identify_trend
from .url_classifier import (
    ClassifiedURL,
    URLClassificationError,
    classify_url_with_redirects,
)
from .visual_preparation import (
    PreparedVisuals,
    prepare_media_visuals,
)


# Compatibility alias for Phase 1 callers that imported this name.
URLAnalysisError = URLClassificationError

TREND_FIELDS = (
    "trend_name",
    "trend_category",
    "confidence",
    "content_summary",
    "content_format",
    "event_context",
    "subjects",
    "activities",
    "objects",
    "visual_aesthetic",
    "cultural_signals",
    "evidence",
)


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def detect_url(url: str) -> Tuple[str, str, str, str]:
    """Compatibility tuple wrapper around the structured classifier."""
    route = classify_url_with_redirects(url)
    return (
        route.platform,
        route.content_type,
        route.content_id,
        route.canonical_url,
    )


def _safe_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _upload_date(metadata: dict) -> Optional[str]:
    value = metadata.get("upload_date")
    if isinstance(value, str) and value:
        if re.fullmatch(r"\d{8}", value):
            return f"{value[:4]}-{value[4:6]}-{value[6:]}"
        return value

    timestamp = metadata.get("timestamp")
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        try:
            return datetime.fromtimestamp(
                timestamp, tz=timezone.utc,
            ).date().isoformat()
        except (OSError, OverflowError, ValueError):
            pass
    return None


def _hashtags(metadata: dict) -> list:
    result = []
    seen = set()
    values = metadata.get("tags")
    if isinstance(values, list):
        candidates = [str(value).lstrip("#") for value in values]
    else:
        candidates = []
    description = metadata.get("description")
    candidates.extend(
        re.findall(r"#([\w]+)", description or "", flags=re.UNICODE)
    )

    for candidate in candidates:
        key = candidate.casefold()
        if candidate and key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def _audio(metadata: dict):
    audio = metadata.get("audio")
    if isinstance(audio, dict):
        normalized = {
            key: audio.get(key)
            for key in ("artist_name", "song_name", "uses_original_audio")
            if audio.get(key) is not None
        }
        return normalized or None

    track = metadata.get("track")
    artist = metadata.get("artist") or metadata.get("creator")
    if track or artist:
        return {"artist_name": artist, "song_name": track}
    return None


def normalize_metadata(metadata: dict) -> dict:
    """Keep only stable, useful source fields for the public result."""
    return {
        "uploader": metadata.get("channel") or metadata.get("uploader"),
        "description": metadata.get("description"),
        "upload_date": _upload_date(metadata),
        "duration": _safe_number(metadata.get("duration")),
        "view_count": _safe_number(metadata.get("view_count")),
        "like_count": _safe_number(metadata.get("like_count")),
        "comment_count": _safe_number(metadata.get("comment_count")),
        "hashtags": _hashtags(metadata),
        "audio": _audio(metadata),
    }


def normalize_analysis_input(prepared: PreparedVisuals) -> dict:
    """Describe the visual material actually supplied to the analyzer.

    This is intentionally separate from platform ``content_type``: a Reel
    and a video Post are different platform objects, but both reach the model
    as chronological video frames.
    """
    context = prepared.visual_context
    return {
        "visual": {
            "source_type": context.source_type,
            "ordered": context.ordered,
            "asset_count": len(prepared.assets),
            "media_composition": context.media_composition,
            "item_types": list(context.item_types),
        },
    }


def _empty_metadata() -> dict:
    return {
        "uploader": None,
        "description": None,
        "upload_date": None,
        "duration": None,
        "view_count": None,
        "like_count": None,
        "comment_count": None,
        "hashtags": [],
        "audio": None,
    }


def _empty_trend() -> dict:
    return {
        "trend_name": None,
        "trend_category": None,
        "confidence": None,
        "content_summary": None,
        "content_format": [],
        "event_context": [],
        "subjects": [],
        "activities": [],
        "objects": [],
        "visual_aesthetic": [],
        "cultural_signals": [],
        "evidence": [],
    }


def _base_result(url: str, route: Optional[ClassifiedURL] = None) -> dict:
    return {
        "platform": route.platform if route else None,
        "content_type": route.content_type if route else None,
        "content_url": route.canonical_url if route else url,
        "content_id": route.content_id if route else None,
        "analysis_input": None,
        "metadata": None,
        "trend": None,
        "analysis_status": "error",
        "analysis_error": None,
    }


def _acquire_instagram_reel(
    route: ClassifiedURL,
    work_dir: str,
) -> AcquiredMedia:
    _log("[INFO] Acquiring public Instagram Reel media and metadata...")
    reel = load_reel(route.canonical_url, work_dir, download_video=True)
    if not reel.video_path:
        raise ReelLoadError("Instagram acquisition returned no Reel video")
    return AcquiredMedia(metadata=reel.metadata, video_path=reel.video_path)


def _acquire_instagram_post(
    route: ClassifiedURL,
    work_dir: str,
) -> AcquiredMedia:
    _log("[INFO] Acquiring public Instagram Post media and metadata...")
    post = load_post(route.canonical_url, route.content_id, work_dir)
    if post.video_path:
        _log("[INFO] Post media type: video")
    elif post.image_path:
        _log("[INFO] Post media type: single image")
    elif post.carousel_items:
        video_count = sum(
            item.media_type == "video" for item in post.carousel_items
        )
        image_count = sum(
            item.media_type == "image" for item in post.carousel_items
        )
        unresolved_count = sum(
            item.media_type == "unresolved" for item in post.carousel_items
        )
        _log(
            "[INFO] Post media type: carousel "
            f"({image_count} image, {video_count} video, "
            f"{unresolved_count} unresolved)"
        )

    return AcquiredMedia(
        metadata=post.metadata,
        video_path=post.video_path,
        image_path=post.image_path,
        items=[
            MediaItem(
                index=item.index,
                media_type=item.media_type,
                path=item.path,
            )
            for item in (post.carousel_items or [])
        ],
        incomplete_reasons=list(post.incomplete_reasons or []),
    )


def _acquire_tiktok(
    route: ClassifiedURL,
    work_dir: str,
) -> AcquiredMedia:
    _log("[INFO] Acquiring public TikTok video and metadata...")
    return load_tiktok_video(
        route.canonical_url, route.content_id, work_dir,
    )


def _acquire_x(
    route: ClassifiedURL,
    work_dir: str,
) -> AcquiredMedia:
    _log("[INFO] Acquiring public X post media and metadata...")
    return load_x_post(route.canonical_url, route.content_id, work_dir)


PLATFORM_ACQUIRERS = {
    ("instagram", "reel"): _acquire_instagram_reel,
    ("instagram", "post"): _acquire_instagram_post,
    ("tiktok", "video"): _acquire_tiktok,
    ("x", "post"): _acquire_x,
}


def analyze_url(url: str) -> dict:
    """Classify and analyze one URL, always returning the JSON contract."""
    try:
        route = classify_url_with_redirects(url)
    except URLClassificationError as exc:
        result = _base_result(url)
        result["analysis_error"] = str(exc)
        _log(f"[ERROR] {exc}")
        return result

    result = _base_result(url, route)
    _log(f"[INFO] Identified platform: {route.platform}")
    _log(f"[INFO] Identified content type: {route.content_type}")

    acquire = PLATFORM_ACQUIRERS.get((route.platform, route.content_type))
    if acquire is None:
        result["analysis_error"] = (
            "URL platform/content type could not be identified."
        )
        _log(f"[ERROR] {result['analysis_error']}")
        return result

    result["metadata"] = _empty_metadata()
    result["trend"] = _empty_trend()

    try:
        with tempfile.TemporaryDirectory(prefix="trend_analysis_") as work_dir:
            acquired = acquire(route, work_dir)
            metadata = acquired.metadata
            result["metadata"] = normalize_metadata(metadata)

            _log(
                "[INFO] Acquisition source: "
                f"{metadata.get('acquisition_source', 'unknown')}"
            )
            if not acquired.has_visual_media:
                result["analysis_status"] = "unsupported"
                result["analysis_error"] = (
                    acquired.no_visual_reason
                    or "No analyzable visual media found in this content"
                )
                _log(f"[ERROR] {result['analysis_error']}")
                return result

            _log("[INFO] Preparing normalized visual asset(s)...")
            prepared = prepare_media_visuals(acquired, work_dir)
            prepared.incomplete_reasons.extend(acquired.incomplete_reasons)

            if not prepared.assets:
                raise ReelLoadError(
                    "No usable visual assets could be prepared from this content"
                )
            result["analysis_input"] = normalize_analysis_input(prepared)
            _log(f"[INFO] Prepared {len(prepared.assets)} visual asset(s)")
            _log(
                "[INFO] Visual context: "
                f"{prepared.visual_context.source_type}"
            )
            if prepared.visual_context.media_composition:
                _log(
                    "[INFO] Visual media composition: "
                    f"{prepared.visual_context.media_composition}"
                )

            _log("[INFO] Running OpenAI multimodal trend identification...")
            trend_result = identify_trend(
                [asset.path for asset in prepared.assets],
                metadata,
                route.canonical_url,
                prepared.visual_context,
            )
            result["trend"] = {
                field: getattr(trend_result, field)
                for field in TREND_FIELDS
            }

            trend_status = trend_result.analysis_status
            if trend_status == "success" and prepared.incomplete_reasons:
                trend_status = "partial"
            result["analysis_status"] = trend_status
            result["analysis_error"] = trend_result.analysis_error

            for reason in prepared.incomplete_reasons:
                _log(f"[INFO] Partial input: {reason}")
            if trend_status == "success":
                _log("[INFO] Trend identified successfully")
            else:
                _log(
                    "[INFO] Trend identification completed with status: "
                    f"{trend_status}"
                )

    except (ReelLoadError, MediaAcquisitionError, FrameExtractionError) as exc:
        result["analysis_error"] = str(exc)
        _log(f"[ERROR] {exc}")
    except (ModelNotConfiguredError, ProviderError) as exc:
        result["analysis_error"] = str(exc)
        _log(f"[ERROR] {exc}")
    except Exception as exc:
        result["analysis_error"] = f"Unexpected error: {exc}"
        _log(f"[ERROR] Unexpected error during URL analysis: {exc}")

    return result


def _parse_args():
    parser = argparse.ArgumentParser(
        prog="./analyze.sh",
        description="Analyze one public social content URL for its underlying trend.",
    )
    parser.add_argument(
        "url",
        help="Instagram, TikTok, or X/Twitter content URL",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    result = analyze_url(args.url)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    successful = result["analysis_status"] in ("success", "partial")
    raise SystemExit(0 if successful else 1)


if __name__ == "__main__":
    main()
