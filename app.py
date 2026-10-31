import os
import sqlite3
from datetime import datetime, date
from functools import wraps

import requests
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "project_jal.sqlite3")

app = Flask(__name__)
app.secret_key = os.environ.get("JAL_SECRET_KEY", "change-this-local-demo-secret")
app.config["JSON_SORT_KEYS"] = False

# Open-Meteo is free for non-commercial use and does not require an API key.
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            display_name TEXT NOT NULL,
            identifier TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tanks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tank_number INTEGER NOT NULL,
            capacity_l REAL NOT NULL DEFAULT 5000,
            level_l REAL NOT NULL DEFAULT 0,
            UNIQUE(user_id, tank_number),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
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
        CREATE TABLE IF NOT EXISTS settings (
            user_id INTEGER PRIMARY KEY,
            village TEXT DEFAULT '',
            latitude REAL,
            longitude REAL,
            canopy_area_m2 REAL DEFAULT 100,
            collection_efficiency REAL DEFAULT 80,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def current_user_id():
    return int(session["user_id"])


def get_settings(user_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM settings WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else {
        "user_id": user_id, "village": "", "latitude": None, "longitude": None,
        "canopy_area_m2": 100, "collection_efficiency": 80
    }


def get_tanks(user_id):
    with db() as conn:
        rows = conn.execute(
            "SELECT tank_number, capacity_l, level_l FROM tanks WHERE user_id=? ORDER BY tank_number",
            (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def ensure_tanks(user_id):
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM tanks WHERE user_id=?", (user_id,)).fetchone()[0]
        if count == 0:
            conn.executemany(
                "INSERT INTO tanks(user_id,tank_number,capacity_l,level_l) VALUES(?,?,?,?)",
                [(user_id, 1, 5000, 1200), (user_id, 2, 5000, 800), (user_id, 3, 5000, 0)]
            )


def record_event(user_id, event_type, event_date, event_time, **kwargs):
    keys = ["village", "rainfall_mm", "duration_hours", "collected_l", "used_l",
            "solar_kwh", "village_energy_kwh", "system_energy_kwh", "details"]
    values = [kwargs.get(k) for k in keys]
    with db() as conn:
        conn.execute("""
            INSERT INTO events(user_id,event_type,event_date,event_time,village,rainfall_mm,
            duration_hours,collected_l,used_l,solar_kwh,village_energy_kwh,system_energy_kwh,
            details,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [user_id, event_type, event_date, event_time] + values + [datetime.now().isoformat(timespec="seconds")])


def water_balance(user_id):
    tanks = get_tanks(user_id)
    total_capacity = sum(float(t["capacity_l"]) for t in tanks)
    total_level = sum(float(t["level_l"]) for t in tanks)
    return {"tanks": tanks, "total_capacity_l": total_capacity, "total_level_l": total_level,
            "total_remaining_space_l": max(0, total_capacity-total_level)}


@app.route("/")
def home():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return redirect(url_for("dashboard"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("display_name", "").strip()
        identifier = request.form.get("identifier", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not identifier or len(password) < 8:
            flash("Enter your name, email/username, and a password with at least 8 characters.", "error")
            return render_template("login.html", mode="register")
        try:
            with db() as conn:
                cur = conn.execute(
                    "INSERT INTO users(display_name,identifier,password_hash,created_at) VALUES(?,?,?,?)",
                    (name, identifier, generate_password_hash(password), datetime.now().isoformat(timespec="seconds"))
                )
                user_id = cur.lastrowid
                conn.execute("INSERT INTO settings(user_id) VALUES(?)", (user_id,))
            session["user_id"] = user_id
            session["display_name"] = name
            ensure_tanks(user_id)
            return redirect(url_for("dashboard"))
        except sqlite3.IntegrityError:
            flash("That email/username is already registered. Please sign in.", "error")
    return render_template("login.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip().lower()
        password = request.form.get("password", "")
        with db() as conn:
            user = conn.execute("SELECT * FROM users WHERE identifier=?", (identifier,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["display_name"] = user["display_name"]
            ensure_tanks(user["id"])
            return redirect(url_for("dashboard"))
        flash("Sign-in details were not recognised.", "error")
    return render_template("login.html", mode="login")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    ensure_tanks(current_user_id())
    return render_template("index.html", name=session.get("display_name", "JAL User"),
                           settings=get_settings(current_user_id()))


@app.route("/simulation")
@login_required
def simulation():
    return render_template("simulation.html", name=session.get("display_name", "JAL User"),
                           settings=get_settings(current_user_id()))


@app.route("/history")
@login_required
def history():
    return render_template("history.html", name=session.get("display_name", "JAL User"))


@app.route("/settings")
@login_required
def settings_page():
    return render_template("settings.html", name=session.get("display_name", "JAL User"),
                           settings=get_settings(current_user_id()))


@app.route("/api/state")
@login_required
def api_state():
    uid = current_user_id()
    ensure_tanks(uid)
    balance = water_balance(uid)
    settings = get_settings(uid)
    with db() as conn:
        events = conn.execute("SELECT * FROM events WHERE user_id=? ORDER BY event_date,event_time,id", (uid,)).fetchall()
        recent = conn.execute("SELECT * FROM events WHERE user_id=? ORDER BY id DESC LIMIT 12", (uid,)).fetchall()
    event_list = [dict(e) for e in events]
    return jsonify({"ok": True, **balance, "settings": settings, "events": event_list,
                    "recent": [dict(e) for e in recent]})


@app.route("/api/rainfall", methods=["POST"])
@login_required
def api_rainfall():
    uid = current_user_id()
    data = request.get_json(silent=True) or request.form
    try:
        rainfall = max(0, float(data.get("rainfall_mm", 0)))
        duration = max(0, float(data.get("duration_hours", 1)))
        area = max(1, float(data.get("canopy_area_m2", get_settings(uid)["canopy_area_m2"] or 100)))
        efficiency = min(100, max(0, float(data.get("collection_efficiency", get_settings(uid)["collection_efficiency"] or 80)))) / 100
        solar = max(0, float(data.get("solar_kwh", 0)))
        village_energy = max(0, float(data.get("village_energy_kwh", solar * 0.75)))
        system_energy = max(0, float(data.get("system_energy_kwh", solar * 0.10)))
        event_date = str(data.get("event_date") or date.today().isoformat())
        event_time = str(data.get("event_time") or datetime.now().strftime("%H:%M"))
        village = str(data.get("village") or get_settings(uid).get("village") or "")
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Please enter valid numeric values."), 400

    collected = rainfall * area * efficiency  # 1 mm rainfall over 1 m² = 1 litre
    with db() as conn:
        tanks = conn.execute("SELECT * FROM tanks WHERE user_id=? ORDER BY tank_number", (uid,)).fetchall()
        remaining = collected
        routing = []
        for tank in tanks:
            space = max(0, float(tank["capacity_l"]) - float(tank["level_l"]))
            added = min(space, remaining)
            if added > 0:
                conn.execute("UPDATE tanks SET level_l=level_l+? WHERE id=?", (added, tank["id"]))
                routing.append({"tank_number": tank["tank_number"], "added_l": round(added, 2)})
                remaining -= added
            if remaining <= 0:
                break
    details = "Water routed: " + (", ".join(f'Tank {r["tank_number"]} +{r["added_l"]:.0f} L' for r in routing) or "no storage space")
    if remaining > 0:
        details += f"; {remaining:.0f} L overflow flagged for recharge/overflow handling"
    record_event(uid, "rainfall", event_date, event_time, village=village, rainfall_mm=rainfall,
                 duration_hours=duration, collected_l=collected, solar_kwh=solar,
                 village_energy_kwh=village_energy, system_energy_kwh=system_energy, details=details)
    return jsonify(ok=True, rainfall_mm=rainfall, collected_l=round(collected, 2),
                   stored_l=round(collected-remaining, 2), overflow_l=round(remaining, 2),
                   routing=routing, message=details, state=water_balance(uid))


@app.route("/api/usage", methods=["POST"])
@login_required
def api_usage():
    uid = current_user_id()
    data = request.get_json(silent=True) or request.form
    try:
        used = max(0, float(data.get("used_l", 0)))
        event_date = str(data.get("event_date") or date.today().isoformat())
        event_time = str(data.get("event_time") or datetime.now().strftime("%H:%M"))
        village = str(data.get("village") or get_settings(uid).get("village") or "")
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Please enter valid usage details."), 400
    if used <= 0:
        return jsonify(ok=False, error="Water used must be greater than zero."), 400

    with db() as conn:
        tanks = conn.execute("SELECT * FROM tanks WHERE user_id=? ORDER BY tank_number DESC", (uid,)).fetchall()
        available = sum(float(t["level_l"]) for t in tanks)
        if used > available:
            return jsonify(ok=False, error=f"Only {available:.0f} L is currently stored. Reduce the usage amount."), 400
        remaining = used
        withdrawals = []
        for tank in tanks:
            take = min(float(tank["level_l"]), remaining)
            if take > 0:
                conn.execute("UPDATE tanks SET level_l=level_l-? WHERE id=?", (take, tank["id"]))
                withdrawals.append({"tank_number": tank["tank_number"], "used_l": round(take, 2)})
                remaining -= take
            if remaining <= 0:
                break
    record_event(uid, "usage", event_date, event_time, village=village, used_l=used,
                 details="Water withdrawn: " + ", ".join(f'Tank {w["tank_number"]} -{w["used_l"]:.0f} L' for w in withdrawals))
    return jsonify(ok=True, used_l=used, withdrawals=withdrawals, state=water_balance(uid))


@app.route("/api/reset", methods=["POST"])
@login_required
def api_reset():
    uid = current_user_id()
    with db() as conn:
        conn.execute("UPDATE tanks SET level_l=0 WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM events WHERE user_id=?", (uid,))
    return jsonify(ok=True, message="Tank levels and history reset.")


@app.route("/api/tanks", methods=["POST"])
@login_required
def api_tanks():
    uid = current_user_id()
    data = request.get_json(silent=True) or {}
    tanks = data.get("tanks", [])
    if len(tanks) != 3:
        return jsonify(ok=False, error="Please provide exactly three tanks."), 400
    try:
        parsed = []
        for i, t in enumerate(tanks, 1):
            cap = float(t["capacity_l"])
            level = float(t["level_l"])
            if cap <= 0 or level < 0 or level > cap:
                raise ValueError()
            parsed.append((cap, level, i))
    except (ValueError, KeyError, TypeError):
        return jsonify(ok=False, error="Each tank needs a positive capacity and a level between zero and capacity."), 400
    with db() as conn:
        for cap, level, number in parsed:
            conn.execute("UPDATE tanks SET capacity_l=?,level_l=? WHERE user_id=? AND tank_number=?",
                         (cap, level, uid, number))
    return jsonify(ok=True, state=water_balance(uid))


@app.route("/api/settings", methods=["POST"])
@login_required
def api_settings():
    uid = current_user_id()
    data = request.get_json(silent=True) or request.form
    try:
        village = str(data.get("village", "")).strip()
        lat_raw, lon_raw = data.get("latitude"), data.get("longitude")
        lat = float(lat_raw) if lat_raw not in (None, "") else None
        lon = float(lon_raw) if lon_raw not in (None, "") else None
        area = float(data.get("canopy_area_m2", 100))
        efficiency = float(data.get("collection_efficiency", 80))
        if not 1 <= area <= 1000000 or not 0 <= efficiency <= 100:
            raise ValueError()
        if lat is not None and not -90 <= lat <= 90: raise ValueError()
        if lon is not None and not -180 <= lon <= 180: raise ValueError()
    except (ValueError, TypeError):
        return jsonify(ok=False, error="Check the village coordinates and canopy values."), 400
    with db() as conn:
        conn.execute("""INSERT INTO settings(user_id,village,latitude,longitude,canopy_area_m2,collection_efficiency)
        VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET village=excluded.village,
        latitude=excluded.latitude,longitude=excluded.longitude,canopy_area_m2=excluded.canopy_area_m2,
        collection_efficiency=excluded.collection_efficiency""",
        (uid, village, lat, lon, area, efficiency))
    return jsonify(ok=True, settings=get_settings(uid))


@app.route("/api/weather")
@login_required
def api_weather():
    """Geocode Indian village/town or fetch historical rainfall for selected dates.
    Uses Open-Meteo geocoding + archive/forecast APIs; no key required for this demo.
    """
    place = request.args.get("place", "").strip()
    lat_arg, lon_arg = request.args.get("latitude"), request.args.get("longitude")
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    try:
        if lat_arg not in (None, "") and lon_arg not in (None, ""):
            lat, lon = float(lat_arg), float(lon_arg)
            if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                raise ValueError()
            location_name = place or f"{lat:.4f}, {lon:.4f}"
        else:
            if not place:
                return jsonify(ok=False, error="Enter an Indian village/town or coordinates."), 400
            geo = requests.get(GEOCODING_URL, params={
                "name": place, "count": 10, "language": "en", "format": "json",
                "countryCode": "IN"
            }, timeout=15)
            geo.raise_for_status()
            results = geo.json().get("results", [])
            if not results:
                return jsonify(ok=False, error="No matching location found. Try a nearby town or enter coordinates."), 404
            match = next((r for r in results if r.get("country_code") == "IN"), results[0])
            lat, lon = match["latitude"], match["longitude"]
            location_name = ", ".join(x for x in [match.get("name"), match.get("admin1"), match.get("country")] if x)

        # User-selectable period; default to last 7 days for easy live demo.
        if not start or not end:
            from datetime import timedelta
            end_date = date.today() - __import__("datetime").timedelta(days=1)
            start_date = end_date - __import__("datetime").timedelta(days=6)
            start, end = start_date.isoformat(), end_date.isoformat()
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
        if end_date < start_date or (end_date-start_date).days > 365:
            return jsonify(ok=False, error="Choose a valid date range up to 366 days."), 400

        today = date.today()
        use_forecast = end_date >= today - __import__("datetime").timedelta(days=1)
        if use_forecast:
            # Forecast API can return recent/current daily precipitation; requested dates are bounded to forecast availability.
            url = FORECAST_URL
            params = {"latitude": lat, "longitude": lon, "daily": "precipitation_sum",
                      "timezone": "auto", "past_days": 7, "forecast_days": 7}
            source = "Open-Meteo forecast (recent/forecast daily precipitation)"
        else:
            url = ARCHIVE_URL
            params = {"latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
                      "daily": "precipitation_sum", "timezone": "auto"}
            source = "Open-Meteo historical archive"

        wx = requests.get(url, params=params, timeout=20)
        wx.raise_for_status()
        payload = wx.json()
        daily = payload.get("daily", {})
        dates = daily.get("time", [])
        rain = daily.get("precipitation_sum", [])
        rows = [{"date": d, "rainfall_mm": r or 0} for d, r in zip(dates, rain) if start <= d <= end]
        if not rows:
            return jsonify(ok=False, error="No daily rainfall values returned for that period. Try dates within the available period."), 404
        total = sum(float(r["rainfall_mm"] or 0) for r in rows)
        settings = get_settings(current_user_id())
        area = float(settings.get("canopy_area_m2") or 100)
        eff = float(settings.get("collection_efficiency") or 80) / 100
        estimate = total * area * eff
        return jsonify(ok=True, place=location_name, latitude=lat, longitude=lon,
                       start=start, end=end, source=source, rows=rows,
                       total_rainfall_mm=round(total, 2), canopy_area_m2=area,
                       collection_efficiency_percent=round(eff*100, 1),
                       estimated_collectable_litres=round(estimate, 2),
                       note="Estimate = rainfall (mm) × effective canopy area (m²) × collection efficiency. It is a planning estimate, not measured village water supply.")
    except requests.RequestException:
        return jsonify(ok=False, error="Weather service could not be reached. Check internet and try again."), 502
    except (ValueError, KeyError):
        return jsonify(ok=False, error="Invalid coordinates or dates. Use YYYY-MM-DD for dates."), 400


@app.route("/api/weather/use", methods=["POST"])
@login_required
def api_weather_use():
    data = request.get_json(silent=True) or {}
    uid = current_user_id()
    try:
        total_rain = float(data["total_rainfall_mm"])
        collected = float(data["estimated_collectable_litres"])
        event_date = str(data.get("event_date") or date.today().isoformat())
        event_time = str(data.get("event_time") or datetime.now().strftime("%H:%M"))
        village = str(data.get("place") or "")
    except (ValueError, KeyError, TypeError):
        return jsonify(ok=False, error="Weather result is incomplete."), 400
    record_event(uid, "weather_import", event_date, event_time, village=village,
                 rainfall_mm=total_rain, collected_l=collected,
                 details="Weather estimate saved for analysis only; not added to tank inventory.")
    return jsonify(ok=True, message="Weather estimate saved to history. It was not added to tank levels.")


@app.route("/api/history")
@login_required
def api_history():
    uid = current_user_id()
    with db() as conn:
        rows = conn.execute("SELECT * FROM events WHERE user_id=? ORDER BY event_date DESC,event_time DESC,id DESC LIMIT 500",
                            (uid,)).fetchall()
    return jsonify(ok=True, events=[dict(r) for r in rows])


@app.route("/api/history/<int:event_id>", methods=["DELETE"])
@login_required
def api_delete_event(event_id):
    with db() as conn:
        conn.execute("DELETE FROM events WHERE id=? AND user_id=?", (event_id, current_user_id()))
    return jsonify(ok=True)


init_db()

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=8000)
