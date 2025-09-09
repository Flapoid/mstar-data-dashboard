import csv
import json
import os
import sys
import argparse
import datetime
import math
import time
from typing import List, Dict, Any

# Remove project root from sys.path to avoid namespace shadowing of installed package
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path = [p for p in sys.path if os.path.abspath(p) != ROOT]

import mstarpy as ms

CONFIG_PATH = os.path.join(ROOT, "methods_config.json")


def read_isins(file_path: str) -> List[str]:
    isins: List[str] = []
    with open(file_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                continue
            if "#" in line:
                line = line.split("#", 1)[0].strip()
            isins.append(line)
    return isins


def load_methods() -> Dict[str, List[str]]:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            return {
                "fund_methods": list(cfg.get("fund_methods", [])),
                "stock_methods": list(cfg.get("stock_methods", [])),
            }
    except Exception:
        # Fallback to built-ins if config missing
        return {
            "fund_methods": [
                "feeLevel","holdings","taxes","graphData","financialMetrics","fixedIncomeStyle",
                "marketCapitalization","maturitySchedule","maxDrawDown","morningstarAnalyst",
                "multiLevelFixedIncomeData","otherFee","parentMstarRating","parentSummary","people",
                "position","productInvolvement","proxyVotingManagement","proxyVotingShareHolder",
                "regionalSector","regionalSectorIncludeCountries","riskReturnScatterplot","riskReturnSummary",
                "riskVolatility","salesFees","sector","starRatingFundAsc","starRatingFundDesc","trailingReturn"
            ],
            "stock_methods": [
                "overview","analysisData","analysisReport","boardOfDirectors","dividends","esgRisk",
                "financialHealth","freeCashFlow","keyExecutives","keyMetricsSummary","keyRatio",
                "mutualFundBuyers","mutualFundConcentratedOwners","mutualFundOwnership","mutualFundSellers",
                "operatingGrowth","profitability","sustainability","split","trailingTotalReturn",
                "transactionHistory","transactionSummary","valuation","tradingInformation"
            ]
        }


def safe_call(obj: Any, method: str) -> Any:
    try:
        # Special handling for parameterized methods
        if method == "nav":
            end_date = datetime.datetime.utcnow()
            start_date = end_date - datetime.timedelta(days=365 * 5)
            return getattr(obj, method)(start_date=start_date, end_date=end_date, frequency="daily")
        # Default: zero-arg method call
        fn = getattr(obj, method)
        return fn()
    except Exception as exc:
        return {"_error": str(exc)}


def fetch_full_fund(isin: str, methods: List[str]) -> Dict[str, Any]:
    fund = ms.Funds(isin)
    out: Dict[str, Any] = {
        "_class": "fund",
        "isin": isin,
        "dataPoint": fund.dataPoint(["isin", "name", "previousClosePrice"]) or {},
    }
    for m in methods:
        out[m] = safe_call(fund, m)
    return out


def fetch_full_stock(isin: str, methods: List[str]) -> Dict[str, Any]:
    stock = ms.Stock(isin)
    out: Dict[str, Any] = {"_class": "stock", "isin": isin}
    for m in methods:
        out[m] = safe_call(stock, m)
    return out


def fetch_info_for_isin(isin: str, full: bool, methods_cfg: Dict[str, List[str]]) -> Dict[str, Any]:
    if full:
        started = time.time()
        try:
            data = fetch_full_fund(isin, methods_cfg.get("fund_methods", []))
            duration = time.time() - started
            print(f"  ✓ Fund fetched for {isin} in {duration:.2f}s")
            return data
        except Exception as exc_fund:
            duration = time.time() - started
            print(f"  … Fund fetch failed for {isin} in {duration:.2f}s: {exc_fund}")
        started = time.time()
        try:
            data = fetch_full_stock(isin, methods_cfg.get("stock_methods", []))
            duration = time.time() - started
            print(f"  ✓ Stock fetched for {isin} in {duration:.2f}s")
            return data
        except Exception as exc:
            duration = time.time() - started
            print(f"  ✗ Both fetches failed for {isin} in {duration:.2f}s: {exc}")
            return {"isin": isin, "_class": "unknown", "_error": str(exc)}
    # lightweight mode
    try:
        fund = ms.Funds(isin)
        data = fund.dataPoint(["isin", "name", "previousClosePrice"]) or {}
        data["source"] = "fund"
        return {"isin": isin, **data}
    except Exception:
        pass
    try:
        stock = ms.Stock(isin)
        overview = stock.overview() or {}
        return {
            "isin": isin,
            "name": overview.get("name") if isinstance(overview, dict) else None,
            "source": "stock",
        }
    except Exception as exc:
        return {"isin": isin, "error": str(exc), "source": "unknown"}


def write_json(rows: List[Dict[str, Any]], out_path: str) -> None:
    try:
        import numpy as _np  # type: ignore
    except Exception:
        _np = None  # type: ignore
    try:
        import pandas as _pd  # type: ignore
    except Exception:
        _pd = None  # type: ignore

    def _to_serializable(obj: Any):
        # Fast-path for primitives
        if obj is None or isinstance(obj, (str, int, bool)):
            return obj
        if isinstance(obj, float):
            return None if not math.isfinite(obj) else obj
        # Datetime-like
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        # Numpy types
        if _np is not None:
            if isinstance(obj, (_np.integer,)):
                return int(obj)
            if isinstance(obj, (_np.floating,)):
                val = float(obj)
                return None if not math.isfinite(val) else val
            if isinstance(obj, (_np.ndarray,)):
                return [_to_serializable(x) for x in obj.tolist()]
        # Pandas types
        if _pd is not None:
            if isinstance(obj, _pd.DataFrame):
                return [_to_serializable(r) for r in obj.to_dict(orient="records")]
            if isinstance(obj, _pd.Series):
                return {str(k): _to_serializable(v) for k, v in obj.to_dict().items()}
            # pandas Timestamp
            if hasattr(_pd, "Timestamp") and isinstance(obj, _pd.Timestamp):
                return obj.isoformat()
        # Collections
        if isinstance(obj, dict):
            return {str(k): _to_serializable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [_to_serializable(v) for v in obj]
        # Fallback to string representation
        return str(obj)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(_to_serializable(rows), f, ensure_ascii=False, indent=2, allow_nan=False)


def write_csv(rows: List[Dict[str, Any]], out_path: str) -> None:
    fieldnames = sorted({k for row in rows for k in row.keys()})
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="Call many zero-arg API methods")
    parser.add_argument("--format", choices=["json", "csv"], default="json", help="Output format")
    args = parser.parse_args()

    methods_cfg = load_methods()

    root = ROOT
    isins_path = os.path.join(root, "ISINs.txt")
    if not os.path.exists(isins_path):
        print(f"ISINs.txt not found at {isins_path}")
        return 1
    isins = read_isins(isins_path)
    if not isins:
        print("No ISINs provided in ISINs.txt")
        return 1
    success = 0
    failure = 0
    results: List[Dict[str, Any]] = []
    for i, isin in enumerate(isins, 1):
        print(f"[{i}/{len(isins)}] Fetching {isin}…")
        started = time.time()
        data = fetch_info_for_isin(isin, full=args.full, methods_cfg=methods_cfg)
        elapsed = time.time() - started
        results.append(data)
        if isinstance(data, dict) and data.get("_class") == "unknown" and data.get("_error"):
            failure += 1
            print(f"  ✗ Failed {isin} ({elapsed:.2f}s): {data.get('_error')}")
        else:
            success += 1
            print(f"  ✓ Done {isin} ({elapsed:.2f}s)")

    if args.format == "json":
        out_path = os.path.join(root, "isin_output.json")
        write_json(results, out_path)
    else:
        out_path = os.path.join(root, "isin_output.csv")
        write_csv(results, out_path)

    # Validate JSON by reloading
    if args.format == "json":
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                _ = json.load(f)
            print("Output JSON validated successfully (parsed without errors).")
        except Exception as exc:
            print(f"Warning: JSON validation failed: {exc}")

    print(f"Wrote {len(results)} rows to {out_path}")
    print(f"Summary: {success} succeeded, {failure} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
