"""Extracts a small number of representative frames from a local video file.

Uses ffmpeg directly via subprocess — the smallest reliable option, with no
new Python video-processing dependency (no opencv/moviepy/etc.). Requires
ffmpeg/ffprobe to be installed and on PATH; fails clearly if not.
"""

import os
import shutil
import subprocess
from typing import List, Optional

# Cap the long edge so frames stay a reasonable size for multimodal
# inference (Reels are native ~1080x1920; we don't need full resolution
# for scene/trend understanding).
MAX_FRAME_WIDTH = 768


class FrameExtractionError(Exception):
    """Raised when ffmpeg is unavailable or frame extraction fails."""


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _probe_duration_seconds(video_path: str) -> Optional[float]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", video_path,
            ],
            capture_output=True, text=True, timeout=15, check=True,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def extract_frames(video_path: str, output_dir: str, frame_count: int = 5) -> List[str]:
    """Extract frame_count representative JPEG frames from video_path into
    output_dir, sampled at the midpoint of frame_count equal time bins
    (e.g. 5 frames -> 10%/30%/50%/70%/90% of duration), which avoids
    first/last-frame bias without needing scene detection. Raises
    FrameExtractionError if ffmpeg is missing, the video is missing, or
    every extraction attempt fails.
    """
    if not ffmpeg_available():
        raise FrameExtractionError(
            "ffmpeg is not installed or not on PATH. Install it "
            "(e.g. `brew install ffmpeg`) to enable frame extraction."
        )

    if not os.path.exists(video_path):
        raise FrameExtractionError(f"Video file not found: {video_path}")

    duration = _probe_duration_seconds(video_path)
    if not duration or duration <= 0:
        duration = 10.0  # conservative fallback when ffprobe can't determine it

    scale_filter = f"scale='min({MAX_FRAME_WIDTH},iw)':-2"

    frame_paths = []
    for i in range(frame_count):
        timestamp = duration * (2 * i + 1) / (2 * frame_count)
        frame_path = os.path.join(output_dir, f"frame_{i + 1}.jpg")
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", f"{timestamp:.2f}", "-i", video_path,
                    "-frames:v", "1", "-vf", scale_filter, "-q:v", "2", frame_path,
                ],
                capture_output=True, timeout=20, check=True,
            )
        except Exception:
            continue

        if os.path.exists(frame_path):
            frame_paths.append(frame_path)

    if not frame_paths:
        raise FrameExtractionError("ffmpeg failed to extract any frames from the Reel video")

    return frame_paths
