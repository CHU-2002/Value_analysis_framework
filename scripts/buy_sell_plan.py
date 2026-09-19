#!/usr/bin/env python3
"""Trigger an executable buy/sell plan for an already-analysed company.

The default value-analysis run only writes the report and the frozen
`value_computed.json`. This entrypoint is the explicit trigger: it collects a
fresh quote, writes `buy_sell_market.json`, then runs the offline
`buy_sell_engine.py` against the frozen valuation basis. It never recomputes or
overwrites the valuation basis.
"""

from __future__ import annotations

import argparse
import sys

from buy_sell_engine import run_directory
from buy_sell_inputs import export_market
from config import get_token, validate_stock_code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", required=True, help="Stock code (e.g. 600887, 00700.HK, AAPL)")
    parser.add_argument("--output-dir", required=True, help="Existing analysis output directory")
    parser.add_argument("--as-of", help="Offline replay session date YYYY-MM-DD; defaults to current exchange-local date")
    args = parser.parse_args(argv)

    ts_code = validate_stock_code(args.code)

    from tushare_collector import TushareClient
    from value_analysis_engine import ValueAnalysisEngine

    print(f"[buy_sell_plan] 正在采集 {ts_code} 行情...", file=sys.stderr)
    client = TushareClient(get_token())
    client.assemble_data_pack(ts_code)
    engine = ValueAnalysisEngine(ts_code, args.output_dir, client)
    export_market(engine)

    try:
        plan = run_directory(args.output_dir, as_of=args.as_of)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        parser.exit(2, f"buy_sell_plan: {exc}\n")
    print(f"buy_sell_plan.json + buy_sell_plan.md: {plan['execution']['action']}")
    return 3 if plan["execution"]["action"] == "BLOCKED" else 0


if __name__ == "__main__":
    sys.exit(main())
