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
    ├── login.html
    ├── index.html
    ├── simulation.html
    ├── history.html
    └── settings.html
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

## Features
- Reference-inspired dashboard and illustrated JAL canopy.
- Three tanks; rainfall events add water cumulatively and route Tank 1 → Tank 2 → Tank 3.
- If all tanks are full, overflow is reported as requiring recharge/overflow handling. It is **not** automatically treated as safe groundwater recharge.
- Record household/village water usage by date and time. Withdrawals reduce stored tank levels and appear in history/charts.
- Solar generation, village-home allocation and sensor/IoT allocation are recorded per event.
- SQLite accounts, settings, tank levels and event history.
- Open-Meteo geocoding and daily precipitation lookup by Indian place name or coordinates, no API key needed for this demo.
- Weather-based collection estimates can be saved to history without incorrectly adding historical rain to current tank inventory.
- Change tank capacities/levels, reset levels and event history.

## Weather data notes
The app uses Open-Meteo's public geocoding, forecast and historical archive endpoints. Availability varies by date and data coverage. Historical data can be modelled/reanalysis estimates rather than an on-site rain gauge. The water estimate is:

`rainfall_mm × canopy_area_m² × collection_efficiency = litres`

This is a simplified estimate. First-flush loss, roof/canopy geometry, evaporation, leaks, water quality, storage constraints and local conditions matter. For operational use, verify the data source and local IMD/official observations.

## Important limitations
- This is a student demo, not a production application. Do not expose it to the public internet as-is.
- The built-in secret key is for local development only; set `JAL_SECRET_KEY` before any deployment.
- The demo does not certify drinking-water quality or groundwater-recharge suitability.
- Chart.js and Google Fonts are loaded from CDNs, so they need an internet connection for those assets.
- SQLite database is created beside `app.py` automatically.
