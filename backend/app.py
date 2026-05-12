from flask import Flask, jsonify, send_from_directory
import os
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import requests
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import logging
from urllib.parse import urlparse

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend')
app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path='')
app.config['SECRET_KEY'] = 'pit-board-live-telemetry'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OPENF1_API = "https://api.openf1.org/v1"
ERGAST_API = "https://api.jolpi.ca/ergast/f1"
OPENMETEO_API = "https://api.open-meteo.com/v1/forecast"

# ===== Smart Caching (keeps us under API rate limits) =====
class DataCache:
    def __init__(self):
        self._data = {}
        self._ts = {}

    def get(self, key, ttl=60):
        if key in self._data and time.time() - self._ts.get(key, 0) < ttl:
            return self._data[key]
        return None

    def set(self, key, value):
        self._data[key] = value
        self._ts[key] = time.time()

cache = DataCache()

current_meeting = None
current_session = None
drivers_map = {}
update_counter = 0

# Gap sparkline history — keyed by driver_number, reset on session change
_gap_hist: dict = {}
_gap_hist_session: str | None = None

CIRCUIT_COORDS = {
    'miami': {'lat': 25.9581, 'lon': -80.2389},
    'melbourne': {'lat': -37.8497, 'lon': 144.9680},
    'jeddah': {'lat': 21.6319, 'lon': 39.1044},
    'shanghai': {'lat': 31.3389, 'lon': 121.2197},
    'suzuka': {'lat': 34.8431, 'lon': 136.5407},
    'sakhir': {'lat': 26.0325, 'lon': 50.5106},
    'bahrain': {'lat': 26.0325, 'lon': 50.5106},
    'imola': {'lat': 44.3439, 'lon': 11.7167},
    'monaco': {'lat': 43.7347, 'lon': 7.4206},
    'barcelona': {'lat': 41.5700, 'lon': 2.2611},
    'montréal': {'lat': 45.5017, 'lon': -73.5267},
    'montreal': {'lat': 45.5017, 'lon': -73.5267},
    'silverstone': {'lat': 52.0786, 'lon': -1.0169},
    'spielberg': {'lat': 47.2197, 'lon': 14.7647},
    'budapest': {'lat': 47.5789, 'lon': 19.2486},
    'spa-francorchamps': {'lat': 50.4372, 'lon': 5.9714},
    'zandvoort': {'lat': 52.3888, 'lon': 4.5408},
    'monza': {'lat': 45.6156, 'lon': 9.2811},
    'baku': {'lat': 40.3725, 'lon': 49.8533},
    'marina bay': {'lat': 1.2914, 'lon': 103.8640},
    'singapore': {'lat': 1.2914, 'lon': 103.8640},
    'austin': {'lat': 30.1328, 'lon': -97.6411},
    'mexico city': {'lat': 19.4042, 'lon': -99.0907},
    'são paulo': {'lat': -23.7014, 'lon': -46.6969},
    'interlagos': {'lat': -23.7014, 'lon': -46.6969},
    'las vegas': {'lat': 36.1162, 'lon': -115.1745},
    'lusail': {'lat': 25.4900, 'lon': 51.4542},
    'yas island': {'lat': 24.4672, 'lon': 54.6031},
    'abu dhabi': {'lat': 24.4672, 'lon': 54.6031},
}

# Fallback UTC offsets — Open-Meteo auto-timezone overrides these with DST-aware values
CIRCUIT_TIMEZONES = {
    'miami': -4, 'melbourne': 11, 'jeddah': 3, 'shanghai': 8,
    'suzuka': 9, 'sakhir': 3, 'bahrain': 3, 'imola': 2,
    'monaco': 2, 'barcelona': 2, 'montréal': -4, 'montreal': -4,
    'silverstone': 1, 'spielberg': 2, 'budapest': 2,
    'spa-francorchamps': 2, 'zandvoort': 2, 'monza': 2,
    'baku': 4, 'marina bay': 8, 'singapore': 8, 'austin': -5,
    'mexico city': -6, 'são paulo': -3, 'interlagos': -3,
    'las vegas': -7, 'lusail': 3, 'yas island': 4, 'abu dhabi': 4,
}

COUNTRY_FLAGS = {
    'Australia': '🇦🇺', 'Saudi Arabia': '🇸🇦', 'Japan': '🇯🇵', 'China': '🇨🇳',
    'United States': '🇺🇸', 'Italy': '🇮🇹', 'Monaco': '🇲🇨', 'Spain': '🇪🇸',
    'Canada': '🇨🇦', 'United Kingdom': '🇬🇧', 'Austria': '🇦🇹', 'Hungary': '🇭🇺',
    'Belgium': '🇧🇪', 'Netherlands': '🇳🇱', 'Azerbaijan': '🇦🇿', 'Singapore': '🇸🇬',
    'Mexico': '🇲🇽', 'Brazil': '🇧🇷', 'Qatar': '🇶🇦', 'UAE': '🇦🇪',
    'Bahrain': '🇧🇭', 'United Arab Emirates': '🇦🇪', 'Las Vegas': '🇺🇸',
}

def resolve_track_utc_offset(meeting):
    loc = (meeting.get('location') or '').lower()
    for key, offset in CIRCUIT_TIMEZONES.items():
        if key in loc:
            return offset
    return 0

# --------------- HTTP ---------------
http_session = requests.Session()
http_session.headers.update({'Accept': 'application/json'})

_rate_backoff_until = {}
_rate_lock = threading.Lock()

def safe_fetch(url, timeout=8, retries=1):
    domain = urlparse(url).netloc
    for attempt in range(retries + 1):
        try:
            now = time.monotonic()
            with _rate_lock:
                backoff = _rate_backoff_until.get(domain, 0)
                if now < backoff:
                    time.sleep(backoff - now)
            resp = http_session.get(url, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                with _rate_lock:
                    _rate_backoff_until[domain] = time.monotonic() + 2
                logger.warning(f"Rate limited: {url} (attempt {attempt+1})")
                if attempt < retries:
                    continue
            return None
        except Exception as e:
            logger.error(f"Fetch error {url}: {e}")
            if attempt == retries:
                return None

# --------------- External Weather ---------------
WMO_CONDITIONS = {
    0: 'Clear', 1: 'Mainly Clear', 2: 'Partly Cloudy', 3: 'Overcast',
    45: 'Foggy', 48: 'Icy Fog', 51: 'Light Drizzle', 53: 'Drizzle',
    55: 'Heavy Drizzle', 61: 'Light Rain', 63: 'Rain', 65: 'Heavy Rain',
    71: 'Light Snow', 73: 'Snow', 75: 'Heavy Snow', 77: 'Snow Grains',
    80: 'Showers', 81: 'Showers', 82: 'Heavy Showers',
    95: 'Thunderstorm', 96: 'Thunderstorm+Hail', 99: 'Heavy Thunderstorm',
}

def fetch_race_forecast(lat: float, lon: float, days: int = 6) -> list:
    """Multi-day daily forecast from Open-Meteo for the race location."""
    key = f'forecast_{round(lat, 1)}_{round(lon, 1)}'
    cached = cache.get(key, 21600)
    if cached:
        return cached
    url = (
        f"{OPENMETEO_API}?latitude={lat}&longitude={lon}"
        f"&daily=temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max"
        f"&wind_speed_unit=ms&timezone=auto&forecast_days={days}"
    )
    data = safe_fetch(url, timeout=12)
    if not data or 'daily' not in data:
        return []
    try:
        d = data['daily']
        times = d.get('time', [])
        result = []
        for i in range(min(days, len(times))):
            wc = int((d.get('weather_code') or [])[i]) if i < len(d.get('weather_code') or []) else 0
            result.append({
                'date': times[i],
                'max_temp': round(float((d.get('temperature_2m_max') or [])[i])) if i < len(d.get('temperature_2m_max') or []) else None,
                'min_temp': round(float((d.get('temperature_2m_min') or [])[i])) if i < len(d.get('temperature_2m_min') or []) else None,
                'weather_code': wc,
                'condition': WMO_CONDITIONS.get(wc, 'Clear'),
                'rain_prob': int((d.get('precipitation_probability_max') or [])[i]) if i < len(d.get('precipitation_probability_max') or []) else 0,
            })
        cache.set(key, result)
        return result
    except Exception as e:
        logger.error(f"Race forecast error: {e}")
        return []


def fetch_external_weather(lat, lon):
    """Real-time weather from Open-Meteo — free, no API key needed."""
    key = f'ext_wx_{round(lat, 2)}_{round(lon, 2)}'
    cached = cache.get(key, 600)
    if cached:
        return cached
    url = (
        f"{OPENMETEO_API}?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,relative_humidity_2m,wind_speed_10m,"
        f"wind_direction_10m,precipitation,weather_code,apparent_temperature,cloud_cover"
        f"&wind_speed_unit=ms&timezone=auto"
    )
    data = safe_fetch(url, timeout=12)
    if not data or 'current' not in data:
        return None
    c = data['current']
    wc = int(c.get('weather_code', 0))
    cond = WMO_CONDITIONS.get(wc, 'Unknown')
    result = {
        'air_temp': round(float(c.get('temperature_2m', 0)), 1),
        'feels_like': round(float(c.get('apparent_temperature', 0)), 1),
        'humidity': int(c.get('relative_humidity_2m', 0)),
        'wind_speed': round(float(c.get('wind_speed_10m', 0)), 1),
        'wind_direction': int(c.get('wind_direction_10m', 0)),
        'precipitation': round(float(c.get('precipitation', 0)), 1),
        'cloud_cover': int(c.get('cloud_cover', 0)),
        'condition': cond,
        'weather_code': wc,
        'timezone_abbr': data.get('timezone_abbreviation', 'UTC'),
        'utc_offset_hours': data.get('utc_offset_seconds', 0) / 3600,
    }
    cache.set(key, result)
    logger.info(f"Ext weather ({lat:.2f},{lon:.2f}): {cond} {result['air_temp']}°C")
    return result

# --------------- Meeting / Session Fetchers ---------------
def fetch_current_meeting():
    cached = cache.get('meeting', 3600)
    if cached:
        return cached
    now = datetime.now(timezone.utc)
    for year in [now.year, now.year - 1]:
        data = safe_fetch(f"{OPENF1_API}/meetings?year={year}")
        if not data:
            continue
        meetings = sorted([m for m in data if not m.get('is_cancelled')],
                          key=lambda m: m.get('date_start', ''))
        result = None
        for m in meetings:
            try:
                start = datetime.fromisoformat(m['date_start'])
                end = datetime.fromisoformat(m['date_end'])
            except Exception:
                continue
            if start <= now <= end:
                result = m
                break
            if start > now:
                result = m
                break
        if not result and meetings:
            result = meetings[-1]
        if result:
            cache.set('meeting', result)
            return result
    return None

def fetch_sessions(meeting_key):
    cached = cache.get(f'sess_{meeting_key}', 600)
    if cached:
        return cached
    data = safe_fetch(f"{OPENF1_API}/sessions?meeting_key={meeting_key}")
    if data:
        cache.set(f'sess_{meeting_key}', data)
    return data or []

def get_active_session(meeting_key):
    sessions = fetch_sessions(meeting_key)
    if not sessions:
        return None
    now = datetime.now(timezone.utc)
    sessions = sorted(sessions, key=lambda s: s.get('date_start', ''))
    for s in sessions:
        try:
            st = datetime.fromisoformat(s['date_start'])
            en = datetime.fromisoformat(s['date_end'])
        except Exception:
            continue
        if st <= now <= en:
            return s
    completed = [s for s in sessions if datetime.fromisoformat(s['date_end']) <= now]
    if completed:
        return completed[-1]
    upcoming = [s for s in sessions if datetime.fromisoformat(s['date_start']) > now]
    return upcoming[0] if upcoming else (sessions[-1] if sessions else None)

def build_weekend_schedule(meeting_key):
    """All sessions for the current meeting with LIVE/UPCOMING/COMPLETED flags."""
    cached = cache.get(f'schedule_{meeting_key}', 60)
    if cached:
        return cached
    sessions = fetch_sessions(meeting_key)
    now = datetime.now(timezone.utc)
    schedule = []
    for s in sorted(sessions, key=lambda x: x.get('date_start', '')):
        try:
            start = datetime.fromisoformat(s['date_start'])
            end = datetime.fromisoformat(s['date_end'])
        except Exception:
            continue
        if start <= now <= end:
            status = 'LIVE'
        elif now > end:
            status = 'COMPLETED'
        else:
            status = 'UPCOMING'
        schedule.append({
            'name': s.get('session_name', ''),
            'type': s.get('session_type', ''),
            'date_start': s.get('date_start', ''),
            'date_end': s.get('date_end', ''),
            'status': status,
            'session_key': s.get('session_key'),
        })
    if schedule:
        cache.set(f'schedule_{meeting_key}', schedule)
    return schedule

def find_fallback_session_with_data():
    cached = cache.get('fallback_session', 600)
    if cached:
        return cached
    now = datetime.now(timezone.utc)
    for year in [now.year, now.year - 1]:
        sessions = safe_fetch(f"{OPENF1_API}/sessions?year={year}")
        if not sessions:
            continue
        completed = [s for s in sessions if datetime.fromisoformat(s['date_end']) <= now]
        completed.sort(key=lambda s: s['date_end'], reverse=True)
        for sess in completed[:5]:
            test = fetch_positions(sess['session_key'])
            if test and len(test) > 0:
                cache.set('fallback_session', sess)
                logger.info(f"Fallback session: {sess.get('session_name')} key={sess['session_key']}")
                return sess
    return None

# --------------- Driver / Position / Interval / Lap Fetchers ---------------
def fetch_drivers(session_key):
    cached = cache.get(f'drv_{session_key}', 3600)
    if cached:
        return cached
    data = safe_fetch(f"{OPENF1_API}/drivers?session_key={session_key}")
    if data:
        result = {d['driver_number']: d for d in data}
        cache.set(f'drv_{session_key}', result)
        return result
    return {}

last_position_date = {}
latest_positions = {}

def fetch_positions(session_key):
    global last_position_date, latest_positions
    cached = cache.get(f'pos_{session_key}', 20)
    if cached:
        return cached
    url = f"{OPENF1_API}/position?session_key={session_key}"
    last_date = last_position_date.get(session_key)
    if last_date:
        url += f"&date>={last_date}"
    data = safe_fetch(url)
    if data:
        if session_key not in latest_positions:
            latest_positions[session_key] = {}
        max_date = last_date or ""
        for e in data:
            dn = e['driver_number']
            if dn not in latest_positions[session_key] or e['date'] > latest_positions[session_key][dn]['date']:
                latest_positions[session_key][dn] = e
            if e['date'] > max_date:
                max_date = e['date']
        if max_date:
            last_position_date[session_key] = max_date
    res = latest_positions.get(session_key, {})
    result = sorted(res.values(), key=lambda x: x.get('position', 99))
    if result:
        cache.set(f'pos_{session_key}', result)
    return result

last_interval_date = {}
latest_intervals = {}

def fetch_intervals(session_key):
    global last_interval_date, latest_intervals
    cached = cache.get(f'int_{session_key}', 20)
    if cached:
        return cached
    url = f"{OPENF1_API}/intervals?session_key={session_key}"
    last_date = last_interval_date.get(session_key)
    if last_date:
        url += f"&date>={last_date}"
    data = safe_fetch(url)
    if data:
        if session_key not in latest_intervals:
            latest_intervals[session_key] = {}
        max_date = last_date or ""
        for e in data:
            dn = e['driver_number']
            if dn not in latest_intervals[session_key] or e['date'] > latest_intervals[session_key][dn]['date']:
                latest_intervals[session_key][dn] = e
            if e['date'] > max_date:
                max_date = e['date']
        if max_date:
            last_interval_date[session_key] = max_date
    result = latest_intervals.get(session_key, {})
    if result:
        cache.set(f'int_{session_key}', result)
    return result

last_lap_num = {}
latest_laps = {}

def fetch_laps(session_key):
    global last_lap_num, latest_laps
    cached = cache.get(f'laps_{session_key}', 20)
    if cached:
        return cached
    url = f"{OPENF1_API}/laps?session_key={session_key}"
    last_num = last_lap_num.get(session_key)
    if last_num:
        url += f"&lap_number>={last_num}"
    data = safe_fetch(url)
    if data:
        if session_key not in latest_laps:
            latest_laps[session_key] = {}
        max_num = last_num or 0
        for e in data:
            dn = e['driver_number']
            if dn not in latest_laps[session_key] or e.get('lap_number', 0) > latest_laps[session_key][dn].get('lap_number', 0):
                latest_laps[session_key][dn] = e
            if e.get('lap_number', 0) > max_num:
                max_num = e['lap_number']
        if max_num:
            last_lap_num[session_key] = max_num
    result = latest_laps.get(session_key, {})
    if result:
        cache.set(f'laps_{session_key}', result)
    return result

def fetch_stints(session_key):
    cached = cache.get(f'stint_{session_key}', 30)
    if cached:
        return cached
    data = safe_fetch(f"{OPENF1_API}/stints?session_key={session_key}")
    if not data:
        return {}
    latest = {}
    for e in data:
        dn = e['driver_number']
        if dn not in latest or e.get('stint_number', 0) > latest[dn].get('stint_number', 0):
            latest[dn] = e
    cache.set(f'stint_{session_key}', latest)
    return latest

def fetch_weather(meeting_key, session_key=None):
    url = (f"{OPENF1_API}/weather?session_key={session_key}" if session_key
           else f"{OPENF1_API}/weather?meeting_key={meeting_key}")
    data = safe_fetch(url)
    return data[-1] if data else None

def fetch_team_radio(session_key):
    cached = cache.get(f'radio_{session_key}', 20)
    if cached:
        return cached
    data = safe_fetch(f"{OPENF1_API}/team_radio?session_key={session_key}")
    result = (data[-15:] if len(data) > 15 else data) if data else []
    cache.set(f'radio_{session_key}', result)
    return result

def fetch_race_control(session_key):
    cached = cache.get(f'rc_{session_key}', 20)
    if cached:
        return cached
    data = safe_fetch(f"{OPENF1_API}/race_control?session_key={session_key}")
    result = (data[-15:] if len(data) > 15 else data) if data else []
    cache.set(f'rc_{session_key}', result)
    return result

def fetch_pit_stops(session_key):
    cached = cache.get(f'pits_{session_key}', 20)
    if cached:
        return cached
    data = safe_fetch(f"{OPENF1_API}/pit?session_key={session_key}")
    if not data:
        return []
    result = data[-15:] if len(data) > 15 else data
    cache.set(f'pits_{session_key}', result)
    return result

# --------------- Calendar ---------------
def fetch_race_calendar():
    cached = cache.get('calendar', 3600)
    if cached:
        return cached
    data = safe_fetch(f"{ERGAST_API}/current.json")
    if not data:
        return []
    try:
        now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        races = []
        for r in data['MRData']['RaceTable']['Races']:
            race_date = r['date']
            races.append({
                'round': int(r['round']),
                'name': r['raceName'],
                'circuit': r['Circuit']['circuitName'],
                'location': r['Circuit']['Location']['locality'],
                'country': r['Circuit']['Location']['country'],
                'date': race_date,
                'time_utc': r.get('time', ''),
                'lat': float(r['Circuit']['Location']['lat']),
                'lon': float(r['Circuit']['Location']['long']),
                'status': 'COMPLETED' if race_date < now_str else 'UPCOMING',
                'flag': COUNTRY_FLAGS.get(r['Circuit']['Location']['country'], '🏁'),
                'circuit_id': r['Circuit'].get('circuitId', ''),
            })
        cache.set('calendar', races)
        return races
    except Exception as e:
        logger.error(f"Calendar parse error: {e}")
        return []

# --------------- Standings ---------------
TEAM_COLOURS = {
    'red bull': '3671C6', 'mclaren': 'FF8000', 'ferrari': 'E8002D',
    'mercedes': '27F4D2', 'aston martin': '229971', 'alpine': 'FF87BC',
    'williams': '64C4FF', 'haas': 'B6BABD', 'rb': '6692FF',
    'visa cash app rb': '6692FF', 'racing bulls': '6692FF',
    'kick sauber': '52E252', 'sauber': '52E252', 'alfa romeo': '900000',
    'alphatauri': '4E7C9B',
}

def get_team_colour(constructor_name):
    name = (constructor_name or '').lower()
    for key, colour in TEAM_COLOURS.items():
        if key in name:
            return colour
    return 'ffffff'

# --------------- Circuit Database ---------------
CIRCUIT_INFO = {
    'bahrain':      {'laps': 57, 'length_km': 5.412, 'drs_zones': 3, 'corners': 15, 'lap_record': '1:31.447', 'record_holder': 'Pedro de la Rosa', 'record_year': 2005, 'first_gp': 2004, 'surface': 'Smooth Asphalt',
                     'dna': {'speed': 7, 'tire_wear': 6, 'overtaking': 8, 'downforce': 'Medium'}},
    'jeddah':       {'laps': 50, 'length_km': 6.174, 'drs_zones': 3, 'corners': 27, 'lap_record': '1:30.734', 'record_holder': 'Lewis Hamilton', 'record_year': 2021, 'first_gp': 2021, 'surface': 'Street Circuit',
                     'dna': {'speed': 9, 'tire_wear': 4, 'overtaking': 6, 'downforce': 'Low'}},
    'albert_park':  {'laps': 58, 'length_km': 5.278, 'drs_zones': 4, 'corners': 16, 'lap_record': '1:20.235', 'record_holder': 'Charles Leclerc', 'record_year': 2022, 'first_gp': 1996, 'surface': 'Street Circuit',
                     'dna': {'speed': 7, 'tire_wear': 5, 'overtaking': 6, 'downforce': 'Medium'}},
    'shanghai':     {'laps': 56, 'length_km': 5.451, 'drs_zones': 2, 'corners': 16, 'lap_record': '1:32.238', 'record_holder': 'Michael Schumacher', 'record_year': 2004, 'first_gp': 2004, 'surface': 'Smooth Asphalt',
                     'dna': {'speed': 7, 'tire_wear': 6, 'overtaking': 7, 'downforce': 'Medium'}},
    'miami':        {'laps': 57, 'length_km': 5.412, 'drs_zones': 3, 'corners': 19, 'lap_record': '1:29.708', 'record_holder': 'Max Verstappen', 'record_year': 2023, 'first_gp': 2022, 'surface': 'Street Circuit',
                     'dna': {'speed': 7, 'tire_wear': 7, 'overtaking': 6, 'downforce': 'Medium'}},
    'imola':        {'laps': 63, 'length_km': 4.909, 'drs_zones': 2, 'corners': 19, 'lap_record': '1:15.484', 'record_holder': 'Rubens Barrichello', 'record_year': 2004, 'first_gp': 1980, 'surface': 'Rough Asphalt',
                     'dna': {'speed': 6, 'tire_wear': 7, 'overtaking': 3, 'downforce': 'High'}},
    'monaco':       {'laps': 78, 'length_km': 3.337, 'drs_zones': 1, 'corners': 19, 'lap_record': '1:10.166', 'record_holder': 'Rubens Barrichello', 'record_year': 2004, 'first_gp': 1950, 'surface': 'Street Circuit',
                     'dna': {'speed': 3, 'tire_wear': 3, 'overtaking': 1, 'downforce': 'Extreme'}},
    'catalunya':    {'laps': 66, 'length_km': 4.675, 'drs_zones': 2, 'corners': 14, 'lap_record': '1:16.330', 'record_holder': 'Max Verstappen', 'record_year': 2023, 'first_gp': 1991, 'surface': 'Medium Asphalt',
                     'dna': {'speed': 6, 'tire_wear': 9, 'overtaking': 4, 'downforce': 'High'}},
    'villeneuve':   {'laps': 70, 'length_km': 4.361, 'drs_zones': 3, 'corners': 13, 'lap_record': '1:13.078', 'record_holder': 'Valtteri Bottas', 'record_year': 2019, 'first_gp': 1978, 'surface': 'Street Circuit',
                     'dna': {'speed': 7, 'tire_wear': 5, 'overtaking': 7, 'downforce': 'Low'}},
    'silverstone':  {'laps': 52, 'length_km': 5.891, 'drs_zones': 2, 'corners': 18, 'lap_record': '1:27.097', 'record_holder': 'Max Verstappen', 'record_year': 2020, 'first_gp': 1950, 'surface': 'Smooth Asphalt',
                     'dna': {'speed': 9, 'tire_wear': 8, 'overtaking': 6, 'downforce': 'Medium'}},
    'red_bull_ring':{'laps': 71, 'length_km': 4.318, 'drs_zones': 3, 'corners': 10, 'lap_record': '1:05.619', 'record_holder': 'Carlos Sainz Jr.', 'record_year': 2020, 'first_gp': 1970, 'surface': 'Smooth Asphalt',
                     'dna': {'speed': 8, 'tire_wear': 6, 'overtaking': 7, 'downforce': 'Low'}},
    'hungaroring':  {'laps': 70, 'length_km': 4.381, 'drs_zones': 1, 'corners': 14, 'lap_record': '1:16.627', 'record_holder': 'Lewis Hamilton', 'record_year': 2020, 'first_gp': 1986, 'surface': 'Medium Asphalt',
                     'dna': {'speed': 5, 'tire_wear': 7, 'overtaking': 2, 'downforce': 'High'}},
    'spa':          {'laps': 44, 'length_km': 7.004, 'drs_zones': 2, 'corners': 19, 'lap_record': '1:46.286', 'record_holder': 'Valtteri Bottas', 'record_year': 2018, 'first_gp': 1950, 'surface': 'Smooth Asphalt',
                     'dna': {'speed': 9, 'tire_wear': 5, 'overtaking': 7, 'downforce': 'Low'}},
    'zandvoort':    {'laps': 72, 'length_km': 4.259, 'drs_zones': 2, 'corners': 14, 'lap_record': '1:11.097', 'record_holder': 'Lewis Hamilton', 'record_year': 2021, 'first_gp': 1952, 'surface': 'Banked Asphalt',
                     'dna': {'speed': 7, 'tire_wear': 7, 'overtaking': 3, 'downforce': 'High'}},
    'monza':        {'laps': 53, 'length_km': 5.793, 'drs_zones': 3, 'corners': 11, 'lap_record': '1:21.046', 'record_holder': 'Rubens Barrichello', 'record_year': 2004, 'first_gp': 1950, 'surface': 'Smooth Asphalt',
                     'dna': {'speed': 10, 'tire_wear': 3, 'overtaking': 8, 'downforce': 'Low'}},
    'baku':         {'laps': 51, 'length_km': 6.003, 'drs_zones': 2, 'corners': 20, 'lap_record': '1:43.009', 'record_holder': 'Charles Leclerc', 'record_year': 2019, 'first_gp': 2016, 'surface': 'Street Circuit',
                     'dna': {'speed': 8, 'tire_wear': 4, 'overtaking': 7, 'downforce': 'Low'}},
    'marina_bay':   {'laps': 62, 'length_km': 5.065, 'drs_zones': 3, 'corners': 23, 'lap_record': '1:35.867', 'record_holder': 'Kevin Magnussen', 'record_year': 2018, 'first_gp': 2008, 'surface': 'Street Circuit',
                     'dna': {'speed': 4, 'tire_wear': 5, 'overtaking': 3, 'downforce': 'High'}},
    'suzuka':       {'laps': 53, 'length_km': 5.807, 'drs_zones': 1, 'corners': 18, 'lap_record': '1:30.983', 'record_holder': 'Lewis Hamilton', 'record_year': 2019, 'first_gp': 1987, 'surface': 'Smooth Asphalt',
                     'dna': {'speed': 8, 'tire_wear': 7, 'overtaking': 4, 'downforce': 'High'}},
    'losail':       {'laps': 57, 'length_km': 5.419, 'drs_zones': 2, 'corners': 16, 'lap_record': '1:24.319', 'record_holder': 'Max Verstappen', 'record_year': 2023, 'first_gp': 2021, 'surface': 'Smooth Asphalt',
                     'dna': {'speed': 8, 'tire_wear': 7, 'overtaking': 5, 'downforce': 'Medium'}},
    'americas':     {'laps': 56, 'length_km': 5.513, 'drs_zones': 2, 'corners': 20, 'lap_record': '1:36.169', 'record_holder': 'Charles Leclerc', 'record_year': 2019, 'first_gp': 2012, 'surface': 'Undulating Asphalt',
                     'dna': {'speed': 7, 'tire_wear': 7, 'overtaking': 6, 'downforce': 'Medium'}},
    'rodriguez':    {'laps': 71, 'length_km': 4.304, 'drs_zones': 3, 'corners': 17, 'lap_record': '1:17.774', 'record_holder': 'Valtteri Bottas', 'record_year': 2021, 'first_gp': 1963, 'surface': 'High Altitude',
                     'dna': {'speed': 6, 'tire_wear': 5, 'overtaking': 6, 'downforce': 'Medium'}},
    'interlagos':   {'laps': 71, 'length_km': 4.309, 'drs_zones': 2, 'corners': 15, 'lap_record': '1:10.540', 'record_holder': 'Rubens Barrichello', 'record_year': 2004, 'first_gp': 1973, 'surface': 'Rough Asphalt',
                     'dna': {'speed': 7, 'tire_wear': 6, 'overtaking': 7, 'downforce': 'Medium'}},
    'las_vegas':    {'laps': 50, 'length_km': 6.201, 'drs_zones': 2, 'corners': 17, 'lap_record': '1:35.490', 'record_holder': 'Oscar Piastri', 'record_year': 2023, 'first_gp': 2023, 'surface': 'Street Circuit',
                     'dna': {'speed': 9, 'tire_wear': 4, 'overtaking': 8, 'downforce': 'Low'}},
    'yas_marina':   {'laps': 58, 'length_km': 5.281, 'drs_zones': 2, 'corners': 16, 'lap_record': '1:26.103', 'record_holder': 'Max Verstappen', 'record_year': 2021, 'first_gp': 2009, 'surface': 'Smooth Asphalt',
                     'dna': {'speed': 7, 'tire_wear': 5, 'overtaking': 7, 'downforce': 'Medium'}},
}

def get_circuit_info(circuit_id, location=''):
    cid = (circuit_id or '').lower()
    if cid in CIRCUIT_INFO:
        return CIRCUIT_INFO[cid]
    loc = (location or '').lower()
    for key in CIRCUIT_INFO:
        if key in loc or loc in key:
            return CIRCUIT_INFO[key]
    return None

# --------------- Last Race Highlights ---------------
def fetch_last_race_highlights():
    """Full results of the most recent completed race from Ergast."""
    cached = cache.get('last_race_hl', 1800)
    if cached:
        return cached
    data = safe_fetch(f"{ERGAST_API}/current/last/results.json")
    if not data:
        return None
    try:
        race = data['MRData']['RaceTable']['Races'][0]
        results = race.get('Results', [])

        podium = []
        for r in results[:3]:
            podium.append({
                'position': int(r['position']),
                'driver': f"{r['Driver']['givenName']} {r['Driver']['familyName']}",
                'code': r['Driver'].get('code', r['Driver']['familyName'][:3].upper()),
                'team': r['Constructor']['name'],
                'team_colour': get_team_colour(r['Constructor']['name']),
                'time': r.get('Time', {}).get('time', r.get('status', 'N/A')),
                'points': float(r.get('points', 0)),
                'grid': r.get('grid', '--'),
                'laps': r.get('laps', '--'),
                'status': r.get('status', 'Finished'),
            })

        fastest = None
        for r in results:
            fl = r.get('FastestLap', {})
            if fl.get('rank') == '1':
                fastest = {
                    'driver': r['Driver'].get('code', r['Driver']['familyName'][:3].upper()),
                    'time': fl.get('Time', {}).get('time', ''),
                    'lap': fl.get('lap', ''),
                    'speed_kph': fl.get('AverageSpeed', {}).get('speed', ''),
                    'team_colour': get_team_colour(r['Constructor']['name']),
                }
                break

        all_results = []
        for r in results:
            all_results.append({
                'position': r.get('position', '--'),
                'code': r['Driver'].get('code', r['Driver']['familyName'][:3].upper()),
                'driver': f"{r['Driver']['givenName']} {r['Driver']['familyName']}",
                'team': r['Constructor']['name'],
                'team_colour': get_team_colour(r['Constructor']['name']),
                'time': r.get('Time', {}).get('time', '') if r.get('Time') else r.get('status', ''),
                'status': r.get('status', ''),
                'points': float(r.get('points', 0)),
                'grid': r.get('grid', '--'),
                'laps': r.get('laps', '--'),
            })

        circuit_id = race.get('Circuit', {}).get('circuitId', '')
        result = {
            'name': race.get('raceName', ''),
            'round': race.get('round', ''),
            'circuit_name': race.get('Circuit', {}).get('circuitName', ''),
            'circuit_id': circuit_id,
            'location': race.get('Circuit', {}).get('Location', {}).get('locality', ''),
            'country': race.get('Circuit', {}).get('Location', {}).get('country', ''),
            'date': race.get('date', ''),
            'flag': COUNTRY_FLAGS.get(race.get('Circuit', {}).get('Location', {}).get('country', ''), '🏁'),
            'podium': podium,
            'fastest_lap': fastest,
            'winner': podium[0] if podium else None,
            'total_entries': len(results),
            'all_results': all_results,
        }
        cache.set('last_race_hl', result)
        return result
    except Exception as e:
        logger.error(f"Last race highlights error: {e}")
        return None

def fetch_circuit_history(circuit_id, limit=5):
    """Recent race winners at a specific circuit."""
    if not circuit_id:
        return []
    cached = cache.get(f'circuit_hist_{circuit_id}', 7200)
    if cached:
        return cached
    # Fetch results without position path-filter — Jolpi supports this reliably
    data = safe_fetch(f"{ERGAST_API}/circuits/{circuit_id}/results.json?limit=100")
    if not data:
        return []
    try:
        history = []
        races = data['MRData']['RaceTable']['Races']
        # Sort descending by season + round, take most recent 'limit'
        races_sorted = sorted(
            races,
            key=lambda r: (r.get('season', '0'), r.get('round', '0').zfill(3)),
            reverse=True
        )
        for race in races_sorted[:limit]:
            results = race.get('Results', [])
            winner = next((r for r in results if r.get('position') == '1'), None)
            if not winner and results:
                winner = results[0]
            if winner:
                history.append({
                    'year': race.get('season', ''),
                    'gp_name': race.get('raceName', ''),
                    'driver': f"{winner['Driver']['givenName']} {winner['Driver']['familyName']}",
                    'code': winner['Driver'].get('code', winner['Driver']['familyName'][:3].upper()),
                    'team': winner['Constructor']['name'],
                    'team_colour': get_team_colour(winner['Constructor']['name']),
                    'time': winner.get('Time', {}).get('time', '') if winner.get('Time') else winner.get('status', ''),
                })
        if history:
            cache.set(f'circuit_hist_{circuit_id}', history)
        return history
    except Exception as e:
        logger.error(f"Circuit history error {circuit_id}: {e}")
        return []


def fetch_driver_form():
    """Last 5 race results (position) for every driver this season."""
    cached = cache.get('driver_form', 1800)
    if cached:
        return cached
    data = safe_fetch(f"{ERGAST_API}/current/results.json?limit=500")
    if not data:
        return {}
    try:
        form: dict = {}
        races = data['MRData']['RaceTable']['Races']
        for race in sorted(races, key=lambda r: int(r.get('round', 0))):
            for result in race.get('Results', []):
                code = result['Driver'].get('code', result['Driver']['familyName'][:3].upper())
                tc = get_team_colour(result['Constructor']['name'])
                pos = result.get('position', '--')
                status = result.get('status', '')
                # Mark as DNF if not a clean finish
                is_dnf = (
                    status not in ('Finished',) and
                    not status.startswith('+') and
                    not (status and status[0].isdigit())
                )
                entry = 'DNF' if is_dnf else pos
                if code not in form:
                    form[code] = {'positions': [], 'team_colour': tc}
                form[code]['positions'].append(entry)
                form[code]['team_colour'] = tc
        for code in form:
            form[code]['positions'] = form[code]['positions'][-5:]
        cache.set('driver_form', form)
        return form
    except Exception as e:
        logger.error(f"Driver form error: {e}")
        return {}

def fetch_ergast_standings():
    cached = cache.get('ergast_standings', 300)
    if cached:
        return cached
    drivers_out, teams_out = [], []

    data = safe_fetch(f"{ERGAST_API}/current/driverStandings.json")
    if data:
        try:
            standings = data['MRData']['StandingsTable']['StandingsLists']
            if standings:
                for d in standings[0]['DriverStandings'][:20]:
                    constructor = d['Constructors'][0]['name'] if d.get('Constructors') else ''
                    drivers_out.append({
                        'position': d['position'],
                        'name': f"{d['Driver']['givenName']} {d['Driver']['familyName']}",
                        'name_acronym': d['Driver'].get('code', d['Driver']['familyName'][:3].upper()),
                        'team_name': constructor,
                        'team_colour': get_team_colour(constructor),
                        'points': float(d['points']),
                        'wins': int(d.get('wins', 0)),
                    })
        except Exception as e:
            logger.error(f"Ergast driver standings: {e}")

    data = safe_fetch(f"{ERGAST_API}/current/constructorStandings.json")
    if data:
        try:
            standings = data['MRData']['StandingsTable']['StandingsLists']
            if standings:
                for t in standings[0]['ConstructorStandings'][:10]:
                    teams_out.append({
                        'position': t['position'],
                        'team_name': t['Constructor']['name'],
                        'team_colour': get_team_colour(t['Constructor']['name']),
                        'points': float(t['points']),
                        'wins': int(t.get('wins', 0)),
                    })
        except Exception as e:
            logger.error(f"Ergast constructor standings: {e}")

    result = {'drivers': drivers_out, 'constructors': teams_out}
    if drivers_out or teams_out:
        cache.set('ergast_standings', result)
    return result

def find_latest_race_session():
    cached = cache.get('latest_race_sk', 3600)
    if cached:
        return cached
    now = datetime.now(timezone.utc)
    for year in [now.year, now.year - 1]:
        data = safe_fetch(f"{OPENF1_API}/sessions?session_name=Race&year={year}")
        if data:
            completed = [s for s in data if datetime.fromisoformat(s['date_end']) <= now]
            if completed:
                completed.sort(key=lambda s: s['date_end'])
                sk = completed[-1]['session_key']
                cache.set('latest_race_sk', sk)
                return sk
    return None

def build_standings(latest_race_sk):
    ergast = fetch_ergast_standings()
    if ergast['drivers'] or ergast['constructors']:
        return ergast
    if not latest_race_sk:
        return {'drivers': [], 'constructors': []}
    drv_info = fetch_drivers(latest_race_sk)
    return {'drivers': [], 'constructors': []}

# --------------- Qualifying / Race Results ---------------
def fetch_qualifying_results(meeting_key):
    cached = cache.get(f'quali_{meeting_key}', 300)
    if cached:
        return cached
    sessions = fetch_sessions(meeting_key)
    quali = [s for s in sessions if 'qualifying' in (s.get('session_name') or '').lower()]
    if not quali:
        return []
    quali.sort(key=lambda s: s.get('date_start', ''))
    sk = quali[-1]['session_key']
    drivers = fetch_drivers(sk)
    positions = fetch_positions(sk)
    laps_data = fetch_laps(sk)
    results = []
    for pos in positions:
        dn = pos['driver_number']
        drv = drivers.get(dn, {})
        lap = laps_data.get(dn, {})
        ld = lap.get('lap_duration')
        time_str = f"{int(ld // 60)}:{ld % 60:06.3f}" if ld else "--"
        results.append({
            'position': pos.get('position', '--'),
            'broadcast_name': drv.get('broadcast_name', f'CAR {dn}'),
            'name_acronym': drv.get('name_acronym', str(dn)),
            'team_name': drv.get('team_name', 'Unknown'),
            'team_colour': drv.get('team_colour', 'ffffff'),
            'time': time_str,
            'driver_number': dn,
        })
    cache.set(f'quali_{meeting_key}', results)
    return results

def fetch_race_results(meeting_key):
    cached = cache.get(f'race_res_{meeting_key}', 300)
    if cached:
        return cached
    sessions = fetch_sessions(meeting_key)
    races = [s for s in sessions if (s.get('session_name') or '').lower() == 'race']
    if not races:
        return []
    sk = races[-1]['session_key']
    now = datetime.now(timezone.utc)
    if datetime.fromisoformat(races[-1]['date_end']) > now:
        return []
    drivers = fetch_drivers(sk)
    positions = fetch_positions(sk)
    laps_data = fetch_laps(sk)
    results = []
    for pos in positions:
        dn = pos['driver_number']
        drv = drivers.get(dn, {})
        lap = laps_data.get(dn, {})
        results.append({
            'position': pos.get('position', '--'),
            'broadcast_name': drv.get('broadcast_name', f'CAR {dn}'),
            'name_acronym': drv.get('name_acronym', str(dn)),
            'team_name': drv.get('team_name', 'Unknown'),
            'team_colour': drv.get('team_colour', 'ffffff'),
            'laps': lap.get('lap_number', 0),
            'driver_number': dn,
        })
    cache.set(f'race_res_{meeting_key}', results)
    return results

# --------------- Coord Resolver ---------------
def resolve_coords(meeting, calendar):
    loc = (meeting.get('location') or '').lower()
    for r in calendar:
        if r.get('location', '').lower() == loc:
            return {'lat': r['lat'], 'lon': r['lon']}
    for key, coords in CIRCUIT_COORDS.items():
        if key in loc:
            return coords
    return CIRCUIT_COORDS.get('monaco', {'lat': 43.7347, 'lon': 7.4206})

# --------------- Cached last payload (for new connections) ---------------
last_payload = {}

# --------------- Main Update Loop ---------------
def update_data():
    global current_meeting, current_session, drivers_map, update_counter, last_payload
    update_counter += 1
    slow = update_counter % 5 == 1

    try:
        if slow or not current_meeting:
            m = fetch_current_meeting()
            if m:
                current_meeting = m
                logger.info(f"Meeting: {m.get('meeting_name')}")
        if not current_meeting:
            logger.warning("No meeting data available")
            return

        mk = current_meeting['meeting_key']

        if slow or not current_session:
            s = get_active_session(mk)
            if s:
                current_session = s
                logger.info(f"Session: {s.get('session_name')} key={s.get('session_key')}")

        sk = current_session['session_key'] if current_session else None

        if sk and (slow or not drivers_map):
            drivers_map = fetch_drivers(sk)

        # Critical fix: initialize session name before any fallback paths can fail
        data_session_name = (current_session.get('session_name', 'UPCOMING')
                             if current_session else 'UPCOMING')

        # Determine session live status
        session_is_live = False
        if current_session:
            try:
                now = datetime.now(timezone.utc)
                ss = datetime.fromisoformat(current_session['date_start'])
                se = datetime.fromisoformat(current_session['date_end'])
                session_is_live = ss <= now <= se
            except Exception:
                pass

        # Resolve coords early so we can fetch external weather in the parallel pool
        cal_cached = cache.get('calendar') or []
        coords = resolve_coords(current_meeting, cal_cached)

        # ---------- PARALLEL FETCH ----------
        results = {}
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {}
            if sk:
                futures['positions'] = pool.submit(fetch_positions, sk)
                futures['intervals'] = pool.submit(fetch_intervals, sk)
                futures['laps'] = pool.submit(fetch_laps, sk)
                futures['stints'] = pool.submit(fetch_stints, sk)
                futures['weather'] = pool.submit(fetch_weather, mk, sk)
                futures['radio'] = pool.submit(fetch_team_radio, sk)
                futures['control'] = pool.submit(fetch_race_control, sk)
                futures['pits'] = pool.submit(fetch_pit_stops, sk)
            futures['ext_weather'] = pool.submit(fetch_external_weather, coords['lat'], coords['lon'])
            # Calendar always submitted — fetch_race_calendar has a 3600s TTL cache
            futures['calendar'] = pool.submit(fetch_race_calendar)
            futures['forecast'] = pool.submit(fetch_race_forecast, coords['lat'], coords['lon'])
            if slow:
                futures['schedule'] = pool.submit(build_weekend_schedule, mk)
                futures['quali'] = pool.submit(fetch_qualifying_results, mk)
                futures['race_res'] = pool.submit(fetch_race_results, mk)
                futures['last_race'] = pool.submit(fetch_last_race_highlights)
                futures['driver_form'] = pool.submit(fetch_driver_form)
            for key, fut in futures.items():
                try:
                    results[key] = fut.result(timeout=20)
                except Exception as e:
                    logger.error(f"Parallel fetch [{key}] error: {e}")
                    results[key] = None

        # Update calendar cache
        if results.get('calendar'):
            coords = resolve_coords(current_meeting, results['calendar'])

        # ---------- BUILD TIMING ----------
        positions = results.get('positions') or []
        intervals = results.get('intervals') or {}
        laps = results.get('laps') or {}
        stints = results.get('stints') or {}

        timing = []
        if sk and positions and drivers_map:
            for pos in positions:
                dn = pos.get('driver_number')
                drv = drivers_map.get(dn, {})
                iv = intervals.get(dn, {})
                lap = laps.get(dn, {})
                st = stints.get(dn, {})
                ld = lap.get('lap_duration')
                if isinstance(ld, (int, float)) and ld > 0:
                    m_part = int(ld // 60)
                    s_part = ld % 60
                    time_str = f"{m_part}:{s_part:06.3f}"
                else:
                    time_str = "--"
                gap = iv.get('gap_to_leader')
                gap_str = ("+0.000" if pos.get('position') == 1
                           else (f"+{gap:.3f}" if isinstance(gap, (int, float))
                                 else (gap if gap else "--")))
                timing.append({
                    'position': pos.get('position', '--'),
                    'driver_number': dn,
                    'broadcast_name': drv.get('broadcast_name', f'CAR {dn}'),
                    'name_acronym': drv.get('name_acronym', str(dn)),
                    'team_name': drv.get('team_name', 'Unknown'),
                    'team_colour': drv.get('team_colour', 'ffffff'),
                    'time': time_str,
                    'gap': gap_str,
                    'interval': iv.get('interval'),
                    'compound': st.get('compound', '--'),
                    'stint_number': st.get('stint_number', 1),
                    'lap_start_stint': st.get('lap_start', 0),
                    'lap_number': lap.get('lap_number', 0),
                    'is_pit_out_lap': lap.get('is_pit_out_lap', False),
                })

        # ---------- GAP SPARKLINE HISTORY ----------
        global _gap_hist, _gap_hist_session
        if sk and sk != _gap_hist_session:
            _gap_hist = {}
            _gap_hist_session = sk
        for d in timing:
            dn = d['driver_number']
            g = d.get('gap', '--')
            if g and isinstance(g, str) and g.startswith('+') and g != '+0.000':
                try:
                    val = float(g[1:])
                    if dn not in _gap_hist:
                        _gap_hist[dn] = []
                    _gap_hist[dn].append(round(val, 3))
                    if len(_gap_hist[dn]) > 20:
                        _gap_hist[dn] = _gap_hist[dn][-20:]
                except (ValueError, TypeError):
                    pass
            d['gap_history'] = _gap_hist.get(dn, [])[-15:]

        # ---------- WEATHER ----------
        w_raw = results.get('weather')
        if w_raw:
            rain = w_raw.get('rainfall', 0)
            cond = ('Rain' if rain and rain > 0
                    else ('Overcast' if w_raw.get('humidity', 0) > 80
                          else ('Hot' if w_raw.get('air_temperature', 25) > 30 else 'Clear')))
            track_weather = {
                'air_temp': w_raw.get('air_temperature', '--'),
                'track_temp': w_raw.get('track_temperature', '--'),
                'humidity': w_raw.get('humidity', '--'),
                'wind_speed': w_raw.get('wind_speed', '--'),
                'wind_direction': w_raw.get('wind_direction', '--'),
                'rainfall': rain,
                'condition': cond,
            }
        else:
            track_weather = {'air_temp': '--', 'track_temp': '--', 'humidity': '--',
                             'wind_speed': '--', 'wind_direction': '--', 'rainfall': 0, 'condition': 'No Data'}

        # External weather from Open-Meteo
        ext_weather = results.get('ext_weather')

        # Use Open-Meteo UTC offset (DST-aware) if available
        track_utc_offset = (ext_weather['utc_offset_hours'] if ext_weather
                            else resolve_track_utc_offset(current_meeting))

        # ---------- RADIO + CONTROL ----------
        radio_raw = results.get('radio') or []
        radio = []
        for msg in radio_raw:
            dn = msg.get('driver_number')
            drv = drivers_map.get(dn, {}) if drivers_map else {}
            radio.append({
                'driver': drv.get('name_acronym', str(dn)),
                'team_colour': drv.get('team_colour', 'ffffff'),
                'message': f"Radio — {drv.get('broadcast_name', f'Car {dn}')}",
                'timestamp': msg.get('date', ''),
                'recording_url': msg.get('recording_url', ''),
            })

        control_raw = results.get('control') or []
        control = []
        for msg in control_raw:
            control.append({
                'type': 'race_control',
                'timestamp': msg.get('date', ''),
                'flag': msg.get('flag', ''),
                'message': msg.get('message', ''),
                'category': msg.get('category', ''),
                'scope': msg.get('scope', ''),
            })

        # ---------- PIT STOPS ----------
        pits_raw = results.get('pits') or []
        pit_stops = []
        for p in pits_raw:
            dn = p.get('driver_number')
            drv = drivers_map.get(dn, {}) if drivers_map else {}
            dur = p.get('pit_duration')
            pit_stops.append({
                'driver': drv.get('name_acronym', str(dn)),
                'driver_number': dn,
                'broadcast_name': drv.get('broadcast_name', f'Car {dn}'),
                'team_colour': drv.get('team_colour', 'ffffff'),
                'lap': p.get('lap_number', '--'),
                'duration': f"{dur:.1f}s" if isinstance(dur, (int, float)) else '--',
                'duration_raw': dur,
                'timestamp': p.get('date', ''),
            })

        # ---------- FALLBACK — if no live timing try last real session ----------
        if not timing and slow:
            fallback = find_fallback_session_with_data()
            if fallback:
                fb_sk = fallback['session_key']
                fb_drivers = fetch_drivers(fb_sk)
                fb_pos = fetch_positions(fb_sk)
                fb_iv = fetch_intervals(fb_sk)
                fb_laps = fetch_laps(fb_sk)
                fb_stints = fetch_stints(fb_sk)
                for pos in fb_pos:
                    dn = pos.get('driver_number')
                    drv = fb_drivers.get(dn, {})
                    iv = fb_iv.get(dn, {})
                    lap = fb_laps.get(dn, {})
                    st = fb_stints.get(dn, {})
                    ld = lap.get('lap_duration')
                    time_str = (f"{int(ld//60)}:{ld%60:06.3f}"
                                if isinstance(ld, (int, float)) and ld > 0 else "--")
                    gap = iv.get('gap_to_leader')
                    gap_str = ("+0.000" if pos.get('position') == 1
                               else (f"+{gap:.3f}" if isinstance(gap, (int, float)) else "--"))
                    timing.append({
                        'position': pos.get('position', '--'),
                        'driver_number': dn,
                        'broadcast_name': drv.get('broadcast_name', f'CAR {dn}'),
                        'name_acronym': drv.get('name_acronym', str(dn)),
                        'team_name': drv.get('team_name', 'Unknown'),
                        'team_colour': drv.get('team_colour', 'ffffff'),
                        'time': time_str,
                        'gap': gap_str,
                        'interval': iv.get('interval'),
                        'compound': st.get('compound', '--'),
                        'stint_number': st.get('stint_number', 1),
                        'lap_start_stint': st.get('lap_start', 0),
                        'lap_number': lap.get('lap_number', 0),
                        'is_pit_out_lap': False,
                    })
                data_session_name = (
                    f"{data_session_name} — LAST DATA: {fallback.get('session_name', '')}"
                )
                logger.info(f"Using fallback data from session {fb_sk}")

        # ---------- STANDINGS ----------
        standings = cache.get('standings_built', 300)
        has_standings = standings and (standings.get('drivers') or standings.get('constructors'))
        if not has_standings or slow:
            lrsk = find_latest_race_session()
            standings = build_standings(lrsk)
            if standings.get('drivers') or standings.get('constructors'):
                cache.set('standings_built', standings)
        if not standings:
            standings = {'drivers': [], 'constructors': []}

        calendar = results.get('calendar') or cache.get('calendar') or []
        schedule = results.get('schedule') or cache.get(f'schedule_{mk}') or []
        qualifying = results.get('quali') or cache.get(f'quali_{mk}') or []
        race_results = results.get('race_res') or cache.get(f'race_res_{mk}') or []

        next_race = None
        if calendar:
            now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            for r in calendar:
                if r['date'] >= now_str:
                    next_race = r
                    break

        # Last race highlights + circuit data for next race
        last_race = results.get('last_race') or cache.get('last_race_hl')
        next_circuit_id = next_race.get('circuit_id', '') if next_race else ''
        circuit_info = get_circuit_info(next_circuit_id, next_race.get('circuit', '') if next_race else '')
        circuit_history = fetch_circuit_history(next_circuit_id) if next_circuit_id else []

        # Season stats from standings + calendar
        completed_races = sum(1 for r in calendar if r.get('status') == 'COMPLETED')
        total_races = len(calendar)
        # Fallback: use last_race round number when calendar is stale or empty
        if completed_races == 0 and last_race and last_race.get('round'):
            try:
                completed_races = int(last_race['round'])
            except (ValueError, TypeError):
                pass
        leaders = [d for d in (standings.get('drivers') or []) if str(d.get('position')) == '1']
        second_place = [d for d in (standings.get('drivers') or []) if str(d.get('position')) == '2']
        season_stats = {
            'completed': completed_races,
            'total': total_races or 24,
            'leader': leaders[0] if leaders else None,
            'second': second_place[0] if second_place else None,
            'points_gap': round((leaders[0].get('points', 0) - second_place[0].get('points', 0)), 1)
                          if leaders and second_place else None,
        }

        driver_form = results.get('driver_form') or cache.get('driver_form') or {}

        # Forecast: prefer next-race coords when off-weekend
        forecast = results.get('forecast') or []
        if is_off_weekend and next_race and next_race.get('lat'):
            nr_lat, nr_lon = float(next_race['lat']), float(next_race['lon'])
            if abs(nr_lat - coords['lat']) > 0.5 or abs(nr_lon - coords['lon']) > 0.5:
                forecast = fetch_race_forecast(nr_lat, nr_lon)

        is_off_weekend = not timing and not session_is_live

        country = current_meeting.get('country_name', '')
        current_race = {
            'name': current_meeting.get('meeting_name', ''),
            'circuit': current_meeting.get('circuit_short_name', ''),
            'location': current_meeting.get('location', ''),
            'country': country,
            'flag': COUNTRY_FLAGS.get(country, '🏁'),
            'session_name': data_session_name,
            'session_type': current_session.get('session_type', '') if current_session else '',
            'is_live': session_is_live,
            'date_start': current_meeting.get('date_start', ''),
            'date_end': current_meeting.get('date_end', ''),
        }

        last_payload = {
            'race_calendar': calendar,
            'weekend_schedule': schedule,
            'live_timing': timing,
            'standings': standings,
            'track_weather': track_weather,
            'external_weather': ext_weather,
            'team_radio': radio,
            'race_control': control,
            'qualifying': qualifying,
            'race_results': race_results,
            'pit_stops': pit_stops,
            'current_race': current_race,
            'next_race': next_race,
            'circuit_coords': coords,
            'track_utc_offset': track_utc_offset,
            'last_race': last_race,
            'circuit_info': circuit_info,
            'circuit_history': circuit_history,
            'season_stats': season_stats,
            'driver_form': driver_form,
            'forecast': forecast,
            'is_off_weekend': is_off_weekend,
        }
        socketio.emit('data_update', last_payload)

    except Exception as e:
        logger.error(f"update_data error: {e}", exc_info=True)


def run_scheduler():
    while True:
        update_data()
        time.sleep(15)


@app.route('/')
def index():
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route('/api/status')
def status():
    return jsonify({
        'status': 'online',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'meeting': current_meeting.get('meeting_name') if current_meeting else None,
        'session': current_session.get('session_name') if current_session else None,
        'session_live': (current_session is not None and
                         datetime.fromisoformat(current_session['date_start']) <=
                         datetime.now(timezone.utc) <=
                         datetime.fromisoformat(current_session['date_end']))
                        if current_session else False,
    })


@socketio.on('connect')
def handle_connect():
    logger.info('Client connected')
    emit('connected', {'message': 'F1 Command Center online'})
    if last_payload:
        emit('data_update', last_payload)


@socketio.on('disconnect')
def handle_disconnect():
    logger.info('Client disconnected')


if __name__ == '__main__':
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
