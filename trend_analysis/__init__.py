"""Independent trend-identification engine.

Public interface:

    from trend_analysis.analyze_reel import analyze_reel
    analyze_reel(reel_url: str) -> dict

(Deliberately not re-exported here at the package level — this module is
also the `python -m trend_analysis.analyze_reel` CLI entry point, and
eagerly importing it in __init__.py causes Python to load it twice under
two different module identities, which triggers a RuntimeWarning.)

Decoupled from the Playwright collector (scraper.py/main.py) by design —
this engine works from any Instagram Reel URL, regardless of how it was
obtained (the Playwright collector, Apify, a manual paste, a spreadsheet,
n8n). It answers "what trend does this Reel belong to?", not "is this
trend going viral?" — see trend_identifier.py for that distinction.
"""
