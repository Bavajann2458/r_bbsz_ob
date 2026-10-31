import ccxt
import pandas as pd
import numpy as np
import time
import requests
from tabulate import tabulate

# --- 1. CONFIGURATION & CREDENTIALS ---
TELEGRAM_BOT_TOKEN = "8826743783:AAHJHhSsRhBCzORo-5MoQi1gA05Dznoaksk"
TELEGRAM_CHAT_ID = "8307615821"

# Using Kraken public data feed (Zero geo-blocking / location restriction issues)
EXCHANGE = ccxt.kraken({
    'enableRateLimit': True,
    'timeout': 30000,
})

TIMEFRAMES_ENTRY = ['5m', '15m']
TIMEFRAME_TREND = '4h'
TOP_N_PAIRS = 30
ALERTS_SENT = set()

def send_telegram_alert(title, message_body):
    formatted_message = (
        f"🎯 *{title}* 🎯\n"
        f"-----------------------------------\n"
        f"{message_body}\n"
        f"-----------------------------------\n"
        f"🤖 *Wowbot Precision Engine Active*"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": formatted_message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")

def fetch_top_usd_pairs():
    try:
        tickers = EXCHANGE.fetch_tickers()
        usd_pairs = [
            s for s, t in tickers.items() 
            if (s.endswith('/USDT') or s.endswith('/USD')) and t.get('quoteVolume') is not None
        ]
        sorted_pairs = sorted(usd_pairs, key=lambda x: tickers[x]['quoteVolume'], reverse=True)
        return sorted_pairs[:TOP_N_PAIRS]
    except Exception as e:
        print(f"[ERROR] Fetching exchange tickers failed: {e}")
        return []

def calculate_indicators(df, period=14):
    if len(df) < 50:
        return df
    
    close = df['close']
    volume = df['volume']
    
    # RSI
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    # Bollinger Bands (Very Tight Squeeze)
    df['bb_mid'] = close.rolling(window=20).mean()
    rolling_std = close.rolling(window=20).std()
    df['bb_upper'] = df['bb_mid'] + (rolling_std * 2.0)
    df['bb_lower'] = df['bb_mid'] - (rolling_std * 2.0)
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']
    df['bb_squeeze'] = df['bb_width'] < df['bb_width'].rolling(20).mean() * 0.20
    
    df['vol_sma'] = volume.rolling(window=20).mean()
    return df

def check_order_block(df, is_long):
    if len(df) < 25:
        return False, 0.0, 0.0
        
    curr_price = df.iloc[-1]['close']
    
    for i in range(len(df) - 5, 10, -1):
        prev_row = df.iloc[i]
        next_row = df.iloc[i+1]
        
        if is_long:
            if prev_row['close'] < prev_row['open'] and next_row['close'] > next_row['open']:
                body_prev = abs(prev_row['close'] - prev_row['open'])
                body_next = abs(next_row['close'] - next_row['open'])
                if body_next >= (body_prev * 0.9):
                    if prev_row['low'] <= curr_price <= (prev_row['high'] * 1.02):
                        return True, prev_row['low'], prev_row['high']
        else:
            if prev_row['close'] > prev_row['open'] and next_row['close'] < next_row['open']:
                body_prev = abs(prev_row['close'] - prev_row['open'])
                body_next = abs(next_row['close'] - next_row['open'])
                if body_next >= (body_prev * 0.9):
                    if (prev_row['low'] * 0.98) <= curr_price <= prev_row['high']:
                        return True, prev_row['low'], prev_row['high']
                        
    return False, 0.0, 0.0

def scan_markets():
    print("="*75)
    print(" WOWBOT: TIGHT SQUEEZE + RSI EXTREMES + ORDER BLOCK SCANNER ")
    print("="*75)
    
    pairs = fetch_top_usd_pairs()
    if not pairs:
        print("[WARNING] No pairs fetched.")
        return

    print(f"\n[SCANNING] Sweeping {len(pairs)} pairs across timeframes...\n")
    
    scan_results = []
    for idx, symbol in enumerate(pairs):
        print(f"[{idx+1}/{len(pairs)}] Scanning {symbol}...", end='\r')
        
        try:
            trend_ohlcv = EXCHANGE.fetch_ohlcv(symbol, TIMEFRAME_TREND, limit=30)
            if len(trend_ohlcv) < 25:
                continue
            df_trend = pd.DataFrame(trend_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_trend = calculate_indicators(df_trend)
            trend_rsi = df_trend.iloc[-1]['rsi']
            
            macro_bullish_4h = trend_rsi < 20
            macro_bearish_4h = trend_rsi > 80
            
            for tf in TIMEFRAMES_ENTRY:
                ohlcv = EXCHANGE.fetch_ohlcv(symbol, tf, limit=50)
                if len(ohlcv) < 40:
                    continue
                    
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df = calculate_indicators(df)
                
                curr = df.iloc[-1]
                rsi = curr['rsi']
                is_squeezing = curr['bb_squeeze']
                price = curr['close']
                volume = curr['volume']
                vol_sma = curr['vol_sma']
                
                if not is_squeezing or pd.isna(rsi) or volume < (vol_sma * 0.8):
                    continue
                
                is_long = (rsi < 20) and macro_bullish_4h
                is_short = (rsi > 80) and macro_bearish_4h
                
                if is_long or is_short:
                    has_ob, ob_low, ob_high = check_order_block(df, is_long)
                    if not has_ob:
                        continue
                        
                    direction = "🟢 LONG" if is_long else "🔴 SHORT"
                    alert_key = f"{symbol}_{tf}_{'LONG' if is_long else 'SHORT'}"
                    price_str = f"{price:,.4f}" if price < 1 else f"{price:,.2f}"
                    
                    scan_results.append({
                        'Coin': symbol,
                        'TF': tf,
                        'Setup': direction,
                        'RSI': f"{rsi:.1f}",
                        '4H RSI': f"{trend_rsi:.1f}",
                        'OB Low': f"{ob_low:,.4f}",
                        'OB High': f"{ob_high:,.4f}",
                        'Price': price_str
                    })
                    
                    if alert_key not in ALERTS_SENT:
                        send_telegram_alert(
                            title="PRECISION SETUP DETECTED",
                            message_body=(
                                f"Pair: `{symbol}`\n"
                                f"TF: `{tf}` | 4H RSI: `{trend_rsi:.1f}`\n"
                                f"Direction: *{direction}*\n"
                                f"RSI: `{rsi:.2f}`\n"
                                f"Order Block Zone: `{ob_low:,.4f} - {ob_high:,.4f}`\n"
                                f"Price: `{price_str}`"
                            )
                        )
                        ALERTS_SENT.add(alert_key)
        except Exception:
            continue
        time.sleep(0.01)

    print("\n" + "="*85)
    print(f"       SCAN COMPLETE — {time.strftime('%Y-%m-%d %H:%M:%S')}       ")
    print("="*85)
    if scan_results:
        print(tabulate(scan_results, headers="keys", tablefmt="grid", showindex=False))
    else:
        print("No matching assets found in this cycle.")
    print("="*85)

if __name__ == "__main__":
    scan_markets()
