import sys
import os
import json
import time
import random
import subprocess
import requests
import textwrap
import logging
import hmac
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from flask import Flask, render_template_string, request, abort
import threading

try:
    from waitress import serve as waitress_serve
    HAS_WAITRESS = True
except ImportError:
    HAS_WAITRESS = False

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("dashboard")

# Import configuration safely (loads local config.py or falls back to template)
try:
    import config
except ImportError:
    import config_example as config

CUBI_IP = config.CUBI_IP
NODE_EXPORTER_PORT = config.NODE_EXPORTER_PORT
LLAMA_PORT = config.LLAMA_PORT
API_KEY = config.API_KEY
LOCATION = config.LOCATION
WEB_TOKEN = getattr(config, 'WEB_TOKEN', 'changeme_in_config')

# Add Waveshare library path
libdir = os.path.expanduser('~/e-Paper/RaspberryPi_JetsonNano/python/lib')
if os.path.exists(libdir):
    sys.path.append(libdir)

from waveshare_epd import epd2in13_V4

# Preferences
VIEW_DURATION = 20
ENABLE_NIGHT_SLEEP = True
NIGHT_START_HOUR = 23
NIGHT_END_HOUR = 6

# AI Muse Personas & Themes
AI_PERSONAS = [
    {
        "theme": "GRUMPY SYSADMIN",
        "prompt": "You are a cynical, burnt-out senior sysadmin. Give a harsh, funny rule or roast about IT/networking. Under 12 words."
    },
    {
        "theme": "LINUX TRIVIA",
        "prompt": "Give an obscure, fascinating Linux command-line or kernel fact. Under 12 words."
    },
    {
        "theme": "CYBER SENTINEL",
        "prompt": "Give a sharp, paranoid cybersecurity best-practice tip. Under 12 words."
    },
    {
        "theme": "CODE PHILOSOPHER",
        "prompt": "Give a profound, minimalist software architecture quote or rule. Under 12 words."
    },
    {
        "theme": "AUTOMATION GURU",
        "prompt": "Give a witty quote on why laziness drives good automation. Under 12 words."
    }
]

# --- SECURE WEB UI SETUP (Homarr iFrame Integration) ---
app = Flask(__name__)

web_state = {
    "current_view": "Initializing...",
    "vitals": {"cpu": 0, "ram": 0, "temp": 0, "disk_pct": 0, "uptime": "N/A", "online": False},
    "weather": {"temp": "--°F", "cond": "Unavailable", "humidity": "--", "wind": "--"},
    "ai_theme": "None",
    "ai_quote": "Waiting for briefing...",
    "last_updated": "Never"
}

@app.before_request
def security_check():
    # Require ?token=XYZ for all web requests to block unauthorized local network scanning
    token = request.args.get('token', '')
    if not hmac.compare_digest(token, WEB_TOKEN):
        abort(403)

@app.route("/")
def web_dashboard():
    return render_template_string("""
        <html>
            <head>
                <meta http-equiv="refresh" content="10">
                <style>
                    body { background: #121212; color: #e0e0e0; font-family: monospace; padding: 15px; margin: 0; }
                    .card { background: #1e1e1e; border: 1px solid #333; padding: 12px; margin-bottom: 10px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.4); }
                    h2 { color: #4CAF50; margin-top: 0; font-size: 1.1em; text-transform: uppercase; }
                    h3 { color: #90CAF9; margin: 0 0 8px 0; font-size: 0.95em; }
                    p { margin: 4px 0; font-size: 0.85em; }
                    .timestamp { font-size: 0.75em; color: #777; margin-top: 10px; }
                </style>
            </head>
            <body>
                <h2>Pi E-Paper Dashboard // {{ state.current_view }}</h2>
                
                <div class="card">
                    <h3>Cubi 5 Vitals</h3>
                    {% if state.vitals.online %}
                        <p><b>CPU:</b> {{ state.vitals.cpu }}% | <b>RAM:</b> {{ state.vitals.ram }}% | <b>Temp:</b> {{ state.vitals.temp }}°F</p>
                        <p><b>Disk:</b> {{ state.vitals.disk_pct }}% | <b>Uptime:</b> {{ state.vitals.uptime }}</p>
                    {% else %}
                        <p style="color: #f44336;">Status: Offline / Unreachable</p>
                    {% endif %}
                </div>

                <div class="card">
                    <h3>Weather ({{ LOCATION }})</h3>
                    <p>{{ state.weather.temp }} - {{ state.weather.cond }}</p>
                    <p><b>Humidity:</b> {{ state.weather.humidity }} | <b>Wind:</b> {{ state.weather.wind }}</p>
                </div>

                <div class="timestamp">Last Synced: {{ state.last_updated }}</div>
            </body>
        </html>
    """, state=web_state, LOCATION=LOCATION)

def run_web_server():
    if HAS_WAITRESS:
        waitress_serve(app, host="0.0.0.0", port=5000, threads=4)
    else:
        logger.warning("waitress not installed (pip install waitress) - falling back to Flask's dev server")
        app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

# Start background web thread
web_thread = threading.Thread(target=run_web_server, daemon=True)
web_thread.start()


# --- GLOBAL FONT CACHE ---
def load_fonts():
    try:
        return {
            "title": ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 11),
            "large": ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 16),
            "body": ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 9),
            "bold": ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 9),
            "small": ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 8),
            "small_bold": ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 8),
        }
    except Exception:
        d = ImageFont.load_default()
        return {"title": d, "large": d, "body": d, "bold": d, "small": d, "small_bold": d}

FONTS = load_fonts()

# --- DATA FETCHERS ---

def ping_host(host_or_ip):
    try:
        res = subprocess.run(
            ["ping", "-c", "1", "-W", "1", host_or_ip],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        if res.returncode == 0:
            for line in res.stdout.split('\n'):
                if "time=" in line:
                    time_ms = line.split("time=")[1].split()[0]
                    return True, f"{int(float(time_ms))}ms"
            return True, "OK"
        return False, "OFF"
    except Exception:
        return False, "ERR"

def get_cubi_vitals():
    vitals = {
        "cpu": 0, "ram": 0, "temp": 0, "disk_pct": 0,
        "disk_str": "N/A", "uptime": "N/A", "online": False
    }
    online, _ = ping_host(CUBI_IP)
    if not online:
        return vitals

    try:
        res = requests.get(f"http://{CUBI_IP}:{NODE_EXPORTER_PORT}/metrics", timeout=2)
        if res.status_code == 200:
            vitals["online"] = True
            mem_total, mem_avail, boot_time = 0, 0, 0
            disk_total, disk_avail = 0, 0
            temps_c = []
            
            for line in res.text.splitlines():
                if line.startswith("#"): continue
                if line.startswith("node_memory_MemTotal_bytes "): mem_total = float(line.split()[-1])
                elif line.startswith("node_memory_MemAvailable_bytes "): mem_avail = float(line.split()[-1])
                elif line.startswith("node_load1 "):
                    load_1m = float(line.split()[-1])
                    vitals["cpu"] = min(int((load_1m / 8.0) * 100), 100)
                elif line.startswith("node_boot_time_seconds "): boot_time = float(line.split()[-1])
                elif 'node_filesystem_size_bytes' in line and 'mountpoint="/"' in line: disk_total = float(line.split()[-1])
                elif 'node_filesystem_avail_bytes' in line and 'mountpoint="/"' in line: disk_avail = float(line.split()[-1])
                elif "node_hwmon_temp_celsius" in line or "node_thermal_zone_temp" in line:
                    try:
                        val = float(line.split()[-1])
                        if val > 200: val /= 1000.0
                        if 10.0 <= val <= 115.0: temps_c.append(val)
                    except Exception: pass

            if mem_total > 0: vitals["ram"] = int(((mem_total - mem_avail) / mem_total) * 100)
            if boot_time > 0:
                uptime_sec = time.time() - boot_time
                days, hours = int(uptime_sec // 86400), int((uptime_sec % 86400) // 3600)
                vitals["uptime"] = f"{days}d {hours}h" if days > 0 else f"{hours}h"
            if disk_total > 0:
                used = disk_total - disk_avail
                vitals["disk_pct"] = int((used / disk_total) * 100)
            if temps_c: vitals["temp"] = int((max(temps_c) * 9 / 5) + 32)
    except Exception as e:
        logger.warning(f"Cubi 5 metrics fetch error: {e}")
        vitals["online"] = True
    return vitals

def get_mesh_devices_status():
    devices = []
    try:
        output = subprocess.check_output(["tailscale", "status", "--json"], text=True, timeout=3)
        data = json.loads(output)
        peers = data.get("Peer", {})
        if not peers:
            peers = data.get("Peers", {})
            
        for _, peer in peers.items():
            dns_name = peer.get("DNSName", "")
            hostname = peer.get("HostName", "unknown")
            
            if dns_name:
                name = dns_name.split('.')[0]
            else:
                name = hostname
                
            short_name = name[:7] + "…" if len(name) > 8 else name
            online = peer.get("Online", False)
            
            if not online:
                status_str = "OFF"
            else:
                cur_addr = peer.get("CurAddr", "")
                peer_relay = peer.get("PeerRelay", "")
                relay = peer.get("Relay", "")
                
                if cur_addr:
                    status_str = "P2P"
                elif peer_relay:
                    status_str = "RELAY"
                elif relay:
                    status_str = "DERP"
                else:
                    status_str = "OK"
                    
            devices.append(f"• {short_name} [{status_str}]")
            if len(devices) >= 8: break
    except Exception as e:
        logger.warning(f"Tailscale JSON status failed: {e}")
        devices = [
            "• Node Offline",
            "• Check Service",
            "• Connection Error"
        ]
        
    return devices

def get_weather():
    try:
        res = requests.get(f"https://wttr.in/{LOCATION}?format=%t|%C|%h|%w", timeout=3)
        if res.status_code == 200 and "|" in res.text:
            parts = [p.strip() for p in res.text.split("|")]
            return {"temp": parts[0].replace("+", ""), "cond": parts[1], "humidity": parts[2], "wind": parts[3]}
    except Exception as e:
        logger.warning(f"Weather fetch failed: {e}")
    return {"temp": "--°F", "cond": "Unavailable", "humidity": "--", "wind": "--"}

def get_ai_briefing():
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    model_name = "default"
    try:
        m_res = requests.get(f"http://{CUBI_IP}:{LLAMA_PORT}/v1/models", headers=headers, timeout=3)
        if m_res.status_code == 200:
            model_data = m_res.json().get('data', [])
            if model_data: model_name = model_data[0]['id']
    except Exception:
        pass

    persona = random.choice(AI_PERSONAS)
    try:
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": persona["prompt"]}],
            "max_tokens": 40,
            "temperature": 0.9
        }
        res = requests.post(f"http://{CUBI_IP}:{LLAMA_PORT}/v1/chat/completions", json=payload, headers=headers, timeout=15)
        if res.status_code == 200:
            res_json = res.json()
            text = res_json['choices'][0]['message']['content'].strip()
            usage = res_json.get('usage', {})
            tokens = usage.get('completion_tokens', usage.get('total_tokens', 12))
            return persona["theme"], text.replace('"', ''), tokens
    except Exception as e:
        logger.warning(f"AI Briefing fetch error: {e}")
    
    return persona["theme"], "AI Muse offline.", 0

# --- UI DRAWING HELPERS ---

def draw_gauge_bar(draw, x, y, width, height, percent):
    draw.rectangle([x - 1, y - 1, x + width + 35, y + height + 1], fill=255)
    draw.rectangle([x, y, x + width, y + height], outline=0, fill=255)
    fill_w = int((width - 2) * (max(0, min(100, percent)) / 100.0))
    if fill_w > 0:
        draw.rectangle([x + 1, y + 1, x + 1 + fill_w, y + height - 1], fill=0)

def create_base_frame(epd, view_title, view_index, total_views):
    width, height = epd.height, epd.width
    image = Image.new('1', (width, height), 255)
    draw = ImageDraw.Draw(image)

    draw.rectangle([0, 0, width, 18], fill=0)
    draw.text((8, 2), f"SYS // {view_title.upper()}", fill=255, font=FONTS["title"])
    
    draw.line([(0, height - 15), (width, height - 15)], fill=0, width=1)
    draw.text((8, height - 12), "TAILSCALE MESH", fill=0, font=FONTS["small"])
    
    dot_x_start = width - 45
    for i in range(total_views):
        cx = dot_x_start + (i * 10)
        cy = height - 8
        if i == view_index:
            draw.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=0)
        else:
            draw.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], outline=0)

    return image, draw, width, height

# --- VIEW RENDERERS ---

def render_vitals_view(epd, view_index, total_views, mode="full"):
    epd.init()
    image, draw, width, height = create_base_frame(epd, "Cubi 5 Vitals", view_index, total_views)
    vitals = get_cubi_vitals()
    
    web_state["current_view"] = "Cubi 5 Vitals"
    web_state["vitals"] = vitals
    web_state["last_updated"] = datetime.now().strftime("%I:%M:%S %p")
    
    if not vitals["online"]:
        draw.text((10, 30), f"Cubi 5 ({CUBI_IP})", fill=0, font=FONTS["bold"])
        draw.text((10, 50), "STATUS: UNREACHABLE / OFF", fill=0, font=FONTS["body"])
    else:
        draw.text((10, 22), "CPU Load:", fill=0, font=FONTS["body"])
        draw_gauge_bar(draw, 75, 23, 110, 8, vitals['cpu'])
        draw.text((192, 22), f"{vitals['cpu']}%", fill=0, font=FONTS["bold"])
        
        draw.text((10, 36), "RAM Usage:", fill=0, font=FONTS["body"])
        draw_gauge_bar(draw, 75, 37, 110, 8, vitals['ram'])
        draw.text((192, 36), f"{vitals['ram']}%", fill=0, font=FONTS["bold"])

        draw.text((10, 50), "NVMe Disk:", fill=0, font=FONTS["body"])
        draw_gauge_bar(draw, 75, 51, 110, 8, vitals['disk_pct'])
        draw.text((192, 50), f"{vitals['disk_pct']}%", fill=0, font=FONTS["bold"])
        
        draw.line([(10, 64), (width - 10, 64)], fill=0, width=1)
        draw.text((10, 69), f"Uptime: {vitals['uptime']}", fill=0, font=FONTS["body"])
        temp_str = f"{vitals['temp']}°F" if vitals['temp'] > 0 else "N/A"
        draw.text((130, 69), f"Cubi Temp: {temp_str}", fill=0, font=FONTS["bold"])

    buffer = epd.getbuffer(image.rotate(180))
    if mode == "partial": epd.displayPartial(buffer)
    else: epd.display(buffer)
    epd.sleep()

def render_mesh_status_view(epd, view_index, total_views):
    epd.init()
    image, draw, width, height = create_base_frame(epd, "Mesh Routes", view_index, total_views)
    devices = get_mesh_devices_status()
    
    web_state["current_view"] = "Mesh Routes"
    web_state["last_updated"] = datetime.now().strftime("%I:%M:%S %p")
    
    draw.text((10, 21), "Node Routing Status:", fill=0, font=FONTS["bold"])
    col1, col2 = devices[:4], devices[4:8]
    
    y_pos = 35
    for dev in col1:
        draw.text((10, y_pos), dev, fill=0, font=FONTS["body"])
        y_pos += 13
        
    y_pos = 35
    for dev in col2:
        draw.text((128, y_pos), dev, fill=0, font=FONTS["body"])
        y_pos += 13

    buffer = epd.getbuffer(image.rotate(180))
    epd.display(buffer)
    epd.sleep()

def render_weather_view(epd, view_index, total_views):
    epd.init()
    image, draw, width, height = create_base_frame(epd, "Weather & Time", view_index, total_views)
    now = datetime.now()
    draw.text((10, 22), now.strftime("%I:%M %p"), fill=0, font=FONTS["large"])
    draw.text((120, 26), now.strftime("%A, %b %d"), fill=0, font=FONTS["bold"])
    draw.line([(10, 44), (width - 10, 44)], fill=0, width=1)

    weather = get_weather()
    
    web_state["current_view"] = "Weather & Time"
    web_state["weather"] = weather
    web_state["last_updated"] = datetime.now().strftime("%I:%M:%S %p")

    draw.text((10, 49), f"{LOCATION}: {weather['temp']} - {weather['cond']}", fill=0, font=FONTS["bold"])
    draw.text((10, 64), f"Humidity: {weather['humidity']}  |  Wind: {weather['wind']}", fill=0, font=FONTS["body"])

    buffer = epd.getbuffer(image.rotate(180))
    epd.display(buffer)
    epd.sleep()

def render_ai_view(epd, view_index, total_views):
    epd.init()
    image, draw, width, height = create_base_frame(epd, "AI Muse", view_index, total_views)
    theme, quote, tokens = get_ai_briefing()
    
    web_state["current_view"] = "AI Muse"
    web_state["ai_theme"] = theme
    web_state["ai_quote"] = quote
    web_state["last_updated"] = datetime.now().strftime("%I:%M:%S %p")
    
    draw.rectangle([10, 22, 132, 34], fill=0)
    draw.text((14, 23), f"THEME: {theme}", fill=255, font=FONTS["small_bold"])
    draw.text((138, 23), f"({tokens} tok)", fill=0, font=FONTS["small_bold"])
    
    wrapped_quote = textwrap.fill(quote, width=42)
    draw.text((10, 42), f'"{wrapped_quote}"', fill=0, font=FONTS["body"])

    buffer = epd.getbuffer(image.rotate(180))
    epd.display(buffer)
    epd.sleep()

# --- MAIN LOOP ---

def interruptible_sleep(duration_seconds):
    """Sleeps in 1-second increments so night mode can trigger instantly."""
    for _ in range(duration_seconds):
        current_hour = datetime.now().hour
        if ENABLE_NIGHT_SLEEP and (current_hour >= NIGHT_START_HOUR or current_hour < NIGHT_END_HOUR):
            return True  # Signal to enter night mode immediately
        time.sleep(1)
    return False

def main():
    epd = epd2in13_V4.EPD()
    views_count = 4
    
    epd.init()
    epd.Clear(0xFF)
    epd.sleep()
    
    try:
        while True:
            current_hour = datetime.now().hour
            if ENABLE_NIGHT_SLEEP and (current_hour >= NIGHT_START_HOUR or current_hour < NIGHT_END_HOUR):
                # Night mode: Keep updating web UI data in the background, but do not touch the e-paper
                web_state["current_view"] = "Night Mode (Display Sleeping)"
                
                # Ensure hardware e-paper is put to sleep cleanly on transition
                try:
                    epd.init()
                    epd.sleep()
                except Exception as e:
                    logger.warning(f"Night-mode sleep transition failed: {e}")

                # Refresh data so Homarr still shows live metrics through the night
                web_state["vitals"] = get_cubi_vitals()
                web_state["weather"] = get_weather()
                web_state["last_updated"] = datetime.now().strftime("%I:%M:%S %p")
                
                time.sleep(60)
                continue

            # View 1: Vitals (Full)
            try:
                render_vitals_view(epd, 0, views_count, mode="full")
            except Exception as e:
                logger.error(f"Vitals (full) render failed: {e}", exc_info=True)
            if interruptible_sleep(10): continue

            # View 1: Vitals (Partial)
            try:
                render_vitals_view(epd, 0, views_count, mode="partial")
            except Exception as e:
                logger.error(f"Vitals (partial) render failed: {e}", exc_info=True)
            if interruptible_sleep(10): continue

            # View 2: Mesh Status
            try:
                render_mesh_status_view(epd, 1, views_count)
            except Exception as e:
                logger.error(f"Mesh status render failed: {e}", exc_info=True)
            if interruptible_sleep(VIEW_DURATION): continue

            # View 3: Weather
            try:
                render_weather_view(epd, 2, views_count)
            except Exception as e:
                logger.error(f"Weather render failed: {e}", exc_info=True)
            if interruptible_sleep(VIEW_DURATION): continue

            # View 4: AI Muse (Includes slow API call)
            try:
                render_ai_view(epd, 3, views_count)
            except Exception as e:
                logger.error(f"AI Muse render failed: {e}", exc_info=True)
            if interruptible_sleep(VIEW_DURATION): continue

    except KeyboardInterrupt:
        epd.init()
        epd.Clear(0xFF)
        epd.sleep()

if __name__ == "__main__":
    main()
