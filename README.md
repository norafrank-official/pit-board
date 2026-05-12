# 🏁 Pit Board — Real-Time F1 Race Intelligence Dashboard

A **live, data-dense dashboard** for Formula 1 fans and data enthusiasts. Watch races unfold with real-time telemetry, live standings, driver positioning, pit strategies, and comprehensive off-weekend insights.

---

## ✨ What It Does

**Pit Board** transforms raw F1 API data into an intuitive, terminal-inspired interface designed for power users. Whether you're tracking a race live or analyzing off-weekend performance metrics, Pit Board delivers:

### 🏎️ **Live Race Mode**
- **Real-time driver positions** with position deltas (gained/lost per update)
- **Lap-by-lap timing** and gap analysis with sparkline mini-charts showing trend
- **Championship battle cards** comparing P1 vs P2 points race
- **Pit stop tracker** with fastest-stop callouts
- **Race control alerts** for safety cars, red flags, VSC
- **Team radio live feed** with direct audio links
- **Live lap progress bar** showing race completion percentage

### 📊 **Off-Weekend Deep Dives**
- **Last race highlights** with podium breakdown and fastest lap stats
- **Full race grid classification** with points and medal icons
- **Driver form guide** showing last 5 races (position history with DNF tracking)
- **Circuit profile** with lap records, DRS zones, track length, and corners
- **Circuit DNA** bars showing speed, tire wear, overtaking, and downforce characteristics
- **Championship standings** (drivers & constructors) with points distribution bars
- **Season snapshot** tracking race completion and championship gaps
- **Teammate battles** showing intra-team points gaps
- **Race calendar** with upcoming schedule and status tracking
- **6-day race weekend forecast** showing weather prediction per race location

### 🌐 **Data Sources**
- **OpenF1 API** — live telemetry, positions, intervals, pit stops, team radio
- **Ergast API** — calendar, standings, race results, circuit history
- **Open-Meteo API** — weather forecasts (free, no key required)

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|------------|
| **Backend** | Flask + Flask-SocketIO (Python 3.9+) |
| **Frontend** | Vanilla JS + Leaflet.js (no frameworks) |
| **Styling** | Pure CSS3 (custom properties, grid, flexbox) |
| **Real-time** | WebSocket (Socket.IO) with 15s push updates |
| **Data Caching** | TTL-based in-memory cache (prevents API throttling) |
| **Fonts** | Titillium Web (official F1 font family) |

---

## 🚀 Quick Start

### Prerequisites
- Python 3.9+
- pip

### Installation

```bash
# Clone the repo
git clone https://github.com/yourusername/pit-board.git
cd pit-board

# Install dependencies
pip install -r backend/requirements.txt

# Run the backend
cd backend
python app.py
```

The backend starts on `http://localhost:5000`.

### Opening in Browser

Simply **open `frontend/index.html`** in your browser (or navigate to `http://localhost:5000` if running the backend).

> **Note:** The dashboard auto-connects to `localhost:5000` when opened locally. During F1 race weekends, it pulls live data every 15 seconds and renders real-time updates.

---

## 📐 Architecture

```
pit-board/
├── backend/
│   ├── app.py              # Flask app + WebSocket logic
│   ├── requirements.txt     # Python dependencies
│   └── CIRCUIT_INFO        # 24 circuits with DNA data + coordinates
├── frontend/
│   ├── index.html          # Dual-mode UI (live + off-weekend)
│   ├── script.js           # Core dashboard logic (1.1K lines)
│   └── style.css           # Brutalist dark theme (1.9K lines)
└── README.md
```

### Data Flow
1. **Backend** polls OpenF1 / Ergast / Open-Meteo every 15s
2. **Caching layer** prevents API rate limits (TTL: 60s–3600s per endpoint)
3. **ThreadPoolExecutor** fetches data in parallel
4. **WebSocket push** sends payload to all connected clients
5. **Frontend** renders real-time updates with smooth animations

---

## 🎯 Key Features Breakdown

### Lap Progress Bar
During races, a gradient progress bar shows current lap / total race laps. Disappears after checkered flag.

### Position Delta Indicator
Green **▲** = position gained since last update  
Red **▼** = position lost since last update

### Circuit DNA Bars
Radar-style visualization of each circuit's character:
- **TOP SPEED** (cyan) — how fast down the straights
- **TYRE WEAR** (amber) — degradation per lap
- **OVERTAKING** (green) — difficulty of passing
- **DOWNFORCE** badge — LOW / MEDIUM / HIGH / EXTREME

### Title Fight Card
When championship is close, P1 vs P2 battle gets its own card with:
- Points comparison bars (P1 always 100%, P2 scaled)
- Win counts
- Points gap display

### Weather Forecast Strip
6-day prediction with WMO weather codes, max/min temps, and rain probability (color-coded by severity).

### Form Guide
Last 5 race finishes for top 8 drivers:
- **Gold (1st)** on amber background
- **Podium (2–3)** on green
- **Top 10 (4–10)** on cyan
- **Outside points (11+)** on gray
- **DNF (Did Not Finish)** on red

---

## 🎨 Design Philosophy

**Data-dense, never cluttered.** Pit Board follows a brutalist, terminal-inspired aesthetic:
- Near-black background (`#0d0d0d`) with minimal contrast hierarchy
- **Color = information.** Red for critical (gaps, DNF), amber for caution, green for positive, cyan for secondary data
- **Grid-based layout** for instant visual scanning
- **No animations** except smooth transitions on data changes
- **Monospace-friendly** typography (Titillium Web for F1 authenticity)

Built for **power users** analyzing race data live, not casual viewers.

---

## 📱 Responsive Design

| Breakpoint | Layout |
|-----------|--------|
| **1400px+** | 3-column (left/center/right) + bottom quad-panel |
| **1100px–1400px** | 2-column with stacked sections |
| **<1100px** | Mobile-friendly single column |

---

## 🔧 Configuration

### Environment Variables (Optional)
Create a `.env` file in the `backend/` directory:

```bash
OPENF1_CACHE_TTL=60
ERGAST_CACHE_TTL=3600
OPENMETEO_CACHE_TTL=21600
```

All APIs use sensible defaults if not set.

### Adding New Circuits
Edit `CIRCUIT_INFO` in `app.py` to add circuit DNA, coordinates, and lap count.

---

## 🌍 Why This Matters

**F1 data is rich.** Every race generates thousands of data points — telemetry, positions, intervals, radio messages, pit strategies. Most dashboards show fragments. **Pit Board consolidates it into one coherent view.**

Built for:
- **Race Engineers** analyzing pit wall decisions in real-time
- **Data Journalists** covering F1 with live insights
- **Casual Fans** who want *deeper* race analysis without clutter
- **Developers** learning real-time WebSocket architecture

---

## 🚦 What's Next?

Potential enhancements:
- [ ] Gap history sparklines in timing grid (frontend rendering)
- [ ] Driver comparison mode (head-to-head telemetry)
- [ ] Qualifying vs race pace analysis
- [ ] DRS activation tracking
- [ ] Fuel consumption estimation
- [ ] Multi-season championship trends

---

## 📄 License

MIT License — free to use, modify, share.

---

## 🤝 Contributing

Have ideas? Found a bug? Open an issue or PR. Contributions welcome.

**Before contributing:**
1. Fork the repo
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes
4. Push and open a PR with a clear description

---

## 📞 Questions?

- **For F1 data questions** → Check OpenF1 / Ergast API docs
- **For dashboard bugs** → Open a GitHub issue
- **For feature requests** → Discussions tab

---

## 🙏 Credits

- **OpenF1 API** for live telemetry
- **Ergast API** for historical data
- **Open-Meteo** for free weather forecasts
- **Leaflet.js** for circuit maps
- **Formula 1** for making it all possible

---

**Pit Board** — Built for those who follow F1 like engineers follow data. Drive hard. 🏁
