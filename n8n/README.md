# n8n Integration (Planned, Not Implemented)

This documents the intended **local** n8n integration for the trend-analysis engine. Nothing in this directory is wired up yet — no workflow file exists here. This is a design reference for when that gets built.

## Principle

n8n must never contain its own copy of the analysis logic. It only ever invokes the same CLI you can already run manually in a terminal:

```bash
python -m trend_analysis.analyze_reel "<REEL_URL>"
```

That command prints exactly one JSON object to stdout on success — all progress/error logs go to stderr — so n8n's "Execute Command" node can hand stdout straight to a JSON-parsing node without any special filtering.

## Planned flow

```
Manual Trigger / Form
        ↓
Reel URL
        ↓
Execute Command node:
  python -m trend_analysis.analyze_reel "{{ $json.reel_url }}"
        ↓
JSON node parses stdout
        ↓
Google Sheet / aggregation   (not yet implemented)
```

## Notes for future setup

- Run n8n locally, configured to execute Python from this project's virtual environment (the one with `yt-dlp`, `ffmpeg`, and whichever model provider dependency are installed).
- Exit code doubles as a coarse success signal: `0` on `analysis_status: "success"`, `1` on `"error"` — useful for an n8n error branch in addition to inspecting the JSON body.
- Any Instagram cookies the analyzer needs for restricted content are supplied via `TREND_ANALYSIS_COOKIES_FILE` in `.env`, never pasted into the n8n workflow itself.
- This integration is intentionally not built yet — see the main [README](../README.md) for what's implemented vs. planned in the trend-analysis engine itself.
