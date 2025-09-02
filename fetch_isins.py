import csv
import json
import os
import sys
import argparse
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
        try:
            return fetch_full_fund(isin, methods_cfg.get("fund_methods", []))
        except Exception:
            pass
        try:
            return fetch_full_stock(isin, methods_cfg.get("stock_methods", []))
        except Exception as exc:
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
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


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
    results: List[Dict[str, Any]] = []
    for i, isin in enumerate(isins, 1):
        print(f"[{i}/{len(isins)}] Fetching {isin}...")
        results.append(fetch_info_for_isin(isin, full=args.full, methods_cfg=methods_cfg))

    if args.format == "json":
        out_path = os.path.join(root, "isin_output.json")
        write_json(results, out_path)
    else:
        out_path = os.path.join(root, "isin_output.csv")
        write_csv(results, out_path)

    print(f"Wrote {len(results)} rows to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
