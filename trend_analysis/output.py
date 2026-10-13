"""Build the platform-neutral public JSON response.

Acquisition adapters may return large, platform-specific metadata objects.
This module keeps only the stable fields exposed by the CLI and centralizes
the empty/error shapes used by every platform.
"""

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .schemas import TrendAnalysisResult
    from .url_classifier import ClassifiedURL
    from .visual_preparation import PreparedVisuals


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


def _safe_number(value) -> Optional[float]:
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
                timestamp,
                tz=timezone.utc,
            ).date().isoformat()
        except (OSError, OverflowError, ValueError):
            pass
    return None


def _hashtags(metadata: dict) -> list[str]:
    candidates = []
    tags = metadata.get("tags")
    if isinstance(tags, list):
        candidates.extend(
            tag.lstrip("#")
            for tag in tags
            if isinstance(tag, str) and tag
        )

    description = metadata.get("description")
    if isinstance(description, str):
        candidates.extend(re.findall(r"#([\w]+)", description, flags=re.UNICODE))

    result = []
    seen = set()
    for candidate in candidates:
        key = candidate.casefold()
        if candidate and key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def _audio(metadata: dict) -> Optional[dict]:
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
    """Return only stable metadata fields from a platform extractor."""
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


def normalize_analysis_input(prepared: "PreparedVisuals") -> dict:
    """Describe the visual material actually supplied to the model."""
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


def empty_metadata() -> dict:
    """Return the stable metadata shape when acquisition did not complete."""
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


def empty_trend() -> dict:
    """Return the stable trend shape before model analysis completes."""
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


def base_result(url: str, route: Optional["ClassifiedURL"] = None) -> dict:
    """Build the top-level response shared by success and failure paths."""
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


def trend_payload(result: "TrendAnalysisResult") -> dict:
    """Expose only fields belonging to the public trend contract."""
    return {field: getattr(result, field) for field in TREND_FIELDS}
