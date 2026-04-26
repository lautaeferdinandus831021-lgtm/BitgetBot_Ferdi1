#!/usr/bin/env python3

import json, os, time, hmac, hashlib, base64, threading, math, traceback
from datetime import datetime, timezone
from flask import Flask, render_template
from flask_socketio import SocketIO, emit

import requests as http_requests
import pandas as pd
import numpy as np

# ================= CONFIG =================
CONFIG_FILE = "config.json"

DEFAULT_CFG = {
    "symbol": "BTCUSDT",
    "market_mode": "spot",
    "order_size": 50,
    "strategy": "smart_score",
    "indicators": {
        "ema": {"enabled": True, "fast": 9, "slow": 21},
        "rsi": {"enabled": True, "period": 14},
        "macd": {"enabled": True}
    }
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        return json.load(open(CONFIG_FILE))
    return DEFAULT_CFG.copy()

# ================= API =================
class BitgetClient:
    BASE = "https://api.bitget.com"

    def __init__(self):
        self.sess = http_requests.Session()

    def get_klines(self, symbol, gran):
        try:
            r = self.sess.get(
                f"{self.BASE}/api/v2/spot/market/candles",
                params={"symbol": symbol, "granularity": gran, "limit": 100},
                timeout=5
            )
            data = r.json()["data"]

            df = pd.DataFrame(data, columns=[
                "ts","o","h","l","c","v","q"
            ])

            df["ts"] = pd.to_datetime(df["ts"].astype(int), unit="ms")
            df[["o","h","l","c"]] = df[["o","h","l","c"]].astype(float)

            return df.rename(columns={
                "ts":"timestamp","o":"open","h":"high","l":"low","c":"close"
            })

        except:
            return None

# ================= INDICATOR =================
class IndicatorEngine:

    def compute(self, df):
        close = df["close"]

        ema_fast = close.ewm(span=9).mean()
        ema_slow = close.ewm(span=21).mean()

        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rsi = 100 - (100/(1+(gain/loss)))

        macd = close.ewm(span=12).mean() - close.ewm(span=26).mean()
        signal = macd.ewm(span=9).mean()

        return {
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "rsi": rsi,
            "macd": macd,
            "signal": signal
        }

# ================= AI LOGIC =================
class SmartAI:

    def decide(self, ind):
        score = 0

        # EMA Trend
        if ind["ema_fast"].iloc[-1] > ind["ema_slow"].iloc[-1]:
            score += 2
        else:
            score -= 2

        # RSI filter
        rsi = ind["rsi"].iloc[-1]
        if rsi < 30:
            score += 2
        elif rsi > 70:
            score -= 2

        # MACD momentum
        if ind["macd"].iloc[-1] > ind["signal"].iloc[-1]:
            score += 1
        else:
            score -= 1

        # Noise filter (anti whipsaw)
        if abs(score) < 2:
            return "NEUTRAL"

        return "LONG" if score > 0 else "SHORT"

# ================= BOT =================
class TradingBot:

    def __init__(self, sio):
        self.sio = sio
        self.running = False
        self.client = BitgetClient()
        self.ind = IndicatorEngine()
        self.ai = SmartAI()
        self.config = load_config()

    def log(self, msg):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def get_data(self):
        df = self.client.get_klines(self.config["symbol"], "1m")

        if df is None:
            # fallback SIM
            price = 30000 + np.random.randn()*100
            df = pd.DataFrame([{
                "timestamp": datetime.now(),
                "open": price,
                "high": price+10,
                "low": price-10,
                "close": price
            }])

        return df.tail(100)

    def loop(self):
        self.log("BOT STARTED")

        while self.running:
            try:
                df = self.get_data()

                ind = self.ind.compute(df)
                signal = self.ai.decide(ind)

                payload = {
                    "price": float(df["close"].iloc[-1]),
                    "signal": signal
                }

                # emit ringan (anti lag)
                self.sio.emit("tick", payload)

                time.sleep(1)

            except Exception as e:
                self.log(f"ERROR {e}")
                time.sleep(3)

    def start(self):
        if self.running:
            return
        self.running = True
        threading.Thread(target=self.loop, daemon=True).start()

    def stop(self):
        self.running = False

# ================= SERVER =================
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", ping_interval=10, ping_timeout=25)

bot = TradingBot(socketio)

@app.route("/")
def index():
    return render_template("index.html")

@socketio.on("connect")
def connect():
    print("CLIENT CONNECTED")

@socketio.on("start")
def start():
    bot.start()

@socketio.on("stop")
def stop():
    bot.stop()

# ================= RUN =================
if __name__ == "__main__":socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
