from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_INDICES = [
    {"symbol": "^GSPC",  "name": "S&P 500",     "flag": "🇺🇸", "currency": "USD"},
    {"symbol": "^DJI",   "name": "Dow Jones",    "flag": "🇺🇸", "currency": "USD"},
    {"symbol": "^FTSE",  "name": "FTSE 100",     "flag": "🇬🇧", "currency": "GBP"},
    {"symbol": "^N225",  "name": "Nikkei 225",   "flag": "🇯🇵", "currency": "JPY"},
    {"symbol": "^GDAXI", "name": "DAX",          "flag": "🇩🇪", "currency": "EUR"},
    {"symbol": "^BSESN", "name": "BSE Sensex",   "flag": "🇮🇳", "currency": "INR"},
    {"symbol": "^NSEI",  "name": "Nifty 50",     "flag": "🇮🇳", "currency": "INR"},
]


def fetch_market_data() -> list[dict]:
    """
    Fetch previous-day close for all 7 indices via yfinance.
    Returns a list of dicts with symbol, name, flag, value, change, pct_change, currency.
    Silently returns empty list on any failure.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed — skipping market data fetch")
        return []

    results = []
    for meta in _INDICES:
        try:
            ticker = yf.Ticker(meta["symbol"])
            hist = ticker.history(period="5d")
            if hist.empty or len(hist) < 2:
                logger.warning("Insufficient history for %s", meta["symbol"])
                continue

            prev_close = float(hist["Close"].iloc[-2])
            latest = float(hist["Close"].iloc[-1])
            change = round(latest - prev_close, 2)
            pct_change = round((change / prev_close) * 100, 2) if prev_close else 0.0

            results.append({
                "symbol":     meta["symbol"],
                "name":       meta["name"],
                "flag":       meta["flag"],
                "value":      round(latest, 2),
                "change":     change,
                "pct_change": pct_change,
                "currency":   meta["currency"],
            })
        except Exception as exc:
            logger.warning("Failed to fetch %s: %s", meta["symbol"], exc)

    logger.info("Fetched market data for %d/%d indices", len(results), len(_INDICES))
    return results
