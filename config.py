import os

from dotenv import load_dotenv

load_dotenv()

INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD")

OUTPUT_FILE = "data/reels.xlsx"
SCREENSHOT_DIR = "screenshots"
BROWSER_DATA_DIR = "browser_data"
DEFAULT_DURATION_SECONDS = 300
