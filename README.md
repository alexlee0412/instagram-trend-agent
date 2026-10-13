# Social Trend Analysis CLI

Analyze one public social-content URL with a shared multimodal trend pipeline:

```bash
./analyze.sh "<social-content-url>"
```

The command classifies and normalizes the URL, acquires public media and
metadata, prepares representative visuals, and asks the configured model for
one structured trend result.

## Supported content

| Platform | Content type | Status |
| --- | --- | --- |
| Instagram | Reel | ✅ Supported |
| Instagram | Single-image or video post | ✅ Supported |
| Instagram | Image or mixed-media carousel | ✅ Supported |
| TikTok | Public video | ✅ Supported |
| X / Twitter | Public video or animated GIF post | ✅ Supported |
| X / Twitter | Single-image or multi-image post | ✅ Supported |
| X / Twitter | Mixed-media post | ✅ Supported |
| X / Twitter | Text-only post | ⚠️ Detected, but no visual analysis |
| Other URLs | — | ❌ Unsupported |

Public acquisition is anonymous and best-effort. Private, deleted,
age-restricted, region-restricted, or login-gated content is not supported.

## Setup

Requirements:

- Python 3.12
- ffmpeg and ffprobe
- an OpenAI API key

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
brew install ffmpeg
cp .env.example .env
```

Set the following values in `.env`:

```env
TREND_MODEL_PROVIDER=openai
OPENAI_API_KEY=your_key_here
```

`OPENAI_TREND_MODEL` is optional. Blank or unset values use the default model.
Never commit `.env`, browser data, or exported cookies.

`analyze.sh` prefers the repository's `.venv/bin/python` and falls back to
`python3` only when that virtual environment does not exist.

## Usage

```bash
./analyze.sh "https://www.instagram.com/reel/<REEL_ID>/"
./analyze.sh "https://www.instagram.com/p/<POST_ID>/"
./analyze.sh "https://www.tiktok.com/@creator/video/<VIDEO_ID>"
./analyze.sh "https://x.com/creator/status/<STATUS_ID>"
./analyze.sh "https://twitter.com/creator/status/<STATUS_ID>"
```

Instagram tracking parameters are removed. TikTok video URLs are normalized,
recognized TikTok short links are resolved, and `twitter.com` status URLs are
normalized to `x.com`.

### CLI contract

- stdout: exactly one indented JSON object
- stderr: progress and diagnostic logs
- exit code `0`: `success` or `partial`
- non-zero exit code: unsupported input, acquisition failure, or analysis
  failure

A successful result has this stable top-level shape:

```json
{
  "platform": "instagram",
  "content_type": "reel",
  "content_url": "https://www.instagram.com/reel/<REEL_ID>/",
  "content_id": "<REEL_ID>",
  "analysis_input": {
    "visual": {
      "source_type": "video_frames",
      "ordered": true,
      "asset_count": 5,
      "media_composition": null,
      "item_types": []
    }
  },
  "metadata": {},
  "trend": {},
  "analysis_status": "success",
  "analysis_error": null
}
```

`content_type` describes the platform object (`reel`, `post`, or `video`).
`analysis_input.visual.source_type` describes what the model actually saw:

- `video_frames`: chronological samples from one continuous video
- `single_image`: one original image with no implied progression
- `carousel`: ordered post items, with composition and item types when known

`trend.content_format` is limited to reusable presentation, editing,
sequencing, or interaction structures. Topics, subjects, aesthetics, events,
audiences, and cultural meanings belong in the other trend fields.

## Architecture

```text
URL
  → URL classifier and canonicalizer
  → platform acquisition adapter
  → AcquiredMedia
  → visual preparation
  → VisualAsset[] + VisualContext
  → shared trend identifier
  → normalized JSON response
```

Key modules:

- `trend_analysis/analyze_url.py`: routing and pipeline orchestration
- `trend_analysis/url_classifier.py`: URL validation and normalization
- `trend_analysis/reel_loader.py`: Instagram acquisition
- `trend_analysis/platforms/`: TikTok and X acquisition adapters
- `trend_analysis/media.py`: platform-neutral acquired-media contract
- `trend_analysis/visual_preparation.py`: frames, images, and carousel handling
- `trend_analysis/trend_identifier.py`: provider-independent model boundary
- `trend_analysis/openai_provider.py`: OpenAI request and result validation
- `trend_analysis/output.py`: public response and metadata normalization

Platform loaders own extractor- and endpoint-specific behavior. The visual
preparation and model layers do not branch on platform names.

## Acquisition behavior

- Instagram uses current yt-dlp first and a logged-out public embed fallback.
- TikTok public videos use anonymous yt-dlp.
- X video posts use yt-dlp where possible. X's public syndication response
  provides post metadata, images, ordered media, and a direct-video fallback.
- No normal public path requires cookies, Playwright, a browser profile, or
  manual authentication.
- Missing public metrics are returned as `null`; values are never guessed.

Video content currently contributes five representative frames. Carousels
contribute up to six ordered assets; each selected video item contributes one
representative frame. If some assets fail but usable input remains, the result
is marked `partial`.

## Tests

```bash
.venv/bin/python -m compileall -q trend_analysis tests
.venv/bin/python -m unittest discover -s tests -v
sh -n analyze.sh
git diff --check
```

Network behavior is mocked in automated tests. Live platform checks are still
useful because public endpoints and extractor behavior can change without
notice.

## Known limitations

- Platform page formats and anonymous endpoints can change independently of
  this project.
- TikTok short links must resolve to a canonical public video URL.
- X text-only posts return `analysis_status: "unsupported"` because the
  current analyzer requires visual input.
- Some public metadata is unavailable anonymously.
- Trend identification describes one content item. It does not measure
  virality or prove that a trend is spreading across creators.

## Legacy collector

`main.py`, `scraper.py`, `enrichment.py`, `excel_writer.py`, and the root
`config.py` belong to an older Playwright-based Instagram discovery prototype.
They are intentionally separate from `./analyze.sh` and are not part of the
URL-analysis pipeline. Their dependencies remain in `requirements.txt` for
compatibility.

## Security

`.env`, `browser_data/`, and cookie exports contain credentials or session
data and are ignored by Git. Keep API keys in `.env`; do not put them in
commands, logs, source files, or chat messages.
