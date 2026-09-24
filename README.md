# TradingView → Smart Money AI (Railway)

TradingView alert fires → your Railway service pulls the candles → detects
**BOS/CHoCH, FVG, IFVG, liquidity (equal highs/lows, sweeps, PDH/PDL), order blocks,
premium/discount** → GPT explains where price is likely heading → message lands in Telegram.

The detection is plain Python (`smc.py`), because LLMs misread raw candles. GPT (`ai.py`) only interprets the result.
It also analyses one higher timeframe automatically for bias context.

## 1. Telegram (2 min)
1. Message **@BotFather** → `/newbot` → copy the **bot token**.
2. Send any message to your new bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `"chat":{"id": ...}` → **chat id**.

## 2. Deploy on Railway
1. Put this folder in a GitHub repo.
2. Railway → **New Project → Deploy from GitHub repo**.
3. **Variables** tab, add everything from `.env.example`.
4. **Settings → Networking → Generate Domain**. Open `https://YOUR-APP.up.railway.app/health` → `{"ok":true}`.

## 3. Test without TradingView
`https://YOUR-APP.up.railway.app/analyze?symbol=BTCUSDT&timeframe=15&secret=YOUR_SECRET&send=true`

## 4. TradingView alert
Alert → **Notifications → Webhook URL**: `https://YOUR-APP.up.railway.app/webhook`
Message (must be valid JSON):
```
{"secret":"YOUR_SECRET","symbol":"{{ticker}}","timeframe":"{{interval}}"}
```
Trigger it "Once per bar close" on the timeframe you care about (or any condition you like).
TradingView webhooks need a paid plan and 2FA enabled on your account.

## Notes
- Crypto (`...USDT`) uses Binance public data, no key. Forex / gold / stocks need a free
  Twelve Data key (`TWELVEDATA_API_KEY`).
- `OPENAI_MODEL` defaults to `gpt-5.4-mini` (cheap, runs on every alert). Set `gpt-5.5` for deeper reasoning.
- You can also POST your own candles in the webhook body: `"candles":[{"t":ms,"o":..,"h":..,"l":..,"c":..}]`.
- It reports probabilities and levels, not certainties. Nothing here is financial advice.
