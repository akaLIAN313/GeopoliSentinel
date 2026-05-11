"""
Polymarket odds fetcher.
Fetches Yes-probabilities for a fixed set of Iran/Israel prediction markets
and saves them to data/polymarket.json for use by notifier.py.

Run standalone:  python src/polymarket.py
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parents[1]
POLYMARKET_PATH = ROOT / "data" / "polymarket.json"
BASE_URL = "https://gamma-api.polymarket.com/markets"

# Markets most relevant to the US/Israel–Iran conflict thesis.
# "yes_is_conflict" marks whether a Yes resolution means more conflict risk (True)
# or less (False — e.g. a peace deal resolving Yes = de-escalation).
MARKETS: list[dict] = [
    {
        "slug": "will-the-us-invade-iran-before-2027",
        "label": "US 入侵伊朗 (2027前)",
        "yes_is_conflict": True,
    },
    {
        "slug": "will-the-iranian-regime-fall-by-the-end-of-2026",
        "label": "伊朗政权崩溃 (2026底前)",
        "yes_is_conflict": True,
    },
    {
        "slug": "netanyahu-out-before-2027-684-719-226-657",
        "label": "内塔尼亚胡下台 (2026底前)",
        "yes_is_conflict": False,
    },
    {
        "slug": "us-iran-nuclear-deal-before-2027",
        "label": "美伊核协议 (2027前)",
        "yes_is_conflict": False,
    },
]


def _fetch_yes_price(slug: str) -> float | None:
    """Return the Yes-probability (0.0–1.0) for a market slug, or None on failure."""
    try:
        resp = requests.get(
            BASE_URL,
            params={"slug": slug},
            timeout=10,
            headers={"User-Agent": "ResonanceBot/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            logger.warning("Polymarket: empty response for slug=%s", slug)
            return None
        prices = data[0].get("outcomePrices") or []
        if not prices:
            return None
        return float(prices[0])  # index 0 = Yes
    except Exception as exc:
        logger.warning("Polymarket fetch failed for slug=%s: %s", slug, exc)
        return None


def fetch_and_save() -> list[dict]:
    """Fetch all target markets and write data/polymarket.json. Returns results list."""
    results = []
    for m in MARKETS:
        price = _fetch_yes_price(m["slug"])
        yes_pct = round(price * 100, 1) if price is not None else None
        results.append({
            "label": m["label"],
            "slug": m["slug"],
            "yes_is_conflict": m["yes_is_conflict"],
            "yes_pct": yes_pct,
        })
        logger.info("Polymarket [%s]: %s%%", m["label"], yes_pct)

    payload = {
        "markets": results,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    POLYMARKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    POLYMARKET_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Polymarket snapshot saved → %s", POLYMARKET_PATH)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    fetch_and_save()
    path = POLYMARKET_PATH
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for m in data["markets"]:
        print(f"  {m['label']}: {m['yes_pct']}%")
