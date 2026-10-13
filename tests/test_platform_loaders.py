import os
import tempfile
import unittest
from unittest.mock import patch

from trend_analysis.media import MediaAcquisitionError
from trend_analysis.platforms.tiktok_loader import load_tiktok_video
from trend_analysis.platforms import x_loader
from trend_analysis.visual_preparation import prepare_media_visuals


class TikTokLoaderTests(unittest.TestCase):
    def test_downloaded_video_and_metadata_use_normalized_media(self):
        info = {
            "id": "1234567890123456789",
            "uploader": "creator",
            "description": "Caption #GRWM",
        }
        with patch(
            "trend_analysis.platforms.tiktok_loader.download_video",
            return_value=(info, "/tmp/tiktok.mp4"),
        ):
            media = load_tiktok_video(
                "https://www.tiktok.com/@creator/video/1234567890123456789",
                "1234567890123456789",
                "/tmp",
            )

        self.assertEqual(media.video_path, "/tmp/tiktok.mp4")
        self.assertEqual(media.metadata["acquisition_source"], "yt-dlp")

    def test_rejects_mismatched_extracted_id(self):
        with patch(
            "trend_analysis.platforms.tiktok_loader.download_video",
            return_value=({"id": "999"}, "/tmp/tiktok.mp4"),
        ):
            with self.assertRaisesRegex(
                MediaAcquisitionError, "different content ID",
            ):
                load_tiktok_video(
                    "https://www.tiktok.com/@creator/video/123",
                    "123",
                    "/tmp",
                )


def x_post(content_id, media_details=None):
    return {
        "__typename": "Tweet",
        "id_str": content_id,
        "text": "Public post #Example",
        "created_at": "2026-01-02T03:04:05.000Z",
        "favorite_count": 10,
        "conversation_count": 2,
        "user": {"name": "Creator", "screen_name": "creator"},
        "entities": {"hashtags": [{"text": "Example"}]},
        "mediaDetails": media_details or [],
    }


def photo(url="https://pbs.twimg.com/media/example.jpg"):
    return {"type": "photo", "media_url_https": url}


def video(url="https://video.twimg.com/example.mp4", media_type="video"):
    return {
        "type": media_type,
        "media_url_https": "https://pbs.twimg.com/example.jpg",
        "video_info": {
            "duration_millis": 2000,
            "variants": [
                {"content_type": "video/mp4", "bitrate": 256000, "url": url},
                {
                    "content_type": "video/mp4",
                    "bitrate": 1024000,
                    "url": url + "?high=1",
                },
            ],
        },
    }


class XLoaderTests(unittest.TestCase):
    def _fake_download(self, _url, destination, _expected_type, _referer):
        with open(destination, "wb") as output:
            output.write(b"media")

    def test_single_video_prefers_ytdlp(self):
        content_id = "1234567890"
        info = {
            "id": "video-media-id",
            "display_id": content_id,
            "extractor_key": "Twitter",
            "formats": [{"vcodec": "h264"}],
            "view_count": 99,
        }
        with tempfile.TemporaryDirectory() as work_dir, patch.object(
            x_loader, "_read_public_post",
            return_value=x_post(content_id, [video()]),
        ), patch.object(
            x_loader, "extract_info", return_value=info,
        ), patch.object(
            x_loader, "download_video",
            return_value=(info, os.path.join(work_dir, "video.mp4")),
        ) as download:
            media = x_loader.load_x_post(
                f"https://x.com/creator/status/{content_id}",
                content_id,
                work_dir,
            )

        download.assert_called_once()
        self.assertTrue(media.video_path.endswith("video.mp4"))
        self.assertEqual(
            media.metadata["acquisition_source"],
            "yt-dlp+x_public_syndication",
        )
        self.assertEqual(media.metadata["view_count"], 99)

    def test_single_image_uses_public_syndication_media(self):
        content_id = "1234567890"
        with tempfile.TemporaryDirectory() as work_dir, patch.object(
            x_loader, "_read_public_post",
            return_value=x_post(content_id, [photo()]),
        ), patch.object(
            x_loader, "extract_info",
            side_effect=MediaAcquisitionError("no video"),
        ), patch.object(
            x_loader, "_download_public_asset",
            side_effect=self._fake_download,
        ):
            media = x_loader.load_x_post(
                f"https://x.com/creator/status/{content_id}",
                content_id,
                work_dir,
            )
            prepared = prepare_media_visuals(media, work_dir)

        self.assertIsNotNone(media.image_path)
        self.assertEqual(prepared.visual_context.source_type, "single_image")

    def test_animated_gif_is_prepared_as_video_frames(self):
        content_id = "1234567890"
        with tempfile.TemporaryDirectory() as work_dir, patch.object(
            x_loader, "_read_public_post",
            return_value=x_post(content_id, [video(media_type="animated_gif")]),
        ), patch.object(
            x_loader, "extract_info",
            side_effect=MediaAcquisitionError("no yt-dlp video"),
        ), patch.object(
            x_loader, "_download_public_asset",
            side_effect=self._fake_download,
        ), patch(
            "trend_analysis.visual_preparation.extract_frames",
            return_value=["/tmp/gif-frame.jpg"],
        ):
            media = x_loader.load_x_post(
                f"https://x.com/creator/status/{content_id}",
                content_id,
                work_dir,
            )
            prepared = prepare_media_visuals(media, work_dir)

        self.assertIsNotNone(media.video_path)
        self.assertEqual(prepared.visual_context.source_type, "video_frames")

    def test_multiple_images_preserve_carousel_order(self):
        content_id = "1234567890"
        details = [
            photo("https://pbs.twimg.com/media/one.jpg"),
            photo("https://pbs.twimg.com/media/two.jpg"),
            photo("https://pbs.twimg.com/media/three.jpg"),
        ]
        with tempfile.TemporaryDirectory() as work_dir, patch.object(
            x_loader, "_read_public_post",
            return_value=x_post(content_id, details),
        ), patch.object(
            x_loader, "extract_info",
            side_effect=MediaAcquisitionError("no video"),
        ), patch.object(
            x_loader, "_download_public_asset",
            side_effect=self._fake_download,
        ):
            media = x_loader.load_x_post(
                f"https://x.com/creator/status/{content_id}",
                content_id,
                work_dir,
            )
            prepared = prepare_media_visuals(media, work_dir)

        self.assertEqual([item.index for item in media.items], [0, 1, 2])
        self.assertEqual(prepared.visual_context.source_type, "carousel")
        self.assertEqual(prepared.visual_context.media_composition, "images")
        self.assertEqual(prepared.visual_context.item_types, ["image"] * 3)

    def test_mixed_media_uses_ordered_carousel(self):
        content_id = "1234567890"
        details = [photo(), video(), photo("https://pbs.twimg.com/media/two.jpg")]
        with tempfile.TemporaryDirectory() as work_dir, patch.object(
            x_loader, "_read_public_post",
            return_value=x_post(content_id, details),
        ), patch.object(
            x_loader, "extract_info", return_value={"extractor_key": "Twitter"},
        ), patch.object(
            x_loader, "_download_public_asset",
            side_effect=self._fake_download,
        ), patch(
            "trend_analysis.visual_preparation.extract_frames",
            return_value=["/tmp/video-frame.jpg"],
        ):
            media = x_loader.load_x_post(
                f"https://x.com/creator/status/{content_id}",
                content_id,
                work_dir,
            )
            prepared = prepare_media_visuals(media, work_dir)

        self.assertEqual(
            prepared.visual_context.item_types,
            ["image", "video-derived frame", "image"],
        )
        self.assertEqual(
            prepared.visual_context.media_composition, "mixed image/video",
        )

    def test_text_only_post_returns_no_visual_reason(self):
        content_id = "1234567890"
        with tempfile.TemporaryDirectory() as work_dir, patch.object(
            x_loader, "_read_public_post", return_value=x_post(content_id),
        ), patch.object(
            x_loader, "extract_info",
            side_effect=MediaAcquisitionError("no video"),
        ):
            media = x_loader.load_x_post(
                f"https://x.com/creator/status/{content_id}",
                content_id,
                work_dir,
            )

        self.assertFalse(media.has_visual_media)
        self.assertIn("No analyzable visual media", media.no_visual_reason)


if __name__ == "__main__":
    unittest.main()
