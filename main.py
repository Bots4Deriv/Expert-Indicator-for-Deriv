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
# CONFIG (Use environment variables for secrets!)
# =====================

APP_ID = os.getenv("APP_ID", "1089")
DERIV_TOKEN = os.getenv("DERIV_TOKEN", "YOUR_DERIV_TOKEN")
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
lock = threading.Lock()  # Thread safety for shared data

# =====================
# TELEGRAM ALERT
# =====================

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": msg}
    
    try:
        requests.post(url, data=data, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

# =====================
# FASTAPI DASHBOARD
# =====================

app = FastAPI(title="Deriv Signal Engine")

@app.get("/", response_class=HTMLResponse)
def dashboard():
    with lock:  # Thread-safe access
        zone_html = "".join([
            f"<li>{z['type']} | {z['price']:.2f} | grade {z['grade']} | touches {z['touch']}</li>"
            for z in list(zones)[-10:]
        ])
        
        signal_html = "".join([
            f"<li>{s}</li>" for s in list(signals)[-10:]
        ])
        
        current_signal = last_signal

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Deriv Signal Engine</title>
        <meta http-equiv="refresh" content="5">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
            .container {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            h1 {{ color: #333; }}
            h2 {{ color: #666; border-bottom: 2px solid #ddd; padding-bottom: 10px; }}
            h3 {{ color: #2c3e50; background: #ecf0f1; padding: 15px; border-radius: 5px; }}
            ul {{ list-style: none; padding: 0; }}
            li {{ padding: 8px; margin: 5px 0; background: #f8f9fa; border-left: 4px solid #3498db; }}
            .status {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #2ecc71; margin-right: 10px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1><span class="status"></span>📊 Deriv Signal Engine</h1>
            <p>Symbol: <strong>{SYMBOL}</strong> | App ID: <strong>{APP_ID}</strong></p>
            
            <h2>Last Signal</h2>
            <h3>{current_signal}</h3>
            
            <h2>Recent Signals ({len(signals)} total)</h2>
            <ul>{signal_html if signal_html else '<li>No signals yet...</li>'}</ul>
            
            <h2>Supply / Demand Zones ({len(zones)} total)</h2>
            <ul>{zone_html if zone_html else '<li>No zones detected...</li>'}</ul>
            
            <p style="margin-top: 30px; color: #999; font-size: 12px;">
                Auto-refresh every 5 seconds | Server time: {time.strftime('%Y-%m-%d %H:%M:%S')}
            </p>
        </div>
    </body>
    </html>
    """
    return html

@app.get("/health")
def health_check():
    return {"status": "running", "symbol": SYMBOL, "signals": len(signals), "zones": len(zones)}

# =====================
# TRADING LOGIC (Thread-safe)
# =====================

def momentum():
    with lock:
        if len(candles) < 10:
            return 0
        return candles[-1] - candles[-10]

def zone_grade(price):
    with lock:
        if len(candles) == 0:
            return "C"
        move = abs(price - candles[-1])
        if move > 1:
            return "A"
        if move > 0.5:
            return "B"
        return "C"

def create_zone(price, zone_type):
    with lock:
        grade = zone_grade(price)
        zone = {
            "price": price,
            "type": zone_type,
            "touch": 0,
            "grade": grade,
            "created": time.time()
        }
        zones.append(zone)
    print(f"🎯 NEW {zone_type.upper()} ZONE @ {price:.2f} (Grade {grade})")
    send_telegram(f"New {zone_type} zone detected at {price:.2f} (Grade {grade})")

def pivot_high(data):
    if len(data) < 5:
        return None
    if data[-3] > data[-5] and data[-3] > data[-4] and data[-3] > data[-2] and data[-3] > data[-1]:
        return data[-3]
    return None

def pivot_low(data):
    if len(data) < 5:
        return None
    if data[-3] < data[-5] and data[-3] < data[-4] and data[-3] < data[-2] and data[-3] < data[-1]:
        return data[-3]
    return None

def spike():
    with lock:
        if len(tick_buffer) < 2:
            return False
        move = abs(tick_buffer[-1] - tick_buffer[-2])
        return move > SPIKE_LEVEL

def check_touch(price):
    global last_signal
    with lock:
        for z in zones:
            if abs(price - z["price"]) < ZONE_RANGE:
                z["touch"] += 1
                if z["touch"] == 1:
                    m = momentum()
                    if z["type"] == "demand" and m > 0:
                        last_signal = f"BUY 🚀 @ {price:.2f} | Zone: {z['price']:.2f} | Grade: {z['grade']}"
                        signals.append(last_signal)
                        print(f"✅ {last_signal}")
                        send_telegram(last_signal)
                    elif z["type"] == "supply" and m < 0:
                        last_signal = f"SELL 🔻 @ {price:.2f} | Zone: {z['price']:.2f} | Grade: {z['grade']}"
                        signals.append(last_signal)
                        print(f"✅ {last_signal}")
                        send_telegram(last_signal)

def build_candle():
    global tick_buffer
    with lock:
        if len(tick_buffer) == 0:
            return None
        
        candle = {
            "open": tick_buffer[0],
            "high": max(tick_buffer),
            "low": min(tick_buffer),
            "close": tick_buffer[-1],
            "time": time.strftime('%H:%M:%S')
        }
        candles.append(candle["close"])
        current_buffer = tick_buffer.copy()
        tick_buffer = []
    
    print(f"📈 CANDLE: O:{candle['open']:.2f} H:{candle['high']:.2f} L:{candle['low']:.2f} C:{candle['close']:.2f}")
    
    # Calculate pivots on a copy to avoid lock issues
    candle_list = list(candles)
    ph = pivot_high(candle_list)
    pl = pivot_low(candle_list)
    
    if ph:
        create_zone(ph, "supply")
    if pl:
        create_zone(pl, "demand")
    
    return candle

# =====================
# DERIV WEBSOCKET STREAM
# =====================

async def stream():
    global tick_buffer
    url = f"wss://ws.derivws.com/websockets/v3?app_id={APP_ID}"
    
    reconnect_delay = 5
    max_reconnect_delay = 60
    
    while True:
        try:
            print(f"🔌 Connecting to Deriv (App ID: {APP_ID})...")
            async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
                # Authorize
                await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
                auth_response = await ws.recv()
                auth_data = json.loads(auth_response)
                
                if "error" in auth_data:
                    print(f"❌ Auth failed: {auth_data['error']}")
                    await asyncio.sleep(10)
                    continue
                
                print("✅ Connected to Deriv")
                send_telegram("🤖 Deriv Signal Engine started and connected!")
                
                # Subscribe to ticks
                await ws.send(json.dumps({
                    "ticks": SYMBOL,
                    "subscribe": 1
                }))
                
                last_candle_time = time.time()
                
                while True:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=35)
                        data = json.loads(msg)
                        
                        if "tick" in data:
                            price = float(data["tick"]["quote"])
                            
                            with lock:
                                tick_buffer.append(price)
                            
                            if spike():
                                print(f"⚡ SPIKE detected! {price}")
                            
                            check_touch(price)
                            
                            # Build candle every CANDLE_TIME seconds
                            current_time = time.time()
                            if current_time - last_candle_time >= CANDLE_TIME:
                                build_candle()
                                last_candle_time = current_time
                                
                        elif "error" in data:
                            print(f"⚠️ Stream error: {data['error']}")
                            
                    except asyncio.TimeoutError:
                        print("⏱️ No data received, sending ping...")
                        try:
                            await ws.send(json.dumps({"ping": 1}))
                        except:
                            break
                    except websockets.exceptions.ConnectionClosed:
                        print("🔌 Connection closed")
                        break
                        
        except Exception as e:
            print(f"💥 Stream error: {e}")
            send_telegram(f"⚠️ Connection error: {e}. Reconnecting in {reconnect_delay}s...")
            
        print(f"🔄 Reconnecting in {reconnect_delay} seconds...")
        await asyncio.sleep(reconnect_delay)
        reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

# =====================
# MAIN EXECUTION
# =====================

def run_stream():
    """Run the websocket stream in a separate thread"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Handle signals gracefully
    def signal_handler(sig, frame):
        print("\n🛑 Shutting down stream...")
        loop.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        loop.run_until_complete(stream())
    except Exception as e:
        print(f"Stream thread error: {e}")

if __name__ == "__main__":
    print("🚀 Starting Deriv Signal Engine...")
    print(f"📊 Symbol: {SYMBOL} | Candle Time: {CANDLE_TIME}s | Zone Range: {ZONE_RANGE}")
    
    # Start websocket stream in background thread
    stream_thread = threading.Thread(target=run_stream, daemon=True)
    stream_thread.start()
    
    # Get port from environment (for cloud deployment)
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    
    print(f"🌐 Starting web server on {host}:{port}")
    
    # Start FastAPI server (this blocks)
    uvicorn.run(app, host=host, port=port, log_level="info")
