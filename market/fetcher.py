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


def _fetch_google_finance(symbols_meta: list[dict]) -> dict:
    """Fallback: scrape Google Finance for price + change data."""
    import re
    results = {}
    session = requests.Session()
    session.headers.update({"User-Agent": _HEADERS["User-Agent"]})

    for meta in symbols_meta:
        sym = meta["symbol"].lstrip("^")
        # Map to Google Finance ticker format
        exchange = "NYSEARCA"
        if meta["symbol"].startswith("^"):
            exchange = "INDEXSP" if "GSPC" in meta["symbol"] else \
                       "INDEXDJX" if "DJI" in meta["symbol"] else \
                       "INDEXFTSE" if "FTSE" in meta["symbol"] else \
                       "INDEXNIKKEI" if "N225" in meta["symbol"] else \
                       "INDEXDB" if "GDAXI" in meta["symbol"] else \
                       "INDEXBOM" if "BSESN" in meta["symbol"] else \
                       "INDEXNSE" if "NSEI" in meta["symbol"] else "NYSEARCA"
            sym = {"GSPC": "SPX", "DJI": "DJI", "FTSE": "UKX", "N225": "NI225",
                   "GDAXI": "DAX", "BSESN": "SENSEX", "NSEI": "NIFTY_50"}.get(sym, sym)
        try:
            url = f"https://www.google.com/finance/quote/{sym}:{exchange}"
            resp = session.get(url, timeout=10)
            price_match = re.search(r'data-last-price="([^"]+)"', resp.text)
            change_match = re.search(r'data-last-normal-market-change-percent="([^"]+)"', resp.text)
            prev_match = re.search(r'data-last-close-price="([^"]+)"', resp.text)
            if price_match:
                latest = float(price_match.group(1))
                prev = float(prev_match.group(1)) if prev_match else latest
                results[meta["symbol"]] = {"prev": prev, "latest": latest}
        except Exception as exc:
            logger.warning("Google Finance failed for %s: %s", meta["symbol"], exc)
        time.sleep(0.3)
    return results


def _fetch_quotes_batch(session: requests.Session, crumb: str, symbols: list[str]) -> dict:
    """Fetch multiple symbols via Yahoo v7 quote endpoint (single request)."""
    try:
        url = "https://query2.finance.yahoo.com/v7/finance/quote"
        resp = session.get(
            url,
            params={
                "symbols": ",".join(symbols),
                "fields": "regularMarketPrice,regularMarketChange,regularMarketChangePercent,regularMarketPreviousClose",
                "crumb": crumb,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        results = {}
        for item in data.get("quoteResponse", {}).get("result", []):
            sym = item.get("symbol")
            price = item.get("regularMarketPrice")
            prev = item.get("regularMarketPreviousClose")
            if price and prev:
                results[sym] = {"prev": prev, "latest": price}
        return results
    except Exception as exc:
        logger.warning("Batch quote fetch failed: %s", exc)
        return {}


def fetch_all_market_data() -> tuple[list[dict], list[dict]]:
    """
    Fetch indices + sectors in minimal requests to avoid rate limiting.
    Uses spark endpoint for batch fetching when possible.
    Returns (indices, sectors) — sectors sorted by pct_change desc.
    """
    session, crumb = _get_session_and_crumb()
    if session is None:
        logger.warning("Could not initialise Yahoo Finance session")
        return [], []

    # Fetch ALL symbols in one batch request
    all_symbols = [m["symbol"] for m in _INDICES] + [m["symbol"] for m in _SECTORS]
    all_prices = _fetch_quotes_batch(session, crumb, all_symbols)

    index_prices = {s: all_prices[s] for s in [m["symbol"] for m in _INDICES] if s in all_prices}
    sector_prices = {s: all_prices[s] for s in [m["symbol"] for m in _SECTORS] if s in all_prices}

    # Fallback: individual Yahoo fetches
    if not index_prices:
        for meta in _INDICES:
            p = _fetch_symbol(session, crumb, meta["symbol"])
            if p:
                index_prices[meta["symbol"]] = p
            time.sleep(0.5)

    # Fallback for sectors: try individual Yahoo, then Google Finance
    if not sector_prices:
        for meta in _SECTORS:
            p = _fetch_symbol(session, crumb, meta["symbol"])
            if p:
                sector_prices[meta["symbol"]] = p
            time.sleep(0.5)

    if not sector_prices:
        logger.info("Yahoo failed for sectors, trying Google Finance fallback...")
        sector_prices = _fetch_google_finance(_SECTORS)

    def _build_results(meta_list, prices, extra_keys):
        results = []
        for meta in meta_list:
            p = prices.get(meta["symbol"])
            if not p:
                continue
            change = round(p["latest"] - p["prev"], 2)
            pct = round((change / p["prev"]) * 100, 2) if p["prev"] else 0.0
            entry = {
                "symbol": meta["symbol"],
                "name": meta["name"],
                "value": round(p["latest"], 2),
                "change": change,
                "pct_change": pct,
            }
            for k in extra_keys:
                if k in meta:
                    entry[k] = meta[k]
            results.append(entry)
        return results

    indices = _build_results(_INDICES, index_prices, ["flag", "currency"])
    logger.info("Fetched market data for %d/%d indices", len(indices), len(_INDICES))

    sectors = _build_results(_SECTORS, sector_prices, ["icon"])
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
