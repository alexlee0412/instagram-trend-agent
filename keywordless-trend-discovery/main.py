import argparse

from config import DEFAULT_DURATION_SECONDS, OUTPUT_FILE


def parse_args():
    parser = argparse.ArgumentParser(
        description="Keywordless Trend Discovery MVP - collect visible Instagram Reels data."
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=DEFAULT_DURATION_SECONDS,
        help=f"Collection duration in seconds (default: {DEFAULT_DURATION_SECONDS})",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("Keywordless Trend Discovery MVP")
    print(f"Duration: {args.duration} seconds")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
