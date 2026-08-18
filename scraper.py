"""Instagram authentication and Reels auto-advance stage.

Uses Playwright's synchronous API to open Chromium with a persistent
profile (browser_data/) so login state survives between runs, then
auto-advances through the Reels feed at a fixed interval. No metadata
extraction, scraping, or XLSX writing happens here — later stages will
reuse this authenticated, Reels-navigating page.
"""

import os
import re
import time
from typing import Optional

from playwright.sync_api import Locator, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from config import (
    BROWSER_DATA_DIR,
    INSTAGRAM_PASSWORD,
    INSTAGRAM_USERNAME,
    REEL_INTERVAL_SECONDS,
    REELS_URL,
    SCREENSHOT_DIR,
)

LOGIN_URL = "https://www.instagram.com/accounts/login/"
LOGIN_DEBUG_SCREENSHOT = os.path.join(SCREENSHOT_DIR, "login_debug.png")
LOGIN_SUBMIT_DEBUG_SCREENSHOT = os.path.join(SCREENSHOT_DIR, "login_submit_debug.png")
VIEWPORT = {"width": 1280, "height": 900}
LOGIN_FIELD_TIMEOUT_MS = 15000
LOGIN_SUBMIT_ENTER_TIMEOUT_MS = 10000
LOGIN_SUBMIT_FALLBACK_TIMEOUT_MS = 15000
REEL_ADVANCE_CHECK_MS = 1500

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


def _get_active_reel_signal(page: Page) -> Optional[str]:
    """Lightweight signal for whether the visible Reel changed — not
    metadata extraction, just enough to confirm the feed advanced.
    """
    try:
        link = page.locator('a[href*="/reel/"]').first
        if link.count() > 0:
            href = link.get_attribute("href")
            if href:
                return href
    except Exception:
        pass

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


def browse_reels(page: Page, duration_seconds: int):
    """Navigate to the Reels feed and auto-advance at a fixed interval for
    duration_seconds. No metadata extraction happens here — this stage only
    confirms the feed advances.
    """
    print("[INFO] Navigating to Instagram Reels...")
    page.goto(REELS_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    print("[INFO] Reels feed loaded")

    start = time.monotonic()
    deadline = start + duration_seconds

    reel_count = 1
    print(f"[INFO] Viewing Reel #{reel_count}")

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        page.wait_for_timeout(int(min(REEL_INTERVAL_SECONDS, remaining) * 1000))

        if time.monotonic() >= deadline:
            break

        _advance_reel(page)
        reel_count += 1
        print(f"[INFO] Viewing Reel #{reel_count}")

    print("[INFO] Reels navigation complete")
    print(f"[INFO] Reels viewed: {reel_count}")
    print(f"[INFO] Duration: {duration_seconds} seconds")


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

        browse_reels(page, duration_seconds)

        print("[INFO] Reels navigation complete.")
        input("Press Enter to close Chromium.\n")
        context.close()
