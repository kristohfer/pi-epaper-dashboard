# Pi Zero E-Paper Rotating Dashboard & AI Muse

A low-power, feature-rich e-paper dashboard built for a **Raspberry Pi Zero** and a **Waveshare 2.13" V4 e-Paper display**. It rotates through live system metrics, Tailscale mesh status, local weather, and dynamic AI-generated prompts powered by a local `llama.cpp` instance.

<p align="center">
  <img src="piDashboard.png" alt="Pi Zero E-Paper Dashboard Collage" width="700"/>
  <br>
  <em>Dashboard Collage: Mesh Routes, System Vitals, Weather/Time, and AI Muse.</em>
</p>

## Features
- 🖥️ **Host Vitals:** Real-time CPU, RAM, disk usage, temperature, and uptime tracking via Node Exporter.
- 🌐 **Tailscale Mesh Status:** Live view of node connectivity and P2P routing across your tailnet.
- 🌤️ **Weather & Time:** Current local weather conditions and clock.
- 🤖 **AI Muse:** Periodically pings a local LLM running on your network (via OpenAI-compatible API) to display witty tech tips and sysadmin roasts.
- 🌙 **Night Sleep Mode:** Automatically pauses updates during sleeping hours to preserve the e-paper display.

## Hardware Requirements
- Raspberry Pi Zero / Zero W / Zero 2 W
- Waveshare 2.13inch e-Paper V4 Display HAT

## Setup & Installation

### 1. Clone the Repository
```bash
cd ~
git clone [https://github.com/kristohfer/pi-epaper-dashboard.git](https://github.com/kristohfer/pi-epaper-dashboard.git)
cd pi-epaper-dashboard
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Setup Waveshare Drivers
Ensure your Waveshare driver library is cloned in your home directory:
```bash
cd ~
git clone [https://github.com/waveshare/e-Paper.git](https://github.com/waveshare/e-Paper.git)
```

### 4. Configure Your Settings
Copy the example config and edit it with your local details:
```bash
cp config_example.py config.py
nano config.py
```

### 5. Install as a Systemd Service
```bash
sudo cp rotating-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rotating-dashboard.service
```

## 🛠️ Credits & Acknowledgements
- **Hardware & System Architecture:** Built, configured, and deployed by [@kristohfer](https://github.com/kristohfer).
- **Code Assistance:** Developed with AI collaboration (Gemini) for python scripting, systemd automation, and refactoring.
- **Libraries & Services:** [Waveshare e-Paper](https://github.com/waveshare/e-Paper), [Tailscale](https://tailscale.com/), and `wttr.in`.
