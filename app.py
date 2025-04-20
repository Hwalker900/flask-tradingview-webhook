from flask import Flask, request
import requests
import datetime
import time
import threading
import yfinance as yf
import pandas as pd
import numpy as np

app = Flask(__name__)

# --- Config ---
BOT_TOKEN = "7776677134:AAGJo3VfwiB5gDpCE5e5jvtHonhTcjv-NWc"
CHAT_ID = "@Supercellsignals"
NEWS_API_KEY = "pub_80721acdf58f8a7b7a1d99e149c28a6ebfcc5"
NEWS_API_URL = "https://newsdata.io/api/1/news"

# --- Data Store ---
daily_signals = []
last_summary_sent = None

# --- Telegram Sender ---
def escape_markdown_v2(text):
    reserved_chars = r'_[](){}~>#+-=|.!'
    for char in reserved_chars:
        text = text.replace(char, f'\\{char}')
    return text

def send_telegram_message(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    print(f"Sending Telegram message (length: {len(msg)}):\n{msg}")
    # Escape the entire message for MarkdownV2
    escaped_msg = escape_markdown_v2(msg)

    if len(escaped_msg) > 4096:
        escaped_msg = escaped_msg[:4000] + "\n*Message truncated due to length.*"
        print("⚠️ Message truncated to fit Telegram limit.")

    payload = {
        "chat_id": CHAT_ID,
        "text": escaped_msg,
        "parse_mode": "MarkdownV2"
    }

    try:
        response = requests.post(url, data=payload)
        response.raise_for_status()
        print(f"✅ Message sent to Telegram (MarkdownV2). Response: {response.json()}")
    except Exception as e:
        print(f"❌ Telegram MarkdownV2 error: {e}, Response: {response.text if 'response' in locals() else 'No response'}")
        # Fallback to plain text
        payload["parse_mode"] = None
        payload["text"] = msg
        try:
            response = requests.post(url, data=payload)
            response.raise_for_status()
            print(f"✅ Message sent to Telegram (plain text fallback). Response: {response.json()}")
        except Exception as e:
            print(f"❌ Telegram plain text error: {e}, Response: {response.text if 'response' in locals() else 'No response'}")

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
            title = escape_markdown_v2(title)  # Use escape function
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

        sentiment = "Positive" if score > 5 else "Negative" if score < -5 else "Neutral"
        confidence = min(max(int(abs(score) * 10), 50), 90)
        return sentiment, headlines, confidence
    except Exception as e:
        print(f"❌ Error fetching news: {e}")
        return "Neutral", ["No recent news found."], 50

# --- Technical Analysis ---
def get_technical_analysis(pair):
    try:
        # Handle cryptocurrencies
        if pair == 'BTC/USD':
            ticker = 'BTC-USD'
        # Check if pair is a stock (no '/' and length < 6)
        elif '/' not in pair and len(pair) < 6:
            ticker = pair
        else:
            ticker = pair.replace('/', '') + '=X'
        print(f"Fetching yfinance data for {ticker}")
        data = yf.download(ticker, period="14d", interval="1d", progress=False)

        if data.empty or len(data) < 14:
            print(f"No sufficient data for {ticker}")
            return "Neutral", ["No price data available for this pair."], 50

        # Calculate RSI
        delta = data['Close'].diff()
        print(f"Delta shape: {delta.shape}, Delta last: {delta.iloc[-1]}")
        gain = delta.where(delta > 0, 0).rolling(window=14, min_periods=1).mean()
        loss = -delta.where(delta < 0, 0).rolling(window=14, min_periods=1).mean()
        print(f"Gain shape: {gain.shape}, Gain last: {gain.iloc[-1]}")
        print(f"Loss shape: {loss.shape}, Loss last: {loss.iloc[-1]}")

        if isinstance(gain, pd.DataFrame):
            gain = gain[ticker] if ticker in gain.columns else gain.iloc[:, 0]
        if isinstance(loss, pd.DataFrame):
            loss = loss[ticker] if ticker in loss.columns else loss.iloc[:, 0]

        if gain.isna().all() or loss.isna().all():
            print(f"Invalid gain or loss data for {ticker}")
            return "Neutral", ["Invalid price data for RSI calculation."], 50

        rs = gain.div(loss.where(loss != 0, 1e-10))
        print(f"RS shape: {rs.shape}, RS last: {rs.iloc[-1]}")
        rs = rs.fillna(0).replace([np.inf, -np.inf], 0)
        rsi = 100 - (100 / (1 + rs))
        print(f"RSI shape: {rsi.shape}, RSI last: {rsi.iloc[-1]}")

        if rsi.isna().all():
            print(f"RSI calculation failed for {ticker}")
            return "Neutral", ["RSI calculation failed."], 50
        latest_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50
        print(f"Latest RSI: {latest_rsi}")

        # Calculate MACD
        exp1 = data['Close'].ewm(span=12, adjust=False).mean()
        exp2 = data['Close'].ewm(span=26, adjust=False).mean()
        macd = exp1 - exp2
        signal_line = macd.ewm(span=9, adjust=False).mean()
        print(f"MACD shape: {macd.shape}, Signal shape: {signal_line.shape}")
        latest_macd = macd.iloc[-1] - signal_line.iloc[-1]
        if isinstance(latest_macd, pd.Series):
            latest_macd = latest_macd[ticker] if ticker in latest_macd else latest_macd.iloc[0]
        print(f"Latest MACD: {latest_macd}")

        # Calculate Volume Trend
        volume = data['Volume']
        print(f"Volume shape: {volume.shape}, Volume last: {volume.iloc[-1]}")
        if isinstance(volume, pd.DataFrame):
            volume = volume[ticker] if ticker in volume.columns else volume.iloc[:, 0]
        if volume.isna().all():
            print(f"Invalid volume data for {ticker}")
            return "Neutral", ["Invalid volume data."], 50
        volume_mean = volume.mean()
        volume_last = volume.iloc[-1]
        if isinstance(volume_last, pd.Series):
            volume_last = volume_last[ticker] if ticker in volume_last else volume_last.iloc[0]
        if isinstance(volume_mean, pd.Series):
            volume_mean = volume_mean[ticker] if ticker in volume_mean else volume_mean.iloc[0]
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
        return "Neutral", ["Unable to fetch chart data at this time."], 50

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
        is_stock = '/' not in pair and len(pair) < 6
        if is_stock:
            asset = pair
            base = pair
        else:
            if '/' in pair:
                base, quote = pair.split('/')
            else:
                base, quote = pair[:3], pair[3:] if len(pair) >= 6 else (pair, "USD")
            asset = base
        print(f"Asset: {asset}{' (Stock)' if is_stock else ''}")

        if signal == "BUY":
            if market_sentiment == "Positive":
                return f"😊 Things are looking good for {asset}! News and charts suggest a potential rise."
            elif market_sentiment == "Negative":
                return f"⚠️ Charts suggest buying {asset}, but news and trends are not as favorable."
            else:
                return f"🤔 {asset} might go up, but the news and charts are mixed."
        else:
            if market_sentiment == "Negative":
                return f"😟 {asset} might go down, and news and charts agree."
            elif market_sentiment == "Positive":
                return f"⚠️ Selling {asset} is suggested, but news and trends are positive."
            else:
                return f"🤔 {asset} might drop, but news and charts are unclear."
    except Exception as e:
        print(f"❌ Error in get_simple_explanation: {e}")
        return f"🤔 Signal for {pair} received, but news and charts are unclear."

# --- Message Formatter ---
def format_message(pair, signal, entry, timestamp):
    print(f"Starting format_message for pair: {pair}, signal: {signal}, entry: {entry}, timestamp: {timestamp}")
    try:
        if not isinstance(timestamp, str) or not timestamp.endswith('Z'):
            print(f"Invalid timestamp format: {timestamp}")
            raise ValueError("Timestamp must be a string in ISO 8601 format (ending with Z)")

        print("Parsing timestamp")
        dt = datetime.datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
        readable_time = dt.strftime('%d %b %H:%M UTC')
        print(f"Parsed timestamp: {readable_time}")

        print("Fetching news analysis")
        news_sentiment, headlines, news_confidence = get_news_analysis(pair)

        print("Fetching technical analysis")
        try:
            tech_result = get_technical_analysis(pair)
            print(f"Technical analysis result: {tech_result}")
            if not isinstance(tech_result, tuple) or len(tech_result) != 3:
                print(f"Invalid technical analysis result for {pair}: {tech_result}")
                tech_sentiment = "Neutral"
                indicators = ["Technical analysis unavailable."]
                tech_confidence = 50
            else:
                tech_sentiment, indicators, tech_confidence = tech_result
        except Exception as e:
            print(f"Technical analysis failed for {pair}: {e}")
            tech_sentiment = "Neutral"
            indicators = ["Technical analysis unavailable."]
            tech_confidence = 50

        print("Calculating market sentiment")
        market_sentiment = get_market_sentiment(news_sentiment, tech_sentiment)
        print("Generating explanation")
        explanation = get_simple_explanation(signal, pair, news_sentiment, tech_sentiment, market_sentiment)
        print(f"Explanation: {explanation}")

        confidence = round((news_confidence + tech_confidence) / 2)
        print(f"Computed confidence: {confidence}")

        print("Constructing message")
        message = f"""
🌟 *New Signal Alert!*

💱 *{'Stock' if '/' not in pair and len(pair) < 6 else 'Pair'}: {pair}*
📢 *Action*: {'📈 Buy' if signal == 'BUY' else '📉 Sell'}*
💵 *Price*: {entry}*
🕒 *Time*: {readable_time}*

📰 *Latest News:*
{chr(10).join(headlines)}*

📊 *Chart Insights:*
{chr(10).join(indicators)}*

🌍 *Market Mood*: {market_sentiment}*

💡 *Why This Signal?*
{explanation}*

🔒 *Confidence*: {confidence}/100*

✅ *Tip*: Always double-check and protect your investment!*
"""
        print("Message constructed successfully")
        return message, confidence
    except Exception as e:
        print(f"❌ Error in format_message: {e}")
        try:
            readable_time = datetime.datetime.utcnow().strftime('%d %b %H:%M UTC')
        except:
            readable_time = "Unknown time"
        message = f"""
🌟 *Signal Alert Error!*

💱 *{'Stock' if '/' not in pair and len(pair) < 6 else 'Pair'}: {pair}*
📢 *Action*: {'📈 Buy' if signal == 'BUY' else '📉 Sell'}*
💵 *Price*: {entry}*
🕒 *Time*: {readable_time}*

📰 *Latest News:*
No news available.*

📊 *Chart Insights:*
Technical analysis unavailable.*

🌍 *Market Mood*: Neutral*

💡 *Why This Signal?*
Error processing signal. Please check manually.*

🔒 *Confidence*: 50/100*

✅ *Tip*: Always double-check and protect your investment!*
"""
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

        if not signal or not pair or not entry or not alert_time:
            print("⚠️ Missing required fields in payload.")
            return "Incomplete data", 400

        if '/' not in pair and len(pair) >= 6:
            pair = f"{pair[:3]}/{pair[3:]}"
        print(f"Normalized pair: {pair}")

        signal = signal.upper()
        if signal not in ['BUY', 'SELL']:
            print(f"⚠️ Invalid signal: {signal}")
            return "Invalid signal", 400

        message, confidence = format_message(pair, signal, entry, alert_time)
        print(f"format_message result: (message length: {len(message)}, confidence: {confidence})")
        daily_signals.append({"pair": pair, "signal": signal, "confidence": confidence})
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
    lines = [f"📅 *Today's Signals – {today}*"]
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
