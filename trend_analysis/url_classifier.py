"""URL classification and narrowly-scoped TikTok short-link resolution.

Canonical URL classification remains pure. Only recognized TikTok share-link
forms may enter the redirect resolver; arbitrary URLs are never fetched.
"""

import re
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .config import YDL_SOCKET_TIMEOUT_SECONDS


class URLClassificationError(ValueError):
    """Raised when a URL is malformed or not a supported content URL."""


@dataclass(frozen=True)
class ClassifiedURL:
    platform: str
    content_type: str
    content_id: str
    canonical_url: str


INSTAGRAM_PATH = re.compile(
    r"^/(?:[^/]+/)?(?P<kind>reel|reels|p)/(?P<id>[A-Za-z0-9_-]+)/?$"
)
TIKTOK_PATH = re.compile(
    r"^/@(?P<username>[A-Za-z0-9._]+)/video/(?P<id>[0-9]+)/?$"
)
TIKTOK_SHORT_PATH = re.compile(r"^/[A-Za-z0-9_-]+/?$")
TIKTOK_T_PATH = re.compile(r"^/t/[A-Za-z0-9_-]+/?$")
X_PATH = re.compile(
    r"^/(?P<username>[^/]+)/status/(?P<id>[0-9]+)(?:/.*)?$"
)

INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com"}
TIKTOK_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}
TIKTOK_SHORT_HOSTS = {"vm.tiktok.com", "vt.tiktok.com"}
X_HOSTS = {
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
}


def _unidentified() -> URLClassificationError:
    return URLClassificationError(
        "URL platform/content type could not be identified."
    )


def classify_url(url: str) -> ClassifiedURL:
    """Classify and canonicalize one supported social-content URL."""
    if not isinstance(url, str) or not url.strip():
        raise _unidentified()

    candidate = url.strip()
    parsed = urlparse(candidate)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in ("http", "https") or not hostname:
        raise _unidentified()

    if hostname in INSTAGRAM_HOSTS:
        match = INSTAGRAM_PATH.fullmatch(parsed.path)
        if not match:
            raise _unidentified()
        kind = match.group("kind")
        content_type = "post" if kind == "p" else "reel"
        content_id = match.group("id")
        path_kind = "p" if content_type == "post" else "reel"
        return ClassifiedURL(
            platform="instagram",
            content_type=content_type,
            content_id=content_id,
            canonical_url=f"https://www.instagram.com/{path_kind}/{content_id}/",
        )

    if hostname in TIKTOK_HOSTS:
        match = TIKTOK_PATH.fullmatch(parsed.path)
        if not match:
            raise _unidentified()
        username = match.group("username")
        content_id = match.group("id")
        return ClassifiedURL(
            platform="tiktok",
            content_type="video",
            content_id=content_id,
            canonical_url=(
                f"https://www.tiktok.com/@{username}/video/{content_id}"
            ),
        )

    if hostname in X_HOSTS:
        match = X_PATH.fullmatch(parsed.path)
        if not match:
            raise _unidentified()
        username = match.group("username")
        content_id = match.group("id")
        return ClassifiedURL(
            platform="x",
            content_type="post",
            content_id=content_id,
            canonical_url=f"https://x.com/{username}/status/{content_id}",
        )

    raise _unidentified()


def is_tiktok_short_url(url: str) -> bool:
    if not isinstance(url, str) or not url.strip():
        return False
    parsed = urlparse(url.strip())
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in ("http", "https"):
        return False
    if hostname in TIKTOK_SHORT_HOSTS:
        return bool(TIKTOK_SHORT_PATH.fullmatch(parsed.path))
    if hostname in TIKTOK_HOSTS:
        return bool(TIKTOK_T_PATH.fullmatch(parsed.path))
    return False


def resolve_tiktok_short_url(url: str, opener=None) -> str:
    """Resolve only a validated TikTok-owned short/share URL."""
    if not is_tiktok_short_url(url):
        raise _unidentified()
    open_url = opener or urlopen
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        request = Request(url.strip(), headers=headers, method="HEAD")
        with open_url(request, timeout=YDL_SOCKET_TIMEOUT_SECONDS) as response:
            final_url = response.geturl()
    except (HTTPError, URLError, TimeoutError, OSError):
        try:
            request = Request(url.strip(), headers=headers)
            with open_url(request, timeout=YDL_SOCKET_TIMEOUT_SECONDS) as response:
                final_url = response.geturl()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise URLClassificationError(
                f"TikTok short URL could not be resolved: {exc}"
            ) from exc

    try:
        route = classify_url(final_url)
    except URLClassificationError as exc:
        raise URLClassificationError(
            "TikTok short URL did not resolve to a supported video URL."
        ) from exc
    if route.platform != "tiktok" or route.content_type != "video":
        raise URLClassificationError(
            "TikTok short URL did not resolve to a supported video URL."
        )
    return route.canonical_url


def classify_url_with_redirects(url: str, opener=None) -> ClassifiedURL:
    """Classify a canonical URL or resolve a recognized TikTok share URL."""
    if is_tiktok_short_url(url):
        return classify_url(resolve_tiktok_short_url(url, opener=opener))
    return classify_url(url)
