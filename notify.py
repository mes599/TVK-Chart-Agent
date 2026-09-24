import logging
import os

import httpx

log = logging.getLogger("notify")


async def send_telegram(text: str) -> bool:
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.warning("Telegram not configured; message not sent:\n%s", text)
        return False
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              json={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True})
    if r.status_code != 200:
        log.error("Telegram error %s: %s", r.status_code, r.text[:200])
    return r.status_code == 200
