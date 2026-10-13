"""Independent trend-identification engine.

Primary public interface:

    ./analyze.sh "<PUBLIC_SOCIAL_CONTENT_URL>"

Reusable URL-router interface:

    from trend_analysis.analyze_url import analyze_url
    analyze_url(url: str) -> dict

Legacy Instagram-specific interface:

    from trend_analysis.analyze_reel import analyze_reel
    analyze_reel(reel_url: str) -> dict

(Deliberately not re-exported here at the package level — this module is
also the `python -m trend_analysis.analyze_reel` CLI entry point, and
eagerly importing it in __init__.py causes Python to load it twice under
two different module identities, which triggers a RuntimeWarning.)

Decoupled from the Playwright collector (scraper.py/main.py) by design. The
root router supports public Instagram, TikTok, and X/Twitter content through
separate acquisition adapters and one shared visual-analysis boundary. It
answers "what trend does this content belong to?", not "is this trend going
viral?" — see trend_identifier.py for that distinction.
"""
