import json
import os
import subprocess
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(ROOT, "isin_output.json")
ISINS_PATH = os.path.join(ROOT, "ISINs.txt")
CONFIG_PATH = os.path.join(ROOT, "methods_config.json")
FETCH_SCRIPT = os.path.join(ROOT, "fetch_isins.py")


def load_data() -> List[Dict[str, Any]]:
    if not os.path.exists(DATA_PATH):
        st.error(f"File not found: {DATA_PATH}. Run fetch_isins.py first.")
        return []
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def flatten_values(obj: Any) -> Any:
    if isinstance(obj, dict):
        if set(obj.keys()) == {"value"}:
            return obj["value"]
        if "value" in obj and isinstance(obj.get("properties"), dict) and len(obj) <= 3:
            base = {k: flatten_values(v) for k, v in obj.items() if k != "properties"}
            props = {f"prop_{k}": flatten_values(v) for k, v in obj.get("properties", {}).items()}
            return {**base, **props}
        return {k: flatten_values(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [flatten_values(x) for x in obj]
    return obj


def run_fetch(full: bool = True) -> None:
    args = ["python", FETCH_SCRIPT, "--format", "json"]
    if full:
        args.append("--full")
    st.info("Running fetch script... (this may take a minute)")
    try:
        subprocess.run(args, cwd=ROOT, check=True)
        st.success("Fetch complete. Reload the page to see updated data.")
    except subprocess.CalledProcessError as e:
        st.error(f"Fetch failed: {e}")


def render_overview(data: List[Dict[str, Any]]) -> None:
    st.subheader("Overview")
    cols = ["isin", "_class"]
    rows = []
    for d in data:
        entry = {k: d.get(k) for k in cols}
        name = None
        if d.get("_class") == "fund":
            dp = d.get("dataPoint", {})
            if isinstance(dp, dict) and isinstance(dp.get("name"), dict):
                name = dp["name"].get("value")
        if not name and "overview" in d:
            ov = d.get("overview")
            if isinstance(ov, dict):
                name = ov.get("name") or ov.get("companyName")
        entry["name"] = name
        rows.append(entry)
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True)


def render_detail(data: List[Dict[str, Any]]) -> None:
    st.subheader("Detail")
    isins = [d.get("isin", "?") for d in data]
    left, right = st.columns([1, 3])
    with left:
        cur = st.selectbox("Select ISIN", isins)
        flatten = st.checkbox("Flatten fields", value=True)
        search = st.text_input("Search (case-insensitive)", "")
    current = next((d for d in data if d.get("isin") == cur), None)
    if not current:
        st.warning("No data for selected ISIN")
        return
    if flatten:
        current = flatten_values(current)
    top_keys = [k for k in current.keys() if not k.startswith("_")]
    with right:
        section = st.selectbox("Section", ["ALL"] + sorted(top_keys))
        obj = current if section == "ALL" else current.get(section, {})
        if search:
            text = json.dumps(obj, ensure_ascii=False, indent=2)
            if search.lower() not in text.lower():
                st.info("No matches in this section.")
            else:
                st.code(text, language="json")
                return
        st.json(obj)


def render_compare(data: List[Dict[str, Any]]) -> None:
    st.subheader("Compare")
    choices = [d.get("isin", "?") for d in data]
    selected = st.multiselect("Select ISINs to compare", choices, default=choices[:2])
    _ = st.checkbox("Flatten fields", value=True, key="cmp_flat")

    cmp_rows = []
    for d in data:
        if d.get("isin") not in selected:
            continue
        row = {"isin": d.get("isin"), "_class": d.get("_class")}
        if d.get("_class") == "fund":
            dp = d.get("dataPoint", {})
            name = dp.get("name", {}).get("value") if isinstance(dp, dict) else None
            prev = dp.get("previousClosePrice", {}).get("value") if isinstance(dp, dict) else None
            row.update({"name": name, "previousClose": prev})
        if "tradingInformation" in d and isinstance(d["tradingInformation"], dict):
            try:
                ti = next(iter(d["tradingInformation"].values()))
                acp = ti.get("adjustedClosePrice", {}).get("value")
                row["adjustedClosePrice"] = acp
            except Exception:
                pass
        cmp_rows.append(row)
    st.dataframe(pd.DataFrame(cmp_rows), use_container_width=True)


def render_downloads(data: List[Dict[str, Any]]) -> None:
    st.subheader("Downloads")
    pretty = json.dumps(data, ensure_ascii=False, indent=2)
    st.download_button("Download JSON", pretty, file_name="isin_output.json", mime="application/json")


def render_settings() -> None:
    st.subheader("Settings")
    st.write("Edit the ISINs list and methods config, then refresh the dataset.")

    col_isin, col_cfg = st.columns(2)
    with col_isin:
        st.caption("ISINs.txt")
        if os.path.exists(ISINS_PATH):
            with open(ISINS_PATH, "r", encoding="utf-8") as f:
                contents = f.read()
        else:
            contents = ""
        new_text = st.text_area("", value=contents, height=240, key="isins")
        if st.button("Save ISINs.txt"):
            with open(ISINS_PATH, "w", encoding="utf-8") as f:
                f.write(new_text)
            st.success("Saved ISINs.txt")
    with col_cfg:
        st.caption("methods_config.json")
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg_text = f.read()
        else:
            cfg_text = json.dumps({"fund_methods": [], "stock_methods": []}, indent=2)
        new_cfg = st.text_area("", value=cfg_text, height=240, key="cfg")
        if st.button("Save methods_config.json"):
            try:
                json.loads(new_cfg)  # validate
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    f.write(new_cfg)
                st.success("Saved methods_config.json")
            except Exception as e:
                st.error(f"Invalid JSON: {e}")

    if st.button("Refresh data (full)"):
        run_fetch(full=True)


def main() -> None:
    st.set_page_config(page_title="Fund & Stock Visualizer", layout="wide")
    st.title("Fund & Stock Visualizer")

    tabs = st.tabs(["Overview", "Detail", "Compare", "Raw JSON", "Downloads", "Settings"])
    data = load_data()

    with tabs[0]:
        if data:
            render_overview(data)
    with tabs[1]:
        if data:
            render_detail(data)
    with tabs[2]:
        if data:
            render_compare(data)
    with tabs[3]:
        if data:
            st.json(data)
    with tabs[4]:
        if data:
            render_downloads(data)
    with tabs[5]:
        render_settings()


if __name__ == "__main__":
    main()
