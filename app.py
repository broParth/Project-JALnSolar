import hashlib
import hmac
import json
import math
import os
import re
import secrets
import smtplib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from email.message import EmailMessage
from functools import wraps

import requests
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, abort
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.environ.get("JAL_DB_PATH") or os.path.join(BASE_DIR, "project_jal.sqlite3")
SECRET_KEY_FILE = os.path.join(BASE_DIR, ".jal_secret_key")

# Number of tanks per site and the capacity of each, in litres (5 x 600,000 L = 600 m³ each,
# 3,000,000 L total storage). A prototype assumption, not a validated structural design.
TANK_COUNT = 5
TANK_CAPACITY_L = 600000
OLD_TANK_CAPACITY_L = 15000   # previous default; such tanks are upgraded once on startup
# Starting water level of tanks 1-5 for a new account's first site, in litres. Extra sites start empty.
TANK_START_LEVELS_L = (1200, 800, 0, 0, 0)

# Energy defaults for a new site.
DEFAULT_BATTERY_KWH = 10.0       # battery that stores spare solar energy
DEFAULT_PUMP_KWH_PER_KL = 0.15   # energy to pump and filter 1,000 L of water to the users
# "Days of water left" uses average demand over at most this many recent days.
DEMAND_WINDOW_DAYS = 14

def load_secret_key():
    """The key that signs login cookies. Taken from JAL_SECRET_KEY, otherwise generated once at
    random and kept in .jal_secret_key beside app.py (git-ignored). Never use a fixed default:
    anyone who knows the key can forge a login."""
    key = os.environ.get("JAL_SECRET_KEY", "").strip()
    if key:
        return key
    try:
        with open(SECRET_KEY_FILE, encoding="utf-8") as f:
            key = f.read().strip()
    except FileNotFoundError:
        key = ""
    if len(key) < 32:
        key = secrets.token_hex(32)
        with open(SECRET_KEY_FILE, "w", encoding="utf-8") as f:
            f.write(key)
    return key


app = Flask(__name__)
app.secret_key = load_secret_key()
app.config.update(
    JSON_SORT_KEYS=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Set JAL_HTTPS=1 when the app is served over https so the cookie is never sent over plain http.
    SESSION_COOKIE_SECURE=os.environ.get("JAL_HTTPS") == "1",
    MAX_CONTENT_LENGTH=1024 * 1024,  # 1 MB per request is plenty for this app
)
# Interactive debugger only when explicitly asked for (it can run code from the browser).
DEBUG = os.environ.get("JAL_DEBUG") == "1"
# Address used in password reset links. Fixed, so a forged Host header can't redirect the link.
BASE_URL = os.environ.get("JAL_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

# Failed sign-in limits: after this many failures within the window, further attempts are refused
# until the window has passed.
LOGIN_MAX_FAILS_PER_ACCOUNT = 5
LOGIN_MAX_FAILS_PER_IP = 20
LOGIN_WINDOW_MINUTES = 15
RESET_REQUESTS_PER_IP = 5

# Sensible limits for typed-in values (anything outside is rejected).
LIMITS = {
    "rainfall_mm": (0, 1000),             # heaviest recorded single-day rain is about 1,800 mm; 1,000 mm per entry is plenty
    "duration_hours": (0, 72),
    "duration_minutes": (0, 72 * 60),
    "solar_use_kwh": (0, 100_000),
    "canopy_area_m2": (1, 1_000_000),
    "collection_efficiency": (0, 100),
    "used_l": (0, 100_000_000),
    "solar_kwh": (0, 100_000),
    "tank_capacity_l": (1, 10_000_000),
    "tank_level_l": (0, 10_000_000),
    "battery_capacity_kwh": (0, 100_000),
    "pump_kwh_per_kl": (0, 50),
    "weather_rain_mm": (0, 20_000),
    "weather_litres": (0, 1e12),
}
EARLIEST_DATE = date(2000, 1, 1)
LATEST_DAYS_AHEAD = 366
DUPLICATE_SECONDS = 10  # an identical entry within this many seconds is treated as a double-submit

# Open-Meteo is free for non-commercial use and does not require an API key.
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
PINCODE_URL = "https://api.postalpincode.in/pincode/{}"          # third-party PIN lookup (not an official India Post API)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"       # OpenStreetMap geocoder: max 1 request/second
NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
# Identifies the app to the free services (Nominatim requires this). No personal details.
HTTP_HEADERS = {"User-Agent": "ProjectJAL/1.0 (student water-management prototype)"}
ARCHIVE_LAG_DAYS = 6       # the archive usually trails today by about 5 days
FORECAST_MAX_PAST_DAYS = 92
FORECAST_MAX_DAYS = 16

# Password reset links expire after this many minutes and work only once.
RESET_TOKEN_MINUTES = 30
# Optional email delivery for reset links. Without JAL_SMTP_HOST the link is printed in the
# terminal running app.py instead (fine for a local demo).
SMTP_HOST = os.environ.get("JAL_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("JAL_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("JAL_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("JAL_SMTP_PASSWORD", "")
MAIL_FROM = os.environ.get("JAL_MAIL_FROM", SMTP_USER)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
TANKS_SQL = """
CREATE TABLE IF NOT EXISTS tanks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    site_id INTEGER NOT NULL,
    tank_number INTEGER NOT NULL,
    capacity_l REAL NOT NULL DEFAULT 600000,
    level_l REAL NOT NULL DEFAULT 0,
    start_level_l REAL NOT NULL DEFAULT 0,
    UNIQUE(site_id, tank_number),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(site_id) REFERENCES sites(id) ON DELETE CASCADE
)"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL,
    identifier TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    village TEXT DEFAULT '',
    latitude REAL,
    longitude REAL,
    canopy_area_m2 REAL NOT NULL DEFAULT 100,
    collection_efficiency REAL NOT NULL DEFAULT 80,
    battery_capacity_kwh REAL NOT NULL DEFAULT 10,
    battery_start_kwh REAL NOT NULL DEFAULT 0,
    battery_level_kwh REAL NOT NULL DEFAULT 0,
    pump_kwh_per_kl REAL NOT NULL DEFAULT 0.15,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
""" + TANKS_SQL + """;
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    event_date TEXT NOT NULL,
    event_time TEXT NOT NULL,
    village TEXT,
    rainfall_mm REAL DEFAULT 0,
    duration_hours REAL DEFAULT 0,
    collected_l REAL DEFAULT 0,
    used_l REAL DEFAULT 0,
    solar_kwh REAL DEFAULT 0,
    village_energy_kwh REAL DEFAULT 0,
    system_energy_kwh REAL DEFAULT 0,
    details TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,          -- 'login' or 'reset'
    identifier TEXT,
    ip TEXT,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_at ON login_attempts(at);
CREATE TABLE IF NOT EXISTS password_resets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash TEXT UNIQUE NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""

# Columns added to events after the first version; added automatically to older databases.
EVENT_EXTRA_COLUMNS = {
    "site_id": "INTEGER",
    "demand_l": "REAL",               # water asked for (usage); used_l is what the tanks could supply
    "unmet_l": "REAL DEFAULT 0",      # demand the tanks could not supply
    "overflow_l": "REAL DEFAULT 0",   # rain that did not fit in the tanks
    "pump_kwh": "REAL DEFAULT 0",     # energy to pump the water that was used
    "battery_in_kwh": "REAL DEFAULT 0",
    "battery_out_kwh": "REAL DEFAULT 0",
    "grid_kwh": "REAL DEFAULT 0",     # pump energy the battery could not cover
    "payload": "TEXT",                # JSON, e.g. target levels for a tank adjustment
    "energy_kwh": "REAL",             # energy asked for by a solar-use entry
    "debris_kg": "REAL",              # ESTIMATED debris caught by the catcher during a rain entry
    "debris_percent": "REAL",         # ESTIMATED share of incoming debris the catcher stopped
}
EVENT_TYPES = ("rainfall", "solar", "solar_use", "usage", "weather_import", "tank_adjust", "debris_clean")

# Debris catcher model (illustrative assumptions, not sensor readings): about 0.015 g of leaves/dust
# per mm of rain per m² of canopy; heavier rain carries more, so the catcher stops 55% plus 0.35% per mm
# (up to 90%). A cleaning reminder shows once the estimated load since the last cleaning reaches 5 kg.
DEBRIS_KG_PER_MM_M2 = 0.000015
DEBRIS_ALERT_KG = 5.0


def debris_estimate(rainfall_mm, area_m2):
    return rainfall_mm * area_m2 * DEBRIS_KG_PER_MM_M2, min(90.0, 55.0 + rainfall_mm * 0.35)
SOLAR_USERS = ("Sensors", "System", "Sensors + system")

TANK_CHANGE_RE = re.compile(r"Tank (\d+) ([+-])(\d+(?:\.\d+)?) L")


@contextmanager
def db():
    """Open the database for one unit of work: commits on success, rolls back on error,
    and always closes the connection (open connections keep the file locked on Windows)."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def init_db():
    with db() as conn:
        conn.executescript(SCHEMA)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
        for col, decl in EVENT_EXTRA_COLUMNS.items():
            if col not in cols:
                conn.execute(f"ALTER TABLE events ADD COLUMN {col} {decl}")
        conn.execute("UPDATE events SET demand_l=used_l WHERE event_type='usage' AND demand_l IS NULL")
        # Older rain entries get a debris estimate from their site's canopy area.
        conn.execute(f"""UPDATE events SET
                debris_kg = rainfall_mm * COALESCE((SELECT canopy_area_m2 FROM sites WHERE sites.id = events.site_id), 100) * {DEBRIS_KG_PER_MM_M2},
                debris_percent = MIN(90.0, 55.0 + rainfall_mm * 0.35)
            WHERE event_type='rainfall' AND debris_kg IS NULL AND site_id IS NOT NULL""")

        migrated = "site_id" not in {r[1] for r in conn.execute("PRAGMA table_info(tanks)")}
        if migrated:
            migrate_to_sites(conn)
        for (uid,) in conn.execute("SELECT id FROM users WHERE id NOT IN (SELECT user_id FROM sites)").fetchall():
            create_site(conn, uid, "Main site", first=True)
        for (sid,) in conn.execute("SELECT id FROM sites").fetchall():
            ensure_tanks(conn, sid)
        if migrated:
            for (sid,) in conn.execute("SELECT id FROM sites").fetchall():
                carry_over_levels(conn, sid)

        # One-time upgrade (database version 1): tanks still at the old 15,000 L default become
        # 600,000 L. Sites are replayed so overflow figures match the bigger tanks; the carried-over
        # tank adjustments keep today's levels where they were.
        if conn.execute("PRAGMA user_version").fetchone()[0] < 1:
            sites = [r[0] for r in conn.execute("SELECT DISTINCT site_id FROM tanks WHERE capacity_l=?", (OLD_TANK_CAPACITY_L,))]
            conn.execute("UPDATE tanks SET capacity_l=? WHERE capacity_l=?", (TANK_CAPACITY_L, OLD_TANK_CAPACITY_L))
            for sid in sites:
                recalculate_site(conn, sid)
            conn.execute("PRAGMA user_version = 1")


def migrate_to_sites(conn):
    """One-time upgrade from 'one set of tanks per account' to sites.
    Each account gets one site built from its old settings, holding all its tanks and events.
    Each tank's starting level is worked out so that replaying the history in date order
    reproduces today's levels as closely as possible."""
    has_settings = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone()
    for (uid,) in conn.execute("SELECT id FROM users").fetchall():
        s = conn.execute("SELECT * FROM settings WHERE user_id=?", (uid,)).fetchone() if has_settings else None
        village = (s["village"] if s else "") or ""
        sid = conn.execute(
            """INSERT INTO sites(user_id,name,village,latitude,longitude,canopy_area_m2,collection_efficiency,
               battery_capacity_kwh,pump_kwh_per_kl,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (uid, village or "Main site", village, s["latitude"] if s else None, s["longitude"] if s else None,
             (s["canopy_area_m2"] if s else None) or 100, (s["collection_efficiency"] if s else None) or 80,
             DEFAULT_BATTERY_KWH, DEFAULT_PUMP_KWH_PER_KL, now_iso())).lastrowid
        conn.execute("UPDATE events SET site_id=? WHERE user_id=?", (sid, uid))

    conn.execute("ALTER TABLE tanks RENAME TO tanks_old")
    conn.execute(TANKS_SQL)
    conn.execute("""INSERT INTO tanks(user_id,site_id,tank_number,capacity_l,level_l,start_level_l)
                    SELECT t.user_id, s.id, t.tank_number, t.capacity_l, t.level_l, t.level_l
                    FROM tanks_old t JOIN sites s ON s.user_id=t.user_id""")
    conn.execute("DROP TABLE tanks_old")

    for (sid,) in conn.execute("SELECT id FROM sites").fetchall():
        delta = {}
        for (details,) in conn.execute(
                "SELECT details FROM events WHERE site_id=? AND event_type IN ('rainfall','usage')", (sid,)):
            for num, sign, amount in TANK_CHANGE_RE.findall(details or ""):
                delta[int(num)] = delta.get(int(num), 0) + (float(amount) if sign == "+" else -float(amount))
        for num, change in delta.items():
            conn.execute("""UPDATE tanks SET start_level_l = MAX(0, MIN(capacity_l, level_l - ?))
                            WHERE site_id=? AND tank_number=?""", (change, sid, num))


def carry_over_levels(conn, site_id):
    """After the upgrade, replaying old history in date order can give different levels than the
    account showed before (e.g. history recorded with smaller tanks, or entries added out of order).
    To keep what users already see, add a visible 'tank adjustment' at the end of the history that
    sets the pre-upgrade levels. Deleting that entry shows the fully recalculated levels instead."""
    before = [float(r[0]) for r in conn.execute("SELECT level_l FROM tanks WHERE site_id=? ORDER BY tank_number", (site_id,))]
    recalculate_site(conn, site_id)
    after = [float(r[0]) for r in conn.execute("SELECT level_l FROM tanks WHERE site_id=? ORDER BY tank_number", (site_id,))]
    if all(abs(a - b) <= 1 for a, b in zip(before, after)):
        return
    last = conn.execute("SELECT event_date, event_time FROM events WHERE site_id=? ORDER BY event_date DESC, event_time DESC, id DESC LIMIT 1",
                        (site_id,)).fetchone()
    event_date, event_time = (last[0], last[1]) if last else (date.today().isoformat(), datetime.now().strftime("%H:%M"))
    site = dict(conn.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone())
    insert_event(conn, site, "tank_adjust", event_date, event_time,
                 payload=json.dumps({"levels": before, "note": "levels carried over from before the date-order upgrade"}))
    recalculate_site(conn, site_id)


def create_site(conn, user_id, name, first=False, **fields):
    """Create a site with its tanks. An account's first site gets the demo starting levels."""
    sid = conn.execute(
        """INSERT INTO sites(user_id,name,village,latitude,longitude,canopy_area_m2,collection_efficiency,
           battery_capacity_kwh,pump_kwh_per_kl,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (user_id, name, fields.get("village", ""), fields.get("latitude"), fields.get("longitude"),
         fields.get("canopy_area_m2", 100), fields.get("collection_efficiency", 80),
         fields.get("battery_capacity_kwh", DEFAULT_BATTERY_KWH),
         fields.get("pump_kwh_per_kl", DEFAULT_PUMP_KWH_PER_KL), now_iso())).lastrowid
    levels = TANK_START_LEVELS_L if first else (0,) * TANK_COUNT
    conn.executemany(
        "INSERT INTO tanks(user_id,site_id,tank_number,capacity_l,level_l,start_level_l) VALUES(?,?,?,?,?,?)",
        [(user_id, sid, n, TANK_CAPACITY_L, levels[n - 1], levels[n - 1]) for n in range(1, TANK_COUNT + 1)])
    return sid


def ensure_tanks(conn, site_id):
    """Give the site all TANK_COUNT tanks; any missing tank is added empty."""
    count = conn.execute("SELECT COUNT(*) FROM tanks WHERE site_id=?", (site_id,)).fetchone()[0]
    if count >= TANK_COUNT:
        return
    uid = conn.execute("SELECT user_id FROM sites WHERE id=?", (site_id,)).fetchone()[0]
    conn.executemany(
        "INSERT OR IGNORE INTO tanks(user_id,site_id,tank_number,capacity_l,level_l,start_level_l) VALUES(?,?,?,?,0,0)",
        [(uid, site_id, n, TANK_CAPACITY_L) for n in range(1, TANK_COUNT + 1)])


# ---------------------------------------------------------------------------
# Tank and energy engine: tank levels are always the result of replaying a site's
# events in date/time order, starting from each tank's starting level.
# ---------------------------------------------------------------------------
def recalculate_site(conn, site_id):
    site = conn.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
    tanks = conn.execute("SELECT id, tank_number, capacity_l, start_level_l FROM tanks WHERE site_id=? ORDER BY tank_number",
                         (site_id,)).fetchall()
    caps = [float(t["capacity_l"]) for t in tanks]
    levels = [min(caps[i], max(0.0, float(t["start_level_l"]))) for i, t in enumerate(tanks)]
    bat_cap = max(0.0, float(site["battery_capacity_kwh"] or 0))
    battery = min(bat_cap, max(0.0, float(site["battery_start_kwh"] or 0)))
    pump_rate = max(0.0, float(site["pump_kwh_per_kl"] or 0))

    events = conn.execute("SELECT * FROM events WHERE site_id=? ORDER BY event_date, event_time, id", (site_id,)).fetchall()
    for e in events:
        etype = e["event_type"]
        if etype == "rainfall":
            # Five collectors, one above each tank, share the canopy equally, so every tank receives
            # the same share at once. The tanks are linked in a cascade: whatever a full tank can't
            # hold passes on to the next tank, and anything left after Tank 5 is overflow.
            per_tank = float(e["collected_l"] or 0) / max(1, len(tanks))
            carry, routing = 0.0, []
            for i, t in enumerate(tanks):
                incoming = per_tank + carry
                added = min(max(0.0, caps[i] - levels[i]), incoming)
                levels[i] += added
                carry = max(0.0, incoming - added)
                part = f"Tank {t['tank_number']} +{added:.0f} L"
                if carry > 0.5 and i < len(tanks) - 1:
                    part += f" ({carry:.0f} L passed on)"
                routing.append(part)
            details = f"Each collector {per_tank:.0f} L. " + ", ".join(routing)
            if carry > 0.5:
                details += f"; {carry:.0f} L overflow from Tank 5 flagged for recharge/overflow handling"
            conn.execute("UPDATE events SET overflow_l=?, details=? WHERE id=?", (round(carry, 2), details, e["id"]))

        elif etype == "usage":
            demand = float(e["demand_l"] if e["demand_l"] is not None else (e["used_l"] or 0))
            take_total = min(demand, sum(levels))
            remaining, parts = take_total, []
            for i in reversed(range(len(tanks))):
                take = min(levels[i], remaining)
                if take > 0:
                    levels[i] -= take
                    remaining -= take
                    parts.append(f"Tank {tanks[i]['tank_number']} -{take:.0f} L")
                if remaining <= 1e-9:
                    break
            unmet = max(0.0, demand - take_total)
            pump = take_total / 1000 * pump_rate
            from_battery = min(battery, pump)
            battery -= from_battery
            grid = pump - from_battery
            details = "Water withdrawn: " + (", ".join(parts) or "none, tanks empty")
            if unmet > 0.5:
                details += f"; {unmet:.0f} L demand unmet"
            if pump > 0:
                details += f"; pump {pump:.2f} kWh ({from_battery:.2f} from battery, {grid:.2f} from grid)"
            conn.execute("""UPDATE events SET used_l=?, unmet_l=?, pump_kwh=?, battery_out_kwh=?, grid_kwh=?, details=?
                            WHERE id=?""", (round(take_total, 2), round(unmet, 2), round(pump, 4),
                                            round(from_battery, 4), round(grid, 4), details, e["id"]))

        elif etype == "solar":
            solar = float(e["solar_kwh"] or 0)
            homes, sensors = float(e["village_energy_kwh"] or 0), float(e["system_energy_kwh"] or 0)
            spare = max(0.0, solar - homes - sensors)
            charge = min(spare, bat_cap - battery)
            battery += charge
            details = f"Solar: {homes:.2f} kWh to village homes, {sensors:.2f} kWh to sensors/IoT, {charge:.2f} kWh to battery"
            if spare - charge > 0.005:
                details += f", {spare - charge:.2f} kWh spare (battery full)"
            conn.execute("UPDATE events SET battery_in_kwh=?, details=? WHERE id=?", (round(charge, 4), details, e["id"]))

        elif etype == "solar_use":
            # Energy used by sensors and/or the system: taken from the battery first, then the grid.
            payload = json.loads(e["payload"] or "{}")
            need = max(0.0, float(e["energy_kwh"] or 0))
            from_battery = min(battery, need)
            battery -= from_battery
            grid = need - from_battery
            details = f"{need:.2f} kWh for {payload.get('purpose') or 'unspecified use'} (used by {payload.get('used_by') or 'system'}): "
            details += f"{from_battery:.2f} kWh from battery"
            if grid > 0.005:
                details += f", {grid:.2f} kWh from grid (battery ran short)"
            conn.execute("UPDATE events SET battery_out_kwh=?, grid_kwh=?, details=? WHERE id=?",
                         (round(from_battery, 4), round(grid, 4), details, e["id"]))

        elif etype == "tank_adjust":
            payload = json.loads(e["payload"] or "{}")
            for i, value in enumerate(payload.get("levels", [])[:len(levels)]):
                levels[i] = min(caps[i], max(0.0, float(value)))
            details = "Tank levels set to: " + ", ".join(f"Tank {t['tank_number']} {levels[i]:.0f} L" for i, t in enumerate(tanks))
            if payload.get("note"):
                details += f" ({payload['note']})"
            conn.execute("UPDATE events SET details=? WHERE id=?", (details, e["id"]))

    for i, t in enumerate(tanks):
        conn.execute("UPDATE tanks SET level_l=? WHERE id=?", (round(levels[i], 2), t["id"]))
    conn.execute("UPDATE sites SET battery_level_kwh=? WHERE id=?", (round(battery, 4), site_id))


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------
def wants_json():
    return request.path.startswith("/api/")


def login_required(view):
    """Signed-in users only. Also checks the account still exists: a browser can keep an old login
    cookie after the database is reset, and that must not crash the app."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        uid = session.get("user_id")
        user = None
        if uid is not None:
            with db() as conn:
                user = conn.execute("SELECT id, display_name FROM users WHERE id=?", (uid,)).fetchone()
        if user is None:
            had_session = uid is not None
            session.clear()
            if wants_json():
                return jsonify(ok=False, error="Please sign in again."), 401
            if had_session:
                flash("Your account could not be found. Please sign in again.", "error")
            return redirect(url_for("login"))
        session["display_name"] = user["display_name"]
        return view(*args, **kwargs)
    return wrapped


# --- Cross-site request protection (CSRF) -------------------------------------------------
# Every state-changing request must carry the session's secret token: HTML forms send it as a
# hidden field, the page's JavaScript sends it in the X-CSRF-Token header. Another website can't
# read the token, so it can't make a signed-in user's browser change or delete their data.
def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def check_csrf():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    if not expected or not sent or not hmac.compare_digest(str(sent), str(expected)):
        if wants_json():
            return jsonify(ok=False, error="Your session has expired. Reload the page and try again."), 403
        flash("Your session expired. Please try again.", "error")
        return redirect(request.path if request.path != url_for("logout") else url_for("login"))
    return None


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    return resp


def json_body():
    """API requests are JSON only (plain form posts are not accepted)."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def num(data, key, default=None, limits=None):
    """Read a number from the request. Rejects text, NaN and infinity, and values outside the limits."""
    raw = data.get(key, default)
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        raw = default
    if raw is None or isinstance(raw, bool):
        raise ValueError(key)
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(key)
    lo, hi = LIMITS[limits or key]
    if not lo <= value <= hi:
        raise ValueError(key)
    return value


# --- Failed sign-in limits -------------------------------------------------------------------
def client_ip():
    return request.remote_addr or "unknown"


def too_many_attempts(conn, kind, identifier=None, per_identifier=None, per_ip=None):
    since = (datetime.now() - timedelta(minutes=LOGIN_WINDOW_MINUTES)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM login_attempts WHERE at < ?", (since,))
    if per_identifier and identifier:
        n = conn.execute("SELECT COUNT(*) FROM login_attempts WHERE kind=? AND identifier=? AND at>=?",
                         (kind, identifier, since)).fetchone()[0]
        if n >= per_identifier:
            return True
    if per_ip:
        n = conn.execute("SELECT COUNT(*) FROM login_attempts WHERE kind=? AND ip=? AND at>=?",
                         (kind, client_ip(), since)).fetchone()[0]
        if n >= per_ip:
            return True
    return False


def note_attempt(conn, kind, identifier=None):
    conn.execute("INSERT INTO login_attempts(kind, identifier, ip, at) VALUES(?,?,?,?)",
                 (kind, identifier, client_ip(), now_iso()))


def current_user_id():
    return int(session["user_id"])


def current_site():
    """The site the user is working on (remembered in the session); falls back to their first site."""
    uid = current_user_id()
    with db() as conn:
        row = None
        if session.get("site_id"):
            row = conn.execute("SELECT * FROM sites WHERE id=? AND user_id=?", (session["site_id"], uid)).fetchone()
        if not row:
            row = conn.execute("SELECT * FROM sites WHERE user_id=? ORDER BY id LIMIT 1", (uid,)).fetchone()
        if not row:
            if not conn.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
                abort(401)
            sid = create_site(conn, uid, "Main site", first=True)
            row = conn.execute("SELECT * FROM sites WHERE id=?", (sid,)).fetchone()
    session["site_id"] = row["id"]
    return dict(row)


def list_sites(user_id):
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT id, name, village FROM sites WHERE user_id=? ORDER BY id", (user_id,))]


def get_tanks(conn, site_id):
    return [dict(r) for r in conn.execute(
        "SELECT tank_number, capacity_l, level_l FROM tanks WHERE site_id=? ORDER BY tank_number", (site_id,))]


def water_balance(site_id):
    with db() as conn:
        tanks = get_tanks(conn, site_id)
    total_capacity = sum(float(t["capacity_l"]) for t in tanks)
    total_level = sum(float(t["level_l"]) for t in tanks)
    return {"tanks": tanks, "total_capacity_l": total_capacity, "total_level_l": total_level,
            "total_remaining_space_l": max(0, total_capacity - total_level)}


def parse_when(data):
    """Validated event date (YYYY-MM-DD) and time (HH:MM); defaults to now."""
    event_date = str(data.get("event_date") or date.today().isoformat())
    event_time = str(data.get("event_time") or datetime.now().strftime("%H:%M"))
    d = date.fromisoformat(event_date)
    if not EARLIEST_DATE <= d <= date.today() + timedelta(days=LATEST_DAYS_AHEAD):
        raise ValueError("event_date")
    t = datetime.strptime(event_time, "%H:%M")
    return d.isoformat(), t.strftime("%H:%M")


def is_duplicate(conn, site_id, event_type, event_date, event_time, **fields):
    """True if the same entry was saved for this site moments ago (a double-clicked submit button)."""
    since = (datetime.now() - timedelta(seconds=DUPLICATE_SECONDS)).isoformat(timespec="seconds")
    sql = "SELECT 1 FROM events WHERE site_id=? AND event_type=? AND event_date=? AND event_time=? AND created_at>=?"
    args = [site_id, event_type, event_date, event_time, since]
    for col, value in fields.items():
        sql += f" AND ABS(COALESCE({col},0) - ?) < 1e-9"
        args.append(value)
    return conn.execute(sql, args).fetchone() is not None


DUPLICATE_ERROR = "This entry was just saved a moment ago, so it was not saved twice."


def insert_event(conn, site, event_type, event_date, event_time, **fields):
    cols = ["village", "rainfall_mm", "duration_hours", "collected_l", "used_l", "demand_l", "solar_kwh",
            "village_energy_kwh", "system_energy_kwh", "energy_kwh", "debris_kg", "debris_percent", "details", "payload"]
    values = [fields.get(c) for c in cols]
    if values[0] is None:
        values[0] = site.get("village") or ""
    return conn.execute(
        f"""INSERT INTO events(user_id,site_id,event_type,event_date,event_time,{",".join(cols)},created_at)
            VALUES(?,?,?,?,?,{",".join("?" * len(cols))},?)""",
        [site["user_id"], site["id"], event_type, event_date, event_time] + values + [now_iso()]).lastrowid


def site_insights(conn, site, total_level):
    """Days of water left at recent demand, unmet demand, and battery/pump energy totals."""
    sid = site["id"]
    last = conn.execute("SELECT MAX(event_date) FROM events WHERE site_id=? AND event_type='usage'", (sid,)).fetchone()[0]
    days_left = avg_demand = window_days = None
    window_unmet = 0.0
    if last:
        end = min(date.fromisoformat(last), date.today())
        first = conn.execute("SELECT MIN(event_date) FROM events WHERE site_id=? AND event_type='usage' AND event_date>=?",
                             (sid, (end - timedelta(days=DEMAND_WINDOW_DAYS - 1)).isoformat())).fetchone()[0]
        if first:
            start = date.fromisoformat(first)
            window_days = (end - start).days + 1
            row = conn.execute("""SELECT COALESCE(SUM(COALESCE(demand_l, used_l)),0), COALESCE(SUM(unmet_l),0)
                                  FROM events WHERE site_id=? AND event_type='usage' AND event_date BETWEEN ? AND ?""",
                               (sid, start.isoformat(), end.isoformat())).fetchone()
            avg_demand = row[0] / window_days
            window_unmet = row[1]
            if avg_demand > 0:
                days_left = total_level / avg_demand
    # Pump figures come from water-use entries only; sensor/system use is reported separately.
    totals = conn.execute("""SELECT COALESCE(SUM(unmet_l),0), COALESCE(SUM(pump_kwh),0),
                             COALESCE(SUM(CASE WHEN event_type='usage' THEN grid_kwh END),0),
                             COALESCE(SUM(CASE WHEN event_type='usage' THEN battery_out_kwh END),0),
                             COALESCE(SUM(battery_in_kwh),0), COALESCE(SUM(overflow_l),0),
                             COALESCE(SUM(CASE WHEN event_type='solar_use' THEN energy_kwh END),0),
                             COALESCE(SUM(CASE WHEN event_type='solar_use' THEN battery_out_kwh END),0),
                             COALESCE(SUM(CASE WHEN event_type='solar_use' THEN grid_kwh END),0)
                             FROM events WHERE site_id=?""", (sid,)).fetchone()
    # Debris catcher: estimated load since the most recent "catcher cleaned" entry (by date order).
    cleaned = conn.execute("""SELECT event_date, event_time FROM events WHERE site_id=? AND event_type='debris_clean'
                              ORDER BY event_date DESC, event_time DESC, id DESC LIMIT 1""", (sid,)).fetchone()
    since = (cleaned[0] + " " + cleaned[1]) if cleaned else ""
    deb = conn.execute("""SELECT COUNT(*), COALESCE(SUM(debris_kg),0), AVG(debris_percent) FROM events
                          WHERE site_id=? AND event_type='rainfall' AND (event_date || ' ' || event_time) > ?""",
                       (sid, since)).fetchone()
    debris = {
        "since": since or None, "rain_entries": deb[0], "kg": round(deb[1], 2),
        "avg_capture_percent": round(deb[2], 1) if deb[2] is not None else None,
        "alert_kg": DEBRIS_ALERT_KG, "clean_due": deb[1] >= DEBRIS_ALERT_KG,
    }
    return {
        "debris": debris,
        "days_left": round(days_left, 1) if days_left is not None else None,
        "avg_daily_demand_l": round(avg_demand, 1) if avg_demand is not None else None,
        "demand_window_days": window_days,
        "window_unmet_l": round(window_unmet, 1),
        "unmet_total_l": round(totals[0], 1),
        "pump_kwh_total": round(totals[1], 2),
        "grid_kwh_total": round(totals[2], 2),
        "battery_used_kwh_total": round(totals[3], 2),
        "battery_charged_kwh_total": round(totals[4], 2),
        "overflow_total_l": round(totals[5], 1),
        "solar_use_kwh_total": round(totals[6], 2),
        "solar_use_battery_kwh_total": round(totals[7], 2),
        "solar_use_grid_kwh_total": round(totals[8], 2),
        "battery_level_kwh": round(float(site["battery_level_kwh"] or 0), 2),
        "battery_capacity_kwh": round(float(site["battery_capacity_kwh"] or 0), 2),
        "pump_kwh_per_kl": float(site["pump_kwh_per_kl"] or 0),
    }


def page_context(active):
    site = current_site()
    return dict(name=session.get("display_name", "JAL User"), settings=site, site=site,
                sites=list_sites(current_user_id()), active=active, solar_users=SOLAR_USERS)


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------
@app.route("/")
def home():
    # Signed-in users go straight to their dashboard; everyone else sees the opening intro,
    # which plays once per browser session and then opens the sign-in page.
    uid = session.get("user_id")
    if uid is not None:
        with db() as conn:
            if conn.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
                return redirect(url_for("dashboard"))
        session.clear()  # old cookie for an account that no longer exists
    return render_template("intro.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("display_name", "").strip()
        identifier = request.form.get("identifier", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not identifier or len(password) < 8:
            flash("Enter your name, email/username, and a password with at least 8 characters.", "error")
            return render_template("login.html", mode="register")
        if len(name) > 80 or len(identifier) > 120 or len(password) > 200:
            flash("Name, email/username or password is too long.", "error")
            return render_template("login.html", mode="register")
        try:
            with db() as conn:
                user_id = conn.execute(
                    "INSERT INTO users(display_name,identifier,password_hash,created_at) VALUES(?,?,?,?)",
                    (name, identifier, generate_password_hash(password), now_iso())).lastrowid
                site_id = create_site(conn, user_id, "Main site", first=True)
            session.clear()
            session["user_id"] = user_id
            session["display_name"] = name
            session["site_id"] = site_id
            return redirect(url_for("dashboard"))
        except sqlite3.IntegrityError:
            flash("That email/username is already registered. Please sign in.", "error")
    return render_template("login.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip().lower()[:120]
        password = request.form.get("password", "")[:200]
        with db() as conn:
            if too_many_attempts(conn, "login", identifier, LOGIN_MAX_FAILS_PER_ACCOUNT, LOGIN_MAX_FAILS_PER_IP):
                flash(f"Too many failed sign-in attempts. Wait {LOGIN_WINDOW_MINUTES} minutes and try again, "
                      "or reset your password.", "error")
                return render_template("login.html", mode="login"), 429
            user = conn.execute("SELECT * FROM users WHERE identifier=?", (identifier,)).fetchone()
            ok = bool(user) and check_password_hash(user["password_hash"], password)
            if ok:
                conn.execute("DELETE FROM login_attempts WHERE kind='login' AND identifier=?", (identifier,))
            else:
                note_attempt(conn, "login", identifier)
        if ok:
            session.clear()
            session["user_id"] = user["id"]
            session["display_name"] = user["display_name"]
            return redirect(url_for("dashboard"))
        flash("Sign-in details were not recognised.", "error")
    return render_template("login.html", mode="login")


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def send_reset_link(identifier, display_name, link):
    """Email the reset link when SMTP is configured and the account uses an email address;
    otherwise print it in the server terminal. Returns 'email' or 'console'."""
    if SMTP_HOST and "@" in identifier:
        msg = EmailMessage()
        msg["Subject"] = "Reset your Project JAL password"
        msg["From"] = MAIL_FROM
        msg["To"] = identifier
        msg.set_content(
            f"Hi {display_name},\n\nUse this link to set a new Project JAL password. "
            f"It works once and expires in {RESET_TOKEN_MINUTES} minutes:\n\n{link}\n\n"
            "If you did not ask for this, you can ignore this email.\n")
        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
                smtp.starttls()
                if SMTP_USER:
                    smtp.login(SMTP_USER, SMTP_PASSWORD)
                smtp.send_message(msg)
            return "email"
        except (smtplib.SMTPException, OSError) as exc:
            print(f"[JAL] Could not email the reset link ({exc}); printing it instead.", flush=True)
    print("\n" + "=" * 70, flush=True)
    print(f"[JAL] Password reset link for '{identifier}' (valid {RESET_TOKEN_MINUTES} min, one use):", flush=True)
    print(link, flush=True)
    print("=" * 70 + "\n", flush=True)
    return "console"


def find_reset(token):
    """Return the reset row for a token if it exists, is unused and has not expired."""
    with db() as conn:
        row = conn.execute("SELECT * FROM password_resets WHERE token_hash=?", (_token_hash(token),)).fetchone()
    if not row or row["used_at"] or datetime.fromisoformat(row["expires_at"]) < datetime.now():
        return None
    return row


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip().lower()[:120]
        if not identifier:
            flash("Enter the email or username you signed up with.", "error")
            return render_template("login.html", mode="forgot")
        with db() as conn:
            if too_many_attempts(conn, "reset", per_ip=RESET_REQUESTS_PER_IP):
                flash(f"Too many reset requests. Wait {LOGIN_WINDOW_MINUTES} minutes and try again.", "error")
                return render_template("login.html", mode="forgot"), 429
            note_attempt(conn, "reset", identifier)
            user = conn.execute("SELECT * FROM users WHERE identifier=?", (identifier,)).fetchone()
        if user:
            token = secrets.token_urlsafe(32)
            now = datetime.now()
            with db() as conn:
                # Only the newest link works: retire any earlier unused ones.
                conn.execute("UPDATE password_resets SET used_at=? WHERE user_id=? AND used_at IS NULL",
                             (now.isoformat(timespec="seconds"), user["id"]))
                conn.execute("INSERT INTO password_resets(user_id,token_hash,expires_at,created_at) VALUES(?,?,?,?)",
                             (user["id"], _token_hash(token),
                              (now + timedelta(minutes=RESET_TOKEN_MINUTES)).isoformat(timespec="seconds"),
                              now.isoformat(timespec="seconds")))
            # Built from the fixed BASE_URL, never from the request's Host header (which a sender can fake).
            send_reset_link(identifier, user["display_name"], BASE_URL + url_for("reset_password", token=token))
        # Same reply whether or not the account exists, so the form can't be used to discover accounts.
        if SMTP_HOST:
            flash("If that account exists, a reset link has been sent to its email address.", "success")
        else:
            flash("If that account exists, a reset link has been created. Email isn't set up on this server, "
                  "so the link is shown in the terminal window running app.py.", "success")
        return redirect(url_for("forgot_password"))
    return render_template("login.html", mode="forgot")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    reset = find_reset(token)
    if not reset:
        flash("That reset link is invalid, already used or expired. Request a new one.", "error")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(password) < 8:
            flash("Use a password with at least 8 characters.", "error")
        elif password != confirm:
            flash("The two passwords don't match.", "error")
        else:
            now = now_iso()
            with db() as conn:
                conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                             (generate_password_hash(password), reset["user_id"]))
                conn.execute("UPDATE password_resets SET used_at=? WHERE user_id=? AND used_at IS NULL",
                             (now, reset["user_id"]))
            session.clear()
            flash("Your password has been updated. Sign in with your new password.", "success")
            return redirect(url_for("login"))
    return render_template("login.html", mode="reset")


@app.route("/logout", methods=["GET", "POST"])
def logout():
    # Signing out needs a POST with the CSRF token, so another page can't sign users out with a link.
    # A plain visit to /logout just goes back to the app.
    if request.method == "POST":
        session.clear()
        flash("You have been signed out.", "success")
        return redirect(url_for("login"))
    return redirect(url_for("dashboard") if "user_id" in session else url_for("login"))


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("index.html", **page_context("dashboard"))


@app.route("/simulation")
@login_required
def simulation():
    return render_template("simulation.html", **page_context("simulation"))


@app.route("/history")
@login_required
def history():
    return render_template("history.html", **page_context("history"))


@app.route("/settings")
@login_required
def settings_page():
    return render_template("settings.html", **page_context("settings"))


@app.route("/profile")
@login_required
def profile_page():
    uid = current_user_id()
    with db() as conn:
        user = conn.execute("SELECT id, display_name, identifier, created_at FROM users WHERE id=?", (uid,)).fetchone()
        stats = conn.execute("SELECT (SELECT COUNT(*) FROM sites WHERE user_id=?), (SELECT COUNT(*) FROM events WHERE user_id=?)",
                             (uid, uid)).fetchone()
    return render_template("profile.html", user=dict(user), site_count=stats[0], event_count=stats[1],
                           **page_context("profile"))


@app.route("/api/profile", methods=["POST"])
@login_required
def api_profile():
    """Change the display name and/or the email/username used to sign in.
    Changing the email/username needs the current password (it decides where reset links go)."""
    uid = current_user_id()
    data = json_body()
    name = str(data.get("display_name", "")).strip()
    identifier = str(data.get("identifier", "")).strip().lower()
    current = str(data.get("current_password", ""))
    if not name or not identifier:
        return jsonify(ok=False, error="Name and email/username can't be empty."), 400
    if len(name) > 80 or len(identifier) > 120:
        return jsonify(ok=False, error="Name or email/username is too long."), 400
    with db() as conn:
        user = conn.execute("SELECT identifier, password_hash FROM users WHERE id=?", (uid,)).fetchone()
        if identifier != user["identifier"]:
            err = check_current_password(conn, user, current)
            if err:
                return err
            taken = conn.execute("SELECT 1 FROM users WHERE identifier=? AND id<>?", (identifier, uid)).fetchone()
            if taken:
                return jsonify(ok=False, error="That email/username is already used by another account."), 400
        conn.execute("UPDATE users SET display_name=?, identifier=? WHERE id=?", (name, identifier, uid))
    session["display_name"] = name
    return jsonify(ok=True, display_name=name, identifier=identifier)


def check_current_password(conn, user, password):
    """Verify the signed-in user's current password; wrong guesses count towards the sign-in limit.
    Returns an error response, or None when the password is right."""
    if too_many_attempts(conn, "login", user["identifier"], LOGIN_MAX_FAILS_PER_ACCOUNT):
        return jsonify(ok=False, error=f"Too many wrong passwords. Wait {LOGIN_WINDOW_MINUTES} minutes and try again."), 429
    if not password:
        return jsonify(ok=False, error="Enter your current password to change your email/username."), 400
    if not check_password_hash(user["password_hash"], password):
        note_attempt(conn, "login", user["identifier"])
        return jsonify(ok=False, error="Your current password is not correct."), 400
    return None


@app.route("/api/profile/password", methods=["POST"])
@login_required
def api_profile_password():
    """Change password; requires the current password."""
    uid = current_user_id()
    data = json_body()
    current = str(data.get("current_password", ""))
    new = str(data.get("new_password", ""))
    confirm = str(data.get("confirm_password", ""))
    with db() as conn:
        user = conn.execute("SELECT identifier, password_hash FROM users WHERE id=?", (uid,)).fetchone()
        if not current:
            return jsonify(ok=False, error="Enter your current password."), 400
        err = check_current_password(conn, user, current)
        if err:
            return err
        if len(new) < 8:
            return jsonify(ok=False, error="Use a new password with at least 8 characters."), 400
        if len(new) > 200:
            return jsonify(ok=False, error="That password is too long (200 characters at most)."), 400
        if new != confirm:
            return jsonify(ok=False, error="The two new passwords don't match."), 400
        if new == current:
            return jsonify(ok=False, error="The new password must be different from the current one."), 400
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), uid))
        # Any outstanding reset links stop working once the password has been changed.
        conn.execute("UPDATE password_resets SET used_at=? WHERE user_id=? AND used_at IS NULL", (now_iso(), uid))
    return jsonify(ok=True, message="Password changed.")


# ---------------------------------------------------------------------------
# API: state and events
# ---------------------------------------------------------------------------
@app.route("/api/state")
@login_required
def api_state():
    site = current_site()
    balance = water_balance(site["id"])
    with db() as conn:
        events = conn.execute("SELECT * FROM events WHERE site_id=? ORDER BY event_date,event_time,id", (site["id"],)).fetchall()
        recent = conn.execute("SELECT * FROM events WHERE site_id=? ORDER BY event_date DESC,event_time DESC,id DESC LIMIT 12",
                              (site["id"],)).fetchall()
        insights = site_insights(conn, site, balance["total_level_l"])
    return jsonify({"ok": True, **balance, "settings": site, "site": site, "sites": list_sites(site["user_id"]),
                    "insights": insights, "events": [dict(e) for e in events], "recent": [dict(e) for e in recent]})


@app.route("/api/rainfall", methods=["POST"])
@login_required
def api_rainfall():
    site = current_site()
    data = json_body()
    try:
        rainfall = num(data, "rainfall_mm", 0)
        # Duration is recorded for the event only; rainfall depth is already the event total.
        if data.get("duration_minutes") not in (None, ""):
            duration = num(data, "duration_minutes") / 60
        else:
            duration = num(data, "duration_hours", 1)
        area = num(data, "canopy_area_m2", site["canopy_area_m2"] or 100)
        efficiency = num(data, "collection_efficiency", site["collection_efficiency"] or 80) / 100
        event_date, event_time = parse_when(data)
        village = str(data.get("village") or site.get("village") or "")[:120]
    except (TypeError, ValueError):
        return jsonify(ok=False, error=f"Please enter valid numbers (rainfall up to {LIMITS['rainfall_mm'][1]:,} mm, "
                                        f"canopy 1–{LIMITS['canopy_area_m2'][1]:,} m², efficiency 0–100%), "
                                        "a date from 2000 to next year (YYYY-MM-DD) and a time (HH:MM)."), 400
    if rainfall <= 0:
        return jsonify(ok=False, error="Rainfall must be greater than zero."), 400

    collected = round(rainfall * area * efficiency, 2)  # 1 mm rainfall over 1 m² = 1 litre
    with db() as conn:
        if is_duplicate(conn, site["id"], "rainfall", event_date, event_time, rainfall_mm=rainfall, collected_l=collected):
            return jsonify(ok=False, error=DUPLICATE_ERROR), 409
        debris_kg, debris_pct = debris_estimate(rainfall, area)
        eid = insert_event(conn, site, "rainfall", event_date, event_time, village=village, rainfall_mm=rainfall,
                           duration_hours=duration, collected_l=collected,
                           debris_kg=round(debris_kg, 4), debris_percent=round(debris_pct, 1))
        recalculate_site(conn, site["id"])
        e = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    overflow = float(e["overflow_l"] or 0)
    return jsonify(ok=True, rainfall_mm=rainfall, duration_hours=round(duration, 3),
                   per_collector_l=round(collected / TANK_COUNT, 2),
                   debris_kg=round(debris_kg, 3), debris_percent=round(debris_pct, 1),
                   collected_l=round(collected, 2), stored_l=round(collected - overflow, 2),
                   overflow_l=round(overflow, 2), message=e["details"], state=water_balance(site["id"]))


@app.route("/api/solar", methods=["POST"])
@login_required
def api_solar():
    """Record solar generation on its own. Energy not given to homes or sensors charges the battery."""
    site = current_site()
    data = json_body()
    try:
        solar = num(data, "solar_kwh", 0)
        village_energy = num(data, "village_energy_kwh", solar * 0.75, limits="solar_kwh")
        system_energy = num(data, "system_energy_kwh", solar * 0.10, limits="solar_kwh")
        event_date, event_time = parse_when(data)
        village = str(data.get("village") or site.get("village") or "")[:120]
    except (TypeError, ValueError):
        return jsonify(ok=False, error=f"Please enter valid numbers (0–{LIMITS['solar_kwh'][1]:,} kWh), "
                                        "a date from 2000 to next year (YYYY-MM-DD) and a time (HH:MM)."), 400
    if solar <= 0:
        return jsonify(ok=False, error="Solar generated must be greater than zero."), 400
    if village_energy + system_energy > solar + 1e-9:
        return jsonify(ok=False, error="Energy given to homes and sensors cannot exceed the energy generated."), 400

    with db() as conn:
        if is_duplicate(conn, site["id"], "solar", event_date, event_time, solar_kwh=solar,
                        village_energy_kwh=village_energy, system_energy_kwh=system_energy):
            return jsonify(ok=False, error=DUPLICATE_ERROR), 409
        eid = insert_event(conn, site, "solar", event_date, event_time, village=village, solar_kwh=solar,
                           village_energy_kwh=village_energy, system_energy_kwh=system_energy)
        recalculate_site(conn, site["id"])
        e = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        battery = conn.execute("SELECT battery_level_kwh, battery_capacity_kwh FROM sites WHERE id=?", (site["id"],)).fetchone()
    return jsonify(ok=True, solar_kwh=solar, village_energy_kwh=village_energy, system_energy_kwh=system_energy,
                   unallocated_kwh=round(solar - village_energy - system_energy, 4),
                   battery_in_kwh=e["battery_in_kwh"], battery_level_kwh=battery[0], battery_capacity_kwh=battery[1],
                   message=e["details"])


@app.route("/api/solar/use", methods=["POST"])
@login_required
def api_solar_use():
    """Record solar energy used by the sensors and/or the system. It is drawn from the battery;
    if the battery runs short at that point in time, the rest is counted as grid energy."""
    site = current_site()
    data = json_body()
    try:
        energy = num(data, "energy_kwh", 0, limits="solar_use_kwh")
        event_date, event_time = parse_when(data)
    except (TypeError, ValueError):
        return jsonify(ok=False, error=f"Please enter a valid amount (up to {LIMITS['solar_use_kwh'][1]:,} kWh), "
                                        "a date from 2000 to next year (YYYY-MM-DD) and a time (HH:MM)."), 400
    purpose = str(data.get("purpose") or "").strip()[:120]
    used_by = str(data.get("used_by") or "").strip()
    if energy <= 0:
        return jsonify(ok=False, error="Energy used must be greater than zero."), 400
    if not purpose:
        return jsonify(ok=False, error="Say what the energy was used for (e.g. lighting, water-level sensors)."), 400
    if used_by not in SOLAR_USERS:
        return jsonify(ok=False, error="Choose who used the energy: " + ", ".join(SOLAR_USERS) + "."), 400

    with db() as conn:
        if is_duplicate(conn, site["id"], "solar_use", event_date, event_time, energy_kwh=energy):
            return jsonify(ok=False, error=DUPLICATE_ERROR), 409
        eid = insert_event(conn, site, "solar_use", event_date, event_time, village=purpose, energy_kwh=energy,
                           payload=json.dumps({"purpose": purpose, "used_by": used_by}))
        recalculate_site(conn, site["id"])
        e = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        battery = conn.execute("SELECT battery_level_kwh, battery_capacity_kwh FROM sites WHERE id=?", (site["id"],)).fetchone()
    return jsonify(ok=True, energy_kwh=energy, battery_out_kwh=e["battery_out_kwh"], grid_kwh=e["grid_kwh"],
                   battery_level_kwh=battery[0], battery_capacity_kwh=battery[1], message=e["details"])


@app.route("/api/debris/clean", methods=["POST"])
@login_required
def api_debris_clean():
    """Record that the debris catcher was emptied; the estimated load starts again from zero."""
    site = current_site()
    data = json_body()
    try:
        event_date, event_time = parse_when(data)
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Enter a date (YYYY-MM-DD) and a time (HH:MM)."), 400
    note = str(data.get("note") or "").strip()[:200]
    with db() as conn:
        if is_duplicate(conn, site["id"], "debris_clean", event_date, event_time):
            return jsonify(ok=False, error=DUPLICATE_ERROR), 409
        insert_event(conn, site, "debris_clean", event_date, event_time,
                     details="Debris catcher cleaned" + (f": {note}" if note else ""))
        balance = water_balance(site["id"])
        insights = site_insights(conn, site, balance["total_level_l"])
    return jsonify(ok=True, message="Recorded: debris catcher cleaned.", debris=insights["debris"])


@app.route("/api/usage", methods=["POST"])
@login_required
def api_usage():
    """Record water demand. The tanks supply what they can; anything short is recorded as unmet demand."""
    site = current_site()
    data = json_body()
    try:
        demand = num(data, "used_l", 0)
        event_date, event_time = parse_when(data)
        village = str(data.get("village") or site.get("village") or "")[:120]
    except (TypeError, ValueError):
        return jsonify(ok=False, error=f"Please enter a valid amount (up to {LIMITS['used_l'][1]:,} L), "
                                        "a date from 2000 to next year (YYYY-MM-DD) and a time (HH:MM)."), 400
    if demand <= 0:
        return jsonify(ok=False, error="Water used must be greater than zero."), 400

    with db() as conn:
        if is_duplicate(conn, site["id"], "usage", event_date, event_time, demand_l=demand):
            return jsonify(ok=False, error=DUPLICATE_ERROR), 409
        eid = insert_event(conn, site, "usage", event_date, event_time, village=village, used_l=demand, demand_l=demand)
        recalculate_site(conn, site["id"])
        e = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    return jsonify(ok=True, demand_l=demand, used_l=e["used_l"], unmet_l=e["unmet_l"], pump_kwh=e["pump_kwh"],
                   battery_out_kwh=e["battery_out_kwh"], grid_kwh=e["grid_kwh"], message=e["details"],
                   state=water_balance(site["id"]))


@app.route("/api/reset", methods=["POST"])
@login_required
def api_reset():
    site = current_site()
    with db() as conn:
        conn.execute("DELETE FROM events WHERE site_id=?", (site["id"],))
        conn.execute("UPDATE tanks SET start_level_l=0, level_l=0 WHERE site_id=?", (site["id"],))
        conn.execute("UPDATE sites SET battery_start_kwh=0, battery_level_kwh=0 WHERE id=?", (site["id"],))
    return jsonify(ok=True, message=f"Tank levels, battery and history reset for {site['name']}.")


@app.route("/api/tanks", methods=["POST"])
@login_required
def api_tanks():
    """Change tank capacities, and/or set current levels. A level change is saved as a
    'tank adjustment' entry in history so the change is visible and can be undone."""
    site = current_site()
    data = json_body()
    tanks = data.get("tanks", [])
    if not isinstance(tanks, list) or len(tanks) != TANK_COUNT:
        return jsonify(ok=False, error=f"Please provide exactly {TANK_COUNT} tanks."), 400
    try:
        parsed = []
        for t in tanks:
            cap = num(t, "capacity_l", limits="tank_capacity_l")
            level = num(t, "level_l", limits="tank_level_l")
            if level > cap:
                raise ValueError()
            parsed.append((cap, level))
    except (ValueError, KeyError, TypeError, AttributeError):
        return jsonify(ok=False, error="Each tank needs a capacity of 1 to 10,000,000 L and a level between zero and its capacity."), 400
    with db() as conn:
        current = get_tanks(conn, site["id"])
        for i, (cap, _) in enumerate(parsed, 1):
            conn.execute("UPDATE tanks SET capacity_l=? WHERE site_id=? AND tank_number=?", (cap, site["id"], i))
        if any(abs(level - float(current[i]["level_l"])) > 0.5 for i, (_, level) in enumerate(parsed)):
            event_date, event_time = date.today().isoformat(), datetime.now().strftime("%H:%M")
            insert_event(conn, site, "tank_adjust", event_date, event_time,
                         payload=json.dumps({"levels": [level for _, level in parsed]}))
        recalculate_site(conn, site["id"])
    return jsonify(ok=True, state=water_balance(site["id"]))


@app.route("/api/settings", methods=["POST"])
@login_required
def api_settings():
    site = current_site()
    data = json_body()
    try:
        # A field left out keeps the site's current value (it is never reset to a default).
        name = str(data.get("name", site["name"])).strip() or site["name"]
        village = str(data.get("village", site["village"] or "")).strip()[:120]
        lat_raw = data.get("latitude", site["latitude"])
        lon_raw = data.get("longitude", site["longitude"])
        lat = float(lat_raw) if lat_raw not in (None, "") else None
        lon = float(lon_raw) if lon_raw not in (None, "") else None
        area = num(data, "canopy_area_m2", site["canopy_area_m2"])
        efficiency = num(data, "collection_efficiency", site["collection_efficiency"])
        battery = num(data, "battery_capacity_kwh", site["battery_capacity_kwh"])
        pump = num(data, "pump_kwh_per_kl", site["pump_kwh_per_kl"])
        if lat is not None and not (math.isfinite(lat) and -90 <= lat <= 90):
            raise ValueError()
        if lon is not None and not (math.isfinite(lon) and -180 <= lon <= 180):
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify(ok=False, error="Check the coordinates, canopy, battery and pump values."), 400
    with db() as conn:
        conn.execute("""UPDATE sites SET name=?, village=?, latitude=?, longitude=?, canopy_area_m2=?,
                        collection_efficiency=?, battery_capacity_kwh=?, pump_kwh_per_kl=? WHERE id=?""",
                     (name[:80], village, lat, lon, area, efficiency, battery, pump, site["id"]))
        recalculate_site(conn, site["id"])
    return jsonify(ok=True, settings=current_site())


# ---------------------------------------------------------------------------
# API: sites
# ---------------------------------------------------------------------------
@app.route("/api/sites", methods=["GET", "POST"])
@login_required
def api_sites():
    uid = current_user_id()
    if request.method == "POST":
        data = json_body()
        name = str(data.get("name", "")).strip()[:80]
        if not name:
            return jsonify(ok=False, error="Give the new site a name."), 400
        with db() as conn:
            if conn.execute("SELECT 1 FROM sites WHERE user_id=? AND lower(name)=lower(?)", (uid, name)).fetchone():
                return jsonify(ok=False, error="You already have a site with that name."), 400
            sid = create_site(conn, uid, name, village=str(data.get("village", "")).strip()[:120])
        session["site_id"] = sid
        return jsonify(ok=True, site_id=sid, sites=list_sites(uid))
    return jsonify(ok=True, sites=list_sites(uid), current=current_site()["id"])


@app.route("/api/sites/<int:site_id>/select", methods=["POST"])
@login_required
def api_select_site(site_id):
    with db() as conn:
        ok = conn.execute("SELECT 1 FROM sites WHERE id=? AND user_id=?", (site_id, current_user_id())).fetchone()
    if not ok:
        return jsonify(ok=False, error="Site not found."), 404
    session["site_id"] = site_id
    return jsonify(ok=True)


@app.route("/api/sites/<int:site_id>", methods=["DELETE"])
@login_required
def api_delete_site(site_id):
    uid = current_user_id()
    with db() as conn:
        if not conn.execute("SELECT 1 FROM sites WHERE id=? AND user_id=?", (site_id, uid)).fetchone():
            return jsonify(ok=False, error="Site not found."), 404
        if conn.execute("SELECT COUNT(*) FROM sites WHERE user_id=?", (uid,)).fetchone()[0] <= 1:
            return jsonify(ok=False, error="You need at least one site."), 400
        conn.execute("DELETE FROM events WHERE site_id=? AND user_id=?", (site_id, uid))
        conn.execute("DELETE FROM sites WHERE id=? AND user_id=?", (site_id, uid))  # tanks are removed with it
    if session.get("site_id") == site_id:
        session.pop("site_id")
    return jsonify(ok=True, sites=list_sites(uid))


# ---------------------------------------------------------------------------
# API: weather
# ---------------------------------------------------------------------------
def lookup_pincode(pin):
    """6-digit Indian PIN code -> (district, state) from the PIN-code service, or (None, None)."""
    try:
        r = requests.get(PINCODE_URL.format(pin), timeout=12, headers=HTTP_HEADERS)
        r.raise_for_status()
        data = r.json()
        offices = (data[0].get("PostOffice") or []) if data and data[0].get("Status") == "Success" else []
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError, AttributeError):
        offices = []
    if not offices:
        return None, None
    return offices[0].get("District"), offices[0].get("State")


def open_meteo_place(name, state=None):
    """Best Open-Meteo match for a name in India (its own ranking), optionally limited to a state."""
    try:
        r = requests.get(GEOCODING_URL, params={"name": name, "count": 10, "language": "en", "format": "json",
                                                "countryCode": "IN"}, timeout=15, headers=HTTP_HEADERS)
        r.raise_for_status()
        results = r.json().get("results") or []
    except (requests.RequestException, ValueError):
        return None
    results = [x for x in results if x.get("country_code") == "IN"] or results
    if state:
        results = [x for x in results if x.get("admin1") == state]
    if not results:
        return None
    m = results[0]
    label = ", ".join(x for x in [m.get("name"), m.get("admin1"), m.get("country")] if x)
    return float(m["latitude"]), float(m["longitude"]), label, "Open-Meteo geocoding"


def nominatim_place(params, fallback_label):
    try:
        r = requests.get(NOMINATIM_URL, params={**params, "format": "jsonv2", "limit": 1}, timeout=15, headers=HTTP_HEADERS)
        r.raise_for_status()
        found = r.json()
    except (requests.RequestException, ValueError):
        return None
    if not found:
        return None
    return float(found[0]["lat"]), float(found[0]["lon"]), found[0].get("display_name", fallback_label), "OpenStreetMap Nominatim"


def geocode(place, pin=""):
    """Find coordinates for a place in India. Returns (lat, lon, label, source) or None.
    Place name: Open-Meteo's best match, then OpenStreetMap Nominatim as a backup.
    PIN code: Nominatim's postcode search (built for postcodes), then the PIN's district via Open-Meteo.
    A PIN covers a postal area, so the point is the area's centre, not an exact plot."""
    if not pin:
        return open_meteo_place(place) or nominatim_place({"q": place, "countrycodes": "in"}, place)
    found = nominatim_place({"postalcode": pin, "country": "India"}, pin)
    if found:
        return found
    district, state = lookup_pincode(pin)
    if district:
        found = open_meteo_place(district, state) or open_meteo_place(district)
        if found:
            lat, lon, label, source = found
            return lat, lon, f"{label} (district of PIN {pin}, approximate)", source
    return None


def fetch_nasa_rain(lat, lon, start_date, end_date):
    """Daily rainfall from NASA POWER (about 50 km grid cells; no forecasts; lags a few days). {date: mm}"""
    r = requests.get(NASA_POWER_URL, params={"parameters": "PRECTOTCORR", "community": "AG", "latitude": lat,
                                              "longitude": lon, "start": start_date.strftime("%Y%m%d"),
                                              "end": end_date.strftime("%Y%m%d"), "format": "JSON"},
                     timeout=30, headers=HTTP_HEADERS)
    r.raise_for_status()
    raw = r.json().get("properties", {}).get("parameter", {}).get("PRECTOTCORR", {})
    out = {}
    for key, mm in raw.items():
        if mm is not None and float(mm) >= 0:  # NASA uses -999 for "no data"
            out[datetime.strptime(key, "%Y%m%d").date().isoformat()] = float(mm)
    return out


def fetch_daily_rain(lat, lon, start_date, end_date):
    """Daily rainfall for every day in the range. Open-Meteo first: the historical archive for older
    days and the forecast service for recent and upcoming days. Any past days Open-Meteo can't supply
    (service down or gaps) are filled from NASA POWER. Returns ({date: mm}, [sources])."""
    values, sources = {}, []
    try:
        values, sources = fetch_open_meteo_rain(lat, lon, start_date, end_date)
    except (requests.RequestException, ValueError):
        values, sources = {}, []
    past_end = min(end_date, date.today())
    missing = [start_date + timedelta(days=i) for i in range((past_end - start_date).days + 1)
               if (start_date + timedelta(days=i)).isoformat() not in values]
    if missing:
        try:
            nasa = fetch_nasa_rain(lat, lon, missing[0], missing[-1])
        except (requests.RequestException, ValueError):
            nasa = {}
        filled = 0
        for d in missing:
            if d.isoformat() in nasa:
                values[d.isoformat()] = nasa[d.isoformat()]
                filled += 1
        if filled:
            sources.append(f"NASA POWER backup ({filled} day{'s' if filled != 1 else ''})")
    if not values and not sources:
        raise requests.RequestException("all rainfall services failed")
    return values, sources


def fetch_open_meteo_rain(lat, lon, start_date, end_date):
    today = date.today()
    values, sources = {}, []
    archive_end = min(end_date, today - timedelta(days=ARCHIVE_LAG_DAYS))
    if start_date <= archive_end:
        r = requests.get(ARCHIVE_URL, params={"latitude": lat, "longitude": lon, "start_date": start_date.isoformat(),
                                              "end_date": archive_end.isoformat(), "daily": "precipitation_sum",
                                              "timezone": "auto"}, timeout=20)
        r.raise_for_status()
        d = r.json().get("daily", {})
        for day, mm in zip(d.get("time", []), d.get("precipitation_sum", [])):
            if mm is not None:
                values[day] = float(mm)
        sources.append("Open-Meteo historical archive")
    fc_start = max(start_date, archive_end + timedelta(days=1)) if start_date <= archive_end else start_date
    if fc_start <= end_date:
        past = max(0, min(FORECAST_MAX_PAST_DAYS, (today - fc_start).days))
        ahead = max(1, min(FORECAST_MAX_DAYS, (end_date - today).days + 1))
        r = requests.get(FORECAST_URL, params={"latitude": lat, "longitude": lon, "daily": "precipitation_sum",
                                               "timezone": "auto", "past_days": past, "forecast_days": ahead}, timeout=20)
        r.raise_for_status()
        d = r.json().get("daily", {})
        for day, mm in zip(d.get("time", []), d.get("precipitation_sum", [])):
            if mm is not None and fc_start.isoformat() <= day <= end_date.isoformat() and day not in values:
                values[day] = float(mm)
        sources.append("Open-Meteo forecast (recent and upcoming days)")
    return values, sources


@app.route("/api/weather")
@login_required
def api_weather():
    """Geocode an Indian village/town (or use coordinates) and fetch daily rainfall for the chosen dates."""
    place = request.args.get("place", "").strip()
    lat_arg, lon_arg = request.args.get("latitude"), request.args.get("longitude")
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    try:
        if lat_arg not in (None, "") and lon_arg not in (None, ""):
            lat, lon = float(lat_arg), float(lon_arg)
            if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                raise ValueError()
            location_name = place[:120] or f"{lat:.4f}, {lon:.4f}"
            geo_source = "coordinates entered"
        else:
            if not place:
                return jsonify(ok=False, error="Enter an Indian village/town, a 6-digit PIN code, or coordinates."), 400
            place = place[:120]
            pin = place if re.fullmatch(r"[1-9]\d{5}", place) else ""
            found = geocode(place, pin)
            if not found:
                return jsonify(ok=False, error="No matching location found (location services may also be down). "
                                                "Try a nearby town, the PIN code, or enter coordinates."), 404
            lat, lon, location_name, geo_source = found

        if not start or not end:  # default: the last 7 complete days
            end_date = date.today() - timedelta(days=1)
            start_date = end_date - timedelta(days=6)
        else:
            start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
        if end_date < start_date or (end_date - start_date).days > 365:
            return jsonify(ok=False, error="Choose a valid date range up to 366 days."), 400
        if end_date > date.today() + timedelta(days=FORECAST_MAX_DAYS - 1):
            return jsonify(ok=False, error=f"Forecasts only reach {FORECAST_MAX_DAYS} days ahead. Choose an earlier end date."), 400
        start, end = start_date.isoformat(), end_date.isoformat()

        values, sources = fetch_daily_rain(lat, lon, start_date, end_date)
        requested = (end_date - start_date).days + 1
        rows = [{"date": (start_date + timedelta(days=i)).isoformat(),
                 "rainfall_mm": values.get((start_date + timedelta(days=i)).isoformat())} for i in range(requested)]
        rows = [r for r in rows if r["rainfall_mm"] is not None]
        if not rows:
            return jsonify(ok=False, error="No daily rainfall values returned for that period. Try other dates."), 404
        total = round(sum(r["rainfall_mm"] for r in rows), 2)
        site = current_site()
        area = float(site.get("canopy_area_m2") or 100)
        eff = float(site.get("collection_efficiency") or 80) / 100
        missing = requested - len(rows)
        note = ("Estimate = rainfall (mm) × effective canopy area (m²) × collection efficiency. "
                "It is a planning estimate, not measured village water supply.")
        if missing:
            note = f"{missing} of {requested} days had no data and are left out of the total. " + note
        # Credit the data sources actually used (Open-Meteo requires attribution under CC BY 4.0).
        credits = []
        if any("Open-Meteo" in s for s in sources) or geo_source == "Open-Meteo geocoding":
            credits.append({"name": "Weather data by Open-Meteo.com (CC BY 4.0)", "url": "https://open-meteo.com/"})
        if any("NASA" in s for s in sources):
            credits.append({"name": "NASA POWER project (NASA Langley Research Center)", "url": "https://power.larc.nasa.gov/"})
        if geo_source == "OpenStreetMap Nominatim":
            credits.append({"name": "Location © OpenStreetMap contributors", "url": "https://www.openstreetmap.org/copyright"})
        return jsonify(ok=True, place=location_name, latitude=lat, longitude=lon, start=start, end=end,
                       location_source=geo_source, credits=credits,
                       source=" + ".join(sources), rows=rows, days_requested=requested, days_with_data=len(rows),
                       total_rainfall_mm=round(total, 2), canopy_area_m2=area,
                       collection_efficiency_percent=round(eff * 100, 1),
                       estimated_collectable_litres=round(total * area * eff, 2), note=note)
    except requests.RequestException:
        return jsonify(ok=False, error="Neither Open-Meteo nor the NASA POWER backup could be reached. "
                                        "Check the internet connection and try again, or enter rainfall manually."), 502
    except (ValueError, KeyError):
        return jsonify(ok=False, error="Invalid coordinates or dates. Use YYYY-MM-DD for dates."), 400


@app.route("/api/weather/use", methods=["POST"])
@login_required
def api_weather_use():
    site = current_site()
    data = json_body()
    try:
        total_rain = num(data, "total_rainfall_mm", limits="weather_rain_mm")
        collected = num(data, "estimated_collectable_litres", limits="weather_litres")
        event_date, event_time = parse_when(data)
        village = str(data.get("place") or "")[:160]
    except (ValueError, KeyError, TypeError):
        return jsonify(ok=False, error="Weather result is incomplete or has invalid numbers."), 400
    # The estimate must match this site's canopy and efficiency (stops made-up numbers being saved).
    expected = total_rain * float(site["canopy_area_m2"] or 0) * float(site["collection_efficiency"] or 0) / 100
    if abs(collected - expected) > max(1.0, expected * 0.001):
        return jsonify(ok=False, error="The estimate no longer matches this site's canopy settings. Fetch the rainfall again."), 400
    with db() as conn:
        insert_event(conn, site, "weather_import", event_date, event_time, village=village,
                     rainfall_mm=total_rain, collected_l=collected,
                     details="Weather estimate saved for analysis only; not added to tank inventory.")
    return jsonify(ok=True, message="Weather estimate saved to history. It was not added to tank levels.")


# ---------------------------------------------------------------------------
# API: history
# ---------------------------------------------------------------------------
@app.route("/api/history")
@login_required
def api_history():
    site = current_site()
    with db() as conn:
        # All entries (no 500-row cap), so the table and the CSV export are complete.
        rows = conn.execute("SELECT * FROM events WHERE site_id=? ORDER BY event_date DESC,event_time DESC,id DESC",
                            (site["id"],)).fetchall()
    return jsonify(ok=True, events=[dict(r) for r in rows])


@app.route("/api/history/<int:event_id>", methods=["DELETE"])
@login_required
def api_delete_event(event_id):
    """Delete an entry; tank levels and battery are recalculated as if it never happened."""
    site = current_site()
    with db() as conn:
        deleted = conn.execute("DELETE FROM events WHERE id=? AND site_id=? AND user_id=?",
                               (event_id, site["id"], current_user_id())).rowcount
        if deleted:
            recalculate_site(conn, site["id"])
    if not deleted:
        return jsonify(ok=False, error="That entry was not found in this site. Reload the page."), 404
    return jsonify(ok=True, state=water_balance(site["id"]))


@app.errorhandler(401)
def unauthorized(_):
    session.clear()
    if wants_json():
        return jsonify(ok=False, error="Please sign in again."), 401
    return redirect(url_for("login"))


@app.errorhandler(413)
def too_large(_):
    return jsonify(ok=False, error="That request is too large."), 413


init_db()

if __name__ == "__main__":
    # Debug mode (interactive debugger) only with JAL_DEBUG=1. Never expose the app on a network
    # address while debug mode is on.
    app.run(debug=DEBUG, host="127.0.0.1", port=int(os.environ.get("JAL_PORT", "8000")))
