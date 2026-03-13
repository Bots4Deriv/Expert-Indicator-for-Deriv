import asyncio
import json
import websockets
import time
import requests
import os
import signal
import sys
from collections import deque
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import threading
import uvicorn

# =====================
# CONFIG
# =====================

APP_ID = os.getenv("APP_ID", "1089")
DERIV_TOKEN = os.getenv("DERIV_TOKEN", "")
SYMBOL = os.getenv("SYMBOL", "R_25")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
CANDLE_TIME = int(os.getenv("CANDLE_TIME", "60"))
ZONE_RANGE = float(os.getenv("ZONE_RANGE", "0.3"))
SPIKE_LEVEL = float(os.getenv("SPIKE_LEVEL", "0.5"))

# =====================
# STORAGE
# =====================

tick_buffer = []
candles = deque(maxlen=200)
zones = []
signals = []
last_signal = "Waiting..."
lock = threading.Lock()

# =====================
# TELEGRAM
# =====================

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg},
            timeout=5
        )
    except:
        pass

# =====================
# FASTAPI
# =====================

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
def dashboard():
    with lock:
        zone_html = "".join([f"<li>{z['type']} | {z['price']:.2f} | {z['grade']}</li>" for z in list(zones)[-10:]])
        signal_html = "".join([f"<li>{s}</li>" for s in list(signals)[-10:]])
        current = last_signal
    
    return f"""
    <html>
    <head><title>Deriv Signals</title><meta http-equiv="refresh" content="5"></head>
    <body style="font-family:Arial;max-width:800px;margin:40px auto">
        <h1>📊 Deriv Signal Engine</h1>
        <h2 style="background:#eee;padding:10px">{current}</h2>
        <h3>Recent Signals</h3>
        <ul>{signal_html or '<li>None</li>'}</ul>
        <h3>Zones</h3>
        <ul>{zone_html or '<li>None</li>'}</ul>
    </body>
    </html>
    """

@app.get("/health")
def health():
    return {"status": "ok", "symbol": SYMBOL}

# =====================
# TRADING LOGIC
# =====================

def momentum():
    with lock:
        return candles[-1] - candles[-10] if len(candles) >= 10 else 0

def zone_grade(price):
    with lock:
        move = abs(price - candles[-1]) if candles else 0
        return "A" if move > 1 else "B" if move > 0.5 else "C"

def create_zone(price, zone_type):
    with lock:
        zones.append({"price": price, "type": zone_type, "touch": 0, "grade": zone_grade(price)})
    print(f"Zone: {zone_type} @ {price}")

def pivot_high(d):
    return d[-3] if len(d) >= 5 and d[-3] > max(d[-5], d[-4], d[-2], d[-1]) else None

def pivot_low(d):
    return d[-3] if len(d) >= 5 and d[-3] < min(d[-5], d[-4], d[-2], d[-1]) else None

def check_touch(price):
    global last_signal
    with lock:
        for z in zones:
            if abs(price - z["price"]) < ZONE_RANGE:
                z["touch"] += 1
                if z["touch"] == 1:
                    m = momentum()
                    if z["type"] == "demand" and m > 0:
                        last_signal = f"BUY @ {price:.2f}"
                        signals.append(last_signal)
                        send_telegram(last_signal)
                    elif z["type"] == "supply" and m < 0:
                        last_signal = f"SELL @ {price:.2f}"
                        signals.append(last_signal)
                        send_telegram(last_signal)

def build_candle():
    global tick_buffer
    with lock:
        if not tick_buffer:
            return
        o, h, l, c = tick_buffer[0], max(tick_buffer), min(tick_buffer), tick_buffer[-1]
        candles.append(c)
        tick_buffer = []
    
    print(f"Candle: {o:.2f} {h:.2f} {l:.2f} {c:.2f}")
    ph = pivot_high(list(candles))
    pl = pivot_low(list(candles))
    if ph:
        create_zone(ph, "supply")
    if pl:
        create_zone(pl, "demand")

# =====================
# WEBSOCKET
# =====================

async def stream():
    global tick_buffer
    url = f"wss://ws.derivws.com/websockets/v3?app_id={APP_ID}"
    
    while True:
        try:
            async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
                await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
                await ws.recv()
                print("Connected to Deriv")
                await ws.send(json.dumps({"ticks": SYMBOL, "subscribe": 1}))
                
                last_candle = time.time()
                while True:
                    msg = json.loads(await ws.recv())
                    if "tick" in msg:
                        price = float(msg["tick"]["quote"])
                        with lock:
                            tick_buffer.append(price)
                        check_touch(price)
                        
                        if time.time() - last_candle >= CANDLE_TIME:
                            build_candle()
                            last_candle = time.time()
        except Exception as e:
            print(f"Error: {e}, reconnecting...")
            await asyncio.sleep(5)

def run_stream():
    asyncio.run(stream())

# =====================
# STARTUP
# =====================

if __name__ == "__main__":
    # Start websocket in background
    threading.Thread(target=run_stream, daemon=True).start()
    
    # Start web server (Railway sets PORT env var)
    port = int(os.getenv("PORT", "8000"))
    print(f"Starting server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
