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
import sys
import tempfile

from .frame_extractor import FrameExtractionError
from .reel_loader import (
    ReelLoadError,
    load_post,
    load_reel,
    validate_content_url,
)
from .schemas import TrendAnalysisResult
from .trend_identifier import ModelNotConfiguredError, ProviderError, identify_trend
from .visual_preparation import prepare_post_visuals, prepare_video_visuals


def _log(message: str):
    print(message, file=sys.stderr)


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
            partial_reasons = []

            if content_kind == "reel":
                _log("[INFO] Fetching Reel metadata/video via yt-dlp...")
                reel = load_reel(url, work_dir, download_video=True)
                content_id = reel.reel_id

                if not reel.video_path:
                    raise ReelLoadError("yt-dlp did not return a downloadable video for this Reel")

                _log("[INFO] Extracting representative frames...")
                prepared = prepare_video_visuals(reel.video_path, work_dir)
                assets = prepared.assets
                partial_reasons.extend(prepared.incomplete_reasons)
                metadata = reel.metadata

            else:
                _log("[INFO] Fetching post metadata/media via yt-dlp...")
                post = load_post(url, content_id, work_dir)
                metadata = post.metadata

                if post.carousel_items:
                    total = len(post.carousel_items)
                    resolved = sum(1 for i in post.carousel_items if i.media_type != "unresolved")
                    _log(f"[INFO] Carousel: {resolved}/{total} slide(s) resolved to usable media")

                _log("[INFO] Preparing representative visual asset(s)...")
                prepared = prepare_post_visuals(post, work_dir)
                assets = prepared.assets
                partial_reasons.extend(prepared.incomplete_reasons)
                partial_reasons.extend(post.incomplete_reasons or [])

            if not assets:
                raise ReelLoadError("No usable visual assets could be extracted from this content")

            _log(f"[INFO] Prepared {len(assets)} visual asset(s)")

            _log("[INFO] Running trend identification...")
            image_paths = [asset.path for asset in assets]
            result = identify_trend(
                image_paths,
                metadata,
                url,
                prepared.visual_context,
            )
            result.reel_id = content_id

            if result.analysis_status == "success" and partial_reasons:
                result.analysis_status = "partial"
                _log(
                    "[INFO] Marking result partial: "
                    + "; ".join(partial_reasons)
                )

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
