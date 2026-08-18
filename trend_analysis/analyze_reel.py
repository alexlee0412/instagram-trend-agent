"""Public entry point for the trend-identification engine.

analyze_reel(reel_url) is the reusable interface — the CLI

    python -m trend_analysis.analyze_reel "<REEL_URL>"

is a thin wrapper around it. Works from a bare Reel URL only; independent
of the Playwright collector and of where the URL came from (Playwright,
Apify, manual input, a spreadsheet, n8n).

stdout carries exactly one JSON object (the result) and nothing else, so
tools like n8n can parse it directly. All progress/error logging goes to
stderr.
"""

import argparse
import json
import sys
import tempfile

from .config import FRAME_COUNT
from .frame_extractor import FrameExtractionError, extract_frames
from .reel_loader import ReelLoadError, load_reel
from .schemas import TrendAnalysisResult
from .trend_identifier import ModelNotConfiguredError, ProviderError, identify_trend


def _log(message: str):
    print(message, file=sys.stderr)


def analyze_reel(reel_url: str) -> dict:
    """Run the full pipeline for one Reel URL and return a
    TrendAnalysisResult as a plain dict. Never raises — every failure mode
    (bad URL, fetch failure, missing ffmpeg, no model configured, anything
    unexpected) is captured into analysis_status="error" with a concrete
    analysis_error message instead of a fabricated result.
    """
    reel_id = None

    try:
        with tempfile.TemporaryDirectory(prefix="trend_analysis_") as work_dir:
            _log("[INFO] Fetching Reel metadata/video via yt-dlp...")
            reel = load_reel(reel_url, work_dir, download_video=True)
            reel_id = reel.reel_id

            if not reel.video_path:
                raise ReelLoadError("yt-dlp did not return a downloadable video for this Reel")

            _log("[INFO] Extracting representative frames...")
            frames = extract_frames(reel.video_path, work_dir, frame_count=FRAME_COUNT)
            _log(f"[INFO] Extracted {len(frames)} frame(s)")

            _log("[INFO] Running trend identification...")
            result = identify_trend(frames, reel.metadata, reel_url)
            result.reel_id = reel.reel_id

            # A validated result based on fewer frames than requested is
            # still a real result, just built on incomplete visual input —
            # flag that transparently rather than reporting plain "success".
            if result.analysis_status == "success" and len(frames) < FRAME_COUNT:
                result.analysis_status = "partial"

            if result.analysis_status == "success":
                _log("[INFO] Trend identified successfully")
            else:
                _log(f"[INFO] Trend identification completed with status: {result.analysis_status}")

            # work_dir (video + frames) is deleted on exit from this block,
            # whether we return normally or an exception propagates.
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
        reel_url=reel_url,
        reel_id=reel_id,
        analysis_status="error",
        analysis_error=analysis_error,
    ).to_dict()


def _parse_args():
    parser = argparse.ArgumentParser(
        prog="python -m trend_analysis.analyze_reel",
        description="Identify the cultural/social trend a single Instagram Reel belongs to.",
    )
    parser.add_argument(
        "reel_url",
        help="Instagram Reel URL, e.g. https://www.instagram.com/reels/<ID>/",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    result = analyze_reel(args.reel_url)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("analysis_status") != "error" else 1)


if __name__ == "__main__":
    main()
