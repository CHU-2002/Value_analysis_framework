"""可版本化 A 股目录；只有 refresh 明确联网，查询始终使用保存的快照。"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter

from .access import MODE_OFFLINE, MODE_REFRESH
from .errors import UsageError
from .security import redact

EXCHANGES = {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}
FIELDS = "ts_code,symbol,name,area,industry,fullname,enname,cnspell,market,exchange,curr_type,list_status,list_date,delist_date,is_hs"
ORDINARY_STOCK_MARKETS = ("主板", "中小板", "创业板", "科创板", "北交所")


def classify_instrument(ticker, market):
    """Separate mainland A-share common stocks from exchange-listed CDRs.

    Tushare's stock_basic can report an SSE CDR in ``科创板``; the 689xxx
    SSE code segment identifies that instrument class more reliably.
    """
    ticker = str(ticker or "").upper()
    market = str(market or "")
    if market == "CDR" or (ticker.endswith(".SH") and ticker[:6].startswith("689")):
        return "境内上市CDR"
    if market in ORDINARY_STOCK_MARKETS:
        return "A股普通股"
    return ""


def annotate_instruments(companies):
    """Return plan rows with instrument types, deriving legacy snapshots safely."""
    return [{**company, "instrument_type": company.get("instrument_type") or classify_instrument(
        company.get("ticker"), (company.get("metadata") or {}).get("market"))} for company in companies]


class Directory:
    """独立于自选股的目录快照，SQLite additive migration 保留旧仓兼容性。"""

    def __init__(self, store):
        self.store = store

    def _connection(self):
        connection = self.store._connect()
        with self.store.transaction() as transaction:
            transaction.execute("CREATE TABLE IF NOT EXISTS directory_snapshot ("
                                "snapshot_id TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, "
                                "payload_json TEXT NOT NULL)")
        return connection

    def list(self):
        return [json.loads(row["payload_json"]) for row in self._connection().execute(
            "SELECT payload_json FROM directory_snapshot ORDER BY fetched_at DESC, rowid DESC")]

    snapshots = list

    def latest(self):
        row = self._connection().execute(
            "SELECT payload_json FROM directory_snapshot ORDER BY fetched_at DESC, rowid DESC LIMIT 1").fetchone()
        return json.loads(row["payload_json"]) if row else None

    def get(self, snapshot_id):
        row = self._connection().execute(
            "SELECT payload_json FROM directory_snapshot WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def refresh(self, client):
        access = client._access
        if access.mode == MODE_OFFLINE:
            raise UsageError("离线模式不能刷新目录")
        previous = access.mode
        companies, excluded, coverage, gaps = {}, [], {}, []
        access.mode = MODE_REFRESH
        try:
            for exchange, suffix in EXCHANGES.items():
                try:
                    frame = access.call("stock_basic", exchange=exchange, list_status="L", fields=FIELDS)
                    if frame is None or frame.empty:
                        raise ValueError("数据源未返回当前上市目录")
                    if not {"ts_code", "name", "market", "exchange", "list_status"}.issubset(frame.columns):
                        raise ValueError("数据源缺少代码、名称、板块、交易所或上市状态字段")
                    records = json.loads(frame.to_json(orient="records", force_ascii=False))
                    count = 0
                    uncertain = False
                    for row in records:
                        ticker = str(row.get("ts_code") or "").upper()
                        market = str(row.get("market") or "")
                        reason = None
                        if not ticker or not row.get("name"):
                            reason = "代码或名称缺失"
                            uncertain = True
                        elif ticker in companies:
                            reason = "重复代码"
                            uncertain = True
                        elif not row.get("curr_type"):
                            reason = "币种缺失，无法确认受支持的人民币证券"
                            uncertain = True
                        elif row.get("list_status") != "L":
                            reason = "非当前上市"
                        elif row.get("exchange") != exchange or not ticker.endswith("." + suffix):
                            reason = "交易所不匹配"
                            uncertain = True
                        elif str(row.get("curr_type") or "CNY") != "CNY":
                            reason = "非人民币证券"
                        elif not classify_instrument(ticker, market):
                            reason = "非支持证券类型或板块未知"
                            uncertain |= market not in ("B股", "基金", "ETF")
                        elif not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", ticker):
                            reason = "代码无效"
                            uncertain = True
                        elif (suffix == "SH" and not ticker.startswith("6")) or (suffix == "SZ" and not ticker.startswith(("0", "3"))):
                            reason = "非A股普通股代码"
                        if reason:
                            excluded.append({"ticker": ticker, "exchange": exchange, "reason": reason, "metadata": row})
                            continue
                        companies[ticker] = {"ticker": ticker, "display_name": row["name"], "market": suffix,
                                             "instrument_type": classify_instrument(ticker, market),
                                             "exchange": exchange, "list_status": "L", "metadata": row}
                        count += 1
                    if not count:
                        uncertain = True
                    reason = "目录字段存在不确定项或无受支持证券" if uncertain else ""
                    coverage[exchange] = {"status": "unknown" if uncertain else "confirmed", "count": count, "reason": reason}
                    if uncertain:
                        gaps.append(f"{exchange} 覆盖未确认：{reason}")
                except Exception as exc:  # noqa: BLE001 -- 按交易所保存缺口，禁止静默漏掉。
                    reason = redact(str(exc), [getattr(access, "token", "")])
                    coverage[exchange] = {"status": "unknown", "count": 0, "reason": reason}
                    gaps.append(f"{exchange} 覆盖未确认：{reason}")
        finally:
            access.mode = previous
        ordered = [companies[key] for key in sorted(companies)]
        digest = hashlib.sha256(json.dumps(ordered, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        snapshot = {"snapshot_id": uuid.uuid4().hex, "source": "Tushare stock_basic", "fetched_at": self.store._now(),
                    "content_sha256": digest, "companies": ordered, "count": len(ordered),
                    "instrument_type_counts": dict(Counter(row["instrument_type"] for row in ordered)),
                    "coverage": coverage,
                    "exclusions": {"count": len(excluded), "by_reason": dict(Counter(row["reason"] for row in excluded)),
                                   "items": excluded}, "gaps": gaps}
        self._connection()
        with self.store.transaction() as transaction:
            transaction.execute("INSERT INTO directory_snapshot VALUES (?, ?, ?)",
                                (snapshot["snapshot_id"], snapshot["fetched_at"], json.dumps(snapshot, ensure_ascii=False)))
        return snapshot

    def compare(self, old_id, new_id):
        old, new = self.get(old_id), self.get(new_id)
        if old is None or new is None:
            raise UsageError("目录快照不存在")
        before = {row["ticker"]: row for row in old["companies"]}
        after = {row["ticker"]: row for row in new["companies"]}
        return {"old_snapshot_id": old_id, "new_snapshot_id": new_id,
                "added": [after[key] for key in sorted(after.keys() - before.keys())],
                "removed": [before[key] for key in sorted(before.keys() - after.keys())],
                "changed": [{"before": before[key], "after": after[key]} for key in sorted(before.keys() & after.keys())
                            if before[key] != after[key]], "gaps": new["gaps"],
                "removal_interpretation": "目录减少不等同于确认退市；须同时核对覆盖缺口与源上市状态"}
