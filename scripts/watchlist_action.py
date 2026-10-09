"""REQ-014 的离线清单动作；GUI 与实跑共用这一个数据层入口。"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

try:
    from .datalayer.errors import UniverseError
    from .datalayer.store import DataStore
    from .datalayer.universe import Universe, market_of, normalize_ticker
except ImportError:  # 直接运行白名单脚本
    from datalayer.errors import UniverseError
    from datalayer.store import DataStore
    from datalayer.universe import Universe, market_of, normalize_ticker


def resolve_ticker(identifier: str) -> str:
    """规范代码、裸数字与公司目录别名归到同一个标的；全程离线。"""
    text = str(identifier or "").strip()
    if "--" in text or ".." in text or not re.fullmatch(r"[\w.\- ]+", text, re.UNICODE):
        raise UniverseError("请输入有效的公司代码")
    code = text.split("_", 1)[0].upper()
    if code.isdigit():
        if len(code) == 5:
            code += ".HK"
        elif len(code) == 6 and code[0] in "06923":
            code += ".SH" if code[0] in "69" else ".SZ"
        else:
            raise UniverseError("无法识别这个代码的市场，请填写完整公司代码")
    try:
        code = normalize_ticker(code)
        market_of(code)
    except UniverseError as exc:
        raise UniverseError("无法识别这个公司代码，请核对后重试") from exc
    return code


def clean_name(name: str) -> str:
    text = str(name or "").strip()
    if not text or len(text) > 80 or re.search(r"[/\\<>\n\r]|--|company_dir|output_dir|run_dir|\b(?:input|force|python)\b|\.venv|\bmake\s|record\.json", text, re.I):
        raise UniverseError("请输入有效的公司简称（最多 80 个字）")
    return text


def import_output(universe: Universe, output_root) -> dict:
    """导入配置的产物目录，逐项解释跳过原因；不更改已有清单条目。"""
    root = Path(output_root).resolve()
    if not root.is_dir():
        raise UniverseError("还没有既有公司产物，可以先添加公司")
    imported, skipped = [], []
    for folder in sorted(root.iterdir()):
        if folder.name.startswith(".") or not folder.is_dir():
            continue
        code, separator, name = folder.name.partition("_")
        reason = ""
        ticker = ""
        if folder.is_symlink():
            reason = "公司产物是链接，未导入"
        elif not separator or not name or not re.fullmatch(r"(?:[0-9]{5,6}(?:\.(?:SH|SZ|HK))?|[A-Z]{1,5}(?:[.\-][A-Z]{1,2})?)", code.upper()):
            reason = "公司名称不符合约定"
        elif not (folder / "record.json").is_file():
            reason = "缺少公司记录"
        elif (folder / "record.json").is_symlink():
            reason = "公司记录是链接，未导入"
        else:
            try:
                record = json.loads((folder / "record.json").read_text(encoding="utf-8"))
                subject = record.get("subject") if isinstance(record, dict) else None
                if not isinstance(subject, dict):
                    raise UniverseError("公司记录缺少公司信息")
                ticker = resolve_ticker(subject.get("ticker") or code)
                if ticker != resolve_ticker(code):
                    raise UniverseError("公司记录与公司名称的代码不一致")
                name = clean_name(subject.get("company") or name)
                if universe.get(ticker):
                    reason = "已在自选股清单里"
            except (OSError, ValueError, UniverseError):
                reason = "公司记录无法识别或与公司名称不一致"
        label = folder.name.replace("_", " ")
        # 目录名属于不可信输入；界面只展示安全的公司名称与原因。
        try:
            label = clean_name(label)
        except UniverseError:
            label = "无法识别的公司"
        if reason:
            skipped.append({"company": label, "reason": reason})
        else:
            universe.add(ticker, name)
            imported.append(ticker)
    return {"imported": len(imported), "skipped": len(skipped),
            "items": skipped, "tickers": imported,
            "total": len(universe.entries()),
            "message": f"导入 {len(imported)} 家、跳过 {len(skipped)} 家"}


def execute(operation: str, *, archive_root, output_root, ticker="", name="") -> dict:
    universe = Universe(DataStore(archive_root))
    if operation == "import":
        return import_output(universe, output_root)
    code = resolve_ticker(ticker)
    if operation == "add":
        label = clean_name(name)
        if universe.get(code):
            return {"message": "这家公司已在自选股清单里", "ticker": code, "created": False}
        result = universe.add(code, label)
        return {"message": f"已添加 {code.split('.')[0]} {label}", "ticker": code,
                "created": result["created"]}
    if operation == "remove":
        removed = universe.remove(code)
        return {"message": "已停止跟踪这家公司，所有产物均已保留" if removed else "这家公司已不在清单里",
                "ticker": code, "removed": removed}
    raise UniverseError("无法识别这个清单动作")


def build_parser():
    parser = argparse.ArgumentParser(description="离线维护自选股清单")
    parser.add_argument("--operation", required=True, choices=("add", "import", "remove"))
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--ticker", default="")
    parser.add_argument("--name", default="")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        result = execute(args.operation, archive_root=args.archive_root,
                         output_root=args.output_root, ticker=args.ticker, name=args.name)
    except (UniverseError, OSError) as exc:
        print(json.dumps({"message": "清单操作失败，请检查公司信息与存储是否可用",
                          "error": type(exc).__name__}, ensure_ascii=False))
        return 1
    print(json.dumps({"watchlist_result": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
