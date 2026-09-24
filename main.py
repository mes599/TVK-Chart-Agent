import asyncio
import hmac
import json
import logging
import os
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

import ai
import data
import notify
import smc

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")
app = FastAPI(title="TradingView SMC AI")


def check_secret(given: Optional[str]):
    expected = os.getenv("WEBHOOK_SECRET", "")
    if expected and not hmac.compare_digest(expected, given or ""):
        raise HTTPException(status_code=401, detail="bad secret")


async def run_analysis(symbol: str, timeframe: str, provider: Optional[str] = None,
                       candles: Optional[list] = None, send: bool = True) -> dict:
    tf = data.normalize_tf(timeframe)
    htf = data.HTF[tf]

    async def analyse(code: str, given: Optional[list] = None):
        c = given or await data.fetch_candles(symbol, code, provider)
        return smc.analyze(c, data.TF[code][2])

    main_task = analyse(tf, candles)
    if htf and not candles:
        main, higher = await asyncio.gather(main_task, analyse(htf), return_exceptions=True)
        if isinstance(main, Exception):
            raise main
        higher = None if isinstance(higher, Exception) else higher
    else:
        main, higher = await main_task, None

    report = {"symbol": data.clean_symbol(symbol), "timeframe": tf, "analysis": main}
    if higher:
        report["higher_timeframe"] = {"timeframe": htf, "analysis": higher}

    text = await ai.interpret(report)
    if send:
        await notify.send_telegram(text)
    return {"text": text, "report": report}


async def safe_run(**kwargs):
    """Background wrapper: never crash silently, tell the user on Telegram instead."""
    try:
        await run_analysis(**kwargs)
    except Exception as e:  # noqa: BLE001
        log.exception("analysis failed")
        await notify.send_telegram(f"⚠️ Analysis failed for {kwargs.get('symbol')}: {e}")


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/webhook")
async def webhook(request: Request, background: BackgroundTasks):
    """TradingView alert target. Must answer in <3s, so the work runs in the background."""
    try:
        body = json.loads((await request.body()).decode("utf-8", "ignore"))
    except ValueError:
        raise HTTPException(status_code=400, detail='Alert message must be JSON, e.g. {"symbol":"{{ticker}}","timeframe":"{{interval}}"}')
    check_secret(body.get("secret") or request.query_params.get("secret"))
    if not body.get("symbol"):
        raise HTTPException(status_code=400, detail="'symbol' is required")
    background.add_task(safe_run, symbol=str(body["symbol"]), timeframe=str(body.get("timeframe", "15")),
                        provider=body.get("provider"), candles=body.get("candles"))
    return {"status": "accepted"}


@app.get("/analyze")
async def analyze_now(symbol: str, timeframe: str = "15", secret: str = "",
                      provider: Optional[str] = None, send: bool = False):
    """On-demand: /analyze?symbol=BTCUSDT&timeframe=15&secret=...&send=true"""
    check_secret(secret)
    try:
        return await run_analysis(symbol, timeframe, provider, send=send)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))
