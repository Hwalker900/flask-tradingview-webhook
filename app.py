from flask import Flask, request
import requests
import datetime
import time
import threading
import yfinance as yf
import pandas as pd
import numpy as np
import ccxt

app = Flask(__name__)

# --- Config ---
BOT_TOKEN = "7776677134:AAGJo3VfwiB5gDpCE5e5jvtHonhTcjv-NWc"
CHAT_ID = "@Supercellsignals"
NEWS_API_KEY = "pub_80721acdf58f8a7b7a1d99e149c28a6ebfcc5"
NEWS_API_URL = "https://newsdata.io/api/1/news"

# --- Valid Pairs ---
VALID_PAIRS = {'BABA', 'TSLA', 'BTCUSD', 'CADJPY', 'USDHUF', 'USDJPY'}

# --- Data Store ---
daily_signals = []
last_summary_sent = None

# --- Telegram Sender ---
def send_telegram_message(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    print(f"Sending Telegram message (length: {len(msg)}):\n{msg}")
    if len(msg) > 4096:
        msg = msg[:4000] + "\n*Message truncated due to length.*"
        print("⚠️ Message truncated to fit Telegram limit.")

    payload = {
        "chat_id": CHAT_ID,
        "text": msg,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, data=payload)
        response.raise_for_status()
        print(f"✅ Message sent to Telegram. Response: {response.json()}")
    except Exception as e:
        print(f"❌ Telegram error: {e}, Response: {response.text if 'response' in locals() else 'No response'}")

# --- News Analysis ---
def get_news_analysis(pair):
    query = pair.replace('/', '')
    url = f"{NEWS_API_URL}?apikey={NEWS_API_KEY}&q={query}&language=en"
    print(f"Calling Newsdata API: {url}")
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        
        headlines = []
        score = 0
        now = datetime.datetime.now().timestamp()
        
        for article in data.get("results", [])[:3]:
            title = article.get("title", "")
            published = article.get("pubDate", "")
            try:
                timestamp = datetime.datetime.strptime(published, "%Y-%m-%d %H:%M:%S").timestamp()
            except:
                timestamp = now
            age_hours = (now - timestamp) / 3600
            
            weight = max(1 - age_hours / 24, 0.1)
            if any(word in title.lower() for word in ["rise", "bull", "strong", "up"]):
                score += 10 * weight
            elif any(word in title.lower() for word in ["fall", "bear", "weak", "down"]):
                score -= 10 * weight
            
            headlines.append(f"📰 {title}")
        
        if not headlines:
            headlines = ["📰 No recent news found."]
        
        sentiment = "Positive" if score > 5 else "Negative" if score < -5 else "Neutral"
        confidence = min(max(int(abs(score) * 10), 50), 90)
        return sentiment, headlines, confidence
    except Exception as e:
        print(f"❌ Error fetching news: {e}")
        return "Neutral", ["📰 No recent news found."], 50

# --- Technical Analysis ---
def get_technical_analysis(pair):
    try:
        # Handle stocks and crypto
        if pair == 'BTCUSD':
            ticker = 'BTC-USD'
        elif pair in {'BABA', 'TSLA'}:
            ticker = pair
        else:
            ticker = pair.replace('/', '') + '=X'
        print(f"Fetching yfinance data for {ticker}")
        
        # Try yfinance first
        for attempt in range(3):
            try:
                data = yf.download(ticker, period="14d", interval="1d", progress=False, auto_adjust=False)
                if not data.empty and len(data) >= 14:
                    break
                print(f"Attempt {attempt + 1}: Insufficient data for {ticker}")
                time.sleep(1)
            except Exception as e:
                print(f"Attempt {attempt + 1}: Error fetching {ticker}: {e}")
                time.sleep(1)
        else:
            # Fallback for forex pairs using ccxt with Kraken
            if pair in {'CADJPY', 'USDHUF', 'USDJPY'}:
                print(f"Falling back to ccxt Kraken for {pair}")
                try:
                    exchange = ccxt.kraken()
                    symbol = f"{pair[:3]}/{pair[3:]}"
                    ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1d', limit=14)
                    if len(ohlcv) < 14:
                        print(f"Insufficient data from ccxt Kraken for {pair}")
                        return "Neutral", ["📏 Technical analysis unavailable."], 50
                    data = pd.DataFrame(ohlcv, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
                    data['Timestamp'] = pd.to_datetime(data['Timestamp'], unit='ms')
                    data.set_index('Timestamp', inplace=True)
                    data = data[['Open', 'High', 'Low', 'Close', 'Volume']]
                except Exception as e:
                    print(f"CCXT Kraken fallback failed for {pair}: {e}")
                    return "Neutral", ["📏 Technical analysis unavailable."], 50
            else:
                print(f"No sufficient data for {ticker} after 3 attempts")
                return "Neutral", ["📏 Technical analysis unavailable."], 50
        
        # Calculate RSI
        delta = data['Close'].diff()
        print(f"Delta shape: {delta.shape}, Delta last: {delta.iloc[-1]}")
        gain = delta.where(delta > 0, 0).rolling(window=14, min_periods=1).mean()
        loss = -delta.where(delta < 0, 0).rolling(window=14, min_periods=1).mean()
        print(f"Gain shape: {gain.shape}, Gain last: {gain.iloc[-1]}")
        print(f"Loss shape: {loss.shape}, Loss last: {loss.iloc[-1]}")
        
        if gain.isna().all() or loss.isna().all():
            print(f"Invalid gain or loss data for {ticker}")
            return "Neutral", ["📏 Technical analysis unavailable."], 50
        
        rs = gain / (loss.where(loss != 0, 1e-10))
        print(f"RS shape: {rs.shape}, RS last: {rs.iloc[-1]}")
        rs = rs.fillna(0).replace([np.inf, -np.inf], 0)
        rsi = 100 - (100 / (1 + rs))
        print(f"RSI shape: {rsi.shape}, RSI last: {rsi.iloc[-1]}")
        
        if rsi.isna().all():
            print(f"RSI calculation failed for {ticker}")
            return "Neutral", ["📏 Technical analysis unavailable."], 50
        latest_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50
        print(f"Latest RSI: {latest_rsi}")

        # Calculate MACD
        exp1 = data['Close'].ewm(span=12, adjust=False).mean()
        exp2 = data['Close'].ewm(span=26, adjust=False).mean()
        macd = exp1 - exp2
        signal_line = macd.ewm(span=9, adjust=False).mean()
        print(f"MACD shape: {macd.shape}, Signal shape: {signal_line.shape}")
        latest_macd = macd.iloc[-1] - signal_line.iloc[-1]
        print(f"Latest MACD: {latest_macd}")

        # Calculate Volume Trend
        volume = data['Volume']
        print(f"Volume shape: {volume.shape}, Volume last: {volume.iloc[-1]}")
        if volume.isna().all():
            print(f"Invalid volume data for {ticker}")
            return "Neutral", ["📏 Technical analysis unavailable."], 50
        volume_mean = volume.mean()
        volume_last = volume.iloc[-1]
        volume_trend = "High" if volume_last > volume_mean else "Low"
        print(f"Volume Trend: {volume_trend}")
        
        tech_score = 0
        if latest_rsi > 70:
            tech_score -= 10
        elif latest_rsi < 30:
            tech_score += 10
        if latest_macd > 0:
            tech_score += 10
        elif latest_macd < 0:
            tech_score -= 10
        if volume_trend == "High":
            tech_score += 5
        
        tech_sentiment = "Positive" if tech_score > 5 else "Negative" if tech_score < -5 else "Neutral"
        confidence = min(max(int(abs(tech_score) * 10), 50), 90)
        
        indicators = [
            f"📏 *RSI*: {round(latest_rsi, 2)} ({'High' if latest_rsi > 70 else 'Low' if latest_rsi < 30 else 'Neutral'})",
            f"📈 *MACD*: {'Uptrend' if latest_macd > 0 else 'Downtrend'}",
            f"📊 *Volume*: {volume_trend}"
        ]
        
        return tech_sentiment, indicators, confidence
    except Exception as e:
        print(f"❌ Error fetching technical data for {pair}: {e}")
        return "Neutral", ["📏 Technical analysis unavailable."], 50

# --- Market Sentiment ---
def get_market_sentiment(news_sentiment, tech_sentiment):
    sentiment_score = 0
    if news_sentiment == "Positive":
        sentiment_score += 10
    elif news_sentiment == "Negative":
        sentiment_score -= 10
    if tech_sentiment == "Positive":
        sentiment_score += 10
    elif tech_sentiment == "Negative":
        sentiment_score -= 10
    overall_sentiment = "Positive" if sentiment_score > 10 else "Negative" if sentiment_score < -10 else "Neutral"
    return overall_sentiment

# --- Explanation Generator ---
def get_simple_explanation(signal, pair, news_sentiment, tech_sentiment, market_sentiment):
    print(f"Generating explanation for pair: {pair}, signal: {signal}")
    try:
        is_stock = pair in {'BABA', 'TSLA'}
        if is_stock:
            base = pair
        else:
            if '/' in pair:
                base, _ = pair.split('/')
            elif len(pair) >= 6:
                base = pair[:3]
            else:
                base = pair
        print(f"Base: {base}{' (Stock)' if is_stock else ''}")
        
        if signal == "BUY":
            if market_sentiment == "Positive":
                return f"😊 Things are looking good for {base}! News and charts suggest a potential rise."
            elif market_sentiment == "Negative":
                return f"⚠️ Charts suggest buying {base}, but news and trends are not as favorable."
            else:
                return f"🤔 {base} might go up, but the news and charts are mixed."
        else:
            if market_sentiment == "Negative":
                return f"😟 {base} might go down, and news and charts agree."
            elif market_sentiment == "Positive":
                return f"⚠️ Selling {base} is suggested, but news and trends are positive."
            else:
                return f"🤔 {base} might drop, but news and charts are unclear."
    except Exception as e:
        print(f"❌ Error in get_simple_explanation: {e}")
        return f"🤔 Signal for {pair} received, but news and charts are unclear."

# --- Message Formatter ---
def format_message(pair, signal, entry, timestamp):
    print(f"Starting format_message for pair: {pair}, signal: {signal}, entry: {entry}, timestamp: {timestamp}")
    try:
        if not isinstance(timestamp, str) or not timestamp.endswith('Z'):
            print(f"Invalid timestamp format: {timestamp}, using current UTC time")
            readable_time = datetime.datetime.utcnow().strftime('%d %b %H:%M UTC')
        else:
            print("Parsing timestamp")
            try:
                dt = datetime.datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
                readable_time = dt.strftime('%d %b %H:%M UTC')
                print(f"Parsed timestamp: {readable_time}")
            except ValueError as e:
                print(f"Timestamp parsing error: {e}, using current UTC time")
                readable_time = datetime.datetime.utcnow().strftime('%d %b %H:%M UTC')
        
        print("Fetching news analysis")
        news_sentiment, headlines, news_confidence = get_news_analysis(pair)
        
        print("Fetching technical analysis")
        tech_sentiment, indicators, tech_confidence = get_technical_analysis(pair)
        print(f"Technical analysis result: {tech_sentiment}, {indicators}, {tech_confidence}")
        
        print("Calculating market sentiment")
        market_sentiment = get_market_sentiment(news_sentiment, tech_sentiment)
        print("Generating explanation")
        explanation = get_simple_explanation(signal, pair, news_sentiment, tech_sentiment, market_sentiment)
        print(f"Explanation: {explanation}")
        
        confidence = round((news_confidence + tech_confidence) / 2)
        print(f"Computed confidence: {confidence}")
        
        print("Constructing message")
        if pair in {'BABA', 'TSLA'}:
            display_pair = pair
        elif pair in {'BTCUSD', 'CADJPY', 'USDHUF', 'USDJPY'}:
            display_pair = f"{pair[:3]}/{pair[3:]}"
        else:
            display_pair = pair
        message = f"""
*🌟 New Signal Alert!*

💱 *{'Stock' if pair in {'BABA', 'TSLA'} else 'Pair'}*: {display_pair}
📢 *Action*: {'📈 Buy' if signal == 'BUY' else '📉 Sell'}
💵 *Price*: {entry}
🕒 *Time*: {readable_time}

*📰 Latest News:*
{chr(10).join(headlines)}

*📊 Chart Insights:*
{chr(10).join(indicators)}

*🌍 Market Mood*: {market_sentiment}

*💡 Why This Signal?*
{explanation}

*🔒 Confidence*: {confidence}/100

*✅ Tip*: Always double-check and protect your investment!
"""
        print(f"Message constructed:\n{message}")
        return message, confidence
    except Exception as e:
        print(f"❌ Error in format_message: {e}")
        readable_time = datetime.datetime.utcnow().strftime('%d %b %H:%M UTC')
        if pair in {'BABA', 'TSLA'}:
            display_pair = pair
        elif pair in {'BTCUSD', 'CADJPY', 'USDHUF', 'USDJPY'}:
            display_pair = f"{pair[:3]}/{pair[3:]}"
        else:
            display_pair = pair
        message = f"""
*🌟 Signal Alert Error!*

💱 *{'Stock' if pair in {'BABA', 'TSLA'} else 'Pair'}*: {display_pair}
📢 *Action*: {'📈 Buy' if signal == 'BUY' else '📉 Sell'}
💵 *Price*: {entry}
🕒 *Time*: {readable_time}

*📰 Latest News:*
📰 No news available.

*📊 Chart Insights:*
📏 Technical analysis unavailable.

*🌍 Market Mood*: Neutral

*💡 Why This Signal?*
Error processing signal. Please check manually.

*🔒 Confidence*: 50/100

*✅ Tip*: Always double-check and protect your investment!
"""
        print(f"Error message constructed:\n{message}")
        return message, 50

# --- Webhook Handler ---
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    print("🔔 Incoming webhook payload:", data)
    try:
        pair = data.get('pair')
        signal = data.get('signal')
        entry = data.get('entry')
        alert_time = data.get('time')

        if not signal or not pair or not entry:
            print("⚠️ Missing required fields in payload.")
            return "Incomplete data", 400
        
        # Normalize pair
        original_pair = pair
        if pair not in VALID_PAIRS and '/' not in pair and len(pair) >= 6:
            pair = f"{pair[:3]}/{pair[3:]}"
        print(f"Normalized pair: {pair}")
        
        # Validate pair
        pair_key = original_pair if original_pair in VALID_PAIRS else pair
        if pair_key not in VALID_PAIRS:
            print(f"⚠️ Invalid pair: {original_pair} (normalized to {pair}) not in {VALID_PAIRS}")
            return f"Invalid pair: {original_pair}", 400

        signal = signal.upper()
        if signal not in ['BUY', 'SELL']:
            print(f"⚠️ Invalid signal: {signal}")
            return "Invalid signal", 400

        message, confidence = format_message(pair_key, signal, entry, alert_time)
        print(f"format_message result: (message length: {len(message)}, confidence: {confidence})")
        daily_signals.append({"pair": pair_key, "signal": signal, "confidence": confidence})
        send_telegram_message(message)
        return "Webhook received!", 200
    except Exception as e:
        print(f"❌ Webhook error: {e}")
        return "Error processing webhook", 500

# --- Daily Summary ---
def send_daily_summary():
    global last_summary_sent
    utc_now = datetime.datetime.utcnow()
    if utc_now.hour != 21 or (last_summary_sent and last_summary_sent.date() == utc_now.date()):
        return
        
    if not daily_signals:
        return
        
    today = utc_now.strftime('%d %b')
    lines = [f"*📅 Today's Signals – {today}*"]
    for s in daily_signals:
        emoji = "📈" if s['signal'] == 'BUY' else "📉"
        lines.append(f"💱 {s['pair']}: {emoji} {s['signal']} (Confidence: {s['confidence']}/100)")
    lines.append("\n🌟 Review these and plan your next move!")
    summary = '\n'.join(lines)
    send_telegram_message(summary)
    daily_signals.clear()
    last_summary_sent = utc_now

# --- Scheduler Thread ---
def background_tasks():
    while True:
        send_daily_summary()
        time.sleep(600)

# --- App Startup ---
if __name__ == '__main__':
    threading.Thread(target=background_tasks, daemon=True).start()
    import os
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port)
