"""Root URL router and shared one-item trend-analysis pipeline.

Canonical URL classification is local; recognized TikTok share links may be
resolved before classification. Every implemented route returns platform-
neutral acquired media before entering shared visual preparation and trend
identification.

stdout is reserved for one JSON result. Operational logging goes to stderr.
"""

import argparse
import json
import sys
import tempfile
from typing import Callable, Dict, Tuple

from .frame_extractor import FrameExtractionError
from .media import AcquiredMedia, MediaAcquisitionError, MediaItem
from .output import (
    base_result,
    empty_metadata,
    empty_trend,
    normalize_analysis_input,
    normalize_metadata,
    trend_payload,
)
from .platforms.tiktok_loader import load_tiktok_video
from .platforms.x_loader import load_x_post
from .reel_loader import ReelLoadError, load_post, load_reel
from .trend_identifier import ModelNotConfiguredError, ProviderError, identify_trend
from .url_classifier import (
    ClassifiedURL,
    URLClassificationError,
    classify_url_with_redirects,
)
from .visual_preparation import prepare_media_visuals


# Compatibility alias for Phase 1 callers that imported this name.
URLAnalysisError = URLClassificationError


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
    return load_tiktok_video(route.canonical_url, route.content_id, work_dir)


def _acquire_x(
    route: ClassifiedURL,
    work_dir: str,
) -> AcquiredMedia:
    _log("[INFO] Acquiring public X post media and metadata...")
    return load_x_post(route.canonical_url, route.content_id, work_dir)


Acquirer = Callable[[ClassifiedURL, str], AcquiredMedia]

PLATFORM_ACQUIRERS: Dict[Tuple[str, str], Acquirer] = {
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
        result = base_result(url)
        result["analysis_error"] = str(exc)
        _log(f"[ERROR] {exc}")
        return result

    result = base_result(url, route)
    _log(f"[INFO] Identified platform: {route.platform}")
    _log(f"[INFO] Identified content type: {route.content_type}")

    acquire = PLATFORM_ACQUIRERS.get((route.platform, route.content_type))
    if acquire is None:
        result["analysis_error"] = (
            "URL platform/content type could not be identified."
        )
        _log(f"[ERROR] {result['analysis_error']}")
        return result

    result["metadata"] = empty_metadata()
    result["trend"] = empty_trend()

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
            result["trend"] = trend_payload(trend_result)

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
