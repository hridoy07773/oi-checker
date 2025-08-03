import time
import datetime
import asyncio
import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_URL = "https://fapi.binance.com"
OI_DATA = {"1h": {}, "4h": {}, "24h": {}}
VOL_DATA = {}
SCAN_INTERVALS = ["1h", "4h", "24h"]
SCAN_DATA = {}
VOL_OI_SCAN = []
HEADERS = {"User-Agent": "Mozilla/5.0"}

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>OI + Volume Change Scanner</title>
        <style>
            body { font-family: Arial; background: #f9f9f9; padding: 20px; }
            h1 { color: #333; }
            select, button { padding: 10px; margin-top: 10px; }
            table { width: 100%; margin-top: 20px; border-collapse: collapse; }
            th, td { border: 1px solid #ccc; padding: 10px; text-align: center; }
            th { background: #333; color: white; }
            tr:nth-child(even) { background: #eee; }
        </style>
    </head>
    <body>
        <h1>Open Interest & Volume Tracker</h1>
        <label for="interval">Select Interval:</label>
        <select id="interval">
            <option value="1h">1 Hour</option>
            <option value="4h">4 Hours</option>
            <option value="24h">24 Hours</option>
        </select>
        <button onclick="scan()">🔍 Scan OI</button>
        <button onclick="scanVolOI()">📈 Scan OI + Volume</button>
        <table>
            <thead>
                <tr>
                    <th>Symbol</th>
                    <th>OI Change (%)</th>
                    <th>Volume Change (%)</th>
                    <th>Current OI</th>
                    <th>Previous OI</th>
                    <th>Current Vol</th>
                    <th>Previous Vol</th>
                </tr>
            </thead>
            <tbody id="results">
            </tbody>
        </table>
        <script>
            async function scan() {
                const interval = document.getElementById("interval").value;
                const res = await fetch(`/top_oi_changes/${interval}`);
                const data = await res.json();
                const table = document.getElementById("results");
                table.innerHTML = "";
                if (data.length === 0) {
                    table.innerHTML = "<tr><td colspan='7'>No data</td></tr>";
                    return;
                }
                data.forEach(row => {
                    const tr = document.createElement("tr");
                    tr.innerHTML = `<td>${row.symbol}</td>
                                    <td>${row.oi_change.toFixed(2)}%</td>
                                    <td>-</td>
                                    <td>${row.current_oi}</td>
                                    <td>${row.previous_oi}</td>
                                    <td>-</td>
                                    <td>-</td>`;
                    table.appendChild(tr);
                });
            }

            async function scanVolOI() {
                const res = await fetch(`/vol_oi_rising`);
                const data = await res.json();
                const table = document.getElementById("results");
                table.innerHTML = "";
                if (data.length === 0) {
                    table.innerHTML = "<tr><td colspan='7'>No coins with both OI and Volume rising</td></tr>";
                    return;
                }
                data.forEach(row => {
                    const tr = document.createElement("tr");
                    tr.innerHTML = `<td>${row.symbol}</td>
                                    <td>${row.oi_change.toFixed(2)}%</td>
                                    <td>${row.volume_change.toFixed(2)}%</td>
                                    <td>${row.current_oi}</td>
                                    <td>${row.previous_oi}</td>
                                    <td>${row.current_volume}</td>
                                    <td>${row.previous_volume}</td>`;
                    table.appendChild(tr);
                });
            }
        </script>
    </body>
    </html>
    """

@app.get("/top_oi_changes/{interval}")
def get_top_changes(interval: str):
    return SCAN_DATA.get(interval, [])

@app.get("/vol_oi_rising")
def get_vol_oi_combo():
    return VOL_OI_SCAN

# Data fetchers
def fetch_symbols(max_retries=5, delay=5):
    for attempt in range(max_retries):
        try:
            print(f"🔁 Attempt {attempt + 1} to fetch symbols...")
            res = requests.get(f"{BASE_URL}/fapi/v1/exchangeInfo", headers=HEADERS, timeout=10)
            res.raise_for_status()
            symbols = [
                s["symbol"] for s in res.json()["symbols"]
                if s["contractType"] == "PERPETUAL" and s["quoteAsset"] == "USDT"
            ]
            return symbols
        except requests.exceptions.Timeout:
            print("[⚠️ TIMEOUT] Retrying symbol fetch...")
        except requests.exceptions.SSLError as e:
            print(f"[SSL ERROR] Could not fetch symbols: {e}")
        except Exception as e:
            print(f"[ERROR] Fetching symbols: {e}")
        time.sleep(delay)
    return []

def fetch_oi(symbol):
    try:
        url = f"{BASE_URL}/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=1"
        res = requests.get(url, headers=HEADERS, timeout=5)
        if res.status_code == 200 and res.json():
            return float(res.json()[-1]["sumOpenInterest"])
    except:
        pass
    return None

def fetch_volume(symbol):
    try:
        url = f"{BASE_URL}/fapi/v1/klines?symbol={symbol}&interval=1h&limit=2"
        res = requests.get(url, headers=HEADERS, timeout=5)
        if res.status_code == 200:
            data = res.json()
            return float(data[-1][7]), float(data[-2][7])  # current, previous quote volume
    except:
        pass
    return None, None

def update_oi_change(interval):
    now = datetime.datetime.utcnow()
    delta = {"1h": datetime.timedelta(hours=1), "4h": datetime.timedelta(hours=4), "24h": datetime.timedelta(hours=24)}[interval]
    results = []

    for symbol, history in OI_DATA[interval].items():
        filtered = [(t, v) for t, v in history if t >= now - delta]
        if len(filtered) < 2:
            continue
        previous = filtered[0][1]
        current = filtered[-1][1]
        if previous == 0:
            continue
        change = ((current - previous) / previous) * 100
        results.append({
            "symbol": symbol,
            "oi_change": change,
            "current_oi": round(current, 2),
            "previous_oi": round(previous, 2)
        })

    SCAN_DATA[interval] = sorted(results, key=lambda x: abs(x["oi_change"]), reverse=True)[:20]

def update_vol_oi_combination():
    VOL_OI_SCAN.clear()
    for symbol in VOL_DATA:
        vol_hist = VOL_DATA[symbol]
        oi_hist = OI_DATA["1h"].get(symbol, [])
        if len(vol_hist) < 2 or len(oi_hist) < 2:
            continue

        v_prev, v_curr = vol_hist[-2][1], vol_hist[-1][1]
        o_prev, o_curr = oi_hist[-2][1], oi_hist[-1][1]

        if v_prev == 0 or o_prev == 0:
            continue

        vol_change = ((v_curr - v_prev) / v_prev) * 100
        oi_change = ((o_curr - o_prev) / o_prev) * 100

        if vol_change > 0 and oi_change > 0:
            VOL_OI_SCAN.append({
                "symbol": symbol,
                "volume_change": vol_change,
                "oi_change": oi_change,
                "current_volume": round(v_curr, 2),
                "previous_volume": round(v_prev, 2),
                "current_oi": round(o_curr, 2),
                "previous_oi": round(o_prev, 2)
            })

    VOL_OI_SCAN.sort(key=lambda x: x["volume_change"] + x["oi_change"], reverse=True)

# Background task
async def scanner_loop():
    symbols = fetch_symbols()
    while not symbols:
        print("⚠️ No symbols fetched. Retrying in 30s...")
        await asyncio.sleep(30)
        symbols = fetch_symbols()

    print(f"🌀 Found {len(symbols)} symbols.")

    while True:
        now = datetime.datetime.utcnow()
        for symbol in symbols:
            oi = fetch_oi(symbol)
            v_curr, v_prev = fetch_volume(symbol)

            if oi:
                for interval in SCAN_INTERVALS:
                    OI_DATA.setdefault(interval, {}).setdefault(symbol, []).append((now, oi))
                    OI_DATA[interval][symbol] = OI_DATA[interval][symbol][-30:]

            if v_curr and v_prev:
                VOL_DATA.setdefault(symbol, []).append((now, v_curr))
                VOL_DATA[symbol] = VOL_DATA[symbol][-30:]

        for interval in SCAN_INTERVALS:
            update_oi_change(interval)

        update_vol_oi_combination()

        print(f"✅ Scanned at {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        await asyncio.sleep(300)  # every 5 minutes

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(scanner_loop())

# Run with: uvicorn oi_ranking:app --reload








