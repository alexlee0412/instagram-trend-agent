"""Anonymous public TikTok video acquisition."""

from ..media import AcquiredMedia, MediaAcquisitionError
from .ytdlp_utils import download_video


def load_tiktok_video(
    content_url: str,
    content_id: str,
    work_dir: str,
) -> AcquiredMedia:
    info, video_path = download_video(content_url, work_dir)
    extracted_id = str(info.get("id") or info.get("display_id") or "")
    if extracted_id and extracted_id != content_id:
        raise MediaAcquisitionError(
            "TikTok extractor returned media for a different content ID"
        )
    metadata = dict(info)
    metadata["acquisition_source"] = "yt-dlp"
    return AcquiredMedia(metadata=metadata, video_path=video_path)
