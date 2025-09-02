# mstar-data-dashboard

A small toolkit to fetch Morningstar data using `mstarpy` for a list of ISINs and explore it with a Streamlit dashboard.

## Features
- Read ISINs from `ISINs.txt`
- Fetch data via `mstarpy` in JSON (light or full mode)
- Configure which API methods to call using `methods_config.json`
- Explore data with a Streamlit app: Overview, Detail, Compare, Raw JSON, Downloads, Settings

## Local setup
```bash
cd "/Users/ndtpaolo/Desktop/Fil/nuovo morningstar"
/usr/bin/python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Fetch data
1) Put your ISINs in `ISINs.txt` (one per line). Lines starting with `#` are ignored.
2) Optional: Adjust `methods_config.json` to include/exclude API methods for full mode.
3) Run fetch (full JSON):
```bash
source .venv/bin/activate
python fetch_isins.py --full --format json
```
This produces `isin_output.json`.

Lite mode (fund datapoints and basic stock overview):
```bash
python fetch_isins.py --format json
```

## Run Streamlit
```bash
source .venv/bin/activate
streamlit run streamlit_app.py
```
App tabs:
- Overview: table of ISINs
- Detail: inspect one ISIN, search and optional field flattening
- Compare: quick side-by-side metrics
- Raw JSON: full payload
- Downloads: export JSON
- Settings: edit `ISINs.txt`, edit `methods_config.json`, and refresh data

## Streamlit Cloud deployment
1) Push the repo (done): `https://github.com/Flapoid/mstar-data-dashboard`
2) In Streamlit Cloud, create a new app pointing to this repo and `streamlit_app.py`.
3) Runtime: Python 3.13
4) The included `requirements.txt` will be installed automatically.
5) After deploy, use Settings in the app to edit ISINs/config and refresh.

## Notes
- The local clone of the upstream `mstarpy` package is ignored; the app uses the PyPI/GitHub-installed package version declared in `requirements.txt`.
- Some API methods can be heavy; use `methods_config.json` to trim output as needed.
