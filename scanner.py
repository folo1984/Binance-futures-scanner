import os
import requests
import pandas as pd
import numpy as np

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

BASE = "https://fapi.binance.com"


def telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": text
        },
        timeout=20
    )

    print("TELEGRAM STATUS:", response.status_code)
    print("TELEGRAM RESPONSE:", response.text[:500])


def get_symbols():
    url = f"{BASE}/fapi/v1/exchangeInfo"

    response = requests.get(url, timeout=20)

    print("BINANCE STATUS:", response.status_code)
    print("BINANCE RESPONSE:", response.text[:1000])

    response.raise_for_status()

    data = response.json()

    if "symbols" not in data:
        raise RuntimeError(
            f"Binance API did not return symbols: {data}"
        )

    symbols = []

    for s in data["symbols"]:
        if (
            s.get("status") == "TRADING"
            and s.get("quoteAsset") == "USDT"
            and s.get("contractType") == "PERPETUAL"
        ):
            symbols.append(s["symbol"])

    print("USDT PERPETUAL SYMBOLS:", len(symbols))

    return symbols


def candles(symbol, interval="1h", limit=250):
    url = f"{BASE}/fapi/v1/klines"

    response = requests.get(
        url,
        params={
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        },
        timeout=20
    )

    if response.status_code != 200:
        print(
            f"CANDLE ERROR {symbol}: "
            f"{response.status_code} {response.text[:300]}"
        )
        return []

    return response.json()


def analyze(symbol):
    data = candles(symbol)

    if not data or len(data) < 210:
        return None

    # Binance error response protection
    if not isinstance(data, list):
        print(f"INVALID DATA {symbol}: {data}")
        return None

    df = pd.DataFrame(
        data,
        columns=[
            "time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "qav",
            "trades",
            "tbav",
            "tqav",
            "ignore"
        ]
    )

    for column in [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

    df = df.dropna()

    if len(df) < 210:
        return None

    # =========================
    # EMA 200
    # =========================

    df["ema200"] = (
        df["close"]
        .ewm(span=200, adjust=False)
        .mean()
    )

    # =========================
    # RSI 14
    # =========================

    delta = df["close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = (
        gain
        .ewm(alpha=1 / 14, adjust=False)
        .mean()
    )

    avg_loss = (
        loss
        .ewm(alpha=1 / 14, adjust=False)
        .mean()
    )

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan
    )

    df["rsi"] = 100 - (
        100 / (1 + rs)
    )

    # =========================
    # ATR 14
    # =========================

    previous_close = df["close"].shift(1)

    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (
                df["high"] - previous_close
            ).abs(),
            (
                df["low"] - previous_close
            ).abs()
        ],
        axis=1
    ).max(axis=1)

    df["atr"] = (
        true_range
        .rolling(14)
        .mean()
    )

    # =========================
    # Last CLOSED 1H candle
    # =========================

    x = df.iloc[-2]

    price = float(x["close"])
    ema200 = float(x["ema200"])
    rsi = float(x["rsi"])
    atr = float(x["atr"])

    if not np.isfinite(atr):
        return None

    if atr <= 0:
        return None

    # =========================
    # LONG
    # =========================

    long_condition = (
        price > ema200
        and rsi >= 55
        and rsi <= 70
    )

    # =========================
    # SHORT
    # =========================

    short_condition = (
        price < ema200
        and rsi >= 30
        and rsi <= 45
    )

    # =========================
    # LONG SIGNAL
    # =========================

    if long_condition:

        entry = price

        sl = entry - (
            1.5 * atr
        )

        risk = entry - sl

        tp1 = entry + risk
        tp2 = entry + (2 * risk)
        tp3 = entry + (3 * risk)

        return {
            "side": "LONG",
            "symbol": symbol,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "rsi": rsi,
            "ema200": ema200
        }

    # =========================
    # SHORT SIGNAL
    # =========================

    if short_condition:

        entry = price

        sl = entry + (
            1.5 * atr
        )

        risk = sl - entry

        tp1 = entry - risk
        tp2 = entry - (2 * risk)
        tp3 = entry - (3 * risk)

        return {
            "side": "SHORT",
            "symbol": symbol,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "rsi": rsi,
            "ema200": ema200
        }

    return None


def main():

    print("================================")
    print("BINANCE FUTURES 1H SCANNER")
    print("================================")

    signals = []

    symbols = get_symbols()

    print(
        f"Scanning {len(symbols)} symbols..."
    )

    for symbol in symbols:

        try:

            result = analyze(symbol)

            if result:

                print(
                    f"SIGNAL: "
                    f"{result['side']} "
                    f"{symbol}"
                )

                signals.append(result)

        except Exception as error:

            print(
                f"ERROR {symbol}: {error}"
            )

    print(
        f"TOTAL SIGNALS: {len(signals)}"
    )

    # =========================
    # No signals
    # =========================

    if not signals:

        telegram(
            "🔎 Binance Futures 1H Scanner\n\n"
            "Այս պահին ուժեղ LONG/SHORT "
            "setup չկա։"
        )

        return

    # =========================
    # Strongest signals
    # =========================

    signals = sorted(
        signals,
        key=lambda x: abs(
            x["rsi"] - 50
        ),
        reverse=True
    )

    signals = signals[:3]

    # =========================
    # Telegram message
    # =========================

    message = (
        "🚨 BINANCE FUTURES 1H SIGNALS\n\n"
    )

    for signal in signals:

        emoji = (
            "🟢"
            if signal["side"] == "LONG"
            else "🔴"
        )

        message += (
            f"{emoji} {signal['side']} — "
            f"{signal['symbol']}\n"
            f"Entry: "
            f"{signal['entry']:.8g}\n"
            f"SL: "
            f"{signal['sl']:.8g}\n"
            f"TP1: "
            f"{signal['tp1']:.8g}\n"
            f"TP2: "
            f"{signal['tp2']:.8g}\n"
            f"TP3: "
            f"{signal['tp3']:.8g}\n"
            f"RSI: "
            f"{signal['rsi']:.1f}\n"
            f"EMA200: "
            f"{signal['ema200']:.8g}\n\n"
        )

    telegram(message)


if __name__ == "__main__":
    main()
