# Project JAL — Smart Water Management Simulation

## Folder structure
```text
project_jal/
├── app.py
├── requirements.txt
├── project_jal.sqlite3       # created automatically when the app runs
├── static/
│   ├── css/style.css
│   └── js/dashboard.js
└── templates/
    ├── _sidebar.html         # shared sidebar with the site switcher
    ├── login.html
    ├── index.html
    ├── simulation.html
    ├── history.html
    ├── settings.html
    └── profile.html
```

## Run locally (Windows)
1. Install Python 3.10+.
2. Open this folder in VS Code.
3. In the terminal:
   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   python app.py
   ```
4. Open `http://127.0.0.1:8000` and create an account.

## Running it on another computer (team setup)
The code comes from GitHub; the database is shared separately because it is never committed.

1. **Get the code** from the `feature/my-changes` branch:
   ```powershell
   git clone -b feature/my-changes https://github.com/broParth/Project-JALnSolar.git
   ```
   Or on GitHub switch the branch dropdown to `feature/my-changes`, then **Code → Download ZIP**.
2. **Add the shared database (optional).** Unzip `project_jal_database.zip` and put `project_jal.sqlite3`
   in the project folder, **next to `app.py`**, with exactly that name. Skip this to start with an empty
   database and register a new account.
3. Install and run as in *Run locally* above, then open `http://127.0.0.1:8000`.

**Signing in with a shared database:** use the existing accounts' email/username and password. If you don't
know a password, use **Forgot password?** on the sign-in page; without email set up, the reset link is printed
in the terminal running `app.py`.

**Each copy is separate.** Entries added on one computer don't appear on another. To bring two copies back in
line, send the database file again (it replaces the other copy completely). Stop the app before copying the
file so it isn't half-written.

### Files git ignores (and whether you need them)
| Ignored item | What it is | Needed from a teammate? |
|---|---|---|
| `project_jal.sqlite3` | The database: accounts, sites, tanks and all entries | Only if you want their data; shared as a zip, never via GitHub |
| `*.sqlite3-journal` | Temporary file SQLite uses while writing | No, created and removed automatically |
| `backups/` | Database copies made before upgrades | No, local safety copies |
| `.jal_secret_key` | Random key that signs login cookies on that computer | **No, never share it.** Each computer creates its own on first run |
| `__pycache__/`, `*.pyc` | Python's compiled cache | No, recreated automatically |
| `.venv/`, `venv/` | Python virtual environment | No, create your own with `pip install -r requirements.txt` |
| `.env` | Private settings such as email (SMTP) passwords | No, only if you set up email yourself |

## Features
- Reference-inspired dashboard and illustrated JAL canopy.
- **Sites:** one account can manage several sites (e.g. one per village). Each site has its own location, canopy, 5 tanks, battery and history. Switch sites from the sidebar; add, rename or delete them in Settings.
- Five tanks of 600,000 L (600 m³) each per site, 3,000,000 L in total; set by `TANK_COUNT` and `TANK_CAPACITY_L` in `app.py`. This is a prototype assumption, not a validated structural design. Databases created when the default was 15,000 L are upgraded once on startup (tanks still at exactly 15,000 L become 600,000 L; capacities changed by hand are left alone).
- **Five collectors, connected tanks:** the canopy is split into five equal collectors, one above each tank, so every rain entry gives each tank the same share at once. The canopy area in the site settings is the **total** for all five (100 m² = 20 m² per collector). The tanks are linked in a cascade: a full tank passes its extra on to the next tank, and whatever Tank 5 can't hold is overflow. Water use draws Tank 5 first, then Tank 4, down to Tank 1.
- **Rain duration** is entered in minutes and recorded with the entry. It does not change the amount of water: the rainfall depth (mm) is already the total for the event.
- **Tank levels are always replayed in date order.** Every entry can be backdated, and tank levels, overflow, unmet demand and battery are recalculated from each tank's starting level by replaying the site's entries in date/time order. Deleting an entry recalculates everything as if it never happened. Changing a level in Settings adds a "Tank adjustment" entry, which can be deleted to undo it.
- If all tanks are full, overflow is reported as requiring recharge/overflow handling. It is **not** automatically treated as safe groundwater recharge.
- **Water demand and unmet demand:** record the water needed; the tanks supply what they can and any shortfall is recorded as unmet demand.
- **Days of water left:** stored water ÷ average daily demand over the most recent (up to) 14 days of recorded use, assuming no more rain. Shown in red under 7 days.
- **Solar, battery and pump:** solar generation is split between village homes, sensors/IoT and a battery (default 10 kWh). Pumping water uses energy (default 0.15 kWh per 1,000 L), taken from the battery first and the grid for the rest. Both values are set per site in Settings.
- **Village water-demand calculator** (Simulation page): population × litres per person per day (default 55) plus yearly crop water, giving daily and yearly demand and how many days full or current storage would last. A planning estimate only; nothing is saved. "Use daily demand" copies the figure into the water-use form.
- **Debris catcher estimate** (Simulation page): each rain entry gets an estimated debris catch (0.015 g per mm per m² of canopy) and capture rate (55% + 0.35% per mm, up to 90%). The load since the last "Mark catcher cleaned" entry is shown, with a cleaning reminder at 5 kg. These are illustrative assumptions, not sensor readings; the constants are at the top of `app.py`.
- **Solar energy use:** record energy used by the sensors, the system, or both, with its purpose. It is drawn from the battery as it stood at that date and time; if the battery is short, the rest is counted as grid energy. These figures are reported separately from the pump's.
- SQLite accounts, sites, tank levels and event history. The database is excluded from git by `.gitignore`.
- Forgot password: "Forgot password?" on the sign-in page creates a one-time reset link that expires after 30 minutes. See "Password reset" below for how the link is delivered.
- **Weather lookup by place name, 6-digit PIN code or coordinates**, no API keys:
  - Place names: Open-Meteo geocoding, with OpenStreetMap Nominatim as the backup. Common names can match several villages; use a PIN code or coordinates to be sure.
  - PIN codes: OpenStreetMap's postcode search (the centre of the postal area, not an exact plot), falling back to the PIN's district via the third-party api.postalpincode.in service and Open-Meteo.
  - Rainfall: Open-Meteo first; any past days it can't supply (service down or gaps) are filled from NASA POWER, whose cells are about 50 km wide, so its values can differ noticeably from Open-Meteo's.
  - The sources used and their credits (Open-Meteo CC BY 4.0, NASA POWER, OpenStreetMap) are shown with each result.
- Open-Meteo daily precipitation: Any range up to 366 days is covered: the historical archive is used for older days and the forecast service for the most recent days and up to 16 days ahead. Days with no data are reported, not silently dropped.
- Weather-based collection estimates can be saved to history without incorrectly adding historical rain to current tank inventory.
- Change tank capacities/levels, reset levels and event history.

## Password reset
1. On the sign-in page, click **Forgot password?** and enter the account's email or username.
2. A reset link is created. It works once, expires after 30 minutes, and requesting a new one cancels the old one. The page shows the same message whether or not the account exists, so it can't be used to find out who has an account.
3. Open the link, enter a new password twice (at least 8 characters), and sign in with it.

**Where the link goes:** by default the app has no email server, so the link is printed in the terminal window running `python app.py`. Copy it from there into the browser. To email it instead (only for accounts registered with an email address), set these before starting the app, for example with a Gmail app password:
```powershell
$env:JAL_SMTP_HOST="smtp.gmail.com"; $env:JAL_SMTP_PORT="587"
$env:JAL_SMTP_USER="you@gmail.com"; $env:JAL_SMTP_PASSWORD="your-app-password"
python app.py
```
If sending the email fails, the link is printed in the terminal as a fallback.

Reset links always start with `JAL_BASE_URL` (default `http://127.0.0.1:8000`). Set it to the app's real address if it is served somewhere else, for example `$env:JAL_BASE_URL="https://jal.example.org"`.

## Security
- **Login cookie key:** on first run the app generates a random key in `.jal_secret_key` beside `app.py` (git-ignored, never commit it). Set `JAL_SECRET_KEY` instead to manage it yourself. Deleting the file signs everyone out.
- **Cross-site protection:** every change (forms and API calls) must carry the page's CSRF token, so other websites can't make a signed-in browser add, change or delete data. The data API accepts JSON only.
- **Sign-in limits:** after 5 wrong passwords for an account (or 20 from one computer) within 15 minutes, sign-in is refused until the 15 minutes pass. Wrong "current password" entries on the Profile page count too. Password reset requests are limited to 5 per 15 minutes per computer.
- **Profile:** changing the sign-in email/username needs the current password. Sign out is a button (a POST), not a link.
- **Browser headers:** Content-Security-Policy, X-Frame-Options, X-Content-Type-Options and Referrer-Policy are sent on every page; the login cookie is HttpOnly and SameSite=Lax. Set `JAL_HTTPS=1` when serving over https so the cookie is marked Secure.
- **Debug mode is off by default.** `$env:JAL_DEBUG="1"` turns on auto-reload and the interactive debugger for development only; never use it on a network address.
- **Input checks:** numbers must be real values within sensible limits (no NaN/infinity), dates must be between 2000 and one year ahead, an identical entry submitted twice within 10 seconds is saved once, and saved weather estimates must match the site's canopy settings.

Other settings: `JAL_PORT` (default 8000) and `JAL_DB_PATH` (database file, default `project_jal.sqlite3` beside `app.py`; useful for testing on a copy).

## Weather data notes
The app uses Open-Meteo's public geocoding, forecast and historical archive endpoints, with OpenStreetMap Nominatim, api.postalpincode.in and NASA POWER as backups (all free and keyless; Open-Meteo is free for non-commercial use, and Nominatim allows at most one request per second). Availability varies by date and data coverage. Historical data can be modelled/reanalysis estimates rather than an on-site rain gauge. The water estimate is:

`rainfall_mm × canopy_area_m² × collection_efficiency = litres`

This is a simplified estimate. First-flush loss, roof/canopy geometry, evaporation, leaks, water quality, storage constraints and local conditions matter. For operational use, verify the data source and local IMD/official observations.

## Important limitations
- This is a student demo, not a production application. Do not expose it to the public internet as-is.
- No admin roles or activity log yet (planned). Each account manages only its own sites.
- The demo does not certify drinking-water quality or groundwater-recharge suitability.
- Chart.js and Google Fonts are loaded from CDNs, so they need an internet connection for those assets.
- SQLite database is created beside `app.py` automatically.
