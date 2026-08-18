# Keywordless Trend Discovery

Two independent local proofs of concept:

1. **Discovery** — a Playwright-based collector that samples Instagram's recommendation feed without predefined keywords and structures what it observes.
2. **Trend Identification** — a standalone engine that takes a single Reel or post URL (from anywhere — the collector, a manual paste, a future n8n/Apify/Sheet source) and answers "what trend does this content appear to belong to?"

These are separate concepts, deliberately not automated together yet:

- **Discovery** determines *what content is surfacing*.
- **Trend Identification** determines *what trend that content represents*.
- **Future aggregation** (not built) would determine *whether that trend is actually spreading* — that requires many labeled Reels over time (creator diversity, post frequency, growth velocity), not a single Reel. Don't confuse trend identification with virality detection.

## 1. Discovery (implemented)

```text
Chromium / Instagram Reels
        ↓
Playwright Collector
        ↓
Structured Reel Records + yt-dlp enrichment
        ↓
data/reels.xlsx
```

### Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

Copy `.env.example` to `.env` and fill in your Instagram credentials:

```bash
cp .env.example .env
```

### Run

```bash
python main.py --duration 300
```

This requires zero AI/model setup — it only needs Playwright + yt-dlp, both installed via `requirements.txt`.

## 2. Trend Identification (implemented — Reels and posts, OpenAI provider live)

Accepts both Reel URLs (`.../reel(s)/<ID>/`) and post URLs (`.../p/<ID>/` — single image, carousel, or video). Both feed the same model boundary through a shared "ordered visual assets" abstraction, so the model never needs to know whether an image came from a sampled video frame, a single post image, or a carousel slide:

```text
Instagram Reel or Post URL
        ↓
validate_content_url()  →  "reel" or "post"
        ↓                              ↓
   Reel / video post            image or carousel post
        ↓                              ↓
yt-dlp (metadata + temp video)   yt-dlp (metadata) + direct
        ↓                        authenticated media fetch
ffmpeg (representative frames)         ↓
        ↓                        original image(s), or one
        └──────────┬───────────  midpoint frame per video slide
                   ↓
        ordered visual assets (image paths)
                   ↓
        OpenAI multimodal trend identifier (gpt-5.6-terra)
                   ↓
        Structured Trend JSON
```

### Setup

```bash
brew install ffmpeg   # required for frame extraction — confirmed working
```

In `.env`, set:

```env
TREND_MODEL_PROVIDER=openai
OPENAI_API_KEY=your_key_here
```

For more reliable fetching (Instagram rate-limits/gates anonymous access), reuse your already-authenticated Playwright session:

```bash
python main.py              # once, to log in and establish browser_data/
python export_instagram_cookies.py   # exports a temp cookie file for trend_analysis
```

Then set `TREND_ANALYSIS_COOKIES_FILE=instagram_cookies.txt` in `.env`. This is optional but recommended — without it, expect frequent login-wall/rate-limit failures on real Instagram content.

### Run

```bash
# Reel
python -m trend_analysis.analyze_reel "https://www.instagram.com/reels/<REEL_ID>/"

# Post (image, carousel, or video)
python -m trend_analysis.analyze_reel "https://www.instagram.com/p/<POST_ID>/"
```

Prints one JSON object to stdout (all logs go to stderr, so this is safe to pipe/parse — e.g. from n8n, see [`n8n/README.md`](n8n/README.md)). Exit code is `0` unless `analysis_status` is `"error"`.

### What's implemented right now

- Full pipeline for both Reels and posts: URL validation, yt-dlp-based metadata/media fetch, ffmpeg-based frame extraction, carousel handling, temp-file cleanup, and the CLI — all verified against real Instagram content.
- The **OpenAI provider is implemented and live** (`trend_analysis/openai_provider.py`), using the Responses API with strict JSON-schema structured output. Model: `gpt-5.6-terra` by default (override via `OPENAI_TREND_MODEL`; verified against OpenAI's official docs, since model naming has moved past the `gpt-4o` era).
- Claude and Ollama remain unimplemented stubs in `trend_analysis/trend_identifier.py` — swapping to either means implementing that one function, nothing else changes.
- Carousel posts: resolvable slides (up to `MAX_VISUAL_ASSETS = 6`) are sent in original order, deterministically sampled (first + last + evenly spaced) when a carousel has more slides than that.

### Known limitation: mixed image+video carousels

Confirmed empirically (not a guess): yt-dlp's current Instagram extractor cannot resolve image URLs for **non-video slides inside a carousel that also contains video** — those slides come back with no usable media at any level (checked three different extraction modes). The pipeline handles this gracefully — unresolvable slides are skipped, logged, and the result is marked `analysis_status: "partial"` rather than failing the whole post or fabricating data — but it means a mixed carousel is currently analyzed only from its video slides. Pure image-only posts/carousels are expected to work but haven't been separately verified yet.

### Not implemented (future layers)

Batch/multi-Reel aggregation, virality scoring, an embedding/vector database, clustering (CLIP/HDBSCAN/etc.), Google Sheets/Docs integration, the actual n8n workflow, scheduled runs, and automatic collector → analyzer chaining (`main.py` never calls the analyzer automatically).

## Security note

`.env` and the `browser_data/` directory contain credentials and session data. Never commit them — both are excluded via `.gitignore`. `python export_instagram_cookies.py` writes a plaintext session-cookie file (default `instagram_cookies.txt`) for the optional `TREND_ANALYSIS_COOKIES_FILE` setting — also gitignored, also never commit it. Never paste `OPENAI_API_KEY` or cookie values into a terminal command, log, or chat — only `.env`.
