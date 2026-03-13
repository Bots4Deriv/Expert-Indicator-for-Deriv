import asyncio
import json
import websockets
import time
import requests
from collections import deque
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import threading

# =====================
# CONFIG
# =====================

APP_ID = "1089"
DERIV_TOKEN = "YOUR_DERIV_TOKEN"
SYMBOL = "R_25"

TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"

CANDLE_TIME = 60
ZONE_RANGE = 0.3
SPIKE_LEVEL = 0.5

# =====================
# STORAGE
# =====================

tick_buffer = []
candles = deque(maxlen=200)

zones = []
signals = []

last_signal = "Waiting..."

# =====================
# TELEGRAM ALERT
# =====================

def send_telegram(msg):

    if TELEGRAM_TOKEN == "":
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    data = {
        "chat_id": CHAT_ID,
        "text": msg
    }

    try:
        requests.post(url, data=data)
    except:
        pass

# =====================
# FASTAPI DASHBOARD
# =====================

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
def dashboard():

    zone_html = ""

    for z in zones[-10:]:

        zone_html += f"<li>{z['type']} | {z['price']} | grade {z['grade']}</li>"

    signal_html = ""

    for s in signals[-10:]:

        signal_html += f"<li>{s}</li>"

    html = f"""

    <html>

    <head>
    <title>Deriv Signal Engine</title>
    </head>

    <body style="font-family:Arial">

    <h1>📊 Deriv Signal Engine</h1>

    <h2>Last Signal</h2>
    <h3>{last_signal}</h3>

    <h2>Recent Signals</h2>
    <ul>
    {signal_html}
    </ul>

    <h2>Supply / Demand Zones</h2>
    <ul>
    {zone_html}
    </ul>

    </body>

    </html>

    """

    return html

# =====================
# MOMENTUM
# =====================

def momentum():

    if len(candles) < 10:
        return 0

    return candles[-1] - candles[-10]

# =====================
# ZONE GRADE
# =====================

def zone_grade(price):

    move = abs(price - candles[-1])

    if move > 1:
        return "A"

    if move > 0.5:
        return "B"

    return "C"

# =====================
# CREATE ZONE
# =====================

def create_zone(price, zone_type):

    grade = zone_grade(price)

    zone = {
        "price": price,
        "type": zone_type,
        "touch": 0,
        "grade": grade
    }

    zones.append(zone)

    print(f"NEW {zone_type} ZONE {price} grade {grade}")

# =====================
# PIVOTS
# =====================

def pivot_high(data):

    if len(data) < 5:
        return None

    if data[-3] > data[-5] and data[-3] > data[-4] and data[-3] > data[-2] and data[-3] > data[-1]:

        return data[-3]

def pivot_low(data):

    if len(data) < 5:
        return None

    if data[-3] < data[-5] and data[-3] < data[-4] and data[-3] < data[-2] and data[-3] < data[-1]:

        return data[-3]

# =====================
# SPIKE
# =====================

def spike():

    if len(tick_buffer) < 2:
        return False

    move = abs(tick_buffer[-1] - tick_buffer[-2])

    return move > SPIKE_LEVEL

# =====================
# SIGNAL CHECK
# =====================

def check_touch(price):

    global last_signal

    for z in zones:

        if abs(price - z["price"]) < ZONE_RANGE:

            z["touch"] += 1

            if z["touch"] == 1:

                m = momentum()

                if z["type"] == "demand" and m > 0:

                    last_signal = f"BUY 🚀 {price}"
                    signals.append(last_signal)

                    print(last_signal)

                    send_telegram(last_signal)

                if z["type"] == "supply" and m < 0:

                    last_signal = f"SELL 🔻 {price}"
                    signals.append(last_signal)

                    print(last_signal)

                    send_telegram(last_signal)

# =====================
# CANDLE BUILDER
# =====================

def build_candle():

    global tick_buffer

    if len(tick_buffer) == 0:
        return

    candle = {

        "open": tick_buffer[0],
        "high": max(tick_buffer),
        "low": min(tick_buffer),
        "close": tick_buffer[-1]

    }

    candles.append(candle["close"])

    print("NEW CANDLE", candle)

    ph = pivot_high(list(candles))
    pl = pivot_low(list(candles))

    if ph:
        create_zone(ph, "supply")

    if pl:
        create_zone(pl, "demand")

    tick_buffer = []

# =====================
# DERIV STREAM
# =====================

async def stream():

    global tick_buffer

    url = f"wss://ws.derivws.com/websockets/v3?app_id={APP_ID}"

    async with websockets.connect(url) as ws:

        await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
        await ws.recv()

        print("CONNECTED TO DERIV")

        await ws.send(json.dumps({
            "ticks": SYMBOL,
            "subscribe": 1
        }))

        last_candle = time.time()

        while True:

            msg = json.loads(await ws.recv())

            if "tick" in msg:

                price = msg["tick"]["quote"]

                tick_buffer.append(price)

                if spike():
                    print("⚡ SPIKE")

                check_touch(price)

                if time.time() - last_candle >= CANDLE_TIME:

                    build_candle()

                    last_candle = time.time()

# =====================
# START STREAM
# =====================

def start():

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    loop.run_until_complete(stream())

threading.Thread(target=start).start()
