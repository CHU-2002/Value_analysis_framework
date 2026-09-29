"""标的宇宙 = 自选股清单（`REQ-011` 的 `AC-1`）。

今天是「`output/` 下碰巧存在哪些目录」在决定维护哪些公司（`webui/plugins/companies.py`
的 `COMPANY_MARKERS`）。本模块把这一层显式化：**清单是唯一事实来源**，
增删改标的不需要动代码（不改常量、不改 `PROFILES`，只改仓里的 `universe` 表）。

清单字段：`ticker` / `display_name` / `market` / `enabled` / `tier`（所需数据档位）/ `note`。
"""

from __future__ import annotations

import re

from .config import archive_root  # noqa: F401  （对外转出，CLI 与 GUI 都用同一个默认值）
from .errors import UniverseError  # noqa: F401  （对外转出：CLI 与调用方共用同一套错误类型）
from .registry import BULK, PROFILES

MARKETS = ("SH", "SZ", "HK", "US")
_TICKER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def market_of(ticker: str) -> str:
    """从标的代码推市场：``600887.SH`` → ``SH``；无后缀的字母代码视为美股。"""

    text = str(ticker or "").strip().upper()
    if "." in text:
        suffix = text.rsplit(".", 1)[-1]
        if suffix in MARKETS:
            return suffix
        raise UniverseError(f"未知市场后缀：{ticker!r}（支持 {', '.join(MARKETS)}）")
    if text.isalpha():
        return "US"
    raise UniverseError(f"无法判断市场：{ticker!r}（请写全，如 600887.SH / 00700.HK / AAPL）")


def normalize_ticker(ticker: str) -> str:
    text = str(ticker or "").strip()
    if not text or not _TICKER_RE.match(text):
        raise UniverseError(f"非法标的代码：{ticker!r}")
    return text.upper() if "." in text or text.isalpha() else text


def validate_tier(tier: str) -> str:
    name = str(tier or "").strip() or BULK
    if name not in PROFILES:
        raise UniverseError(
            f"未知数据档位：{name!r}（可用：{', '.join(sorted(PROFILES))}）"
        )
    return name


class Universe:
    """清单的读写入口。所有方法都只碰本地仓，不联网。"""

    def __init__(self, store):
        self.store = store

    # ------------------------------------------------------------------ 写

    def add(self, ticker: str, display_name: str, market: str | None = None,
            enabled: bool = True, tier: str = BULK, note: str | None = None) -> dict:
        code = normalize_ticker(ticker)
        name = str(display_name or "").strip()
        if not name:
            raise UniverseError("清单条目必须带显示名（--name）")
        resolved_market = str(market or "").strip().upper() or market_of(code)
        if resolved_market not in MARKETS:
            raise UniverseError(f"未知市场：{market!r}（支持 {', '.join(MARKETS)}）")
        entry = {
            "ticker": code,
            "display_name": name,
            "market": resolved_market,
            "enabled": 1 if enabled else 0,
            "tier": validate_tier(tier),
            "note": note,
            "updated_at": self.store.clock().isoformat(),
        }
        existing = self.get(code)
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO universe (ticker, display_name, market, enabled, tier, note, updated_at)"
                " VALUES (:ticker, :display_name, :market, :enabled, :tier, :note, :updated_at)"
                " ON CONFLICT(ticker) DO UPDATE SET display_name = excluded.display_name,"
                " market = excluded.market, enabled = excluded.enabled, tier = excluded.tier,"
                " note = excluded.note, updated_at = excluded.updated_at",
                entry,
            )
        return {"entry": self.get(code), "created": existing is None}

    def update(self, ticker: str, **fields) -> dict:
        code = normalize_ticker(ticker)
        entry = self.get(code)
        if entry is None:
            raise UniverseError(f"清单里没有 {code}")
        allowed = {"display_name", "market", "enabled", "tier", "note"}
        unknown = set(fields) - allowed
        if unknown:
            raise UniverseError(f"清单不认这些字段：{', '.join(sorted(unknown))}")
        merged = dict(entry)
        merged.update({key: value for key, value in fields.items() if value is not None})
        return self.add(merged["ticker"], merged["display_name"], merged["market"],
                        bool(merged["enabled"]), merged["tier"], merged.get("note"))["entry"]

    def set_enabled(self, ticker: str, enabled: bool) -> dict:
        return self.update(ticker, enabled=enabled)

    def remove(self, ticker: str) -> bool:
        code = normalize_ticker(ticker)
        with self.store.transaction() as connection:
            removed = connection.execute("DELETE FROM universe WHERE ticker = ?", (code,)).rowcount
        return bool(removed)

    # ------------------------------------------------------------------ 读

    def get(self, ticker: str) -> dict | None:
        code = str(ticker or "").strip()
        row = self.store._connect().execute(
            "SELECT * FROM universe WHERE ticker = ?", (code,)).fetchone()
        return self._entry(row) if row is not None else None

    def entries(self, *, enabled_only: bool = False, tier: str | None = None) -> list[dict]:
        sql = "SELECT * FROM universe"
        clauses, values = [], []
        if enabled_only:
            clauses.append("enabled = 1")
        if tier:
            clauses.append("tier = ?")
            values.append(validate_tier(tier))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY market, ticker"
        return [self._entry(row) for row in self.store._connect().execute(sql, tuple(values))]

    def tickers(self, *, enabled_only: bool = True) -> list[str]:
        return [entry["ticker"] for entry in self.entries(enabled_only=enabled_only)]

    def tiers(self) -> dict[str, list[str]]:
        """启用标的按档位分组——目标枚举的输入。"""

        grouped: dict[str, list[str]] = {}
        for entry in self.entries(enabled_only=True):
            grouped.setdefault(entry["tier"], []).append(entry["ticker"])
        return grouped

    # ------------------------------------------------------------------ 兼容

    def suggest_from_output(self, output_root) -> list[dict]:
        """从既有 `output/` 目录**建议**一份清单（不自动写入）。

        清单是使用者的资产，自动生成会掩盖「哪些是真在维护的」这个判断，
        所以这里只返回建议，由人确认后再 `add`（`DATA_LAYER_PLAN` §5.1）。
        """

        import json
        from pathlib import Path

        root = Path(output_root).expanduser()
        suggestions = []
        if not root.is_dir():
            return suggestions
        for folder in sorted(path for path in root.iterdir() if path.is_dir()):
            record = folder / "record.json"
            ticker = ""
            if record.is_file():
                try:
                    payload = json.loads(record.read_text(encoding="utf-8"))
                    ticker = str((payload.get("subject") or {}).get("ticker", "")).strip()
                except (OSError, json.JSONDecodeError):
                    ticker = ""
            if not ticker and "_" not in folder.name:
                continue
            ticker = ticker or folder.name.split("_", 1)[0]
            try:
                market = market_of(ticker)
            except UniverseError:
                continue
            suggestions.append({
                "ticker": ticker,
                "display_name": folder.name.split("_", 1)[-1] or folder.name,
                "market": market,
                "enabled": True,
                "tier": BULK,
                "dir": folder.name,
            })
        return suggestions

    @staticmethod
    def _entry(row) -> dict:
        entry = {key: row[key] for key in row.keys()}
        entry["enabled"] = bool(entry["enabled"])
        return entry
