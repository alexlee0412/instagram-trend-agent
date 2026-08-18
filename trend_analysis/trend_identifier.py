"""Model-abstraction boundary for trend identification.

identify_trend(frames, metadata, reel_url) is the ONLY function the rest of
the pipeline calls. Swapping providers (OpenAI, Claude, a local Ollama
multimodal model, etc.) means changing this module (and its provider
module) only — no model-specific calls should appear anywhere else in
trend_analysis, and analyze_reel.py never imports a provider directly.

Only the OpenAI provider is implemented so far (see openai_provider.py).
Claude/Ollama intentionally still raise ModelNotConfiguredError rather
than fabricating a result — see analyze_reel.py, which turns any of these
into a clean analysis_status="error" JSON response instead of crashing or
faking a trend.
"""

from typing import List

from .config import TREND_MODEL_PROVIDER
from .openai_provider import ProviderError
from .schemas import TrendAnalysisResult

__all__ = ["identify_trend", "ModelNotConfiguredError", "ProviderError"]


class ModelNotConfiguredError(Exception):
    """Raised when no trend-identification model provider is configured
    (or the configured one isn't implemented yet)."""


def identify_trend(frames: List[str], metadata: dict, reel_url: str) -> TrendAnalysisResult:
    """Analyze extracted frames + Reel metadata and return a structured
    TrendAnalysisResult. Never fabricates a result: raises
    ModelNotConfiguredError or ProviderError instead.
    """
    if TREND_MODEL_PROVIDER == "openai":
        from . import openai_provider
        return openai_provider.identify_trend(frames, metadata, reel_url)
    if TREND_MODEL_PROVIDER == "claude":
        return _identify_trend_claude(frames, metadata, reel_url)
    if TREND_MODEL_PROVIDER == "ollama":
        return _identify_trend_ollama(frames, metadata, reel_url)

    raise ModelNotConfiguredError(
        "No trend-identification model is configured. Set TREND_MODEL_PROVIDER "
        "in .env to 'openai' (implemented, requires OPENAI_API_KEY) — 'claude' "
        "and 'ollama' are not implemented yet."
    )


def _identify_trend_claude(frames: List[str], metadata: dict, reel_url: str) -> TrendAnalysisResult:
    raise ModelNotConfiguredError(
        "TREND_MODEL_PROVIDER=claude is set, but the Claude provider is not "
        "implemented yet — requires an ANTHROPIC_API_KEY and the anthropic package."
    )


def _identify_trend_ollama(frames: List[str], metadata: dict, reel_url: str) -> TrendAnalysisResult:
    raise ModelNotConfiguredError(
        "TREND_MODEL_PROVIDER=ollama is set, but the Ollama provider is not "
        "implemented yet — requires choosing/pulling a local multimodal model first."
    )
