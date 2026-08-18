"""Structured output schema for a single Instagram content item's trend
analysis (Reels and now /p/ posts — image, carousel, or video).

This is a per-item TREND IDENTIFICATION result, not a virality/trend-
detection result — see trend_identifier.py's module docstring for the
distinction. Aggregating many of these into a virality signal is a future,
separate batch stage.

Note: reel_url/reel_id are kept as-is (not renamed to content_url/
content_id) for /p/ posts too, to avoid an MVP-stage schema break — see
analyze_reel.py's module docstring for the tradeoff this was weighed
against.
"""

from dataclasses import asdict, dataclass, field
from typing import List, Optional


@dataclass
class VisualAsset:
    """One ordered image ready for the trend identifier — the model never
    needs to know whether it came from a sampled video frame, a single
    post image, or a carousel slide.
    """
    path: str
    media_type: str  # "video_frame" | "image"
    source_index: int
    timestamp: Optional[float] = None  # seconds, only for video_frame


@dataclass
class TrendAnalysisResult:
    reel_url: str
    reel_id: Optional[str] = None

    trend_name: Optional[str] = None
    trend_category: Optional[str] = None
    confidence: Optional[float] = None

    content_summary: Optional[str] = None

    event_context: List[str] = field(default_factory=list)
    subjects: List[str] = field(default_factory=list)
    activities: List[str] = field(default_factory=list)
    objects: List[str] = field(default_factory=list)

    visual_aesthetic: List[str] = field(default_factory=list)
    cultural_signals: List[str] = field(default_factory=list)

    evidence: List[str] = field(default_factory=list)

    # "success" | "partial" (validated result, but based on incomplete
    # input such as fewer frames than requested) | "error"
    analysis_status: str = "error"
    analysis_error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)
