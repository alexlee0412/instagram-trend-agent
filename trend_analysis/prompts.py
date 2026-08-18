"""Prompt templates for trend identification.

Kept separate from trend_identifier.py's provider-dispatch logic so prompt
wording can be iterated on without touching model-calling code.
"""

TREND_IDENTIFICATION_SYSTEM_PROMPT = """\
You are analyzing a short-form Instagram Reel video to identify what \
cultural or social trend it belongs to. You are given several \
representative frames from the video along with its caption and other \
metadata.

Identify:
- subjects, activities, and objects visible across the frames
- any event or cultural context the content references
- the visual aesthetic (style, mood, setting)
- cultural signals (fandoms, subcultures, references)
- a concise, specific trend name that best describes what this content is \
  part of. Do not force it into a predefined category — invent a new, \
  specific label if nothing existing fits.
- a broader trend category (e.g. "Sports x Lifestyle")
- a confidence score between 0 and 1. Be honest: if the content is \
  ambiguous, use a low score and a label like "Unclear / New Trend \
  Candidate" rather than a confident-sounding guess.
- concrete evidence (short observations) supporting your conclusion

Respond ONLY with a JSON object matching the required schema. Do not \
fabricate details that are not visible in the frames or present in the \
metadata.
"""


def build_user_prompt(caption: str, metadata_summary: str) -> str:
    return (
        "Reel caption:\n"
        f"{caption or '(no caption available)'}\n\n"
        "Additional metadata:\n"
        f"{metadata_summary}\n\n"
        "Analyze the attached frames and return the structured trend JSON."
    )
