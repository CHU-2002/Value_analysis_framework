#!/usr/bin/env python3
"""Value Analysis Framework - Tushare Data Collector (Phase 1A).

Facade module: re-exports all public names and defines TushareClient
which inherits from mixin classes in tushare_modules/.

Collects 5 years of financial data from Tushare Pro API and outputs
a structured data_pack_market.md file.

Usage:
    python3 scripts/tushare_collector.py --code 600887.SH
    python3 scripts/tushare_collector.py --code 600887.SH --output output/data_pack.md
    python3 scripts/tushare_collector.py --code 600887.SH --dry-run
"""

import argparse
import functools
import os
import sys
import time

import pandas as pd
import tushare as ts

try:
    import yfinance as yf
    _yf_available = True
except ImportError:
    _yf_available = False

from config import get_token, get_api_url, validate_stock_code
from format_utils import format_number, format_table, format_header

# REQ-011：取数的唯一收口点搬到 `datalayer.access.DataAccess`（仓的读写也在那里）。
# 这里保留扁平导入，让「直接执行 scripts/ 下的脚本」与测试的既有做法不变。
from datalayer.access import MODE_OFFLINE, MODE_ONLINE, DataAccess, DataMissing
from datalayer.config import archive_root as resolve_archive_root
from datalayer.store import DataStore

# Re-export all constants and mixin classes for backward compatibility.
# Tests and external code import these from tushare_collector directly:
#   from tushare_collector import TushareClient, WarningsCollector, rate_limit
#   from tushare_collector import _VIP_MAP, HK_INCOME_MAP, US_INCOME_MAP
from tushare_modules.infrastructure import is_permanent_api_error
from tushare_modules import (
    _VIP_MAP,
    HK_INCOME_MAP, HK_BALANCE_MAP, HK_CASHFLOW_MAP,
    US_INCOME_MAP, US_BALANCE_MAP, US_CASHFLOW_MAP,
    _YF_INCOME_MAP, _YF_BALANCE_MAP, _YF_CASHFLOW_MAP,
    InfrastructureMixin, YFinanceMixin, FinancialsMixin,
    OtherDataMixin, DerivedMetricsMixin, AssemblyMixin,
    WarningsCollector,
)


def rate_limit(func):
    """Decorator to enforce 0.5s delay between Tushare API calls."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        time.sleep(0.5)
        return func(*args, **kwargs)
    return wrapper


class TushareClient(
    InfrastructureMixin,
    YFinanceMixin,
    FinancialsMixin,
    OtherDataMixin,
    DerivedMetricsMixin,
    AssemblyMixin,
):
    """Client for Tushare Pro API with rate limiting and retry logic."""

    MAX_RETRIES = 5
    RETRY_DELAY = 2.0  # seconds between retries

    BASIC_CACHE_TTL = 7 * 86400  # 7 days in seconds

    def __init__(self, token: str, *, store=None, mode: str = MODE_ONLINE, batch_id=None,
                 rate_limit_seconds: float = 0.5, retry_delay=None, framework_version=None,
                 clock=None):
        """``mode='offline'`` 时不建远程客户端：重建路径**根本不该**有联网的可能。

        仓由 `--store` / `TURTLE_ARCHIVE_ROOT` 决定（`datalayer.config`），
        也可直接传一个 `DataStore`。传 ``store=False`` 可显式关掉仓（测试与临时脚本用）。
        """

        self.token = token
        self._mode = mode
        # 离线重建时仓里没有的目标记在这里：产物末尾的缺口说明与 CLI 的收尾计数都用它。
        self.offline_gaps: list[dict] = []
        # Inject the token straight into pro_api() instead of calling
        # ts.set_token(): set_token() persists the credential to ~/tk.csv,
        # which raises PermissionError when HOME is read-only (sandboxes/CI).
        self.pro = None if mode == MODE_OFFLINE else self._new_pro_api()
        self._store = {}  # {key: pd.DataFrame} for derived metrics computation
        self._yf_available = _yf_available
        self._cache_dir = os.path.join("output", ".collector_cache")
        self._fy_end_month: int = 12  # default: calendar year
        self._currency: str = "CNY"
        # Broker API support: route calls through custom URL + enable VIP endpoints
        api_url = get_api_url()
        self._vip_mode = bool(api_url)
        if api_url and self.pro is not None:
            self._apply_broker_hacks()

        if store is False:
            self.store = None
        elif store is None:
            self.store = DataStore(resolve_archive_root(), framework_version=framework_version,
                                   clock=clock)
        else:
            self.store = store
        self._access = DataAccess(
            self.store, client=self, token=token, mode=mode,
            rate_limit_seconds=rate_limit_seconds, retry_delay=retry_delay,
            batch_id=batch_id, clock=clock,
        )

    def _new_pro_api(self):
        """Build a Tushare pro client, passing the token in-process.

        ``ts.set_token()`` writes the credential to ``~/tk.csv``; that side
        effect fails with ``PermissionError`` when HOME is read-only, so the
        token is handed to ``ts.pro_api()`` directly instead (AC-1.2). When no
        token is supplied, tushare resolves it itself — still without writing
        anything to disk.
        """
        if not self.token:
            print(
                "⚠️ No Tushare token provided (--token / TUSHARE_TOKEN); "
                "falling back to tushare's own token lookup "
                "(no ~/tk.csv is written).",
                file=sys.stderr,
            )
        return ts.pro_api(self.token, timeout=30)

    def _apply_broker_hacks(self) -> None:
        """把 broker 的 token / URL 打到当前 pro 客户端上（连接重建后要重打一次）。"""

        api_url = get_api_url()
        if api_url and self.pro is not None:
            self.pro._DataApi__token = self.token
            self.pro._DataApi__http_url = api_url

    def _safe_call(self, api_name: str, **kwargs) -> pd.DataFrame:
        """取数收口点：**转调** `datalayer.access.DataAccess`（`REQ-011`）。

        重试、限流、VIP 路由与仓的读写都在 `DataAccess` 里；这里只做两件事：
        ① 保留既有签名与异常语义（重试耗尽仍是 `RuntimeError`，错误原文不变）；
        ② 离线模式下仓里没有的记录返回空表并记账——既有 `get_*` 方法本来就是
        按「空表 = 数据缺失」渲染的，于是**换数据源不换模具**（`AC-5`）。
        """
        try:
            return self._access.call(api_name, **kwargs)
        except DataMissing as exc:
            self.offline_gaps.append({
                "dataset": exc.dataset,
                "period": str(exc.params.get("period") or "latest"),
                "params": exc.params,
            })
            return pd.DataFrame()

    def _cached_basic_call(self, api_name: str, **kwargs) -> pd.DataFrame:
        """基本信息调用：缓存语义交给仓（`REQ-011` 的 `AC-8` —— 旧文件缓存停写）。

        原来这里是 `output/.collector_cache/` 的 7 天 TTL 文件缓存；TTL 会把
        「过期」和「要重新花钱」混为一谈（`DATA_LAYER_PLAN` D4）。现在
        「已拉过就不再联网」由仓的唯一键去重保证，旧缓存目录**只读、不再写**。
        """
        return self._safe_call(api_name, **kwargs)

    def _cached_us_daily(self, ts_code: str = None) -> pd.DataFrame:
        """美股全市场日线：一次调用取回，整体作为一条记录进仓（不拆成 6000 条）。"""

        df = self._safe_call("us_daily", limit=6000,
                             fields="ts_code,trade_date,open,high,low,close,"
                                    "vol,amount,pe,pb,total_mv")
        if ts_code and not df.empty:
            df = df[df["ts_code"] == ts_code]
        return df


def parse_args():
    parser = argparse.ArgumentParser(
        description="Collect financial data from Tushare Pro API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --code 600887.SH
  %(prog)s --code 600887 --output output/data_pack_market.md
  %(prog)s --code 00700.HK --extra-fields balancesheet.defer_tax_assets
        """,
    )
    parser.add_argument(
        "--code",
        required=True,
        help="Stock code (e.g., 600887.SH, 000858.SZ, 00700.HK, or plain digits)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Tushare API token (defaults to TUSHARE_TOKEN env var)",
    )
    parser.add_argument(
        "--output",
        default="output/data_pack_market.md",
        help="Output file path (default: output/data_pack_market.md)",
    )
    parser.add_argument(
        "--extra-fields",
        nargs="*",
        help="Additional fields to fetch (format: endpoint.field_name)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print parsed arguments and exit without calling API",
    )
    parser.add_argument(
        "--refresh-market",
        action="store_true",
        help="Only refresh market-sensitive sections (§1/§2/§11/§14) in existing data pack",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Validate and normalize stock code
    try:
        ts_code = validate_stock_code(args.code)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print("=== Dry Run ===")
        print(f"  Stock code: {args.code} -> {ts_code}")
        print(f"  Token: {'provided via --token' if args.token else 'from TUSHARE_TOKEN env'}")
        print(f"  Output: {args.output}")
        print(f"  Extra fields: {args.extra_fields or 'none'}")
        return

    # Get token
    token = args.token or get_token()
    client = TushareClient(token)

    if args.refresh_market:
        from pathlib import Path
        output_path = Path(args.output)
        if not output_path.exists():
            print(f"⚠️ {output_path} does not exist, falling back to full collection")
            print(f"Collecting data for {ts_code}...")
            data_pack = client.assemble_data_pack(ts_code)
        else:
            existing = output_path.read_text(encoding="utf-8")
            age_days = client._check_staleness(existing)
            if age_days > 7:
                print(f"⚠️ Data pack is {age_days} days old, falling back to full collection")
                print(f"Collecting data for {ts_code}...")
                data_pack = client.assemble_data_pack(ts_code)
            else:
                print(f"Refreshing market data for {ts_code} (data pack is {age_days} day(s) old)...")
                data_pack = client.refresh_market_sections(ts_code, existing)
    else:
        print(f"Collecting data for {ts_code}...")
        data_pack = client.assemble_data_pack(ts_code)

    # Handle extra fields
    if args.extra_fields:
        extra_lines = ["\n", format_header(2, "附加字段"), ""]
        for field_spec in args.extra_fields:
            parts = field_spec.split(".", 1)
            if len(parts) != 2:
                extra_lines.append(f"- 无效字段格式: {field_spec} (应为 endpoint.field_name)")
                continue
            endpoint, field_name = parts
            try:
                df = client._safe_call(endpoint, ts_code=ts_code, fields=f"ts_code,end_date,{field_name}")
                if not df.empty:
                    extra_lines.append(f"**{endpoint}.{field_name}**:")
                    extra_lines.append(df.to_markdown(index=False))
                    extra_lines.append("")
                else:
                    extra_lines.append(f"- {endpoint}.{field_name}: 无数据")
            except Exception as e:
                extra_lines.append(f"- {endpoint}.{field_name}: 获取失败 ({e})")
        data_pack += "\n".join(extra_lines)

    # Write output
    import os
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(data_pack)
    print(f"Output written to {args.output}")
    print(f"File size: {os.path.getsize(args.output):,} bytes")


if __name__ == "__main__":
    main()
