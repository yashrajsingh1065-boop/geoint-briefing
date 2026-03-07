from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_INDICES = [
    {"symbol": "^GSPC",  "name": "S&P 500",   "flag": "\U0001f1fa\U0001f1f8", "currency": "USD"},
    {"symbol": "^DJI",   "name": "Dow Jones",  "flag": "\U0001f1fa\U0001f1f8", "currency": "USD"},
    {"symbol": "^FTSE",  "name": "FTSE 100",   "flag": "\U0001f1ec\U0001f1e7", "currency": "GBP"},
    {"symbol": "^N225",  "name": "Nikkei 225", "flag": "\U0001f1ef\U0001f1f5", "currency": "JPY"},
    {"symbol": "^GDAXI", "name": "DAX",        "flag": "\U0001f1e9\U0001f1ea", "currency": "EUR"},
    {"symbol": "^BSESN", "name": "BSE Sensex", "flag": "\U0001f1ee\U0001f1f3", "currency": "INR"},
    {"symbol": "^NSEI",  "name": "Nifty 50",   "flag": "\U0001f1ee\U0001f1f3", "currency": "INR"},
]

# Sector ETFs for industry-level gainers/losers
_SECTORS = [
    {"symbol": "XLK",  "name": "Technology",             "icon": "\U0001f4bb"},
    {"symbol": "XLF",  "name": "Financials",             "icon": "\U0001f3e6"},
    {"symbol": "XLE",  "name": "Energy",                 "icon": "\u26fd"},
    {"symbol": "XLV",  "name": "Healthcare",             "icon": "\U0001f3e5"},
    {"symbol": "XLI",  "name": "Industrials",            "icon": "\U0001f3ed"},
    {"symbol": "XLC",  "name": "Communication",          "icon": "\U0001f4e1"},
    {"symbol": "XLY",  "name": "Consumer Discretionary", "icon": "\U0001f6cd\ufe0f"},
    {"symbol": "XLP",  "name": "Consumer Staples",       "icon": "\U0001f6d2"},
    {"symbol": "XLU",  "name": "Utilities",              "icon": "\u26a1"},
    {"symbol": "XLB",  "name": "Materials",              "icon": "\U0001f9f1"},
    {"symbol": "XLRE", "name": "Real Estate",            "icon": "\U0001f3e0"},
]


def _fetch_via_yfinance(symbols: list[str]) -> dict:
    """Primary method: use yfinance library which handles Yahoo auth automatically."""
    try:
        import yfinance as yf
        tickers = yf.Tickers(" ".join(symbols))
        results = {}
        for sym in symbols:
            try:
                ticker = tickers.tickers.get(sym)
                if ticker is None:
                    continue
                info = ticker.fast_info
                price = getattr(info, "last_price", None)
                prev = getattr(info, "previous_close", None)
                if price and prev:
                    results[sym] = {"prev": float(prev), "latest": float(price)}
            except Exception:
                continue
        return results
    except Exception as exc:
        logger.warning("yfinance batch fetch failed: %s", exc)
        return {}


def _fetch_via_yfinance_history(symbols: list[str]) -> dict:
    """Fallback: use yfinance history() for 5-day OHLC data."""
    try:
        import yfinance as yf
        results = {}
        for sym in symbols:
            try:
                ticker = yf.Ticker(sym)
                hist = ticker.history(period="5d")
                if len(hist) >= 2:
                    closes = hist["Close"].dropna().tolist()
                    if len(closes) >= 2:
                        results[sym] = {"prev": float(closes[-2]), "latest": float(closes[-1])}
            except Exception:
                continue
        return results
    except Exception as exc:
        logger.warning("yfinance history fallback failed: %s", exc)
        return {}


def fetch_all_market_data() -> tuple[list[dict], list[dict]]:
    """
    Fetch indices + sectors. Uses yfinance library (handles Yahoo auth).
    Returns (indices, sectors) — sectors sorted by pct_change desc.
    """
    all_symbols = [m["symbol"] for m in _INDICES] + [m["symbol"] for m in _SECTORS]

    # Try fast_info first (single lightweight call per symbol)
    all_prices = _fetch_via_yfinance(all_symbols)

    # If that didn't work well, try history-based fallback
    missing = [s for s in all_symbols if s not in all_prices]
    if len(missing) > len(all_symbols) // 2:
        logger.info("fast_info got %d/%d, trying history fallback for all...", len(all_prices), len(all_symbols))
        fallback = _fetch_via_yfinance_history(all_symbols)
        for sym, data in fallback.items():
            if sym not in all_prices:
                all_prices[sym] = data
    elif missing:
        logger.info("Fetching %d missing symbols via history...", len(missing))
        fallback = _fetch_via_yfinance_history(missing)
        all_prices.update(fallback)

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

    indices = _build_results(_INDICES, all_prices, ["flag", "currency"])
    logger.info("Fetched market data for %d/%d indices", len(indices), len(_INDICES))

    sectors = _build_results(_SECTORS, all_prices, ["icon"])
    sectors.sort(key=lambda x: x["pct_change"], reverse=True)
    logger.info("Fetched sector data for %d/%d sectors", len(sectors), len(_SECTORS))

    return indices, sectors
