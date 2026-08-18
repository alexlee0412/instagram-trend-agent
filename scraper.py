"""Instagram authentication and Reels collection stage.

Uses Playwright's synchronous API to open Chromium with a persistent
profile (browser_data/) so login state survives between runs, then
auto-advances through the Reels feed at a fixed interval, extracting only
visibly-available metadata for each unique Reel and appending it to
data/reels.xlsx. Once the browsing loop finishes, hands the newly collected
Reel URLs to enrichment.py for a post-collection yt-dlp metadata pass. No
AI/visual analysis happens here.
"""

import os
import re
import time
from datetime import datetime
from typing import Optional

from playwright.sync_api import ElementHandle, Locator, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from config import (
    BROWSER_DATA_DIR,
    INSTAGRAM_PASSWORD,
    INSTAGRAM_USERNAME,
    OUTPUT_FILE,
    REEL_INTERVAL_SECONDS,
    REELS_URL,
    SCREENSHOT_DIR,
)
from enrichment import enrich_collected_reels
from excel_writer import append_row, load_existing_reel_urls, load_max_discovery_rank

LOGIN_URL = "https://www.instagram.com/accounts/login/"
LOGIN_DEBUG_SCREENSHOT = os.path.join(SCREENSHOT_DIR, "login_debug.png")
LOGIN_SUBMIT_DEBUG_SCREENSHOT = os.path.join(SCREENSHOT_DIR, "login_submit_debug.png")
VIEWPORT = {"width": 1280, "height": 900}
LOGIN_FIELD_TIMEOUT_MS = 15000
LOGIN_SUBMIT_ENTER_TIMEOUT_MS = 10000
LOGIN_SUBMIT_FALLBACK_TIMEOUT_MS = 15000
REEL_ADVANCE_CHECK_MS = 1500

SOURCE_SURFACE = "Instagram Reels"

# The currently playing Reel's video player — confirmed via live DOM
# inspection; there is no /reel/ or /reels/ permalink anchor in the DOM.
VIDEO_PLAYER_SELECTOR = '[aria-label="Video player"][role="group"]'
CONTAINER_WALK_MAX_LEVELS = 12

# The active Reel's canonical URL/id come from page.url itself, e.g.
# https://www.instagram.com/reels/Db_JMSvsA29/ (plural "reels" is what
# Instagram actually uses while browsing; singular "reel" is tolerated too).
REEL_URL_PATTERN = re.compile(r"/(reels?)/([^/?#]+)/?")

# Creator/profile links are shaped like href="/<username>/reels/".
USERNAME_HREF_PATTERN = re.compile(r"^/([^/]+)/reels/?$")

EXCLUDED_USERNAME_PATHS = {
    "reels",
    "reel",
    "explore",
    "direct",
    "accounts",
    "p",
    "stories",
}

CAPTION_EXCLUDE_EXACT = {"follow", "following", "more", "... more", "… more"}
CAPTION_EXCLUDE_PATTERNS = [
    re.compile(r"^\.\.\.\s*more$", re.I),
    re.compile(r"^…\s*more$", re.I),
    re.compile(r"^follow(ing)?$", re.I),
]
# Instagram appends a trailing "... more" / "… more" to expandable captions;
# strip it rather than discarding the whole (real) caption.
MORE_SUFFIX_PATTERN = re.compile(r"\s*(?:\.\.\.|…)\s*more\s*$", re.I)

COUNT_PATTERN = re.compile(r"([\d,]*\.?\d+)\s*([kmb])?", re.I)
LIKE_LABEL_PATTERN = re.compile(r"[\d,.]+\s*[kmb]?\s*likes?", re.I)
COMMENT_LABEL_PATTERN = re.compile(r"[\d,.]+\s*[kmb]?\s*comments?", re.I)
VIEW_LABEL_PATTERN = re.compile(r"[\d,.]+\s*[kmb]?\s*(?:views?|plays?)", re.I)

# Elements only present once the authenticated home UI has loaded.
LOGGED_IN_SELECTORS = [
    'svg[aria-label="Home"]',
    'a[href="/direct/inbox/"]',
]

# Instagram routes these challenge/verification flows to distinct URLs.
CHALLENGE_URL_KEYWORDS = ["challenge", "checkpoint", "two_factor", "suspicious"]

COOKIE_DISMISS_LABELS = ["Allow all cookies", "Allow essential and optional cookies"]


def _dismiss_cookie_banner(page: Page):
    for label in COOKIE_DISMISS_LABELS:
        try:
            page.get_by_role("button", name=label).click(timeout=1500)
            return
        except PlaywrightTimeoutError:
            continue


def _element_visible(page: Page, selector: str, timeout: int = 4000) -> bool:
    try:
        page.wait_for_selector(selector, state="visible", timeout=timeout)
        return True
    except PlaywrightTimeoutError:
        return False


def _is_logged_in(page: Page) -> bool:
    return any(_element_visible(page, selector) for selector in LOGGED_IN_SELECTORS)


def _is_challenge(page: Page) -> bool:
    url = page.url.lower()
    return any(keyword in url for keyword in CHALLENGE_URL_KEYWORDS)


def _detect_state(page: Page) -> str:
    if _is_challenge(page):
        return "challenge"
    if _is_logged_in(page):
        return "authenticated"
    return "logged_out"


def _username_candidates(page: Page):
    return [
        lambda: page.locator('input[name="username"]'),
        lambda: page.locator('input[autocomplete="username"]'),
        lambda: page.get_by_label(re.compile("username", re.I)),
        lambda: page.get_by_placeholder(re.compile("username|phone|email", re.I)),
    ]


def _password_candidates(page: Page):
    return [
        lambda: page.locator('input[name="password"]'),
        lambda: page.locator('input[type="password"]'),
        lambda: page.get_by_label(re.compile("password", re.I)),
    ]


def _first_visible_locator(candidates) -> Optional[Locator]:
    for make_locator in candidates:
        try:
            locator = make_locator()
            if locator.count() > 0 and locator.first.is_visible():
                return locator.first
        except Exception:
            continue
    return None


def _locate_login_fields(page: Page, timeout_ms: int = LOGIN_FIELD_TIMEOUT_MS):
    """Poll fallback candidate locators until both fields appear or the
    timeout elapses, since Instagram doesn't always expose the same
    selector for the login form.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    username_field = None
    password_field = None

    while time.monotonic() < deadline:
        if username_field is None:
            username_field = _first_visible_locator(_username_candidates(page))
        if password_field is None:
            password_field = _first_visible_locator(_password_candidates(page))

        if username_field is not None and password_field is not None:
            break

        page.wait_for_timeout(300)

    return username_field, password_field


def _save_debug_screenshot(page: Page, path: str):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    try:
        page.screenshot(path=path)
    except Exception:
        pass


def _login_button_candidates(page: Page):
    login_pattern = re.compile(r"log\s*in", re.I)
    return [
        lambda: page.get_by_role("button", name=login_pattern),
        lambda: page.get_by_text(login_pattern, exact=False),
    ]


def _poll_for_state_change(page: Page, timeout_ms: int) -> str:
    """Poll authentication state rather than waiting on a navigation event,
    since Instagram may update the page client-side after login.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    state = _detect_state(page)

    while state == "logged_out" and time.monotonic() < deadline:
        page.wait_for_timeout(500)
        state = _detect_state(page)

    return state


def _submit_login(page: Page, password_field: Locator) -> str:
    """Submit the login form and poll for the resulting state.

    Tries pressing Enter from the password field first, then falls back to
    a semantically-located visible login control. Submission errors are
    caught locally so a Playwright TimeoutError here never crashes the run.
    """
    try:
        password_field.press("Enter")
    except Exception:
        pass

    state = _poll_for_state_change(page, LOGIN_SUBMIT_ENTER_TIMEOUT_MS)

    if state != "logged_out":
        return state

    print("[DEBUG] Enter submission did not complete login; trying visible login control...")

    control = _first_visible_locator(_login_button_candidates(page))
    if control is not None:
        try:
            control.click(timeout=5000)
        except Exception:
            pass
        state = _poll_for_state_change(page, LOGIN_SUBMIT_FALLBACK_TIMEOUT_MS)

    if state == "logged_out":
        _save_debug_screenshot(page, LOGIN_SUBMIT_DEBUG_SCREENSHOT)
        print("[ERROR] Login submission did not complete")

    return state


def _perform_login(page: Page) -> str:
    print("[INFO] Attempting login...")

    username_field, password_field = _locate_login_fields(page)

    if username_field is None or password_field is None:
        _save_debug_screenshot(page, LOGIN_DEBUG_SCREENSHOT)
        print(f"[DEBUG] Current URL: {page.url}")
        print("[ERROR] Instagram login form could not be located.")
        print(f"[ERROR] Debug screenshot saved to {LOGIN_DEBUG_SCREENSHOT}")
        return "form_not_found"

    print(f"[DEBUG] Current URL: {page.url}")
    print(f"[DEBUG] Page title: {page.title()}")

    username_field.fill(INSTAGRAM_USERNAME)
    password_field.fill(INSTAGRAM_PASSWORD)

    state = _submit_login(page, password_field)

    if state == "logged_out":
        return "submission_failed"

    return state


def _wait_for_manual_completion(page: Page, message: str) -> str:
    print(f"[INFO] {message}")
    print("[INFO] Please complete it manually in the browser window.")
    input("Press Enter here once you have finished in the browser...\n")

    page.wait_for_timeout(2000)
    return _detect_state(page)


def _handle_challenge(page: Page) -> str:
    return _wait_for_manual_completion(
        page,
        "Instagram is requesting additional verification "
        "(CAPTCHA, checkpoint, suspicious-login warning, or two-factor code).",
    )


def _get_active_reel_signal(page: Page) -> str:
    """Lightweight signal for whether the visible Reel changed. Instagram
    updates page.url as ArrowDown advances between Reels (there is no
    /reel/ or /reels/ permalink anchor in the DOM), so the URL itself is
    the signal — not metadata extraction, just enough to confirm the feed
    advanced.
    """
    return page.url


def _advance_reel(page: Page) -> bool:
    before = _get_active_reel_signal(page)

    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(REEL_ADVANCE_CHECK_MS)

    if _get_active_reel_signal(page) != before:
        return True

    print("[DEBUG] ArrowDown did not advance Reel; trying mouse wheel...")
    page.mouse.wheel(0, 800)
    page.wait_for_timeout(REEL_ADVANCE_CHECK_MS)

    if _get_active_reel_signal(page) != before:
        return True

    print("[WARN] Could not confirm Reel advancement")
    return False


def normalize_count(raw: Optional[str]) -> Optional[int]:
    """Convert visible engagement text ("1,234", "1.2K", "3M") to an int.
    Returns None when the value is missing or not straightforwardly parseable.
    """
    if not raw:
        return None

    match = COUNT_PATTERN.search(raw.replace(",", ""))
    if not match:
        return None

    number_part, suffix = match.groups()
    try:
        number = float(number_part)
    except ValueError:
        return None

    multipliers = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    multiplier = multipliers.get((suffix or "").upper())
    if multiplier is None:
        return None

    return int(round(number * multiplier))


def _now_iso8601() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_reel_url(url: str):
    """Parse the canonical reel_url/reel_id straight from page.url — the
    active Reel's permalink is the page URL itself, not a DOM anchor.
    """
    match = REEL_URL_PATTERN.search(url)
    if not match:
        return None

    prefix, reel_id = match.groups()
    if not reel_id:
        return None

    return f"https://www.instagram.com/{prefix}/{reel_id}/", reel_id


def _find_active_video_player(page: Page) -> Optional[Locator]:
    """Among all mounted video players, pick the visible one whose bounding
    box is closest to the viewport's vertical center — Instagram keeps
    neighboring Reels' players mounted off-screen.
    """
    players = page.locator(VIDEO_PLAYER_SELECTOR)
    try:
        count = players.count()
    except Exception:
        return None

    if count == 0:
        return None

    viewport = page.viewport_size or VIEWPORT
    viewport_center = viewport["height"] / 2

    best_locator = None
    best_distance = None

    for i in range(count):
        player = players.nth(i)
        try:
            if not player.is_visible():
                continue
            box = player.bounding_box()
        except Exception:
            continue
        if not box:
            continue

        center_y = box["y"] + box["height"] / 2
        if center_y < 0 or center_y > viewport["height"]:
            continue

        distance = abs(center_y - viewport_center)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_locator = player

    return best_locator


def _get_reel_container_handle(video_player: Locator) -> Optional[ElementHandle]:
    """Walk up from the video player until finding the nearest ancestor
    that also contains a creator link (a[href$="/reels/"]) — the smallest
    reasonable container for the current Reel's UI content.
    """
    try:
        player_handle = video_player.element_handle()
    except Exception:
        return None
    if player_handle is None:
        return None

    try:
        container_handle = player_handle.evaluate_handle(
            """(playerEl, maxLevels) => {
                let node = playerEl;
                let depth = 0;
                while (node && depth <= maxLevels) {
                    if (node.querySelector && node.querySelector('a[href$="/reels/"]')) {
                        return node;
                    }
                    node = node.parentElement;
                    depth += 1;
                }
                return playerEl;
            }""",
            CONTAINER_WALK_MAX_LEVELS,
        )
    except Exception:
        return player_handle

    element = container_handle.as_element()
    return element if element is not None else player_handle


def _extract_username_from_container(container: ElementHandle) -> Optional[str]:
    try:
        links = container.query_selector_all('a[href$="/reels/"]')
    except Exception:
        return None

    for link in links[:20]:
        try:
            if not link.is_visible():
                continue
            href = link.get_attribute("href")
        except Exception:
            continue

        match = USERNAME_HREF_PATTERN.match(href) if href else None
        if not match:
            continue

        username = match.group(1)
        if username.lower() in EXCLUDED_USERNAME_PATHS:
            continue

        return username

    return None


def _strip_more_suffix(text: str) -> str:
    return MORE_SUFFIX_PATTERN.sub("", text).strip()


def _is_excluded_caption_text(text: str, username: Optional[str]) -> bool:
    normalized = text.strip()
    if not normalized:
        return True
    if username and normalized.lower() == username.lower():
        return True

    lowered = normalized.lower()
    if lowered in CAPTION_EXCLUDE_EXACT:
        return True

    return any(pattern.match(normalized) for pattern in CAPTION_EXCLUDE_PATTERNS)


def _extract_caption_from_container(container: ElementHandle, username: Optional[str]) -> Optional[str]:
    try:
        candidates = container.query_selector_all('[dir="auto"]')
    except Exception:
        return None

    for el in candidates[:20]:
        try:
            if not el.is_visible():
                continue
            # Skip text belonging to a real <a>/<button> control (creator
            # link, Follow button). Do NOT skip merely for role="button" —
            # Instagram wraps the caption's own expand-on-click behavior in
            # a role="button" div, and that wrapper is where the real
            # caption text lives.
            if el.evaluate("node => Boolean(node.closest('a, button'))"):
                continue
            text = el.inner_text().strip()
        except Exception:
            continue

        text = _strip_more_suffix(text)

        if _is_excluded_caption_text(text, username):
            continue

        return text

    return None


def _extract_posted_at_from_container(container: ElementHandle) -> Optional[str]:
    try:
        time_el = container.query_selector("time[datetime]")
        if time_el is not None:
            return time_el.get_attribute("datetime")
    except Exception:
        pass
    return None


def _extract_audio_name_from_container(container: ElementHandle) -> Optional[str]:
    try:
        audio_link = container.query_selector('a[href*="/reels/audio/"]')
        if audio_link is not None:
            text = audio_link.inner_text().strip()
            if text:
                return text
    except Exception:
        pass
    return None


def _extract_label_text_from_container(container: ElementHandle, pattern: re.Pattern) -> Optional[str]:
    """Search elements with a direct aria-label attribute (a stable,
    accessibility-driven attribute) for one matching the given count pattern.
    """
    try:
        candidates = container.query_selector_all("[aria-label]")
    except Exception:
        return None

    for el in candidates[:20]:
        try:
            label = el.get_attribute("aria-label")
        except Exception:
            continue
        if label and pattern.search(label):
            return label

    return None


def _extract_current_reel(page: Page) -> Optional[dict]:
    """Identify and extract metadata for the currently visible Reel.
    Returns None if the Reel cannot even be identified (reel_url/reel_id
    from page.url, or username from the creator link, missing); individual
    optional fields are None when not reliably obtainable, never fabricated.
    """
    url_info = _parse_reel_url(page.url)
    if url_info is None:
        return None

    reel_url, reel_id = url_info

    video_player = _find_active_video_player(page)
    if video_player is None:
        return None

    container = _get_reel_container_handle(video_player)
    if container is None:
        return None

    username = _extract_username_from_container(container)
    if username is None:
        return None

    caption = _extract_caption_from_container(container, username)
    posted_at = _extract_posted_at_from_container(container)
    audio_name = _extract_audio_name_from_container(container)
    view_count = normalize_count(_extract_label_text_from_container(container, VIEW_LABEL_PATTERN))
    like_count = normalize_count(_extract_label_text_from_container(container, LIKE_LABEL_PATTERN))
    comment_count = normalize_count(_extract_label_text_from_container(container, COMMENT_LABEL_PATTERN))

    scrape_status = "success" if caption else "partial"

    return {
        "reel_url": reel_url,
        "reel_id": reel_id,
        "username": username,
        "posted_at": posted_at,
        "caption": caption,
        "audio_name": audio_name,
        "view_count": view_count,
        "like_count": like_count,
        "comment_count": comment_count,
        "scrape_status": scrape_status,
    }


def browse_reels(page: Page, duration_seconds: int) -> list:
    """Navigate to the Reels feed, then for duration_seconds: identify the
    currently visible Reel, extract its visible metadata, append unique
    Reels to data/reels.xlsx, and advance at a ~6-second interval
    (extraction time counts toward that interval). Returns the list of
    {"reel_url", "reel_id"} dicts newly appended this run, for the
    post-collection enrichment pass — no yt-dlp calls happen in this loop.
    """
    print("[INFO] Navigating to Instagram Reels...")
    page.goto(REELS_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    print("[INFO] Reels feed loaded")

    seen_reels = load_existing_reel_urls(OUTPUT_FILE)
    discovery_rank = load_max_discovery_rank(OUTPUT_FILE)

    deadline = time.monotonic() + duration_seconds

    collected_this_run = 0
    duplicates_skipped = 0
    extraction_failures = 0
    new_reels_this_run = []

    while True:
        cycle_start = time.monotonic()
        if cycle_start >= deadline:
            break

        try:
            record = _extract_current_reel(page)
        except Exception:
            record = None
            print("[WARN] Could not extract current Reel metadata")
            extraction_failures += 1
        else:
            if record is None:
                print("[WARN] Could not extract current Reel metadata")
                extraction_failures += 1

        if record is not None:
            reel_url = record["reel_url"]

            if reel_url in seen_reels:
                print("[INFO] Duplicate Reel skipped")
                duplicates_skipped += 1
            else:
                discovery_rank += 1
                collected_this_run += 1
                row = {
                    "scraped_at": _now_iso8601(),
                    "discovery_rank": discovery_rank,
                    "reel_url": record["reel_url"],
                    "reel_id": record["reel_id"],
                    "username": record["username"],
                    "posted_at": record["posted_at"],
                    "caption": record["caption"],
                    "audio_name": record["audio_name"],
                    "view_count": record["view_count"],
                    "like_count": record["like_count"],
                    "comment_count": record["comment_count"],
                    "source_surface": SOURCE_SURFACE,
                    "scrape_status": record["scrape_status"],
                }
                append_row(OUTPUT_FILE, row)
                seen_reels.add(reel_url)
                new_reels_this_run.append({
                    "reel_url": record["reel_url"],
                    "reel_id": record["reel_id"],
                })

                username_display = record["username"] or "unknown"
                print(f"[INFO] Reel #{discovery_rank} collected | @{username_display}")
                if record["scrape_status"] == "partial":
                    print("[WARN] Current Reel metadata incomplete")

        remaining_total = deadline - time.monotonic()
        if remaining_total <= 0:
            break

        elapsed_this_cycle = time.monotonic() - cycle_start
        wait_seconds = max(0.0, REEL_INTERVAL_SECONDS - elapsed_this_cycle)
        wait_seconds = min(wait_seconds, remaining_total)
        if wait_seconds > 0:
            page.wait_for_timeout(wait_seconds * 1000)

        if time.monotonic() >= deadline:
            break

        _advance_reel(page)

    print("[INFO] Collection complete")
    print(f"[INFO] Unique reels collected this run: {collected_this_run}")
    print(f"[INFO] Duplicates skipped: {duplicates_skipped}")
    print(f"[INFO] Extraction failures: {extraction_failures}")
    print(f"[INFO] Output: {OUTPUT_FILE}")

    return new_reels_this_run


def open_authenticated_instagram(duration_seconds: int):
    """Launch Chromium with a persistent profile, confirm Instagram
    authentication (prompting for manual login/verification when needed),
    then auto-advance through Reels for duration_seconds. Leaves the
    browser open until the user presses Enter.
    """
    if not INSTAGRAM_USERNAME or not INSTAGRAM_PASSWORD:
        print("[ERROR] Instagram credentials are missing from .env")
        return

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            BROWSER_DATA_DIR,
            headless=False,
            viewport=VIEWPORT,
        )
        page = context.pages[0] if context.pages else context.new_page()

        print("[INFO] Launching Chromium...")
        print("[INFO] Opening Instagram...")
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        _dismiss_cookie_banner(page)

        state = _detect_state(page)

        if state == "authenticated":
            print("[INFO] Existing Instagram session detected")
        elif state == "challenge":
            state = _handle_challenge(page)
        else:
            print("[INFO] No active session detected")
            login_result = _perform_login(page)

            if login_result == "form_not_found":
                state = _wait_for_manual_completion(
                    page,
                    "Instagram login form could not be located automatically.",
                )
            elif login_result == "submission_failed":
                state = _wait_for_manual_completion(
                    page,
                    "Instagram login submission did not complete automatically.",
                )
            elif login_result == "challenge":
                state = _handle_challenge(page)
            else:
                state = login_result
                if state == "authenticated":
                    print("[INFO] Instagram login successful")
                    print("[INFO] Browser session saved in browser_data/")

        if state != "authenticated":
            print("[ERROR] Could not confirm Instagram authentication. Exiting.")
            context.close()
            return

        print("[INFO] Authentication confirmed")

        new_reels_this_run = browse_reels(page, duration_seconds)

        # Enrichment reuses this same still-open context's cookies, so it
        # must run before context.close().
        enrich_collected_reels(context, new_reels_this_run, OUTPUT_FILE)

        print("[INFO] Reels navigation complete.")
        input("Press Enter to close Chromium.\n")
        context.close()
