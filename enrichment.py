"""Post-collection metadata enrichment via yt-dlp's Python API.

Runs after the Playwright discovery/collection loop finishes — never during
the 6-second browsing interval. Enriches only the Reel rows newly appended
during this run, filling caption/posted_at/view_count/like_count/comment_count
when currently blank. Never downloads video/media, never touches
reel_url/reel_id/username (Playwright already collects those reliably), and
never fabricates values: a failed or partial yt-dlp response just leaves the
enrichment cells blank and moves on. Authentication is reused from the
already-open Playwright context via a temporary, auto-deleted cookie file —
no persistent credentials are written to disk.
"""

import os
import tempfile
import time
from datetime import datetime, timezone
from typing import Optional

from yt_dlp import YoutubeDL

import excel_writer
from config import ENRICHMENT_SOCKET_TIMEOUT_SECONDS

INSTAGRAM_COOKIE_DOMAIN_SUFFIX = "instagram.com"
ENRICHMENT_FIELDS = ("caption", "posted_at", "view_count", "like_count", "comment_count")


class _SilentLogger:
    """Swallows yt-dlp's own debug/warning/error output — our own [INFO]/
    [WARN] logs are printed separately and are unaffected.
    """

    def debug(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def _export_instagram_cookies(context) -> str:
    """Write a temporary Netscape-format cookie file containing only
    Instagram-domain cookies from the authenticated Playwright context.
    Never logs cookie names/values. Caller must delete the returned path.
    """
    cookies = context.cookies()

    fd, path = tempfile.mkstemp(prefix="ig_enrich_cookies_", suffix=".txt")
    fallback_expiry = int(time.time()) + 86400  # session cookies have no fixed expiry

    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for cookie in cookies:
            domain = cookie.get("domain", "")
            if INSTAGRAM_COOKIE_DOMAIN_SUFFIX not in domain:
                continue

            expires = cookie.get("expires")
            expires = int(expires) if expires and expires > 0 else fallback_expiry

            f.write("\t".join([
                domain,
                "TRUE" if domain.startswith(".") else "FALSE",
                cookie.get("path", "/"),
                "TRUE" if cookie.get("secure") else "FALSE",
                str(expires),
                cookie.get("name", ""),
                cookie.get("value", ""),
            ]) + "\n")

    return path


def _cleanup_cookie_file(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


def _build_ydl_options(cookie_file: str) -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "skip_download": True,
        "cookiefile": cookie_file,
        "socket_timeout": ENRICHMENT_SOCKET_TIMEOUT_SECONDS,
        "extractor_retries": 0,
        "retries": 0,
        "fragment_retries": 0,
        "logger": _SilentLogger(),
    }


def _timestamp_to_iso8601(timestamp) -> Optional[str]:
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return None


def _map_info_to_updates(info: dict) -> dict:
    """Map yt-dlp's info dict onto our XLSX schema. Only the fields this
    project uses yt-dlp for — never reel_url/reel_id/username, and never
    audio_name (yt-dlp doesn't expose Instagram audio/music names).
    """
    if info.get("_type") == "playlist":
        info = (info.get("entries") or [{}])[0] or {}

    return {
        "caption": info.get("description") or None,
        "posted_at": _timestamp_to_iso8601(info.get("timestamp")),
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "comment_count": info.get("comment_count"),
    }


def _extract_reel_metadata(reel_url: str, ydl_opts: dict) -> Optional[dict]:
    """One best-effort, non-retrying yt-dlp extraction attempt. Never
    downloads media (download=False). Returns None on any failure.
    """
    try:
        with YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(reel_url, download=False)
    except Exception:
        return None


def enrich_collected_reels(context, new_reels_this_run: list, output_file: str):
    """Phase 2: enrich only the Reel rows newly appended during this run.
    Must never raise or block the collector — every failure is caught,
    logged as a single concise warning, and skipped.
    """
    if not new_reels_this_run:
        return

    total = len(new_reels_this_run)
    print(f"[INFO] Starting metadata enrichment for {total} newly collected Reels...")

    cookie_file = None
    enriched_count = 0
    failed_count = 0

    try:
        cookie_file = _export_instagram_cookies(context)
        ydl_opts = _build_ydl_options(cookie_file)

        workbook, sheet = excel_writer.open_workbook_for_update(output_file)

        for index, reel in enumerate(new_reels_this_run, start=1):
            reel_url = reel["reel_url"]
            try:
                info = _extract_reel_metadata(reel_url, ydl_opts)
                if info is None:
                    raise ValueError("no metadata returned")

                row_num = excel_writer.find_row_by_reel_url(sheet, reel_url)
                if row_num is None:
                    raise ValueError("row not found for reel_url")

                updates = _map_info_to_updates(info)
                excel_writer.apply_enrichment_updates(sheet, row_num, updates)
            except Exception:
                print(f"[WARN] Metadata enrichment failed for Reel {index}/{total}")
                failed_count += 1
                continue

            enriched_count += 1
            print(f"[INFO] Enriched Reel {index}/{total}")

        workbook.save(output_file)
    except Exception:
        print("[WARN] Metadata enrichment pass encountered an unexpected error")
    finally:
        if cookie_file:
            _cleanup_cookie_file(cookie_file)

    print("[INFO] Metadata enrichment complete")
    print(f"[INFO] Enriched: {enriched_count}")
    print(f"[INFO] Failed: {failed_count}")
