"""离线重建派生产物：仓 → 既有装配 → `data_pack_market.md`（`REQ-011` 的 `AC-5`）。

**换数据源，不换模具**：装配代码（`tushare_modules/assembly.py` 与各 `get_*` 方法）
一个字不改——`data_pack_market.md` 的小节与表头是 `REQ-002`/`REQ-006` 与下游脚本的
输入契约，重写装配会让「重建」与「联网产出」出现两份实现、必然漂移。
重建 = 把 `DataAccess` 切到 `offline` 模式，让既有代码从仓里取数。

顺带修掉一个既有盲区（`DATA_LAYER_PLAN` §8.2）：完备度行按**仓里的实际情况**算，
`no_permission` 不再被算成成功（实跑证据：`yc_cb` 连续 5 次无权限而末尾仍写 `14/14`）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .errors import UsageError

# 既有产物末尾的完备度行。保留**原样的那一行**（下游与既有测试都在看它），
# 再补一行按仓统计的分解——「成功」的口径因此变了，格式没有变。
FOOTER_RE = re.compile(r"^\*共 \d+/\d+ 个数据板块成功获取\*$", re.MULTILINE)
DEFAULT_PACK_NAME = "data_pack_market.md"
DATE_COLUMNS = ("trade_date", "end_date", "ann_date", "cal_date")


def find_output_dir(output_root, ticker: str) -> Path:
    """定位某标的的产物目录（`output/<公司>/`）；找不到就按 ticker 新建一个。"""

    root = Path(output_root).expanduser()
    if root.is_dir():
        for folder in sorted(path for path in root.iterdir() if path.is_dir()):
            record = folder / "record.json"
            if record.is_file():
                try:
                    payload = json.loads(record.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    payload = {}
                if str((payload.get("subject") or {}).get("ticker", "")) == ticker:
                    return folder
            pack = folder / DEFAULT_PACK_NAME
            if pack.is_file():
                try:
                    head = pack.read_text(encoding="utf-8")[:400]
                except OSError:
                    continue
                if ticker in head:
                    return folder
    return root / ticker


def offline_client(store, *, clock=None):
    """离线模式的既有客户端：不建远程连接（**根本不该**有联网的可能）。"""

    from tushare_collector import TushareClient

    from .access import MODE_OFFLINE

    return TushareClient("", store=store, mode=MODE_OFFLINE, clock=clock)


def data_as_of(store, ticker: str) -> str:
    """该标的在仓里最新的一条抓取时间（「数据截至」与「报表生成时间」不再混为一谈）。"""

    stamps = [record["fetched_at"] for record in store.records(ticker=ticker)]
    return max(stamps) if stamps else ""


def apply_store_facts(markdown: str, store, ticker: str, missing) -> str:
    """把「完备度按仓的实际情况」与「重建缺口」写进产物。"""

    counts = store.completeness(ticker=ticker)["counts"]
    footer = (f"*共 {counts['complete']}/{counts['total']} 个数据板块成功获取*\n"
              f"*其中：有效 {counts['ok']} · 空 {counts['empty']} · "
              f"无权限 {counts['no_permission']} · 频率受限 {counts['rate_limited']} · "
              f"其他错误 {counts['error']}；数据截至 {data_as_of(store, ticker) or '未知'}*")
    gap_note = ""
    if missing:
        listed = "、".join(
            f"{item['dataset']}" + (f" {item['period']}" if item.get("period") not in (None, "latest")
                                    else "")
            for item in missing[:20]
        )
        more = f"（另 {len(missing) - 20} 条）" if len(missing) > 20 else ""
        gap_note = (f"\n\n> ⚠️ 重建缺口 {len(missing)} 条：{listed}{more}\n"
                    f"> 补齐：`make data-pull ARGS='--only-gaps --yes'`")
    if FOOTER_RE.search(markdown):
        return FOOTER_RE.sub(footer + gap_note, markdown, count=1)
    return markdown.rstrip("\n") + "\n\n" + footer + gap_note + "\n"


def rebuild(store, ticker: str, *, out_path=None, output_root="output", client=None,
            clock=None, today=None) -> dict:
    """从仓离线重建一份数据包；返回可打印的重建报告。"""

    if not ticker:
        raise UsageError("重建需要标的（--ticker）")
    client = client or offline_client(store, clock=clock)
    markdown = client.assemble_data_pack(ticker)
    missing = list(getattr(client, "offline_gaps", []) or [])
    markdown = apply_store_facts(markdown, store, ticker, missing)

    path = Path(out_path).expanduser() if out_path else \
        find_output_dir(output_root, ticker) / DEFAULT_PACK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")

    counts = store.completeness(ticker=ticker)["counts"]
    report = {
        "ticker": ticker,
        "out_path": str(path),
        "bytes": len(markdown.encode("utf-8")),
        "records": counts["total"],
        "complete": counts["complete"],
        "counts": counts,
        "missing": missing,
        "archived_hits": getattr(client, "_access", None).archived_hits
        if getattr(client, "_access", None) is not None else 0,
        "data_as_of": data_as_of(store, ticker),
    }
    return report


def format_report(report: dict) -> str:
    lines = [f"离线重建 {report['ticker']} → {report['out_path']}",
             f"  仓内记录 {report['records']} 条，完备 {report['complete']} 条，"
             f"命中存档 {report['archived_hits']} 次",
             f"  数据截至 {report['data_as_of'] or '未知'}"]
    if report["missing"]:
        lines.append(f"  缺口 {len(report['missing'])} 条（产物末尾已列出）")
        lines.append("  补齐：make data-pull ARGS='--only-gaps --yes'")
    return "\n".join(lines)
