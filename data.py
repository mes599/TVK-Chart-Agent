"""Fetches OHLCV candles. TradingView has no public data API, so we pull the same
market from an exchange/data provider using the symbol + timeframe TradingView sends."""
import os
import time
from datetime import datetime, timezone

import httpx

# TradingView interval code -> (Binance interval, Twelve Data interval, minutes)
TF = {
    "1": ("1m", "1min", 1), "3": ("3m", None, 3), "5": ("5m", "5min", 5),
    "15": ("15m", "15min", 15), "30": ("30m", "30min", 30), "60": ("1h", "1h", 60),
    "120": ("2h", "2h", 120), "240": ("4h", "4h", 240), "D": ("1d", "1day", 1440),
    "W": ("1w", "1week", 10080),
}
ALIASES = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "60m": "60",
           "2h": "120", "4h": "240", "1d": "D", "d": "D", "1w": "W", "w": "W"}
# which higher timeframe to add for bias context
HTF = {"1": "15", "3": "15", "5": "60", "15": "60", "30": "240", "60": "240",
       "120": "D", "240": "D", "D": "W", "W": None}
CRYPTO_QUOTES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")


def normalize_tf(value) -> str:
    s = str(value).strip().lower()
    s = ALIASES.get(s, s)
    s = s.upper() if s in ("d", "w") else s
    if s not in TF:
        raise ValueError(f"Unsupported timeframe '{value}'. Use one of: {', '.join(TF)}")
    return s


def clean_symbol(symbol: str) -> str:
    # "BINANCE:BTCUSDT.P" -> "BTCUSDT"
    return symbol.split(":")[-1].replace(".P", "").replace("/", "").strip().upper()


def pick_provider(symbol: str, provider: str | None = None) -> str:
    if provider:
        return provider.lower()
    if symbol.endswith(CRYPTO_QUOTES):
        return "binance"
    return "twelvedata" if os.getenv("TWELVEDATA_API_KEY") else "binance"


async def _binance(symbol: str, tf: str, limit: int):
    url = "https://data-api.binance.vision/api/v3/klines"  # public market-data endpoint
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, params={"symbol": symbol, "interval": TF[tf][0], "limit": limit})
        r.raise_for_status()
    now = time.time() * 1000
    return [{"t": k[0], "o": float(k[1]), "h": float(k[2]), "l": float(k[3]), "c": float(k[4]), "v": float(k[5])}
            for k in r.json() if k[6] < now]  # drop the still-forming candle


async def _twelvedata(symbol: str, tf: str, limit: int):
    key = os.getenv("TWELVEDATA_API_KEY")
    if not key:
        raise RuntimeError("TWELVEDATA_API_KEY is not set (needed for forex / stocks / gold)")
    if TF[tf][1] is None:
        raise ValueError(f"Timeframe {tf} is not supported by Twelve Data")
    sym = f"{symbol[:3]}/{symbol[3:]}" if len(symbol) == 6 and symbol.isalpha() else symbol
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get("https://api.twelvedata.com/time_series",
                             params={"symbol": sym, "interval": TF[tf][1], "outputsize": limit,
                                     "timezone": "UTC", "apikey": key})
        j = r.json()
    if j.get("status") == "error" or "values" not in j:
        raise RuntimeError(f"Twelve Data: {j.get('message', 'no data')}")
    now, mins, out = time.time() * 1000, TF[tf][2], []
    for v in reversed(j["values"]):
        fmt = "%Y-%m-%d %H:%M:%S" if " " in v["datetime"] else "%Y-%m-%d"
        t = int(datetime.strptime(v["datetime"], fmt).replace(tzinfo=timezone.utc).timestamp() * 1000)
        if t + mins * 60_000 > now:  # still-forming candle
            continue
        out.append({"t": t, "o": float(v["open"]), "h": float(v["high"]), "l": float(v["low"]),
                    "c": float(v["close"]), "v": float(v.get("volume") or 0)})
    return out


async def fetch_candles(symbol: str, tf: str, provider: str | None = None, limit: int = 300):
    symbol = clean_symbol(symbol)
    prov = pick_provider(symbol, provider)
    candles = await (_twelvedata if prov == "twelvedata" else _binance)(symbol, tf, limit)
    if len(candles) < 50:
        raise RuntimeError(f"Only got {len(candles)} candles for {symbol} ({prov}); need at least 50")
    return candles
