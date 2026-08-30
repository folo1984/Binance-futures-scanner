import os
import requests
import pandas as pd
import numpy as np

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

BASE = "https://fapi.binance.com"

def telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": text})

def get_symbols():
    data = requests.get(f"{BASE}/fapi/v1/exchangeInfo").json()
    return [
        s["symbol"] for s in data["symbols"]
        if s["status"] == "TRADING"
        and s["quoteAsset"] == "USDT"
        and s["contractType"] == "PERPETUAL"
    ]

def candles(symbol, interval="1h", limit=250):
    r = requests.get(
        f"{BASE}/fapi/v1/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit}
    )
    return r.json()

def analyze(symbol):
    data = candles(symbol)

    if not data or len(data) < 210:
        return None

    df = pd.DataFrame(data, columns=[
        "time","open","high","low","close","volume",
        "close_time","qav","trades","tbav","tqav","ignore"
    ])

    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)

    # EMA 200
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

    # RSI 14
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    # ATR 10
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs()
    ], axis=1).max(axis=1)

    df["atr"] = tr.rolling(10).mean()

    # ATR trailing stop — UT Bot style
    sensitivity = 2.0
    df["stop"] = df["close"] - sensitivity * df["atr"]

    # Last CLOSED candle
    x = df.iloc[-2]

    price = x["close"]
    ema = x["ema200"]
    rsi = x["rsi"]
    atr = x["atr"]

    if not np.isfinite(atr):
        return None

    # Trend conditions
    long_condition = price > ema and rsi > 55
    short_condition = price < ema and rsi < 45

    if not long_condition and not short_condition:
        return None

    if long_condition:
        entry = price
        sl = entry - 1.5 * atr
        risk = entry - sl

        return {
            "side": "LONG",
            "symbol": symbol,
            "entry": entry,
            "sl": sl,
            "tp1": entry + risk,
            "tp2": entry + 2 * risk,
            "tp3": entry + 3 * risk,
            "rsi": rsi
        }

    if short_condition:
        entry = price
        sl = entry + 1.5 * atr
        risk = sl - entry

        return {
            "side": "SHORT",
            "symbol": symbol,
            "entry": entry,
            "sl": sl,
            "tp1": entry - risk,
            "tp2": entry - 2 * risk,
            "tp3": entry - 3 * risk,
            "rsi": rsi
        }

def main():
    signals = []

    for symbol in get_symbols():
        try:
            result = analyze(symbol)
            if result:
                signals.append(result)
        except Exception:
            continue

    if not signals:
        telegram("🔎 Binance Futures 1H Scanner\n\nԱյս պահին ուժեղ LONG/SHORT setup չկա։")
        return

    # strongest signals first
    signals = sorted(
        signals,
        key=lambda x: abs(x["rsi"] - 50),
        reverse=True
    )[:3]

    message = "🚨 BINANCE FUTURES 1H SIGNALS\n\n"

    for s in signals:
        message += (
            f"{'🟢' if s['side']=='LONG' else '🔴'} {s['side']} — {s['symbol']}\n"
            f"Entry: {s['entry']:.8g}\n"
            f"SL: {s['sl']:.8g}\n"
            f"TP1: {s['tp1']:.8g}\n"
            f"TP2: {s['tp2']:.8g}\n"
            f"TP3: {s['tp3']:.8g}\n"
            f"RSI: {s['rsi']:.1f}\n\n"
        )

    telegram(message)

if __name__ == "__main__":
    main()
