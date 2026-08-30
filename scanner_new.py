import os
import time
import requests
import pandas as pd
import numpy as np

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

BASE = "https://fapi.binance.com"


def telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    r = requests.post(
        url,
        json={"chat_id": CHAT_ID, "text": text},
        timeout=20
    )

    print("TELEGRAM:", r.status_code, r.text[:300])


def get_symbols():
    r = requests.get(
        f"{BASE}/fapi/v1/exchangeInfo",
        timeout=30
    )

    print("BINANCE STATUS:", r.status_code)
    print("BINANCE RESPONSE:", r.text[:500])

    r.raise_for_status()

    data = r.json()

    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected Binance response: {data}")

    if "symbols" not in data:
        raise RuntimeError(
            f"Binance did not return symbols: {data}"
        )

    result = []

    for s in data["symbols"]:
        if (
            s.get("status") == "TRADING"
            and s.get("quoteAsset") == "USDT"
            and s.get("contractType") == "PERPETUAL"
        ):
            result.append(s["symbol"])

    print("SYMBOLS FOUND:", len(result))

    return result


def get_candles(symbol):
    r = requests.get(
        f"{BASE}/fapi/v1/klines",
        params={
            "symbol": symbol,
            "interval": "1h",
            "limit": 250
        },
        timeout=20
    )

    if r.status_code != 200:
        print(
            f"CANDLE ERROR {symbol}: "
            f"{r.status_code} {r.text[:200]}"
        )
        return None

    data = r.json()

    if not isinstance(data, list):
        return None

    if len(data) < 210:
        return None

    df = pd.DataFrame(
        data,
        columns=[
            "time", "open", "high", "low", "close",
            "volume", "close_time", "qav", "trades",
            "tbav", "tqav", "ignore"
        ]
    )

    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df.dropna()


def analyze(symbol):
    df = get_candles(symbol)

    if df is None or len(df) < 210:
        return None

    # EMA 200
    df["ema200"] = df["close"].ewm(
        span=200,
        adjust=False
    ).mean()

    # RSI 14
    delta = df["close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    df["rsi"] = 100 - (
        100 / (1 + rs)
    )

    # ATR 14
    prev = df["close"].shift(1)

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev).abs(),
            (df["low"] - prev).abs()
        ],
        axis=1
    ).max(axis=1)

    df["atr"] = tr.rolling(14).mean()

    # Last CLOSED candle
    x = df.iloc[-2]

    price = float(x["close"])
    ema = float(x["ema200"])
    rsi = float(x["rsi"])
    atr = float(x["atr"])

    if not np.isfinite(atr) or atr <= 0:
        return None

    # LONG
    if price > ema and 55 <= rsi <= 70:

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

    # SHORT
    if price < ema and 30 <= rsi <= 45:

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

    return None


def main():

    print("================================")
    print("BINANCE FUTURES 1H SCANNER")
    print("================================")

    symbols = get_symbols()

    signals = []

    # Scan symbols
    for i, symbol in enumerate(symbols):

        try:
            result = analyze(symbol)

            if result:
                print(
                    "SIGNAL:",
                    result["side"],
                    result["symbol"]
                )
                signals.append(result)

        except Exception as e:
            print(
                f"ERROR {symbol}: {e}"
            )

        # Don't hit Binance too aggressively
        if i % 20 == 0:
            time.sleep(0.5)

    print(
        "TOTAL SIGNALS:",
        len(signals)
    )

    # No signal
    if not signals:

        telegram(
            "🔎 Binance Futures 1H Scanner\n\n"
            "Այս պահին ուժեղ LONG/SHORT setup չկա։"
        )

        return

    # Strongest RSI deviation
    signals.sort(
        key=lambda x: abs(x["rsi"] - 50),
        reverse=True
    )

    signals = signals[:3]

    message = "🚨 BINANCE FUTURES 1H SIGNALS\n\n"

    for s in signals:

        emoji = (
            "🟢"
            if s["side"] == "LONG"
            else "🔴"
        )

        message += (
            f"{emoji} {s['side']} — {s['symbol']}\n"
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
