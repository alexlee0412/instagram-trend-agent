import argparse

from config import DEFAULT_DURATION_SECONDS
from scraper import open_authenticated_instagram


def parse_args():
    parser = argparse.ArgumentParser(
        description="Keywordless Trend Discovery MVP - collect visible Instagram Reels data."
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=DEFAULT_DURATION_SECONDS,
        help=f"Reels viewing duration in seconds (default: {DEFAULT_DURATION_SECONDS}).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    open_authenticated_instagram(args.duration)


if __name__ == "__main__":
    main()
