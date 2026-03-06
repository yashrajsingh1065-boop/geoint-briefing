from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)

_INDICES = [
    {"symbol": "^GSPC",  "name": "S&P 500",   "flag": "🇺🇸", "currency": "USD"},
    {"symbol": "^DJI",   "name": "Dow Jones",  "flag": "🇺🇸", "currency": "USD"},
    {"symbol": "^FTSE",  "name": "FTSE 100",   "flag": "🇬🇧", "currency": "GBP"},
    {"symbol": "^N225",  "name": "Nikkei 225", "flag": "🇯🇵", "currency": "JPY"},
    {"symbol": "^GDAXI", "name": "DAX",        "flag": "🇩🇪", "currency": "EUR"},
    {"symbol": "^BSESN", "name": "BSE Sensex", "flag": "🇮🇳", "currency": "INR"},
    {"symbol": "^NSEI",  "name": "Nifty 50",   "flag": "🇮🇳", "currency": "INR"},
]

# Sector ETFs for industry-level gainers/losers
_SECTORS = [
    {"symbol": "XLK",  "name": "Technology",             "icon": "💻"},
    {"symbol": "XLF",  "name": "Financials",             "icon": "🏦"},
    {"symbol": "XLE",  "name": "Energy",                 "icon": "⛽"},
    {"symbol": "XLV",  "name": "Healthcare",             "icon": "🏥"},
    {"symbol": "XLI",  "name": "Industrials",            "icon": "🏭"},
    {"symbol": "XLC",  "name": "Communication",          "icon": "📡"},
    {"symbol": "XLY",  "name": "Consumer Discretionary", "icon": "🛍️"},
    {"symbol": "XLP",  "name": "Consumer Staples",       "icon": "🛒"},
    {"symbol": "XLU",  "name": "Utilities",              "icon": "⚡"},
    {"symbol": "XLB",  "name": "Materials",              "icon": "🧱"},
    {"symbol": "XLRE", "name": "Real Estate",            "icon": "🏠"},
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://finance.yahoo.com",
    "Referer": "https://finance.yahoo.com/",
}


def _get_session_and_crumb() -> tuple[requests.Session, str] | tuple[None, None]:
    """Obtain a Yahoo Finance cookie + crumb for authenticated requests."""
    session = requests.Session()
    session.headers.update(_HEADERS)
    try:
        # Step 1: hit finance.yahoo.com to get cookies
        session.get("https://finance.yahoo.com", timeout=10)
        time.sleep(1)
        # Step 2: get crumb
        resp = session.get(
            "https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=10
        )
        crumb = resp.text.strip()
        if not crumb or "<" in crumb:
            return None, None
        return session, crumb
    except Exception as exc:
        logger.warning("Failed to obtain Yahoo crumb: %s", exc)
        return None, None


def _fetch_symbol(
    session: requests.Session, crumb: str, symbol: str
) -> dict | None:
    """Fetch 5-day chart data for one symbol."""
    try:
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
        resp = session.get(
            url,
            params={"interval": "1d", "range": "5d", "crumb": crumb},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        closes = (
            data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        )
        closes = [c for c in closes if c is not None]
        if len(closes) < 2:
            return None
        return {"prev": closes[-2], "latest": closes[-1]}
    except Exception as exc:
        logger.warning("Failed to fetch %s: %s", symbol, exc)
        return None


def _fetch_symbols(session, crumb, symbols_meta, extra_keys=None):
    """Fetch price data for a list of symbol metadata dicts."""
    results = []
    for meta in symbols_meta:
        prices = _fetch_symbol(session, crumb, meta["symbol"])
        if prices is None:
            continue
        prev_close = prices["prev"]
        latest = prices["latest"]
        change = round(latest - prev_close, 2)
        pct_change = round((change / prev_close) * 100, 2) if prev_close else 0.0
        entry = {
            "symbol":     meta["symbol"],
            "name":       meta["name"],
            "value":      round(latest, 2),
            "change":     change,
            "pct_change": pct_change,
        }
        if extra_keys:
            for k in extra_keys:
                if k in meta:
                    entry[k] = meta[k]
        results.append(entry)
        time.sleep(0.5)
    return results


def fetch_all_market_data() -> tuple[list[dict], list[dict]]:
    """
    Fetch indices + sectors in a single session to avoid rate limiting.
    Returns (indices, sectors) — sectors sorted by pct_change desc.
    """
    session, crumb = _get_session_and_crumb()
    if session is None:
        logger.warning("Could not initialise Yahoo Finance session")
        return [], []

    indices = _fetch_symbols(session, crumb, _INDICES, extra_keys=["flag", "currency"])
    logger.info("Fetched market data for %d/%d indices", len(indices), len(_INDICES))

    sectors = _fetch_symbols(session, crumb, _SECTORS, extra_keys=["icon"])
    sectors.sort(key=lambda x: x["pct_change"], reverse=True)
    logger.info("Fetched sector data for %d/%d sectors", len(sectors), len(_SECTORS))

    return indices, sectors


# Legacy wrappers for backward compatibility
def fetch_market_data() -> list[dict]:
    indices, _ = fetch_all_market_data()
    return indices


def fetch_sector_data() -> list[dict]:
    _, sectors = fetch_all_market_data()
    return sectors
