import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from trend_analysis.analyze_url import (
    URLAnalysisError,
    analyze_url,
    detect_url,
    normalize_analysis_input,
    normalize_metadata,
)
from trend_analysis import reel_loader
from trend_analysis.reel_loader import CarouselItem, PostMedia, ReelMedia
from trend_analysis.media import AcquiredMedia
from trend_analysis.schemas import TrendAnalysisResult, VisualAsset, VisualContext
from trend_analysis.url_classifier import (
    classify_url,
    classify_url_with_redirects,
)
from trend_analysis.visual_preparation import PreparedVisuals, prepare_post_visuals


class URLDetectionTests(unittest.TestCase):
    def test_detects_and_canonicalizes_instagram_reel(self):
        detected = detect_url(
            "https://www.instagram.com/reel/DcEo6qrMr7b/?stkn=tracking"
        )
        self.assertEqual(
            detected,
            (
                "instagram",
                "reel",
                "DcEo6qrMr7b",
                "https://www.instagram.com/reel/DcEo6qrMr7b/",
            ),
        )

    def test_rejects_unsupported_platform(self):
        with self.assertRaises(URLAnalysisError):
            detect_url("https://example.com/reel/DcEo6qrMr7b/")

    def test_detects_and_canonicalizes_instagram_post(self):
        route = classify_url(
            "https://instagram.com/p/DeCqO6CzpmZ/?img_index=4&stkn=tracking"
        )
        self.assertEqual(route.platform, "instagram")
        self.assertEqual(route.content_type, "post")
        self.assertEqual(route.content_id, "DeCqO6CzpmZ")
        self.assertEqual(
            route.canonical_url,
            "https://www.instagram.com/p/DeCqO6CzpmZ/",
        )

    def test_detects_tiktok_video(self):
        route = classify_url(
            "https://www.tiktok.com/@creator/video/1234567890123456789?is_from_webapp=1"
        )
        self.assertEqual(
            (route.platform, route.content_type, route.content_id),
            ("tiktok", "video", "1234567890123456789"),
        )
        self.assertEqual(
            route.canonical_url,
            "https://www.tiktok.com/@creator/video/1234567890123456789",
        )

    def test_resolves_recognized_tiktok_short_link_then_reclassifies(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return (
                    "https://www.tiktok.com/@creator/video/1234567890123456789"
                    "?share_app_id=1233"
                )

        opener = unittest.mock.Mock(return_value=FakeResponse())
        route = classify_url_with_redirects(
            "https://vm.tiktok.com/ZMshort1/", opener=opener,
        )

        self.assertEqual(route.platform, "tiktok")
        self.assertEqual(route.content_id, "1234567890123456789")
        self.assertEqual(
            route.canonical_url,
            "https://www.tiktok.com/@creator/video/1234567890123456789",
        )
        opener.assert_called_once()

    def test_does_not_resolve_arbitrary_short_link_lookalike(self):
        opener = unittest.mock.Mock()
        with self.assertRaises(URLAnalysisError):
            classify_url_with_redirects(
                "https://example.com/t/ZTRC5xgJp", opener=opener,
            )
        opener.assert_not_called()

    def test_detects_x_status_on_both_domains(self):
        for url in (
            "https://x.com/openai/status/123456789",
            "https://twitter.com/openai/status/123456789?ref_src=test",
        ):
            with self.subTest(url=url):
                route = classify_url(url)
                self.assertEqual(
                    (route.platform, route.content_type, route.content_id),
                    ("x", "post", "123456789"),
                )
                self.assertEqual(
                    route.canonical_url,
                    "https://x.com/openai/status/123456789",
                )

    def test_rejects_malformed_unknown_and_unrelated_instagram_urls(self):
        for url in (
            "not-a-url",
            "https://example.com/foo",
            "https://instagram.com/explore/",
            "https://www.tiktok.com/@creator/photo/1234567890123456789",
            "https://www.tiktok.com/explore",
            "https://x.com/user/status/not-a-number",
            "https://x.com/status/1234567890",
        ):
            with self.subTest(url=url):
                with self.assertRaises(URLAnalysisError):
                    classify_url(url)


class MetadataTests(unittest.TestCase):
    def test_normalizes_only_public_contract_fields(self):
        normalized = normalize_metadata({
            "channel": "creator",
            "description": "A caption #Autumn #autumn #ApplePicking",
            "upload_date": "20251012",
            "duration": 8.7,
            "view_count": 10,
            "like_count": None,
            "comment_count": 2,
            "audio": {
                "artist_name": "Artist",
                "song_name": "Song",
                "uses_original_audio": False,
                "internal_only": "not exported",
            },
            "formats": [{"url": "must not leak"}],
        })

        self.assertEqual(normalized["upload_date"], "2025-10-12")
        self.assertEqual(normalized["hashtags"], ["Autumn", "ApplePicking"])
        self.assertNotIn("formats", normalized)
        self.assertEqual(
            normalized["audio"],
            {
                "artist_name": "Artist",
                "song_name": "Song",
                "uses_original_audio": False,
            },
        )

    def test_hashtags_merge_extractor_tags_and_caption_fallback(self):
        normalized = normalize_metadata({
            "description": "Testing #GRWM #ApplePicking #grwm",
            "tags": ["Existing", "ApplePicking"],
        })

        self.assertEqual(
            normalized["hashtags"],
            ["Existing", "ApplePicking", "GRWM"],
        )


class AnalysisInputTests(unittest.TestCase):
    def _prepared(self, context, asset_count):
        return PreparedVisuals(
            assets=[
                VisualAsset(
                    path=f"/tmp/{index}.jpg",
                    media_type="image",
                    source_index=index,
                )
                for index in range(asset_count)
            ],
            visual_context=context,
        )

    def test_video_frames_are_exposed_separately_from_content_type(self):
        prepared = self._prepared(
            VisualContext(
                source_type="video_frames",
                ordered=True,
                sequence_description="chronological video samples",
            ),
            asset_count=5,
        )

        self.assertEqual(
            normalize_analysis_input(prepared),
            {
                "visual": {
                    "source_type": "video_frames",
                    "ordered": True,
                    "asset_count": 5,
                    "media_composition": None,
                    "item_types": [],
                },
            },
        )

    def test_single_image_is_exposed_as_one_unordered_asset(self):
        prepared = self._prepared(
            VisualContext(
                source_type="single_image",
                ordered=False,
                sequence_description="one original image",
            ),
            asset_count=1,
        )

        visual = normalize_analysis_input(prepared)["visual"]
        self.assertEqual(visual["source_type"], "single_image")
        self.assertFalse(visual["ordered"])
        self.assertEqual(visual["asset_count"], 1)

    def test_mixed_carousel_preserves_composition_and_item_order(self):
        prepared = self._prepared(
            VisualContext(
                source_type="carousel",
                ordered=True,
                sequence_description="ordered carousel items",
                media_composition="mixed image/video",
                item_types=["image", "video-derived frame", "image"],
            ),
            asset_count=3,
        )

        visual = normalize_analysis_input(prepared)["visual"]
        self.assertEqual(visual["source_type"], "carousel")
        self.assertTrue(visual["ordered"])
        self.assertEqual(visual["media_composition"], "mixed image/video")
        self.assertEqual(
            visual["item_types"],
            ["image", "video-derived frame", "image"],
        )


class PublicEmbedTests(unittest.TestCase):
    def test_parses_matching_public_embed_media(self):
        media = {
            "shortcode": "abc123",
            "is_video": True,
            "video_url": "https://cdn.example/video.mp4",
        }
        context = {"gql_data": {"shortcode_media": media}}
        body = (
            "<html><script>{\"contextJSON\":"
            + json.dumps(json.dumps(context))
            + "}</script></html>"
        ).encode()

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, _limit):
                return body

        with patch.object(reel_loader, "urlopen", return_value=FakeResponse()):
            self.assertEqual(reel_loader._read_public_embed("abc123"), media)

    def test_streams_public_media_to_disk(self):
        payload = b"video-bytes"

        class FakeHeaders:
            def get_content_type(self):
                return "video/mp4"

        class FakeResponse:
            headers = FakeHeaders()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size=-1):
                if getattr(self, "done", False):
                    return b""
                self.done = True
                return payload

        with tempfile.TemporaryDirectory() as work_dir:
            path = os.path.join(work_dir, "reel.mp4")
            with patch.object(reel_loader, "urlopen", return_value=FakeResponse()):
                reel_loader._download_public_media(
                    "https://cdn.example/video.mp4",
                    path,
                    "https://www.instagram.com/reel/abc123/embed/captioned/",
                )
            with open(path, "rb") as downloaded:
                self.assertEqual(downloaded.read(), payload)

    def test_public_embed_fallback_supports_single_image_post(self):
        media = {
            "__typename": "GraphImage",
            "shortcode": "image123",
            "is_video": False,
            "display_url": "https://cdn.example/image.jpg",
            "owner": {"username": "creator"},
        }

        def fake_download(_url, path, _referer, expected_type):
            self.assertEqual(expected_type, "image")
            with open(path, "wb") as output:
                output.write(b"image")

        with tempfile.TemporaryDirectory() as work_dir:
            with patch.object(
                reel_loader,
                "_read_public_embed_content",
                return_value=media,
            ), patch.object(
                reel_loader,
                "_download_public_asset",
                side_effect=fake_download,
            ):
                post = reel_loader._load_post_from_public_embed(
                    "https://www.instagram.com/p/image123/",
                    "image123",
                    work_dir,
                )

            self.assertIsNotNone(post.image_path)
            self.assertIsNone(post.video_path)
            self.assertEqual(
                post.metadata["acquisition_source"],
                "instagram_public_embed",
            )

    def test_public_embed_fallback_supports_mixed_carousel(self):
        media = {
            "__typename": "GraphSidecar",
            "shortcode": "mixed123",
            "is_video": False,
            "edge_sidecar_to_children": {
                "edges": [
                    {"node": {
                        "is_video": False,
                        "display_url": "https://cdn.example/image.jpg",
                    }},
                    {"node": {
                        "is_video": True,
                        "video_url": "https://cdn.example/video.mp4",
                        "display_url": "https://cdn.example/cover.jpg",
                    }},
                ]
            },
        }

        def fake_download(_url, path, _referer, expected_type):
            with open(path, "wb") as output:
                output.write(expected_type.encode())

        with tempfile.TemporaryDirectory() as work_dir:
            with patch.object(
                reel_loader,
                "_read_public_embed_content",
                return_value=media,
            ), patch.object(
                reel_loader,
                "_download_public_asset",
                side_effect=fake_download,
            ):
                post = reel_loader._load_post_from_public_embed(
                    "https://www.instagram.com/p/mixed123/",
                    "mixed123",
                    work_dir,
                )

            self.assertEqual(
                [item.media_type for item in post.carousel_items],
                ["image", "video"],
            )

    def test_load_post_uses_public_embed_when_ytdlp_fails(self):
        fallback = PostMedia(
            content_url="https://www.instagram.com/p/image123/",
            content_id="image123",
            metadata={"acquisition_source": "instagram_public_embed"},
            image_path="/tmp/image123.jpg",
        )
        with patch.object(
            reel_loader,
            "_load_post_with_ytdlp",
            side_effect=RuntimeError("yt-dlp unavailable"),
        ), patch.object(
            reel_loader,
            "_load_post_from_public_embed",
            return_value=fallback,
        ) as embed:
            result = reel_loader.load_post(
                fallback.content_url,
                fallback.content_id,
                "/tmp",
            )

        self.assertIs(result, fallback)
        embed.assert_called_once()


class VisualPreparationTests(unittest.TestCase):
    def test_instagram_video_post_becomes_video_frames(self):
        post = PostMedia(
            content_url="https://www.instagram.com/p/video123/",
            content_id="video123",
            metadata={},
            video_path="/tmp/video.mp4",
        )
        with patch(
            "trend_analysis.visual_preparation.extract_frames",
            return_value=["/tmp/frame-1.jpg", "/tmp/frame-2.jpg"],
        ):
            prepared = prepare_post_visuals(post, "/tmp")

        self.assertEqual(prepared.visual_context.source_type, "video_frames")
        self.assertTrue(prepared.visual_context.ordered)

    def test_single_image_post_becomes_one_normalized_asset(self):
        post = PostMedia(
            content_url="https://www.instagram.com/p/image123/",
            content_id="image123",
            metadata={},
            image_path="/tmp/image123.jpg",
        )
        prepared = prepare_post_visuals(post, "/tmp")
        self.assertEqual(len(prepared.assets), 1)
        self.assertEqual(prepared.assets[0].media_type, "image")

    def test_unresolved_carousel_item_marks_preparation_partial(self):
        post = PostMedia(
            content_url="https://www.instagram.com/p/carousel123/",
            content_id="carousel123",
            metadata={},
            carousel_items=[
                CarouselItem(index=0, media_type="image", path="/tmp/0.jpg"),
                CarouselItem(index=1, media_type="unresolved"),
            ],
        )
        prepared = prepare_post_visuals(post, "/tmp")
        self.assertEqual(len(prepared.assets), 1)
        self.assertTrue(prepared.incomplete_reasons)

    def test_instagram_image_carousel_preserves_order(self):
        post = PostMedia(
            content_url="https://www.instagram.com/p/images123/",
            content_id="images123",
            metadata={},
            carousel_items=[
                CarouselItem(index=0, media_type="image", path="/tmp/0.jpg"),
                CarouselItem(index=1, media_type="image", path="/tmp/1.jpg"),
            ],
        )

        prepared = prepare_post_visuals(post, "/tmp")

        self.assertEqual(prepared.visual_context.source_type, "carousel")
        self.assertEqual(prepared.visual_context.media_composition, "images")
        self.assertEqual(prepared.visual_context.item_types, ["image", "image"])

    def test_instagram_mixed_carousel_preserves_media_composition(self):
        post = PostMedia(
            content_url="https://www.instagram.com/p/mixed123/",
            content_id="mixed123",
            metadata={},
            carousel_items=[
                CarouselItem(index=0, media_type="image", path="/tmp/0.jpg"),
                CarouselItem(index=1, media_type="video", path="/tmp/1.mp4"),
            ],
        )
        with patch(
            "trend_analysis.visual_preparation.extract_frames",
            return_value=["/tmp/video-frame.jpg"],
        ):
            prepared = prepare_post_visuals(post, "/tmp")

        self.assertEqual(
            prepared.visual_context.media_composition, "mixed image/video",
        )
        self.assertEqual(
            prepared.visual_context.item_types,
            ["image", "video-derived frame"],
        )


class RouterBehaviorTests(unittest.TestCase):
    def test_instagram_reel_still_routes_through_shared_video_analysis(self):
        url = "https://www.instagram.com/reel/DcEo6qrMr7b/"
        reel = ReelMedia(
            reel_url=url,
            reel_id="DcEo6qrMr7b",
            video_path="/tmp/reel.mp4",
            metadata={"acquisition_source": "fixture"},
        )
        trend = TrendAnalysisResult(
            reel_url=url,
            trend_name="Fixture trend",
            trend_category="Fixture",
            confidence=0.8,
            analysis_status="success",
        )
        with patch(
            "trend_analysis.analyze_url.load_reel", return_value=reel,
        ), patch(
            "trend_analysis.visual_preparation.extract_frames",
            return_value=[f"/tmp/frame-{index}.jpg" for index in range(5)],
        ), patch(
            "trend_analysis.analyze_url.identify_trend", return_value=trend,
        ) as identify:
            result = analyze_url(url)

        context = identify.call_args.args[3]
        self.assertEqual(context.source_type, "video_frames")
        self.assertEqual(result["platform"], "instagram")
        self.assertEqual(result["content_type"], "reel")
        self.assertEqual(result["analysis_status"], "success")

    def test_tiktok_and_x_route_to_platform_adapters_not_instagram(self):
        trend = TrendAnalysisResult(
            reel_url="https://example.invalid/",
            trend_name="Fixture trend",
            trend_category="Fixture",
            confidence=0.8,
            analysis_status="success",
        )
        for url, platform in (
            (
                "https://www.tiktok.com/@creator/video/1234567890123456789",
                "tiktok",
            ),
            ("https://x.com/openai/status/1234567890", "x"),
        ):
            acquired = AcquiredMedia(
                metadata={"acquisition_source": "fixture"},
                image_path="/tmp/fixture.jpg",
            )
            adapter = unittest.mock.Mock(return_value=acquired)
            with self.subTest(url=url), patch.dict(
                "trend_analysis.analyze_url.PLATFORM_ACQUIRERS",
                {(platform, "video" if platform == "tiktok" else "post"): adapter},
            ), patch(
                "trend_analysis.analyze_url.load_reel"
            ) as load_reel, patch(
                "trend_analysis.analyze_url.load_post"
            ) as load_post, patch(
                "trend_analysis.analyze_url.identify_trend", return_value=trend,
            ):
                result = analyze_url(url)

                self.assertEqual(result["platform"], platform)
                self.assertEqual(result["analysis_status"], "success")
                self.assertEqual(
                    result["analysis_input"]["visual"]["source_type"],
                    "single_image",
                )
                adapter.assert_called_once()
                load_reel.assert_not_called()
                load_post.assert_not_called()

    def test_x_text_only_post_returns_unsupported_without_model_call(self):
        acquired = AcquiredMedia(
            metadata={
                "description": "Text only",
                "acquisition_source": "x_public_syndication",
            },
            no_visual_reason="No analyzable visual media found in this X post",
        )
        adapter = unittest.mock.Mock(return_value=acquired)
        with patch.dict(
            "trend_analysis.analyze_url.PLATFORM_ACQUIRERS",
            {("x", "post"): adapter},
        ), patch(
            "trend_analysis.analyze_url.identify_trend"
        ) as identify:
            result = analyze_url("https://x.com/openai/status/1234567890")

        self.assertEqual(result["analysis_status"], "unsupported")
        self.assertIsNone(result["analysis_input"])
        self.assertEqual(result["metadata"]["description"], "Text only")
        self.assertIn("No analyzable visual media", result["analysis_error"])
        identify.assert_not_called()

    def test_instagram_post_routes_through_shared_analysis(self):
        fake_post = PostMedia(
            content_url="https://www.instagram.com/p/DeCqO6CzpmZ/",
            content_id="DeCqO6CzpmZ",
            metadata={"uploader": "creator", "acquisition_source": "fixture"},
            image_path="/tmp/post.jpg",
        )
        trend = TrendAnalysisResult(
            reel_url=fake_post.content_url,
            trend_name="Fixture trend",
            trend_category="Fixture",
            confidence=0.8,
            analysis_status="success",
        )

        with patch(
            "trend_analysis.analyze_url.load_post", return_value=fake_post,
        ) as load_post, patch(
            "trend_analysis.analyze_url.identify_trend", return_value=trend,
        ) as identify:
            result = analyze_url(
                "https://instagram.com/p/DeCqO6CzpmZ/?img_index=4"
            )

        load_post.assert_called_once()
        identify.assert_called_once()
        supplied_context = identify.call_args.args[3]
        self.assertEqual(supplied_context.source_type, "single_image")
        self.assertEqual(result["content_type"], "post")
        self.assertEqual(
            result["analysis_input"],
            {
                "visual": {
                    "source_type": "single_image",
                    "ordered": False,
                    "asset_count": 1,
                    "media_composition": None,
                    "item_types": [],
                },
            },
        )
        self.assertEqual(result["analysis_status"], "success")
        self.assertEqual(result["trend"]["trend_name"], "Fixture trend")


class CLIContractTests(unittest.TestCase):
    def test_unsupported_url_is_json_only_on_stdout_and_nonzero(self):
        repository_root = os.path.dirname(os.path.dirname(__file__))
        completed = subprocess.run(
            [
                os.path.join(repository_root, "analyze.sh"),
                "https://example.com/reel/not-instagram/",
            ],
            cwd=repository_root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        parsed = json.loads(completed.stdout)
        self.assertTrue(completed.stdout.startswith("{\n  \"platform\""))
        self.assertEqual(parsed["analysis_status"], "error")
        self.assertIsNone(parsed["analysis_input"])
        self.assertIn("could not be identified", parsed["analysis_error"])
        self.assertNotIn("[ERROR]", completed.stdout)
        self.assertIn("[ERROR]", completed.stderr)


if __name__ == "__main__":
    unittest.main()
