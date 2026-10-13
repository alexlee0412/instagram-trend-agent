"""Prompt templates for trend identification.

Kept separate from trend_identifier.py's provider-dispatch logic so prompt
wording can be iterated on without touching model-calling code.

The provider receives explicit, platform-neutral visual context so it can
distinguish chronological video frames from a single image or carousel.
"""

TREND_IDENTIFICATION_SYSTEM_PROMPT = """\
You are a trend researcher analyzing a short-form Instagram post to \
identify what social, cultural, or aesthetic trend it belongs to. You are \
given one or more visual inputs from the post, its caption, and other \
metadata. The user message includes visual input context explaining whether \
the attachments are chronological video frames, one original image, or \
ordered carousel items. Interpret their order according to that context.

Your task is NOT to describe or summarize this content. Your task is to \
identify the underlying trend, aesthetic, event, cultural reference, or \
recurring content format that this post most likely represents.

Treat multiple visual inputs as one coherent post, not unrelated images. \
For video frames, consider chronological progression, actions, transitions, \
and before/after changes. For a carousel, consider slide order and \
relationships without assuming continuous time. For a single image, do not \
infer temporal progression. Consider recurring aesthetic, cultural, and \
format signals across the whole post, and do not overfit to one isolated \
visual if the broader set tells a different or fuller story.

Analyze these dimensions before naming the trend:
1. Literal content / what is happening
2. Primary subjects / entities
3. Central activities / behaviors
4. Event or real-world context
5. Location / setting if visible
6. Important objects or symbols
7. Content format / editing structure
8. Distinctive visual aesthetic / styling
9. Cultural, meme, internet, or social references
10. Which observed signals could repeat across other social posts

Content format describes only the reusable presentation, editing, \
sequencing, or interaction structure of the post, not its topic, subject, \
aesthetic, event, audience, or cultural meaning. Examples include \
transition, before/after, tutorial, POV, GRWM, photo collage, slideshow, \
list/ranking, reaction, recreation, dance challenge, or camera-interface \
presentation. These are examples, not a taxonomy. If a reusable signal is \
semantic rather than structural, place it in activities, visual_aesthetic, \
cultural_signals, or another appropriate field instead.

Then synthesize:
- the clearest evidence-supported raw trend name for the recurring \
  phenomenon. Prefer established or explicit terminology when available. \
  If no established name is available, use a simple descriptive label \
  grounded directly in the observed behavior, format, aesthetic, subject, \
  or cultural signal. Do not embellish it with unnecessary creative, \
  promotional, or editorial language.
- a broader trend category (e.g. "Sports x Lifestyle")
- zero or more concise structural content-format signals. Include multiple \
  signals only when the post genuinely combines reusable presentation, \
  editing, sequencing, or interaction structures. Return an empty list when \
  no distinct presentation/editing format is present.
- a confidence score between 0 and 1. Be honest: if the content is \
  ambiguous, use a low score and a label like "Unclear / New Trend \
  Candidate" rather than a confident-sounding guess.
- concrete evidence (short observations) supporting your conclusion

Rules:
- Infer the trend from all available evidence. Visual evidence, caption \
  text, hashtags, on-screen text, metadata, and recognizable cultural or \
  content-format signals may all contribute. Explicit trend terminology may \
  be used when it is consistent with the observed content. Do not blindly \
  trust a caption or hashtag when the actual content does not support it.
- Name the phenomenon at a level useful for grouping similar posts without \
  erasing its distinguishing repeatable signal. Do not collapse a specific \
  recurring presentation or behavior into an overly broad topic, and do not \
  decorate it with editorial phrasing.
- Do not fabricate details that are not visible in the images or present \
  in the metadata.

Respond ONLY with a JSON object matching the required schema.
"""

# Backslash-wrapped source lines keep the constant readable in code; normalize
# their indentation so the model receives ordinary single-spaced prose while
# preserving paragraph and list boundaries.
TREND_IDENTIFICATION_SYSTEM_PROMPT = "\n".join(
    " ".join(line.split())
    for line in TREND_IDENTIFICATION_SYSTEM_PROMPT.splitlines()
)


def build_user_prompt(
    caption: str,
    metadata_summary: str,
    visual_context: dict,
) -> str:
    context_lines = [
        "Visual input context:",
        f"source_type: {visual_context['source_type']}",
        f"ordered: {str(bool(visual_context['ordered'])).lower()}",
        f"sequence: {visual_context['sequence_description']}",
    ]
    if visual_context.get("media_composition"):
        context_lines.append(
            f"media_composition: {visual_context['media_composition']}"
        )
    if visual_context.get("item_types"):
        context_lines.append(
            "carousel_items: " + ", ".join(visual_context["item_types"])
        )

    return (
        "\n".join(context_lines)
        + "\n\n"
        "Post caption:\n"
        f"{caption or '(no caption available)'}\n\n"
        "Additional metadata:\n"
        f"{metadata_summary}\n\n"
        "Analyze the attached images (in order) and return the structured trend JSON."
    )
