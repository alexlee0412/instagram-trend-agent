"""One-off utility: export Instagram cookies from the persistent Playwright
profile (browser_data/) into a Netscape-format cookie file that the
trend_analysis engine can optionally use (via TREND_ANALYSIS_COOKIES_FILE
in .env) for more reliable yt-dlp access.

This is NOT part of the trend_analysis package and trend_analysis does not
import it or Playwright — the analysis engine stays independent of the
collector at runtime. This script is only a convenience bridge to reuse an
already-authenticated browser_data/ session instead of logging in again.

Usage:
    python export_instagram_cookies.py [output_path]

Requires browser_data/ to already hold an authenticated session, i.e. you
have run `python main.py` at least once and completed login.
"""

import sys
import time

from playwright.sync_api import sync_playwright

from config import BROWSER_DATA_DIR

DEFAULT_OUTPUT_PATH = "instagram_cookies.txt"
COOKIE_DOMAIN_SUFFIX = "instagram.com"


def _to_netscape_lines(cookies: list) -> list:
    fallback_expiry = int(time.time()) + 86400 * 30  # 30 days; session cookies have no fixed expiry

    lines = ["# Netscape HTTP Cookie File"]
    for cookie in cookies:
        domain = cookie.get("domain", "")
        if COOKIE_DOMAIN_SUFFIX not in domain:
            continue

        expires = cookie.get("expires")
        expires = int(expires) if expires and expires > 0 else fallback_expiry

        lines.append("\t".join([
            domain,
            "TRUE" if domain.startswith(".") else "FALSE",
            cookie.get("path", "/"),
            "TRUE" if cookie.get("secure") else "FALSE",
            str(expires),
            cookie.get("name", ""),
            cookie.get("value", ""),
        ]))

    return lines


def main():
    output_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUTPUT_PATH

    print(f"[INFO] Reading existing session from {BROWSER_DATA_DIR}...", file=sys.stderr)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            BROWSER_DATA_DIR,
            headless=True,
        )
        cookies = context.cookies()
        context.close()

    instagram_cookie_count = sum(1 for c in cookies if COOKIE_DOMAIN_SUFFIX in c.get("domain", ""))
    if instagram_cookie_count == 0:
        print(
            "[ERROR] No Instagram cookies found in browser_data/. "
            "Run `python main.py` first and complete login.",
            file=sys.stderr,
        )
        sys.exit(1)

    lines = _to_netscape_lines(cookies)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"[INFO] Exported {instagram_cookie_count} Instagram cookie(s) to {output_path}", file=sys.stderr)
    print(f"[INFO] Set TREND_ANALYSIS_COOKIES_FILE={output_path} in .env to use it.", file=sys.stderr)


if __name__ == "__main__":
    main()
