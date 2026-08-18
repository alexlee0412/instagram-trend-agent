# Keywordless Trend Discovery

Two independent local proofs of concept:

1. **Discovery** — a Playwright-based collector that samples Instagram's recommendation feed without predefined keywords and structures what it observes.
2. **Trend Identification** — a standalone engine that takes a single Reel URL (from anywhere — the collector, a manual paste, a future n8n/Apify/Sheet source) and answers "what trend does this Reel appear to belong to?"

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

## 2. Trend Identification (implemented pipeline; model provider not yet configured)

```text
Reel URL
        ↓
yt-dlp (metadata + temporary video)
        ↓
ffmpeg (a few representative frames, extracted to a temp dir)
        ↓
Trend Identification model  ← NOT YET CONFIGURED, see below
        ↓
Structured Trend JSON
```

### Run

```bash
python -m trend_analysis.analyze_reel "https://www.instagram.com/reels/<REEL_ID>/"
```

Prints one JSON object to stdout (all logs go to stderr, so this is safe to pipe/parse — e.g. from n8n, see [`n8n/README.md`](n8n/README.md)).

### What's implemented right now

- URL validation, yt-dlp-based metadata + temporary video fetch, ffmpeg-based frame extraction, temp-file cleanup, the output schema, and the CLI — all runnable and testable without any AI model.
- The model-calling boundary (`trend_analysis/trend_identifier.py`) exists and is where a real multimodal call plugs in — but **no provider is implemented yet**. Until `TREND_MODEL_PROVIDER` is set *and* implemented, `analyze_reel` returns a clean `analysis_status: "error"` with an actionable message rather than a fabricated trend.

### Prerequisites for a live run

- **ffmpeg** — required for frame extraction. Not currently installed on this machine; install with e.g. `brew install ffmpeg`.
- **A configured model provider** — set `TREND_MODEL_PROVIDER` in `.env` to `ollama`, `openai`, or `claude`, and implement/enable that provider in `trend_analysis/trend_identifier.py`. Not done yet — this is a deliberate stopping point pending a model decision (local Ollama vs. hosted API), since it involves either a multi-GB model download or API credentials.

### Not implemented (future layers)

Batch/multi-Reel aggregation, virality scoring, an embedding/vector database, clustering (CLIP/HDBSCAN/etc.), Google Sheets/Docs integration, the actual n8n workflow, scheduled runs, and automatic collector → analyzer chaining (`main.py` never calls the analyzer automatically).

## Security note

`.env` and the `browser_data/` directory contain credentials and session data. Never commit them — both are excluded via `.gitignore`. The optional `TREND_ANALYSIS_COOKIES_FILE` (for the trend-identification engine) points to a cookie file that must also stay outside version control.
