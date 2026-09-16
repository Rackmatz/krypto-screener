"""
Krypto-Alarmsystem: Top 30 nach Marktkap (ohne Stablecoins), sendet
Telegram-Nachricht bei auffälligen Signalen (RSI-Extreme, Volumen-Spikes,
Nähe zu 7-Tage-Levels).

Läuft eigenständig (z.B. per GitHub Actions Cronjob) - braucht keinen
Server, nur die zwei Umgebungsvariablen TELEGRAM_BOT_TOKEN und
TELEGRAM_CHAT_ID.
"""

import os
import sys
import requests

# ---------------------------------------------------------------------------
# Konfiguration - hier kannst du die Kriterien anpassen
# ---------------------------------------------------------------------------

TOP_N = 30           # so viele "echte" Coins sollen am Ende geprueft werden
FETCH_N = 50         # es wird mehr abgerufen, weil Stablecoins rausgefiltert werden
RSI_PERIOD = 14

# Symbole, die als Stablecoins gelten und ausgeschlossen werden (Kleinschreibung)
STABLECOIN_SYMBOLS = {
    "usdt", "usdc", "dai", "fdusd", "usde", "tusd", "usdd", "frax",
    "gusd", "usds", "pyusd", "busd", "eurs", "usdy", "usdp", "susd",
    "lusd", "crvusd", "eure",
}
RSI_OVERBOUGHT = 72
RSI_OVERSOLD = 28
CHANGE_24H_STRONG = 8.0      # % - starke Bewegung
CHANGE_24H_MEDIUM = 4.0      # % - moderate Bewegung
CHANGE_1H_SPIKE = 2.5        # % - plötzlicher Ausschlag
NEAR_HIGH_LOW_PCT = 0.02     # 2% Nähe zu 7-Tage-Hoch/Tief
VOL_MCAP_HIGH = 0.35         # sehr hohes Volumen relativ zur Marktkap
VOL_MCAP_ELEVATED = 0.18     # erhöhtes Volumen

# Nur senden, wenn mindestens ein Coin ein Signal hat (spart Spam).
# Auf True setzen, wenn du auch bei ruhigem Markt eine Nachricht willst.
ALWAYS_SEND = False

MAX_COINS_IN_MESSAGE = 10

WATCHLIST = {"bitcoin", "ethereum", "ripple", "solana", "hyperliquid"}

COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&order=market_cap_desc"
    f"&per_page={FETCH_N}&page=1&sparkline=true"
    "&price_change_percentage=1h,24h,7d"
)


# ---------------------------------------------------------------------------
# Indikatoren
# ---------------------------------------------------------------------------

def compute_rsi(prices, period=RSI_PERIOD):
    if not prices or len(prices) < period + 1:
        return None
    window = prices[-(period + 1):]
    gains = losses = 0.0
    for i in range(1, len(window)):
        diff = window[i] - window[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def build_signals(coin):
    prices = (coin.get("sparkline_in_7d") or {}).get("price") or []
    rsi = compute_rsi(prices)
    high7d = max(prices) if prices else None
    low7d = min(prices) if prices else None
    current = coin.get("current_price") or 0
    market_cap = coin.get("market_cap") or 0
    volume = coin.get("total_volume") or 0
    vol_mcap = (volume / market_cap) if market_cap else 0

    chg24 = coin.get("price_change_percentage_24h_in_currency") or 0
    chg1 = coin.get("price_change_percentage_1h_in_currency") or 0

    flags = []
    score = 0

    if abs(chg24) >= CHANGE_24H_STRONG:
        flags.append(f"{'Starker Anstieg' if chg24 >= 0 else 'Starker Rueckgang'} 24h ({chg24:+.1f}%)")
        score += 2
    elif abs(chg24) >= CHANGE_24H_MEDIUM:
        flags.append(f"Bewegung 24h ({chg24:+.1f}%)")
        score += 1

    if abs(chg1) >= CHANGE_1H_SPIKE:
        flags.append(f"1h-Ausschlag ({chg1:+.1f}%)")
        score += 1.5

    if rsi is not None:
        if rsi >= RSI_OVERBOUGHT:
            flags.append(f"RSI ueberkauft ({rsi:.0f})")
            score += 1.5
        elif rsi <= RSI_OVERSOLD:
            flags.append(f"RSI ueberverkauft ({rsi:.0f})")
            score += 1.5

    if high7d and current >= high7d * (1 - NEAR_HIGH_LOW_PCT):
        flags.append("Nahe 7T-Hoch")
        score += 1.5
    if low7d and current <= low7d * (1 + NEAR_HIGH_LOW_PCT):
        flags.append("Nahe 7T-Tief")
        score += 1

    if vol_mcap >= VOL_MCAP_HIGH:
        flags.append("Sehr hohes Volumen")
        score += 2
    elif vol_mcap >= VOL_MCAP_ELEVATED:
        flags.append("Erhoehtes Volumen")
        score += 1

    return {"flags": flags, "score": score, "rsi": rsi}


# ---------------------------------------------------------------------------
# Datenabruf & Nachricht
# ---------------------------------------------------------------------------

def fetch_coins():
    resp = requests.get(COINGECKO_URL, timeout=20)
    resp.raise_for_status()
    coins = resp.json()

    # Stablecoins raus, dann auf TOP_N "echte" Coins begrenzen
    filtered = [c for c in coins if c.get("symbol", "").lower() not in STABLECOIN_SYMBOLS]
    return filtered[:TOP_N]


def build_message(rows):
    flagged = [r for r in rows if r["signal"]["flags"]]
    flagged.sort(key=lambda r: r["signal"]["score"], reverse=True)

    if not flagged and not ALWAYS_SEND:
        return None

    lines = ["<b>Krypto-Screener - Top 30</b>"]
    if not flagged:
        lines.append("Kein auffaelliges Signal gerade - ruhiger Markt.")
        return "\n".join(lines)

    for r in flagged[:MAX_COINS_IN_MESSAGE]:
        coin = r["coin"]
        pin = "\u2b50 " if coin["id"] in WATCHLIST else ""
        symbol = coin["symbol"].upper()
        price = coin["current_price"]
        lines.append(f"\n{pin}<b>{symbol}</b> - ${price:,.4g}")
        for f in r["signal"]["flags"]:
            lines.append(f"  \u2022 {f}")

    return "\n".join(lines)


def send_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN oder TELEGRAM_CHAT_ID fehlt.", file=sys.stderr)
        sys.exit(1)

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
        timeout=20,
    )
    resp.raise_for_status()


def main():
    try:
        coins = fetch_coins()
    except Exception as e:
        print(f"Fehler beim Abrufen der Marktdaten: {e}", file=sys.stderr)
        sys.exit(1)

    rows = [{"coin": c, "signal": build_signals(c)} for c in coins]
    message = build_message(rows)

    if message is None:
        print("Keine Signale, keine Nachricht gesendet (ALWAYS_SEND=False).")
        return

    send_telegram(message)
    print("Nachricht gesendet.")


if __name__ == "__main__":
    main()
