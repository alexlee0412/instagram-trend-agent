# Keywordless Trend Discovery

A local Playwright-based collector for sampling recommendation feeds without predefined keywords and structuring observed content for trend analysis.

## Architecture

```text
Chromium / Instagram Reels
        ↓
Playwright Collector
        ↓
Structured Reel Records
        ↓
data/reels.xlsx
```

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

Copy `.env.example` to `.env` and fill in your Instagram credentials:

```bash
cp .env.example .env
```

## Run

```bash
python main.py --duration 300
```

## Security note

`.env` and the `browser_data/` directory contain credentials and session data. Never commit them — both are excluded via `.gitignore`.
