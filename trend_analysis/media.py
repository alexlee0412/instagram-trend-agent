"""Platform-neutral acquired-media representation.

Platform loaders own network and extractor behavior.  They return this small
shape so visual preparation and model code never branch on a platform name.
"""

from dataclasses import dataclass, field
from typing import List, Optional


class MediaAcquisitionError(Exception):
    """Raised when public media or metadata cannot be acquired safely."""


@dataclass
class MediaItem:
    index: int
    media_type: str  # "video" | "image" | "unresolved"
    path: Optional[str] = None


@dataclass
class AcquiredMedia:
    metadata: dict
    video_path: Optional[str] = None
    image_path: Optional[str] = None
    items: List[MediaItem] = field(default_factory=list)
    incomplete_reasons: List[str] = field(default_factory=list)
    no_visual_reason: Optional[str] = None

    @property
    def has_visual_media(self) -> bool:
        return bool(self.video_path or self.image_path or self.items)
