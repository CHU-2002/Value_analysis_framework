#!/usr/bin/env python3
"""把「动作 + 标的」翻译成**一次** agent CLI 的非交互调用（REQ-013.1）。

这个脚本是 `REQ-013` 的确定性外壳：它自己**不做任何分析**，只负责
「校验输入 → 拼出固定模板的命令行 → 起一个子进程 → 透传退出码」。

## 三条刻意写死的边界

1. **不经过 shell**：`subprocess.run(argv, shell=False, timeout=…)`，argv 是列表。
   用户能影响的只有 `--ticker`，而且它先过 `config.validate_stock_code` 规范化、
   再过「本机是否真有这家公司的目录」的检查，最后只是被**插进模板字符串内部**当作
   一个普通参数值——它不会出现在命令行的语法位置，也就没有拆分/注入的空间。
2. **没有自由文本 prompt**：动作来自 `ACTION_CHOICES`（枚举），每个动作对应
   `ACTIONS` 里**一条固定模板**。要新能力就加一个枚举值 + 一条模板，
   不接受任何「让用户随便写一句提示词」的入口（同一动作的两次执行必须可比、可审计）。
3. **凭据由 agent CLI 自己管**：本脚本不读、不存、不回显任何模型/agent 凭据，
   也不接受凭据参数。认证完全取决于 agent CLI 自身的登录态与环境
   （`claude` 的 `-p/--print` 就是它文档里的非交互入口）。

## 退出码约定

| 码 | 含义 |
|----|------|
| `0` | 成功（agent CLI 退出码为 0） |
| `2` | 用法或校验错误：非法动作 / 非法标的 / 本机找不到该公司目录 |
| `3` | 找不到或无法执行 agent CLI |
| `4` | 超时（agent CLI 在 `--timeout` 秒内没有结束，已被终止） |
| 其它 | **透传** agent CLI 自己的退出码（非 0 即失败；本脚本不自动重试） |

`--dry-run` 只打印将执行的命令行与解析出的路径，**不启动任何进程**；
它同样会做 CLI 可用性检查（退出码 `3`），这样「配置对不对」在真正跑之前就能发现。

用法：

    python scripts/agent_action.py --action update-analysis --ticker 600887.SH
    python scripts/agent_action.py --action update-analysis --ticker 600887.SH --dry-run
    AGENT_CLI=/path/to/claude python scripts/agent_action.py --action value-analysis --ticker 00700.HK
"""

from __future__ import annotations

import argparse
import os
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

#: 动作枚举（**唯一**的可执行动作来源；`--action` 的 choices 直接用它）。
ACTION_CHOICES = ("update-analysis", "business-analysis", "value-analysis")

#: 动作 → agent CLI 命令行模板。模板里只有 `{ticker}` 一个占位符，
#: 它的值来自校验后的标的（见模块 docstring 的「三条边界」）。
ACTIONS = {
    "update-analysis": "/update-analysis {ticker}",
    "business-analysis": "/business-analysis {ticker}",
    "value-analysis": "/value-analysis {ticker}",
}

DEFAULT_CLI = "claude"
DEFAULT_TIMEOUT = 1800
DEFAULT_OUTPUT_ROOT = "output"

INSTALL_HINT = (
    "安装并登录 Claude Code（或其它 agent CLI）后重试；"
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


def build_agent_argv(cli, action: str, ticker: str) -> list:
    """agent CLI 的非交互调用形状：`[cli, "-p", prompt]`。

    `-p`/`--print` 是 `claude --help` 里写明的非交互入口。prompt 是**一个** argv 元素
    （含空格也不拆分）——这就是「不经过 shell」的可断言形式。
    """
    return [str(cli), "-p", build_prompt(action, ticker)]


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
            return str(path)
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
        help="agent CLI 可执行文件（默认取环境变量 AGENT_CLI，再默认 claude）",
    )
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


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    # ① 标的校验：规范化（校验失败 → 用法错误，且到这里为止没有任何进程）
    try:
        ticker = validate_stock_code(args.ticker)
    except ValueError as exc:
        return _fail(f"标的校验失败：{exc}", TICKER_HINT)

    # ② 本机是否有这家公司（与 .claude/commands 的 {directory_code} 推导一致）
    company_dirs = find_company_dirs(args.output_root, ticker)
    if not company_dirs:
        return _fail(
            f"本机找不到 {ticker} 的公司目录：{args.output_root}/"
            f"{directory_code(ticker)}_*/ 不存在。",
            "先跑一次取数/首次分析把该公司目录建出来，或用 --output-root 指定正确的根。",
        )
    company_dir = company_dirs[0]
    if len(company_dirs) > 1:
        print(
            f"警告：{ticker} 匹配到 {len(company_dirs)} 个公司目录，将使用 {company_dir}；"
            "请确认是否需要在继续之前把它们合并。",
            file=sys.stderr,
        )

    # ③ agent CLI 是否可用（认证由它自己管，这里只检查可执行）
    cli_value = args.cli or os.environ.get("AGENT_CLI") or DEFAULT_CLI
    cli = resolve_cli(cli_value)
    if cli is None:
        print(f"找不到 agent CLI：{cli_value!r}", file=sys.stderr)
        print(f"提示：{INSTALL_HINT}", file=sys.stderr)
        return EXIT_NO_CLI

    command = build_agent_argv(cli, args.action, ticker)
    print(f"动作：{args.action}")
    print(f"标的：{ticker}")
    print(f"公司目录：{company_dir}")
    print(f"命令行：{shlex.join(command)}", flush=True)

    if args.dry_run:
        print("（--dry-run：没有启动任何进程）")
        return EXIT_OK

    try:
        # 子进程继承本进程的 stdout/stderr：这样任务运行器能**逐行**收日志
        # （capture_output 会把输出憋到结束，长任务在界面上看不到进度）。
        completed = subprocess.run(command, shell=False, timeout=args.timeout)
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

    if completed.returncode != 0:
        print(
            f"agent CLI 以退出码 {completed.returncode} 结束：本次动作没有成功，"
            "本脚本不自动重试。",
            file=sys.stderr,
        )
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
