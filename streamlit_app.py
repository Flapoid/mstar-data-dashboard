import json
import os
import sys
import subprocess
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import numpy as np
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
def _fund_display_name(entry: Dict[str, Any]) -> str:
    isin = entry.get("isin", "?")
    name = None
    if entry.get("_class") == "fund":
        dp = entry.get("dataPoint", {})
        if isinstance(dp, dict):
            nm = dp.get("name")
            if isinstance(nm, dict):
                name = nm.get("value")
    if not name:
        name = entry.get("overview", {}).get("name") if isinstance(entry.get("overview"), dict) else None
    return f"{name or 'Unnamed'} ({isin})"



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
    args = [sys.executable, FETCH_SCRIPT, "--format", "json"]
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
def _parse_nav_series(nav_payload: Any) -> Optional[pd.DataFrame]:
    # Expect a list of points; be resilient to different shapes
    if not isinstance(nav_payload, list) or not nav_payload:
        return None
    records: List[Dict[str, Any]] = []
    for point in nav_payload:
        if not isinstance(point, dict):
            continue
        # Date candidates
        raw_date = (
            point.get("date")
            or point.get("t")
            or point.get("d")
            or point.get("timestamp")
            or point.get("time")
        )
        # Value candidates
        val = (
            point.get("nav")
            or point.get("value")
            or point.get("v")
        )
        # Some APIs use vector values: { v: [nav, totalReturn, ...] }
        if val is None and isinstance(point.get("values"), list) and point.get("values"):
            val = point.get("values")[0]
        if val is None and isinstance(point.get("v"), list) and point.get("v"):
            val = point.get("v")[0]
        if raw_date is None or val is None:
            continue
        # Parse date
        try:
            if isinstance(raw_date, (int, float)):
                # Assume ms since epoch if very large
                if raw_date > 10_000_000_000:
                    dt = pd.to_datetime(int(raw_date), unit="ms", utc=True).tz_convert(None)
                else:
                    dt = pd.to_datetime(int(raw_date), unit="s", utc=True).tz_convert(None)
            else:
                dt = pd.to_datetime(str(raw_date))
        except Exception:
            continue
        price = pd.to_numeric(val, errors="coerce")
        if pd.isna(price):
            continue
        records.append({"date": dt, "price": float(price)})
    if not records:
        return None
    df = pd.DataFrame(records).dropna().sort_values("date")
    return df if not df.empty else None


def _price_series_from_any(current: Dict[str, Any]) -> Tuple[Optional[pd.DataFrame], str]:
    # Prefer NAV if available
    if isinstance(current, dict) and "nav" in current:
        df_nav = _parse_nav_series(current.get("nav"))
        if df_nav is not None and not df_nav.empty:
            return df_nav, "NAV"
    # Fallback to graphData-derived quarterly series
    return _price_series_from_graphdata(current), "graphData"


def _net_assets_series(current: Dict[str, Any]) -> Optional[pd.DataFrame]:
    gd = current.get("graphData") if isinstance(current, dict) else None
    if not isinstance(gd, dict):
        return None
    rows = gd.get("data")
    if not isinstance(rows, list):
        return None
    records: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict) or "yr" not in r:
            continue
        yr = r["yr"]
        for q_idx, key in enumerate(["naQ1", "naQ2", "naQ3", "naQ4"], start=1):
            val = r.get(key)
            if val is None:
                continue
            month_day = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[q_idx]
            records.append({"date": pd.to_datetime(f"{yr}-{month_day}"), "value": pd.to_numeric(val, errors="coerce"), "type": "Quarterly"})
        if r.get("naYr") is not None:
            records.append({"date": pd.to_datetime(f"{yr}-12-31"), "value": pd.to_numeric(r.get("naYr"), errors="coerce"), "type": "Yearly"})
    if not records:
        return None
    df = pd.DataFrame(records).dropna().sort_values("date")
    return df if not df.empty else None


def _net_flows_series(current: Dict[str, Any]) -> Optional[pd.DataFrame]:
    gd = current.get("graphData") if isinstance(current, dict) else None
    if not isinstance(gd, dict):
        return None
    rows = gd.get("data")
    if not isinstance(rows, list):
        return None
    records: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict) or "yr" not in r:
            continue
        yr = r["yr"]
        for q_idx, key in enumerate(["nfQ1", "nfQ2", "nfQ3", "nfQ4"], start=1):
            val = r.get(key)
            if val is None:
                continue
            month_day = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[q_idx]
            records.append({"date": pd.to_datetime(f"{yr}-{month_day}"), "value": pd.to_numeric(val, errors="coerce"), "type": "Quarterly"})
        if r.get("nfYr") is not None:
            records.append({"date": pd.to_datetime(f"{yr}-12-31"), "value": pd.to_numeric(r.get("nfYr"), errors="coerce"), "type": "Yearly"})
    if not records:
        return None
    df = pd.DataFrame(records).dropna().sort_values("date")
    return df if not df.empty else None


def render_detail(data: List[Dict[str, Any]]) -> None:
    st.subheader("Detail")
    # Build labeled choices with names
    label_to_isin = {_fund_display_name(d): d.get("isin") for d in data}
    labels = list(label_to_isin.keys())
    left, right = st.columns([1, 3])
    with left:
        sel_label = st.selectbox("Select fund", labels)
        cur = label_to_isin.get(sel_label)
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
            # Build a rich, full-detail table by normalizing all keys
            dfh_full = pd.json_normalize([h for h in holdings if isinstance(h, dict)])
            # Prefer core columns first if available
            core_cols = [
                "securityName","ticker","isin","country","sector","sectorCode",
                "weighting","numberOfShare","marketValue","shareChange",
                "susEsgRiskScore","susEsgRiskCategory","stockRating","assessment",
                "economicMoat","currency","localCurrencyCode","currencyName",
                "firstBoughtDate","maturityDate","coupon",
            ]
            # Sort by weighting if present
            if "weighting" in dfh_full.columns:
                dfh_full = dfh_full.sort_values("weighting", ascending=False)
            dfh_full = dfh_full.reset_index(drop=True)
            # Add 1-based rank column at the front
            dfh_full.insert(0, "rank", dfh_full.index + 1)
            # Reorder columns: rank + core + rest (without duplicates)
            rest_cols = [c for c in dfh_full.columns if c not in {"rank", *core_cols}]
            ordered_cols = ["rank"] + [c for c in core_cols if c in dfh_full.columns] + rest_cols
            dfh_full = dfh_full[ordered_cols]
            # Show an expander with full table and a compact top-25 preview above
            dfh = dfh_full[[c for c in ["rank","securityName","weighting","country","sector","susEsgRiskScore","stockRating"] if c in dfh_full.columns]].head(25)
            st.dataframe(dfh, use_container_width=True)
            with st.expander("Show all holding fields (full table)"):
                st.dataframe(dfh_full, use_container_width=True)
                csv = dfh_full.to_csv(index=False).encode("utf-8")
                st.download_button("Download holdings (CSV)", csv, file_name=f"holdings_{current.get('isin')}.csv", mime="text/csv")

            # Charts
            st.caption("Top Holdings (Pie by weight)")
            top_n = min(15, len(dfh))
            # Ensure strictly descending order for slices and legend
            # Use full detail frame for chart source to avoid missing/renamed columns
            src_for_chart = dfh_full if "securityName" in dfh_full.columns else dfh
            dfh_top = src_for_chart.sort_values("weighting", ascending=False).head(top_n).copy()
            for col in ["weighting", "esgRisk"]:
                if col in dfh_top:
                    dfh_top[col] = pd.to_numeric(dfh_top[col], errors="coerce")
            # Build a rank-prefixed label for legend and slice ordering
            dfh_top = dfh_top.reset_index(drop=True)
            dfh_top["rank"] = dfh_top.index + 1
            # Guard against missing securityName field
            dfh_top["_sec"] = dfh_top.get("securityName", pd.Series(["?"]*len(dfh_top)))
            dfh_top["label"] = dfh_top.apply(lambda r: f"{int(r['rank'])}. {r['_sec']}", axis=1)
            # Set categorical order for legend based on descending weight
            dfh_top["label"] = pd.Categorical(
                dfh_top["label"], categories=list(dfh_top["label"]), ordered=True
            )
            pie = (
                alt.Chart(dfh_top)
                .mark_arc(innerRadius=60)
                .encode(
                    theta=alt.Theta("weighting:Q", stack=True),
                    color=alt.Color("label:N", sort=None, legend=alt.Legend(title="Security")),
                    order=alt.Order("weighting:Q", sort="descending"),
                    tooltip=["label", alt.Tooltip("weighting:Q", format=".2f"), "country", "sector"],
                )
            ).properties(height=380)
            st.altair_chart(pie, use_container_width=True)

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
                ).properties(height=320)
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
                ).properties(height=320)
            )
            st.altair_chart(ctry_bar.interactive(), use_container_width=True)
        else:
            st.info("No holdings available.")

        st.divider()
        dfp, src = _price_series_from_any(current)
        st.caption("Price" + (" (NAV)" if src == "NAV" else " (fallback)"))
        if dfp is not None:
            if src != "NAV":
                st.info("NAV not available for this fund. Falling back to quarterly graphData-derived series.")
            line = (
                alt.Chart(dfp)
                .mark_line(point=True)
                .encode(
                    x=alt.X("date:T", title="Date", axis=alt.Axis(format="%b %Y", labelAngle=-30)),
                    y=alt.Y("price:Q", title="NAV / Price"),
                    tooltip=[alt.Tooltip("date:T", title="Date"), alt.Tooltip("price:Q", format=".2f", title="Price")],
                ).properties(height=360)
            )
            st.altair_chart(line.interactive(), use_container_width=True)
        else:
            st.info("No price series available.")

        # Net assets and net flows
        na_df = _net_assets_series(current)
        nf_df = _net_flows_series(current)
        if na_df is not None or nf_df is not None:
            c1, c2 = st.columns(2)
            with c1:
                st.caption("Net Assets Over Time")
                if na_df is not None:
                    na_chart = (
                        alt.Chart(na_df)
                        .mark_line(point=True)
                        .encode(x=alt.X("date:T", axis=alt.Axis(format="%b %Y", labelAngle=-30)), y=alt.Y("value:Q", title="Net Assets (Bil)"), color=alt.Color("type:N", title=""))
                    ).properties(height=320)
                    st.altair_chart(na_chart.interactive(), use_container_width=True)
                else:
                    st.write("-")
            with c2:
                st.caption("Net Flows Over Time")
                if nf_df is not None:
                    nf_chart = (
                        alt.Chart(nf_df)
                        .mark_bar()
                        .encode(x=alt.X("date:T", axis=alt.Axis(format="%b %Y", labelAngle=-30)), y=alt.Y("value:Q", title="Net Flows (Bil)"), color=alt.condition(alt.datum.value >= 0, alt.value("#2ca02c"), alt.value("#d62728")))
                    ).properties(height=320)
                    st.altair_chart(nf_chart.interactive(), use_container_width=True)
                else:
                    st.write("-")

        # Position analytics (from position section)
        pos = current.get("position") if isinstance(current, dict) else None
        if isinstance(pos, dict):
            st.divider()
            st.caption("Position Summary")
            hs = pos.get("holdingSummary", {}) if isinstance(pos.get("holdingSummary"), dict) else {}
            act = pos.get("holdingActiveShare", {}) if isinstance(pos.get("holdingActiveShare"), dict) else {}
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.metric("Holdings (total)", pos.get("numberOfHolding"))
                st.metric("Equity holdings", pos.get("numberOfEquityHolding"))
            with c2:
                st.metric("Other holdings", pos.get("numberOfOtherHolding"))
                st.metric("Short positions", pos.get("numberOfHoldingShort"))
            with c3:
                st.metric("Active Share %", act.get("activeShareValue"))
                st.metric("Top N weight %", hs.get("topHoldingWeighting"))
            with c4:
                st.metric("Turnover %", hs.get("lastTurnover"))
                st.metric("Women directors %", hs.get("womenDirectors"))

            # Holding counts bar
            counts_rows = []
            for label, key in [
                ("Equity", "numberOfEquityHolding"),
                ("Bonds", "numberOfBondHolding"),
                ("Other", "numberOfOtherHolding"),
                ("Short", "numberOfHoldingShort"),
            ]:
                val = pos.get(key)
                if val is not None:
                    counts_rows.append({"Type": label, "Count": val})
            if counts_rows:
                st.altair_chart(
                    alt.Chart(pd.DataFrame(counts_rows))
                    .mark_bar()
                    .encode(x=alt.X("Type:N"), y=alt.Y("Count:Q"), tooltip=["Type", "Count"])\
                    .properties(height=360),
                    use_container_width=True,
                )

            # Equity holding page detail (top by weight) and download
            eq_page = pos.get("equityHoldingPage", {})
            eq_list = eq_page.get("holdingList") if isinstance(eq_page, dict) else None
            if isinstance(eq_list, list) and eq_list:
                df_eq = pd.json_normalize([h for h in eq_list if isinstance(h, dict)])
                if "weighting" in df_eq.columns:
                    df_eq = df_eq.sort_values("weighting", ascending=False)
                st.caption("Position – Equity holdings (from position.equityHoldingPage)")
                st.dataframe(df_eq.head(50), use_container_width=True)
                with st.expander("Download full equity holding list"):
                    csv = df_eq.to_csv(index=False).encode("utf-8")
                    st.download_button(
                        "Download CSV",
                        csv,
                        file_name=f"position_equity_holdings_{current.get('isin')}.csv",
                        mime="text/csv",
                    )

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

        # Risk KPIs from riskVolatility
        st.divider()
        st.caption("Risk Metrics (Alpha/Beta/R^2)")
        rv = current.get("riskVolatility") or {}
        frv = rv.get("fundRiskVolatility") if isinstance(rv, dict) else None
        if isinstance(frv, dict):
            cols = st.columns(3)
            periods = ["for1Year", "for3Year", "for5Year"]
            labels = ["1 Year", "3 Years", "5 Years"]
            for col, period, label in zip(cols, periods, labels):
                with col:
                    vals = frv.get(period) or {}
                    st.metric(f"{label} Alpha", vals.get("alpha", "-"))
                    st.metric(f"{label} Beta", vals.get("beta", "-"))
                    st.metric(f"{label} R-Squared", vals.get("rSquared", "-"))
        else:
            st.info("No risk metrics available.")
        return

    # Fallback for non-fund types: concise key metrics only
    st.info("Selected instrument is not a fund. Displaying basic fields.")
    basic = {k: current.get(k) for k in ["isin", "_class"]}
    st.table(pd.Series(basic, name="Info"))


def render_compare(data: List[Dict[str, Any]]) -> None:
    st.subheader("Compare")
    # Build labeled choices with names
    label_to_isin = {}
    for d in data:
        label_to_isin[_fund_display_name(d)] = d.get("isin")
    default_labels = list(label_to_isin.keys())[:1]
    selected_labels = st.multiselect("Select funds to compare", list(label_to_isin.keys()), default=default_labels)
    selected_isins = [label_to_isin[l] for l in selected_labels]

    # Table summary
    cmp_rows = []
    for d in data:
        if d.get("isin") not in selected_isins:
            continue
        row = {"isin": d.get("isin"), "name": _fund_display_name(d), "_class": d.get("_class")}
        if d.get("_class") == "fund":
            dp = d.get("dataPoint", {})
            prev = dp.get("previousClosePrice", {}).get("value") if isinstance(dp, dict) else None
            row.update({"previousClose": prev})
        cmp_rows.append(row)
    if cmp_rows:
        st.dataframe(pd.DataFrame(cmp_rows), use_container_width=True)

    # Relative performance (normalize to 100)
    st.caption("Relative performance (normalized to 100)")
    series_list = []
    fallback_funds: List[str] = []
    for d in data:
        if d.get("isin") not in selected_isins or d.get("_class") != "fund":
            continue
        dfp, src = _price_series_from_any(d)
        if dfp is None or dfp.empty:
            continue
        if src != "NAV":
            fallback_funds.append(_fund_display_name(d))
        dfp = dfp.sort_values("date")
        # Use the first finite price as base to avoid NaN/zero issues
        finite = dfp["price"][np.isfinite(dfp["price"])].astype(float)
        if finite.empty:
            continue
        base = float(finite.iloc[0])
        if base == 0.0:
            continue
        dfp = dfp.assign(rel=(dfp["price"].astype(float) / base) * 100.0)
        dfp = dfp[["date", "rel"]].rename(columns={"rel": _fund_display_name(d)})
        series_list.append(dfp.set_index("date"))
    if series_list:
        merged = pd.concat(series_list, axis=1).dropna(how="all").reset_index().melt("date", var_name="Fund", value_name="Index")
        chart = (
            alt.Chart(merged)
            .mark_line(point=True, opacity=0.9)
            .encode(
                x=alt.X("date:T", title="Date", axis=alt.Axis(format="%b %Y", labelAngle=-30)),
                y=alt.Y("Index:Q", title="Index (100=base)"),
                color=alt.Color("Fund:N", title="Fund"),
                strokeDash=alt.StrokeDash("Fund:N", title="Fund"),
                tooltip=["Fund", alt.Tooltip("date:T"), alt.Tooltip("Index:Q", format=".2f")],
            )
        )
        st.altair_chart(chart.interactive(), use_container_width=True)
        if fallback_funds:
            st.info("Using fallback (graphData-derived) series for: " + ", ".join(fallback_funds))
    else:
        st.info("No comparable price series found for selected funds.")

    # Benchmark comparison removed per user request


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
