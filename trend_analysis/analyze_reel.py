"""Public entry point for the trend-identification engine.

analyze_reel(reel_url) is the reusable interface — the CLI

    python -m trend_analysis.analyze_reel "<URL>"

is a thin wrapper around it. Works from a bare Instagram URL only;
independent of the Playwright collector and of where the URL came from
(Playwright, Apify, manual input, a spreadsheet, n8n).

Accepts both Reel URLs (.../reel(s)/<ID>/) and post URLs (.../p/<ID>/ —
single image, carousel, or video). Both feed the same identify_trend(...)
model boundary via a shared "ordered visual assets" abstraction: a Reel
becomes sampled video frames, a single-image post becomes one image, and a
carousel becomes its (capped, deterministically-sampled) resolvable
slides — the model never knows which case it's looking at.

Function/CLI names and the reel_url/reel_id output fields are kept as-is
for post URLs too (not renamed to content_url/content_id) to avoid an
MVP-stage schema/CLI break; see the project README for the tradeoff.

Known limitation (confirmed empirically against a real mixed-media
carousel): yt-dlp's current Instagram extractor does not expose usable
image data for some non-video carousel slides — those are skipped rather
than crashing the whole post, and analysis_status is downgraded to
"partial" when that happens. See the final report in conversation history
for the investigation.

stdout carries exactly one JSON object (the result) and nothing else, so
tools like n8n can parse it directly. All progress/error logging goes to
stderr.
"""

import argparse
import json
import os
import sys
import tempfile
from typing import List

from .config import FRAME_COUNT, MAX_VISUAL_ASSETS
from .frame_extractor import FrameExtractionError, extract_frames
from .reel_loader import (
    CarouselItem,
    PostMedia,
    ReelLoadError,
    load_post,
    load_reel,
    validate_content_url,
)
from .schemas import TrendAnalysisResult, VisualAsset
from .trend_identifier import ModelNotConfiguredError, ProviderError, identify_trend


def _log(message: str):
    print(message, file=sys.stderr)


def _select_carousel_indices(total: int, max_assets: int) -> List[int]:
    """Deterministically pick up to max_assets positions from range(total),
    always including the first and last, evenly spaced between them —
    never random, so the same post always selects the same slides.
    """
    if total <= max_assets:
        return list(range(total))
    if max_assets <= 1:
        return [0]

    step = (total - 1) / (max_assets - 1)
    indices = sorted({round(i * step) for i in range(max_assets)})
    return indices


def _build_reel_visual_assets(video_path: str, work_dir: str) -> List[VisualAsset]:
    frame_paths = extract_frames(video_path, work_dir, frame_count=FRAME_COUNT)
    return [
        VisualAsset(path=path, media_type="video_frame", source_index=0, timestamp=None)
        for path in frame_paths
    ]


def _build_post_visual_assets(post: PostMedia, work_dir: str) -> List[VisualAsset]:
    if post.video_path:
        return _build_reel_visual_assets(post.video_path, work_dir)

    if post.image_path:
        return [VisualAsset(path=post.image_path, media_type="image", source_index=0, timestamp=None)]

    if post.carousel_items:
        return _build_carousel_visual_assets(post.carousel_items, work_dir)

    return []


def _build_carousel_visual_assets(items: List[CarouselItem], work_dir: str) -> List[VisualAsset]:
    resolved = [item for item in items if item.media_type != "unresolved"]
    if not resolved:
        return []

    selected_positions = _select_carousel_indices(len(resolved), MAX_VISUAL_ASSETS)
    selected = [resolved[i] for i in selected_positions]

    assets = []
    for item in selected:
        if item.media_type == "image":
            assets.append(VisualAsset(
                path=item.path, media_type="image", source_index=item.index, timestamp=None,
            ))
        elif item.media_type == "video":
            # One representative midpoint frame per video slide — never
            # expand a single carousel item into multiple frames, which
            # would overweight it relative to image slides.
            frame_dir = os.path.join(work_dir, f"carousel_{item.index}_frames")
            os.makedirs(frame_dir, exist_ok=True)
            try:
                frame_paths = extract_frames(item.path, frame_dir, frame_count=1)
            except FrameExtractionError:
                continue
            if frame_paths:
                assets.append(VisualAsset(
                    path=frame_paths[0], media_type="video_frame",
                    source_index=item.index, timestamp=None,
                ))

    return assets


def analyze_reel(url: str) -> dict:
    """Run the full pipeline for one Instagram Reel or post URL and return
    a TrendAnalysisResult as a plain dict. Never raises — every failure
    mode (bad URL, fetch failure, missing ffmpeg, no model configured,
    anything unexpected) is captured into analysis_status="error" with a
    concrete analysis_error message instead of a fabricated result.
    """
    content_id = None

    try:
        content_kind, content_id = validate_content_url(url)

        with tempfile.TemporaryDirectory(prefix="trend_analysis_") as work_dir:
            partial_reason = None

            if content_kind == "reel":
                _log("[INFO] Fetching Reel metadata/video via yt-dlp...")
                reel = load_reel(url, work_dir, download_video=True)
                content_id = reel.reel_id

                if not reel.video_path:
                    raise ReelLoadError("yt-dlp did not return a downloadable video for this Reel")

                _log("[INFO] Extracting representative frames...")
                assets = _build_reel_visual_assets(reel.video_path, work_dir)
                metadata = reel.metadata

                if len(assets) < FRAME_COUNT:
                    partial_reason = "fewer video frames extracted than requested"

            else:
                _log("[INFO] Fetching post metadata/media via yt-dlp...")
                post = load_post(url, content_id, work_dir)
                metadata = post.metadata

                if post.carousel_items:
                    total = len(post.carousel_items)
                    resolved = sum(1 for i in post.carousel_items if i.media_type != "unresolved")
                    _log(f"[INFO] Carousel: {resolved}/{total} slide(s) resolved to usable media")
                    if resolved < total:
                        partial_reason = f"{total - resolved} of {total} carousel slide(s) could not be fetched"

                _log("[INFO] Preparing representative visual asset(s)...")
                assets = _build_post_visual_assets(post, work_dir)

            if not assets:
                raise ReelLoadError("No usable visual assets could be extracted from this content")

            _log(f"[INFO] Prepared {len(assets)} visual asset(s)")

            _log("[INFO] Running trend identification...")
            image_paths = [asset.path for asset in assets]
            result = identify_trend(image_paths, metadata, url)
            result.reel_id = content_id

            if result.analysis_status == "success" and partial_reason:
                result.analysis_status = "partial"
                _log(f"[INFO] Marking result partial: {partial_reason}")

            if result.analysis_status == "success":
                _log("[INFO] Trend identified successfully")
            else:
                _log(f"[INFO] Trend identification completed with status: {result.analysis_status}")

            # work_dir (any downloaded media + prepared assets) is deleted
            # on exit from this block, whether we return normally or an
            # exception propagates.
            return result.to_dict()

    except ModelNotConfiguredError as exc:
        _log(f"[ERROR] {exc}")
        analysis_error = str(exc)
    except ProviderError as exc:
        _log(f"[ERROR] {exc}")
        analysis_error = str(exc)
    except (ReelLoadError, FrameExtractionError) as exc:
        _log(f"[ERROR] {exc}")
        analysis_error = str(exc)
    except Exception as exc:
        _log(f"[ERROR] Unexpected error during trend analysis: {exc}")
        analysis_error = f"Unexpected error: {exc}"

    return TrendAnalysisResult(
        reel_url=url,
        reel_id=content_id,
        analysis_status="error",
        analysis_error=analysis_error,
    ).to_dict()


def _parse_args():
    parser = argparse.ArgumentParser(
        prog="python -m trend_analysis.analyze_reel",
        description="Identify the cultural/social trend a single Instagram Reel or post belongs to.",
    )
    parser.add_argument(
        "reel_url",
        help="Instagram Reel or post URL, e.g. https://www.instagram.com/reels/<ID>/ or "
             "https://www.instagram.com/p/<ID>/",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    result = analyze_reel(args.reel_url)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("analysis_status") != "error" else 1)


if __name__ == "__main__":
    main()
