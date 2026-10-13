"""Configuration for the trend-analysis engine.

Deliberately independent of the root config.py — per the architecture
principle, this engine must work from a bare Reel URL regardless of source
(Playwright, Apify, manual input, a spreadsheet, n8n) and must not depend
on the Playwright collector's live browser session to function.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# Optional: path to a manually-exported Netscape-format cookie file, used
# only if set, to improve yt-dlp's Reel-fetch reliability for restricted
# content. Never required — public Reels work without it.
INSTAGRAM_COOKIES_FILE = os.getenv("TREND_ANALYSIS_COOKIES_FILE") or None

YDL_SOCKET_TIMEOUT_SECONDS = 15

FRAME_COUNT = 5

# Carousel posts: cap how many slides get sent to the model. If a carousel
# has more usable slides than this, deterministically sample first + last +
# evenly-spaced interior slides (never random) so the same post always
# selects the same slides.
MAX_VISUAL_ASSETS = 6

# Which multimodal model provider identify_trend() should use.
# OpenAI is the only implemented provider, so it is also the default. This
# keeps a copied older .env (where TREND_MODEL_PROVIDER may be blank) working
# with the documented root CLI as long as OPENAI_API_KEY is configured.
TREND_MODEL_PROVIDER = (
    (os.getenv("TREND_MODEL_PROVIDER") or "").strip().lower() or "openai"
)
