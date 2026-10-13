import unittest
from unittest.mock import patch

from trend_analysis.openai_provider import (
    ProviderError,
    RESULT_JSON_SCHEMA,
    _validate_and_build_result,
)
from trend_analysis.prompts import (
    TREND_IDENTIFICATION_SYSTEM_PROMPT,
    build_user_prompt,
)
from trend_analysis.reel_loader import CarouselItem, PostMedia
from trend_analysis.visual_preparation import (
    prepare_post_visuals,
    prepare_video_visuals,
)


def valid_model_result():
    return {
        "trend_name": "Apple Picking Photo Inspiration",
        "trend_category": "Seasonal Lifestyle",
        "confidence": 0.9,
        "content_summary": "An apple-picking outing presented as photo inspiration.",
        "content_format": ["photo collage", "recreation"],
        "event_context": ["autumn apple picking"],
        "subjects": ["person in an orchard"],
        "activities": ["posing with apples"],
        "objects": ["apples"],
        "visual_aesthetic": ["autumn colors"],
        "cultural_signals": ["seasonal activity inspiration"],
        "evidence": ["caption and visuals both present apple picking"],
    }


class VisualContextTests(unittest.TestCase):
    def test_video_frames_are_described_as_chronological(self):
        with patch(
            "trend_analysis.visual_preparation.extract_frames",
            return_value=["/tmp/one.jpg", "/tmp/two.jpg"],
        ):
            prepared = prepare_video_visuals(
                "/tmp/video.mp4", "/tmp", frame_count=2,
            )

        context = prepared.visual_context
        self.assertEqual(context.source_type, "video_frames")
        self.assertTrue(context.ordered)
        prompt = build_user_prompt("caption", "metadata", context.to_dict())
        self.assertIn("source_type: video_frames", prompt)
        self.assertIn("chronological samples from one continuous video", prompt)

    def test_single_image_has_no_temporal_progression(self):
        post = PostMedia(
            content_url="https://www.instagram.com/p/image123/",
            content_id="image123",
            metadata={},
            image_path="/tmp/image.jpg",
        )
        prepared = prepare_post_visuals(post, "/tmp")
        context = prepared.visual_context

        self.assertEqual(context.source_type, "single_image")
        self.assertFalse(context.ordered)
        prompt = build_user_prompt("caption", "metadata", context.to_dict())
        self.assertIn("source_type: single_image", prompt)
        self.assertIn("do not infer temporal progression", prompt)

    def test_image_carousel_is_ordered_but_not_continuous_video(self):
        post = PostMedia(
            content_url="https://www.instagram.com/p/carousel123/",
            content_id="carousel123",
            metadata={},
            carousel_items=[
                CarouselItem(index=0, media_type="image", path="/tmp/0.jpg"),
                CarouselItem(index=1, media_type="image", path="/tmp/1.jpg"),
            ],
        )
        prepared = prepare_post_visuals(post, "/tmp")
        context = prepared.visual_context

        self.assertEqual(context.source_type, "carousel")
        self.assertTrue(context.ordered)
        self.assertEqual(context.media_composition, "images")
        self.assertEqual(context.item_types, ["image", "image"])
        prompt = build_user_prompt("caption", "metadata", context.to_dict())
        self.assertIn("source_type: carousel", prompt)
        self.assertIn("not frames from one continuous video", prompt)
        self.assertIn("carousel_items: image, image", prompt)

    def test_mixed_carousel_preserves_ordered_media_composition(self):
        post = PostMedia(
            content_url="https://www.instagram.com/p/mixed123/",
            content_id="mixed123",
            metadata={},
            carousel_items=[
                CarouselItem(index=0, media_type="image", path="/tmp/0.jpg"),
                CarouselItem(index=1, media_type="video", path="/tmp/1.mp4"),
                CarouselItem(index=2, media_type="image", path="/tmp/2.jpg"),
            ],
        )
        with patch(
            "trend_analysis.visual_preparation.extract_frames",
            return_value=["/tmp/video-frame.jpg"],
        ):
            prepared = prepare_post_visuals(post, "/tmp")

        context = prepared.visual_context
        self.assertEqual(context.media_composition, "mixed image/video")
        self.assertEqual(
            context.item_types,
            ["image", "video-derived frame", "image"],
        )
        prompt = build_user_prompt("caption", "metadata", context.to_dict())
        self.assertIn("media_composition: mixed image/video", prompt)
        self.assertIn(
            "carousel_items: image, video-derived frame, image", prompt,
        )


class PromptPolicyTests(unittest.TestCase):
    def test_prompt_contains_new_format_and_cross_modal_policy(self):
        prompt = TREND_IDENTIFICATION_SYSTEM_PROMPT
        self.assertIn("Content format / editing structure", prompt)
        self.assertIn(
            "Content format describes only the reusable presentation", prompt,
        )
        self.assertIn(
            "not its topic, subject, aesthetic, event, audience, or cultural meaning",
            prompt,
        )
        self.assertIn("semantic rather than structural", prompt)
        self.assertIn(
            "Return an empty list when no distinct presentation/editing format is present",
            prompt,
        )
        self.assertIn("Infer the trend from all available evidence", prompt)
        self.assertIn("Explicit trend terminology may be used", prompt)
        self.assertIn("Do not blindly trust a caption or hashtag", prompt)
        self.assertIn("Do not embellish it", prompt)
        self.assertNotIn("invent a new, specific label", prompt)
        self.assertNotIn("secondary corroborating evidence", prompt)
        self.assertNotIn("outfit/inspiration formats", prompt)


class StructuredResultTests(unittest.TestCase):
    def test_content_format_is_required_by_json_schema(self):
        self.assertIn("content_format", RESULT_JSON_SCHEMA["properties"])
        self.assertIn("content_format", RESULT_JSON_SCHEMA["required"])
        self.assertIn(
            "presentation, editing, sequencing, or interaction structures only",
            RESULT_JSON_SCHEMA["properties"]["content_format"]["description"],
        )

    def test_successful_result_parsing_preserves_content_format(self):
        result = _validate_and_build_result(
            valid_model_result(),
            "https://www.instagram.com/reel/example/",
        )
        self.assertEqual(
            result.content_format,
            ["photo collage", "recreation"],
        )
        self.assertEqual(result.analysis_status, "success")

    def test_content_format_must_be_an_array(self):
        parsed = valid_model_result()
        parsed["content_format"] = "recreation"
        with self.assertRaisesRegex(ProviderError, "content_format"):
            _validate_and_build_result(
                parsed,
                "https://www.instagram.com/reel/example/",
            )

    def test_content_format_items_must_be_strings(self):
        parsed = valid_model_result()
        parsed["content_format"] = ["recreation", 42]
        with self.assertRaisesRegex(ProviderError, "non-string"):
            _validate_and_build_result(
                parsed,
                "https://www.instagram.com/reel/example/",
            )


if __name__ == "__main__":
    unittest.main()
