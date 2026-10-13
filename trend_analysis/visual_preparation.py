"""Normalize acquired media into ordered images for multimodal analysis."""

import os
from dataclasses import dataclass, field
from typing import List

from .config import FRAME_COUNT, MAX_VISUAL_ASSETS
from .frame_extractor import FrameExtractionError, extract_frames
from .media import AcquiredMedia, MediaItem
from .schemas import VisualAsset, VisualContext


@dataclass
class PreparedVisuals:
    assets: List[VisualAsset]
    visual_context: VisualContext
    incomplete_reasons: List[str] = field(default_factory=list)


def select_carousel_indices(total: int, max_assets: int) -> List[int]:
    """Select first, last, and evenly spaced interior carousel positions."""
    if total <= max_assets:
        return list(range(total))
    if max_assets <= 1:
        return [0]

    step = (total - 1) / (max_assets - 1)
    return sorted({round(i * step) for i in range(max_assets)})


def prepare_video_visuals(
    video_path: str,
    work_dir: str,
    source_index: int = 0,
    frame_count: int = FRAME_COUNT,
) -> PreparedVisuals:
    frame_paths = extract_frames(video_path, work_dir, frame_count=frame_count)
    reasons = []
    if len(frame_paths) < frame_count:
        reasons.append(
            f"only {len(frame_paths)} of {frame_count} requested video frames were extracted"
        )
    return PreparedVisuals(
        assets=[
            VisualAsset(
                path=path,
                media_type="video_frame",
                source_index=source_index,
                timestamp=None,
            )
            for path in frame_paths
        ],
        visual_context=VisualContext(
            source_type="video_frames",
            ordered=True,
            sequence_description=(
                "chronological samples from one continuous video; temporal "
                "progression, actions, and transitions may be meaningful"
            ),
        ),
        incomplete_reasons=reasons,
    )


def _prepare_carousel_visuals(
    items: List[MediaItem],
    work_dir: str,
) -> PreparedVisuals:
    resolved = [item for item in items if item.media_type != "unresolved"]
    reasons = []
    if len(resolved) < len(items):
        reasons.append(
            f"{len(items) - len(resolved)} of {len(items)} carousel items could not be acquired"
        )
    if not resolved:
        return PreparedVisuals(
            assets=[],
            visual_context=VisualContext(
                source_type="carousel",
                ordered=True,
                sequence_description=(
                    "ordered carousel items; these are not frames from one "
                    "continuous video"
                ),
                media_composition="unknown",
            ),
            incomplete_reasons=reasons,
        )

    selected_positions = select_carousel_indices(
        len(resolved), MAX_VISUAL_ASSETS,
    )
    selected = [resolved[index] for index in selected_positions]
    assets = []
    item_types = []

    for item in selected:
        if item.media_type == "image":
            assets.append(
                VisualAsset(
                    path=item.path,
                    media_type="image",
                    source_index=item.index,
                    timestamp=None,
                )
            )
            item_types.append("image")
            continue

        if item.media_type == "video":
            frame_dir = os.path.join(
                work_dir, f"carousel_{item.index}_frames",
            )
            os.makedirs(frame_dir, exist_ok=True)
            try:
                prepared = prepare_video_visuals(
                    item.path,
                    frame_dir,
                    source_index=item.index,
                    frame_count=1,
                )
            except FrameExtractionError:
                reasons.append(
                    f"carousel video item {item.index} could not be converted to a frame"
                )
                continue
            assets.extend(prepared.assets)
            item_types.extend(
                "video-derived frame" for _asset in prepared.assets
            )
            reasons.extend(prepared.incomplete_reasons)

    has_images = "image" in item_types
    has_video_frames = "video-derived frame" in item_types
    if has_images and has_video_frames:
        composition = "mixed image/video"
    elif has_video_frames:
        composition = "video"
    elif has_images:
        composition = "images"
    else:
        composition = "unknown"

    return PreparedVisuals(
        assets=assets,
        visual_context=VisualContext(
            source_type="carousel",
            ordered=True,
            sequence_description=(
                "ordered carousel items; item order may be meaningful, and "
                "these are not frames from one continuous video"
            ),
            media_composition=composition,
            item_types=item_types,
        ),
        incomplete_reasons=reasons,
    )


def _prepare_media_fields(
    video_path: str,
    image_path: str,
    items: list,
    work_dir: str,
) -> PreparedVisuals:
    if video_path:
        return prepare_video_visuals(video_path, work_dir)

    if image_path:
        return PreparedVisuals(
            assets=[
                VisualAsset(
                    path=image_path,
                    media_type="image",
                    source_index=0,
                    timestamp=None,
                )
            ],
            visual_context=VisualContext(
                source_type="single_image",
                ordered=False,
                sequence_description=(
                    "one original post image; do not infer temporal progression"
                ),
            ),
        )

    if items:
        return _prepare_carousel_visuals(items, work_dir)

    return PreparedVisuals(
        assets=[],
        visual_context=VisualContext(
            source_type="single_image",
            ordered=False,
            sequence_description="no usable visual input was prepared",
        ),
    )


def prepare_media_visuals(
    media: AcquiredMedia,
    work_dir: str,
) -> PreparedVisuals:
    """Prepare visuals from any platform's normalized acquired media."""
    return _prepare_media_fields(
        media.video_path,
        media.image_path,
        media.items,
        work_dir,
    )


def prepare_post_visuals(post, work_dir: str) -> PreparedVisuals:
    """Compatibility wrapper for the historical Instagram PostMedia type."""
    return _prepare_media_fields(
        post.video_path,
        post.image_path,
        post.carousel_items or [],
        work_dir,
    )
