import json
import os
import subprocess
from typing import Any, Dict, List, Optional

import pandas as pd
import altair as alt
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


def _price_series_from_graphdata(current: Dict[str, Any]) -> Optional[pd.DataFrame]:
    if not isinstance(current, dict):
        return None
    gd = current.get("graphData")
    if not isinstance(gd, dict):
        return None
    rows = gd.get("data")
    if not isinstance(rows, list) or not rows:
        return None
    records: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        yr = r.get("yr")
        if not isinstance(yr, int):
            continue
        for q_idx, q_key in enumerate(["naQ1", "naQ2", "naQ3", "naQ4"], start=1):
            val = r.get(q_key)
            if val is None:
                continue
            # Quarter end dates
            month_day = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[q_idx]
            date = f"{yr}-{month_day}"
            records.append({"date": pd.to_datetime(date), "price": pd.to_numeric(val, errors="coerce")})
    if not records:
        return None
    df = pd.DataFrame(records).dropna().sort_values("date")
    if df.empty:
        return None
    return df


def render_detail(data: List[Dict[str, Any]]) -> None:
    st.subheader("Detail")
    isins = [d.get("isin", "?") for d in data]
    left, right = st.columns([1, 3])
    with left:
        cur = st.selectbox("Select ISIN", isins)
    current = next((d for d in data if d.get("isin") == cur), None)
    if not current:
        st.warning("No data for selected ISIN")
        return

    def get_dp(field: str, default: Any = None) -> Any:
        dp = current.get("dataPoint", {}) if isinstance(current, dict) else {}
        val = None
        if isinstance(dp, dict):
            v = dp.get(field)
            if isinstance(v, dict):
                val = v.get("value", default)
        return val if val is not None else default

    # Specialized fund view
    if current.get("_class") == "fund":
        name = get_dp("name", "-")
        prev_close = get_dp("previousClosePrice")
        st.markdown(f"**{name}** ({current.get('isin')})")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Prev Close", prev_close)
        fee = current.get("feeLevel", {})
        with col2:
            st.metric("Fee Level", fee.get("morningstarFeeLevel"))
        with col3:
            st.metric("Fee Percentile", fee.get("morningstarFeeLevelPercentileRank"))
        with col4:
            st.metric("Domicile", fee.get("domicileCountryId"))

        st.divider()
        st.caption("Top Holdings")
        holdings = current.get("holdings") or []
        if isinstance(holdings, list) and holdings:
            # Build a concise table
            rows = []
            for h in holdings:
                if not isinstance(h, dict):
                    continue
                rows.append({
                    "securityName": h.get("securityName"),
                    "weighting": h.get("weighting"),
                    "country": h.get("country"),
                    "sector": h.get("sector"),
                    "esgRisk": h.get("susEsgRiskScore"),
                    "rating": h.get("stockRating"),
                })
            dfh = pd.DataFrame(rows).sort_values(by=["weighting"], ascending=False).head(25)
            st.dataframe(dfh, use_container_width=True)

            # Charts
            st.caption("Top Holdings (by weight)")
            top_n = min(15, len(dfh))
            dfh_top = dfh.head(top_n).copy()
            # Ensure numeric
            for col in ["weighting", "esgRisk"]:
                if col in dfh_top:
                    dfh_top[col] = pd.to_numeric(dfh_top[col], errors="coerce")
            bar = (
                alt.Chart(dfh_top)
                .mark_bar()
                .encode(
                    x=alt.X("weighting:Q", title="Weighting (%)"),
                    y=alt.Y("securityName:N", sort='-x', title="Security"),
                    tooltip=["securityName", alt.Tooltip("weighting:Q", format=".2f"), "country", "sector"],
                )
                .properties(height=25 * top_n)
            )
            st.altair_chart(bar.interactive(), use_container_width=True)

            # Sector distribution
            st.caption("Sector Distribution")
            sec_df = (
                dfh.assign(sector=dfh["sector"].fillna("Unknown"))
                .groupby("sector", dropna=False)["weighting"].sum()
                .reset_index()
                .sort_values("weighting", ascending=False)
            )
            sec_bar = (
                alt.Chart(sec_df)
                .mark_bar()
                .encode(
                    x=alt.X("weighting:Q", title="Total Weighting (%)"),
                    y=alt.Y("sector:N", sort='-x', title="Sector"),
                    tooltip=["sector", alt.Tooltip("weighting:Q", format=".2f")],
                )
            )
            st.altair_chart(sec_bar.interactive(), use_container_width=True)

            # Country distribution
            st.caption("Country Distribution")
            ctry_df = (
                dfh.assign(country=dfh["country"].fillna("Unknown"))
                .groupby("country", dropna=False)["weighting"].sum()
                .reset_index()
                .sort_values("weighting", ascending=False)
            )
            ctry_bar = (
                alt.Chart(ctry_df)
                .mark_bar()
                .encode(
                    x=alt.X("weighting:Q", title="Total Weighting (%)"),
                    y=alt.Y("country:N", sort='-x', title="Country"),
                    tooltip=["country", alt.Tooltip("weighting:Q", format=".2f")],
                )
            )
            st.altair_chart(ctry_bar.interactive(), use_container_width=True)
        else:
            st.info("No holdings available.")

        st.divider()
        st.caption("Price (Quarterly)")
        dfp = _price_series_from_graphdata(current)
        if dfp is not None:
            line = (
                alt.Chart(dfp)
                .mark_line(point=True)
                .encode(
                    x=alt.X("date:T", title="Date"),
                    y=alt.Y("price:Q", title="NAV / Price"),
                    tooltip=[alt.Tooltip("date:T", title="Date"), alt.Tooltip("price:Q", format=".2f", title="Price")],
                )
            )
            st.altair_chart(line.interactive(), use_container_width=True)
        else:
            st.info("No price series available.")

        # Secondary panels (compact KPIs, no raw JSON)
        st.divider()
        kpi1, kpi2 = st.columns(2)
        with kpi1:
            st.caption("Risk & Return Summary (selected fields)")
            rrs = current.get("riskReturnSummary") or {}
            try:
                rr_df = pd.DataFrame(rrs) if isinstance(rrs, list) else pd.json_normalize(rrs)
                st.dataframe(rr_df.head(30), use_container_width=True)
            except Exception:
                st.write("-")
        with kpi2:
            st.caption("Other Fees (selected fields)")
            other_fee = current.get("otherFee") or {}
            try:
                fee_df = pd.json_normalize(other_fee)
                st.dataframe(fee_df.T, use_container_width=True)
            except Exception:
                st.write("-")
        return

    # Fallback for non-fund types: concise key metrics only
    st.info("Selected instrument is not a fund. Displaying basic fields.")
    basic = {k: current.get(k) for k in ["isin", "_class"]}
    st.table(pd.Series(basic, name="Info"))


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

    tabs = st.tabs(["Overview", "Detail", "Compare", "Downloads", "Settings"])
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
            render_downloads(data)
    with tabs[4]:
        render_settings()


if __name__ == "__main__":
    main()
