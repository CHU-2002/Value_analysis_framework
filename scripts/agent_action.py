#!/usr/bin/env python3
"""Run one fixed report action through the local agent CLI (REQ-013).

Default: authenticated Codex ``exec``; Claude ``-p`` remains configurable.
Validate action, ticker, directory and executable before launch; no shell or
free-text prompt. Dry-run is read-only. Cancellation/timeout reap the process
group; a per-action/company flock also protects standalone CLI submissions.
Exit codes: 0 success, 2 invalid input, 3 unavailable CLI, 4 timeout, 5 busy;
other CLI exit codes are propagated without retries.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import signal
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from config import validate_stock_code

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


def build_agent_argv(cli, action: str, ticker: str, backend="claude", company_dir=None) -> list:
    """Build the fixed non-interactive argv for the configured protocol."""
    intent = build_prompt(action, ticker)
    if backend == "codex":
        prompt = (f"执行固定分析动作：{intent}。\n"
                  f"读取 .claude/commands/{action}.md，将其中 $ARGUMENTS 替换为 {ticker}，"
                  "严格执行该文件引用的完整分析流程。只生成该公司分析产物，"
                  "不要修改程序、需求或测试。复用本机既有登录态与模型配置。"
                  "凭据只从环境变量读取，不得写入日志、报告或提交；"
                  "数据存档沿用 TURTLE_ARCHIVE_ROOT 环境变量。"
                  "报告实际执行情况、产出文件、耗时与消耗；遇到阻断明确说明，不得假报成功。")
        options = []
        if company_dir is not None:
            prompt += (f"公司目录固定为 {company_dir}；命令文档中的 output/公司目录统一替换为此目录，"
                       "不要访问另一份同代码公司的产物。")
            options = ["--add-dir", str(company_dir)]
        return [str(cli), "exec", "--sandbox", "workspace-write", "--json", *options, prompt]
    return [str(cli), "-p", intent]


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


def preflight(action, raw_ticker, output_root, cli_value=None, backend=None):
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
    value = cli_value or os.environ.get("AGENT_CLI") or DEFAULT_CLI
    cli = resolve_cli(value)
    if cli is None:
        raise FileNotFoundError("本机找不到 agent CLI，或它不可执行。" + INSTALL_HINT)
    return ticker, directory, build_agent_argv(
        cli, action, ticker, resolve_backend(value, backend), directory,
    )


def _execute(command, timeout):
    """Cancellation and timeout reap the whole CLI process group."""
    process = subprocess.Popen(command, shell=False, start_new_session=True)

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
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.timeout <= 0:
        return _fail("超时时间必须大于 0。")

    try:
        ticker, company_dir, command = preflight(
            args.action, args.ticker, args.output_root, args.cli, args.backend,
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
        with (lock_root / f"{ticker}_{args.action}.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("同一动作正在为这家公司执行，请等待结束或先取消。", file=sys.stderr)
                return EXIT_BUSY
            code = _execute(command, args.timeout)
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
