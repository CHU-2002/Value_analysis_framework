#!/usr/bin/env python3
"""Run one fixed report action through the local agent CLI (REQ-013).

Default: authenticated Codex ``exec``; Claude ``-p`` remains configurable.
Validate action, ticker, directory and executable before launch; no shell or
free-text prompt. Dry-run is read-only. Cancellation/timeout reap the process
group; a per-company flock also protects standalone CLI submissions.
Exit codes: 0 success, 2 invalid input, 3 unavailable CLI, 4 timeout, 5 busy;
other CLI exit codes are propagated without retries.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import threading
import sqlite3
import uuid
from datetime import datetime, timezone
import os
import signal
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from config import validate_stock_code
from agent_models import validate_model, validate_default

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NO_CLI = 3
EXIT_TIMEOUT = 4
EXIT_BUSY = 5

#: 动作枚举（**唯一**的可执行动作来源；`--action` 的 choices 直接用它）。
ACTION_CHOICES = ("update-analysis", "business-analysis", "value-analysis")

#: 动作 → agent CLI 命令行模板。模板里只有 `{ticker}` 一个占位符，
#: 它的值来自校验后的标的（见模块 docstring 的「三条边界」）。
ACTIONS = {
    "update-analysis": "/update-analysis {ticker}",
    "business-analysis": "/business-analysis {ticker}",
    "value-analysis": "/value-analysis {ticker}",
}

DEFAULT_CLI = "codex"
DEFAULT_TIMEOUT = 1800
DEFAULT_OUTPUT_ROOT = "output"

INSTALL_HINT = (
    "安装并登录 Codex（codex login），或 Claude Code 后重试；"
    "也可以用 --cli <可执行文件> 指定路径，或设置环境变量 AGENT_CLI。"
)
TICKER_HINT = "股票代码形如 600887.SH / 000858.SZ / 00700.HK / AAPL。"


def build_prompt(action: str, ticker: str) -> str:
    """动作 + 标的 → 固定模板的 prompt（`/<动作> <代码>`）。"""
    try:
        template = ACTIONS[action]
    except KeyError:
        raise ValueError(
            f"未知动作：{action!r}；可用动作：{', '.join(ACTION_CHOICES)}"
        ) from None
    return template.format(ticker=ticker)


def build_agent_argv(cli, action: str, ticker: str, backend="claude", company_dir=None, model=None, record_config=False) -> list:
    """Build the fixed non-interactive argv for the configured protocol."""
    model = validate_model(backend, model)
    intent = build_prompt(action, ticker)
    if backend == "codex":
        prompt = (f"执行固定分析动作：{intent}。\n"
                  f"读取 .claude/commands/{action}.md，将其中 $ARGUMENTS 替换为 {ticker}，"
                  "严格执行该文件引用的完整分析流程。只生成该公司分析产物，"
                  "不要修改程序、需求或测试。复用本机既有登录态与模型配置。"
                  "凭据只从环境变量读取，不得写入日志、报告或提交；"
                  "数据存档沿用 TURTLE_ARCHIVE_ROOT 环境变量。"
                  "报告实际执行情况、产出文件、耗时与消耗；遇到阻断明确说明，不得假报成功。")
        options = ["--model", model] if model else []
        if company_dir is not None:
            prompt += (f"公司目录固定为 {company_dir}；命令文档中的 output/公司目录统一替换为此目录，"
                       "不要访问另一份同代码公司的产物。")
            options += ["--add-dir", str(company_dir)]
        return [str(cli), "exec", "--sandbox", "workspace-write", "--json", *options, prompt]
    options = ["--model", model] if model else []
    if record_config:
        options += ["--output-format", "stream-json", "--verbose"]
        if company_dir is not None:
            options += ["--add-dir", str(company_dir)]
            intent += f"。公司产物目录固定为 {company_dir}，只生成这家公司产物；凭据不进入报告或日志。"
    return [str(cli), "-p", *options, intent]


def resolve_backend(cli_value, backend=None):
    value = backend or os.environ.get("AGENT_BACKEND") or "auto"
    if value == "auto":
        return "codex" if Path(cli_value).name in ("codex", "codex.js") else "claude"
    if value not in ("codex", "claude"):
        raise ValueError("AGENT_BACKEND 只能是 codex / claude / auto。")
    return value


def directory_code(ticker: str) -> str:
    """`600887.SH` → `600887`、`AAPL.US` → `AAPL`。

    与 `.claude/commands/*.md` 的 `{directory_code}` 推导一致：「只去掉最后的市场后缀」。
    """
    return ticker.rsplit(".", 1)[0]


def find_company_dirs(output_root, ticker: str) -> list:
    """`output/<directory_code>_*/` 里已存在的公司目录（按名字排序）。"""
    root = Path(output_root)
    if not root.is_dir():
        return []
    return sorted(path for path in root.glob(f"{directory_code(ticker)}_*") if path.is_dir())


def resolve_cli(value):
    """把 `--cli` / `AGENT_CLI` 的值解析成可执行文件路径；解析不到返回 None。

    带路径分隔符的值按**文件**判定（必须存在且可执行）；否则按 `PATH` 查找。
    """
    if not value:
        return None
    if value.startswith("~") or os.sep in value or (os.altsep and os.altsep in value):
        path = Path(value).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
        return None
    return shutil.which(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent_action.py",
        description="把「动作 + 标的」翻译成一次 agent CLI 的非交互调用（不经过 shell）。",
        epilog=(
            "示例：python scripts/agent_action.py --action update-analysis "
            "--ticker 600887.SH --dry-run"
        ),
    )
    parser.add_argument(
        "--action",
        required=True,
        choices=ACTION_CHOICES,
        help="要执行的 agent 动作（枚举，不接受自由文本）",
    )
    parser.add_argument(
        "--ticker",
        required=True,
        help="股票代码，如 600887.SH / 00700.HK / AAPL",
    )
    parser.add_argument(
        "--cli",
        default=None,
        help="agent CLI 可执行文件（默认取环境变量 AGENT_CLI，再默认 codex）",
    )
    parser.add_argument("--model", default=None, help="本次模型；default 沿用CLI，不修改全局配置")
    parser.add_argument("--require-report", action="store_true", help="GUI：退出0且缺少本次报告也视为未产出")
    parser.add_argument("--backend", choices=("auto", "codex", "claude"), default=None,
                        help="非交互协议；默认按可执行文件名识别，可用 AGENT_BACKEND 配置")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"单次调用的超时秒数（默认 {DEFAULT_TIMEOUT}）",
    )
    parser.add_argument(
        "--output-root",
        default=DEFAULT_OUTPUT_ROOT,
        help=f"公司目录所在的根（默认 {DEFAULT_OUTPUT_ROOT}；仅供测试/多工作区使用）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将执行的命令行与解析出的路径，不启动任何进程",
    )
    return parser


def _fail(message: str, hint: str = "") -> int:
    print(message, file=sys.stderr)
    if hint:
        print(f"提示：{hint}", file=sys.stderr)
    return EXIT_USAGE


def preflight(action, raw_ticker, output_root, cli_value=None, backend=None, model=None):
    """Read-only validation shared by CLI and GUI; never launches a process."""
    build_prompt(action, "")
    ticker = validate_stock_code(raw_ticker)
    directories = find_company_dirs(output_root, ticker)
    if not directories:
        raise ValueError(f"找不到 {ticker}：这家公司还没有公司目录；请先获取数据或建立首次分析。")
    if len(directories) != 1:
        raise ValueError("这家公司有多个公司目录；请先确认并合并目录后再执行。")
    directory = directories[0].resolve()
    if not directory.is_relative_to(Path(output_root).resolve()):
        raise ValueError("公司目录指向数据目录之外，不能执行。")
    validate_default(backend, model)
    protocol = resolve_backend(cli_value or DEFAULT_CLI, backend)
    validate_model(protocol, model)
    value = cli_value or (protocol if backend in ("codex", "claude") else os.environ.get("AGENT_CLI")) or DEFAULT_CLI
    cli = resolve_cli(value)
    if cli is None:
        raise FileNotFoundError("本机找不到 agent CLI，或它不可执行。" + INSTALL_HINT)
    return ticker, directory, build_agent_argv(
        cli, action, ticker, resolve_backend(value, backend), directory, model, bool(backend),
    )


def _execute(command, timeout, audit=None):
    """Cancellation and timeout reap the whole CLI process group."""
    process = subprocess.Popen(command, shell=False, start_new_session=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)

    def read_output(stream, output):
        for line in stream:
            print(line, end="", flush=True, file=output)
            if audit is not None:
                try:
                    event = json.loads(line)
                except ValueError:
                    event = {}
                if isinstance(event, dict):
                    if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
                        audit["thread_id"] = event["thread_id"]
                    value = event.get("model") if (event.get("type") in ("system", "session.started", "session_meta") or "modelUsage" in event) else None
                    if event.get("modelUsage"):
                        names = list(event["modelUsage"])
                        value = names[0] if len(names) == 1 else None
                    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
                        audit["actual_model"] = value
                        audit["actual_source"] = "CLI 结构化事件回报"
                match = re.match(r"^model:\s*([A-Za-z0-9_.:-]+)\s*$", line.strip())
                if match:
                    audit["actual_model"] = match[1]
                    audit["actual_source"] = "CLI model 启动回报"
        stream.close()

    readers = [threading.Thread(target=read_output, args=(process.stdout, sys.stdout), daemon=True),
               threading.Thread(target=read_output, args=(process.stderr, sys.stderr), daemon=True)]
    for reader in readers:
        reader.start()

    def stop(*_):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        # A child may outlive the CLI leader or ignore SIGTERM.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()

    def cancelled(signum, _frame):
        stop()
        raise SystemExit(128 + signum)

    previous = {sig: signal.signal(sig, cancelled) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop()
        raise
    finally:
        for reader in readers:
            reader.join(timeout=5)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _codex_recorded_model(thread_id):
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    for path in sorted(home.glob("state_*.sqlite"), reverse=True):
        try:
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1) as conn:
                row = conn.execute("SELECT model FROM threads WHERE id=?", (thread_id,)).fetchone()
            if row and isinstance(row[0], str) and re.fullmatch(r"[A-Za-z0-9_.:-]+", row[0]):
                return row[0]
        except sqlite3.Error:
            continue
    return None


def _report_hashes(base):
    result = {}
    for path in base.rglob("*"):
        if path.is_file() and not path.is_symlink() and path.suffix.lower() == ".md" and path.resolve().is_relative_to(base.resolve()):
            rel = path.relative_to(base).as_posix()
            if "report" in path.name.lower() or "报告" in path.name:
                result[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _action_reports(action, names):
    if action == "value-analysis":
        return [n for n in names if "价值分析报告" in n or "valuation_report" in n or "value_report" in n]
    return [n for n in names if "qualitative_report" in n or "business_analysis" in n or "商业" in n or "定性" in n]


def _missing_reports(action, names):
    missing = [] if _action_reports(action, names) else ["价值分析报告" if action == "value-analysis" else "商业质量报告"]
    if action == "update-analysis" and not any("change_report" in name or "变化报告" in name for name in names):
        missing.append("变化报告")
    return missing


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.timeout <= 0:
        return _fail("超时时间必须大于 0。")

    try:
        ticker, company_dir, command = preflight(
            args.action, args.ticker, args.output_root, args.cli, args.backend, args.model,
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_NO_CLI
    except ValueError as exc:
        return _fail(f"标的校验失败：{exc}", TICKER_HINT)

    print(f"动作：{args.action}")
    print(f"标的：{ticker}")
    print(f"公司目录：{company_dir}")
    print(f"命令行：{shlex.join(command)}", flush=True)

    if args.dry_run:
        print("（--dry-run：没有启动任何进程）")
        return EXIT_OK

    try:
        # Inherit stdout/stderr so the task runner can stream progress.
        lock_root = Path(args.output_root) / ".agent_locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        with (lock_root / f"{company_dir.name}.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("这家公司已有分析任务在执行，请等待结束或先取消。", file=sys.stderr)
                return EXIT_BUSY
            before = _report_hashes(company_dir)
            audit = {"id": uuid.uuid4().hex, "ticker": ticker, "action": args.action,
                     "requested_agent": resolve_backend(args.cli or DEFAULT_CLI, args.backend),
                     "requested_model": args.model or "default", "actual_model": "未知",
                     "actual_source": "CLI 未回报模型", "started_at": datetime.now(timezone.utc).isoformat()}
            code = _execute(command, args.timeout, audit)
            if audit["requested_agent"] == "codex" and audit.get("thread_id") and audit["actual_model"] == "未知":
                actual = _codex_recorded_model(audit["thread_id"])
                if actual:
                    audit.update(actual_model=actual, actual_source="CLI 本次 thread 运行记录（只读）")
            after = _report_hashes(company_dir)
            fresh = [name for name, digest in after.items() if before.get(name) != digest]
            audit.update(exit_code=code, reports=fresh, missing_reports=_missing_reports(args.action, fresh), finished_at=datetime.now(timezone.utc).isoformat())
            audit_root = Path(args.output_root) / ".agent_audit"
            audit_root.mkdir(parents=True, exist_ok=True)
            (audit_root / (audit["id"] + ".json")).write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
            print("AGENT_AUDIT " + json.dumps(audit, ensure_ascii=False), flush=True)
            if args.require_report and code == 0 and audit["missing_reports"]:
                print("报告未产出：CLI 退出 0，但缺少本次声明产物：" + "、".join(audit["missing_reports"]) + "。不会自动重跑。", flush=True)
                return 6
    except subprocess.TimeoutExpired:
        print(
            f"超时：agent CLI 在 {args.timeout} 秒内没有结束，已被终止。",
            file=sys.stderr,
        )
        print("提示：用 --timeout 放宽上限，或先在 agent CLI 里手动跑一次确认耗时。", file=sys.stderr)
        return EXIT_TIMEOUT
    except OSError as exc:  # 存在但起不来（权限/格式/被删）
        print(f"启动 agent CLI 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"提示：{INSTALL_HINT}", file=sys.stderr)
        return EXIT_NO_CLI

    if code != 0:
        print(
            f"agent CLI 以退出码 {code} 结束：本次动作没有成功，"
            "请展开日志确认原因，检查 CLI 登录态和公司数据后手动重新提交；本脚本不自动重试。",
            file=sys.stderr,
        )
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
