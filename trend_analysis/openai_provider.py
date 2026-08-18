"""OpenAI Responses API provider for trend identification.

Isolated here so trend_identifier.py's dispatch logic — and everything
else in trend_analysis — stays provider-agnostic. No other module should
import the `openai` package or know about its request/response shape.

Verified against OpenAI's official docs (developers.openai.com/api/docs)
on 2026-08-19: the Responses API (client.responses.create) is the current
recommended surface for vision input, using `input_image` content items
with base64 data URLs, and `text.format` with `type: "json_schema"` for
guaranteed-structured output.
"""

import base64
import json
import os
from datetime import datetime, timezone
from typing import List

from .prompts import TREND_IDENTIFICATION_SYSTEM_PROMPT, build_user_prompt
from .schemas import TrendAnalysisResult

# "terra" balances reasoning quality against cost for a single-image-set
# call; override with OPENAI_TREND_MODEL if a different tier is preferred.
DEFAULT_MODEL = "gpt-5.6-terra"
REQUEST_TIMEOUT_SECONDS = 60

RESULT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "trend_name": {"type": "string"},
        "trend_category": {"type": "string"},
        "confidence": {"type": "number"},
        "content_summary": {"type": "string"},
        "event_context": {"type": "array", "items": {"type": "string"}},
        "subjects": {"type": "array", "items": {"type": "string"}},
        "activities": {"type": "array", "items": {"type": "string"}},
        "objects": {"type": "array", "items": {"type": "string"}},
        "visual_aesthetic": {"type": "array", "items": {"type": "string"}},
        "cultural_signals": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "trend_name", "trend_category", "confidence", "content_summary",
        "event_context", "subjects", "activities", "objects",
        "visual_aesthetic", "cultural_signals", "evidence",
    ],
    "additionalProperties": False,
}

LIST_FIELDS = (
    "event_context", "subjects", "activities", "objects",
    "visual_aesthetic", "cultural_signals", "evidence",
)


class ProviderError(Exception):
    """Raised for any OpenAI-provider-specific failure (missing key,
    missing package, timeout, malformed response, failed validation).
    Callers treat this as an enrichment failure, never a crash.
    """


def _summarize_metadata(metadata: dict) -> str:
    """Extract only the minimal useful fields from yt-dlp's (large) info
    dict. Never send the raw dict — it includes CDN URLs, format lists,
    and other irrelevant fields.
    """
    timestamp = metadata.get("timestamp")
    posted_at = None
    if timestamp:
        try:
            posted_at = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            posted_at = None

    lines = [
        f"uploader: {metadata.get('channel') or metadata.get('uploader') or 'unknown'}",
        f"posted_at: {posted_at or 'unknown'}",
        f"duration_seconds: {metadata.get('duration')}",
        f"view_count: {metadata.get('view_count')}",
        f"like_count: {metadata.get('like_count')}",
        f"comment_count: {metadata.get('comment_count')}",
    ]
    return "\n".join(lines)


def _encode_image(image_path: str) -> str:
    with open(image_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def _validate_and_build_result(parsed: dict, reel_url: str) -> TrendAnalysisResult:
    trend_name = parsed.get("trend_name")
    if not isinstance(trend_name, str) or not trend_name.strip():
        raise ProviderError("Model response missing a usable trend_name")

    confidence = parsed.get("confidence")
    if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1):
        raise ProviderError(f"Model response had an invalid confidence value: {confidence!r}")

    for field in LIST_FIELDS:
        if not isinstance(parsed.get(field), list):
            raise ProviderError(f"Model response field '{field}' was not a list")

    return TrendAnalysisResult(
        reel_url=reel_url,
        trend_name=trend_name.strip(),
        trend_category=str(parsed.get("trend_category") or "").strip() or None,
        confidence=float(confidence),
        content_summary=str(parsed.get("content_summary") or "").strip() or None,
        event_context=parsed["event_context"],
        subjects=parsed["subjects"],
        activities=parsed["activities"],
        objects=parsed["objects"],
        visual_aesthetic=parsed["visual_aesthetic"],
        cultural_signals=parsed["cultural_signals"],
        evidence=parsed["evidence"],
        analysis_status="success",
        analysis_error=None,
    )


def identify_trend(image_paths: List[str], metadata: dict, reel_url: str) -> TrendAnalysisResult:
    """image_paths is an ordered list of local image files — sampled video
    frames, a single post image, or carousel slides. This function (and
    OpenAI) never needs to know which.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ProviderError(
            "OPENAI_API_KEY is not set. Add it to .env to use the OpenAI trend-identification provider."
        )

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ProviderError(
            "The 'openai' package is not installed. Run `pip install openai`."
        ) from exc

    model = os.getenv("OPENAI_TREND_MODEL", DEFAULT_MODEL)
    caption = metadata.get("description")
    metadata_summary = _summarize_metadata(metadata)
    user_prompt = build_user_prompt(caption, metadata_summary)

    content = [{"type": "input_text", "text": user_prompt}]
    for image_path in image_paths:
        content.append({
            "type": "input_image",
            "image_url": _encode_image(image_path),
            "detail": "auto",
        })

    client = OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS)

    try:
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": TREND_IDENTIFICATION_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "trend_analysis_result",
                    "strict": True,
                    "schema": RESULT_JSON_SCHEMA,
                }
            },
        )
    except Exception as exc:
        raise ProviderError(f"OpenAI request failed: {exc}") from exc

    raw_text = getattr(response, "output_text", None)
    if not raw_text:
        raise ProviderError("OpenAI returned no output text")

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"OpenAI response was not valid JSON: {exc}") from exc

    return _validate_and_build_result(parsed, reel_url)
