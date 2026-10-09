"""任务运行器（AC-1.1~AC-1.4 + REQ-012.2 的任务层）：

白名单命令 → 子进程（`shell=False`）→ 状态 / 日志 / 历史；
动作（`JobTypeSpec`）→ 有序步骤（既有按键 + 人机交接点）→ 进度 / 取消 / 重试 / 产物。

这是内核能力，不含任何具体业务：命令与动作都从**注册表**里取，内核只负责
「校验 → 排队 → 起进程 → 收日志 → 落历史」。所以给项目加一个按键、加一个动作
仍然是插件的事（AC-9）。

刻意写下来的九条：

1. **提交立即返回**（AC-1.1）：命令在后台线程里跑，HTTP 不阻塞到命令结束；
2. **拒绝路径不启动任何子进程**（AC-1.3）：未知命令、未声明参数、缺必填、shell 元字符、
   **动作里没写清的参数占位符**全部在 `Popen` 之前判掉——先建进程再校验等于把「拒绝」
   变成了「跑一下再拒」；
3. **并发上限**（`config.max_concurrent_jobs`）：超限**排队**（`AC-5` 要求任务中心能列出
   队列），队列也满了才 `TOO_MANY_QUEUED`（429）——不静默丢弃；
4. **日志环形缓冲**（`config.job_log_tail` 行）：跑很久的命令不能让内存无上界增长；
5. **落盘与响应都脱敏**（AC-1.4）：token 既不能进日志，也不能进历史文件与响应体；
6. **一个动作 = 一个任务**（`AC-2.4`）：多步在同一 job 内顺序执行，某步失败**不继续**
   后续步，并说清「第几步失败、前面落了什么、可以怎么重试」；
7. **人机交接不占并发槽、不轮询子进程**（`AC-2.4` 的修订条款）：走到交接点任务进
   `awaiting_agent`，线程不再空转；用户说「已跑完」后由服务端**校验该步声明的产物**
   （存在且比本步开始时新）才继续，校验不过就停在原地并说缺什么；
8. **失败给人话 + 原始日志两份**（`AC-5`）：`failure_summary` 是「发生了什么 + 怎么办」，
   `log` 一个字都不删；
9. **产出可点**（`AC-5`）：动作声明 `writes`，服务端在任务上解析成实际产物路径。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..config import REPO_ROOT
from .errors import (
    InvalidParam,
    JobNotFound,
    NotFound,
    TooManyJobs,
    UnknownCommand,
    WebUIError,
)
from .security import collect_secrets, redact, reject_shell_metachars

RUNNING = "running"
QUEUED = "queued"
AWAITING = "awaiting_agent"
FINISHED = "finished"
FAILED = "failed"
CANCELLED = "cancelled"
TERMINAL = (FINISHED, FAILED, CANCELLED)
#: 占着执行槽的状态：`AWAITING` 不算（`AC-2.4`：交接步**不占并发**）。
ACTIVE = (RUNNING,)

#: 交接点的默认等待上限（秒）：到点不算失败，只是提醒「还停在这一步」。
HANDOFF_TIMEOUT = 30 * 60
_HANDOFF_POLL = 1.0

#: `{…}` 占位符语法：`{ticker}` / `{enum:a|b}` / `{run_dir}/modules`。
_TOKEN_RE = re.compile(r"\{([^{}]*)\}")
#: 枚举占位符 `{enum:a|b|c}`：候选来自参数声明的 `choices`，默认取第一个。
_ENUM_RE = re.compile(r"\{enum:([^{}]*)\}")
#: 枚举占位符的**暂存槽**（NUL 包起来，正常文本里不会出现）。
_ENUM_SLOT_RE = re.compile("\x00(\\d+)\x00")

#: 已知的错误 → 「发生了什么 + 怎么办」。命中不了就给通用兜底（不硬猜原因）。
_FAILURE_RULES: tuple = (
    (re.compile(r"NO_TOKEN|未配置\s*Tushare\s*token|TUSHARE_TOKEN", re.I),
     "没有可用的数据源凭据",
     "在项目 .env 或环境变量里配置 TUSHARE_TOKEN 后重试。"),
    (re.compile(r"QUOTA_CONFIRM_REQUIRED|需要显式确认", re.I),
     "这一步会消耗调用配额，需要先确认",
     "回到动作入口，按提示确认调用量后再执行。"),
    (re.compile(r"BATCH_RUNNING|已有批次在运行", re.I),
     "已经有一个采集批次在跑",
     "等它结束，或在数据页恢复那个批次。"),
    (re.compile(r"no latest\.json pointer|run not found", re.I),
     "这家公司还没有可用的分析记录",
     "先跑一次「准备分析」，让这一步产出 run 记录后再继续。"),
    (re.compile(r"No such file or directory|FileNotFoundError", re.I),
     "某个约定的产物或目录不存在",
     "按上面的步骤说明补齐上一步的产物，然后重试这个任务。"),
    (re.compile(r"Permission denied", re.I),
     "没有读写该路径的权限",
     "检查文件权限，或换一个有权限的位置重试。"),
    (re.compile(r"ModuleNotFoundError|ImportError", re.I),
     "运行环境缺少某个 Python 模块",
     "在仓库的虚拟环境里安装依赖（requirements.txt）后重试。"),
    (re.compile(r"MemoryError|Killed", re.I),
     "进程内存不足被系统终止",
     "关掉其他任务，或缩小这次处理的范围后重试。"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def coerce_value(param, raw):
    """按声明把参数值转成目标类型；转不了就 422，不静默用默认值。"""
    if param.type == "int":
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise InvalidParam(f"参数 {param.name!r} 需要整数，实际收到 {raw!r}") from exc
    if param.type == "float":
        try:
            return float(raw)
        except (TypeError, ValueError) as exc:
            raise InvalidParam(f"参数 {param.name!r} 需要数字，实际收到 {raw!r}") from exc
    if param.type == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
        raise InvalidParam(f"参数 {param.name!r} 需要布尔值，实际收到 {raw!r}")
    if param.type == "list":
        if isinstance(raw, (list, tuple)):
            items = [str(item) for item in raw]
        else:
            items = [part for part in str(raw).replace(",", " ").split()]
        return [item for item in items if item.strip()]
    if param.type == "enum" and param.choices and raw not in param.choices:
        raise InvalidParam(
            f"参数 {param.name!r} 只能是 {list(param.choices)} 之一，实际收到 {raw!r}"
        )
    return raw


def build_argv(spec, params: dict) -> list:
    """把结构化参数拼成 argv。**这里是唯一的参数→命令行转换点**（不经过 shell）。

    未知参数、缺必填、类型不符、shell 元字符都在这里失败——调用方据此在起进程前收口。
    """
    declared = {param.name: param for param in spec.params}
    unknown = sorted(set(params or {}) - set(declared))
    if unknown:
        raise InvalidParam(
            f"按键 {spec.id!r} 不接受这些参数：{unknown}",
            hint=f"可用参数：{sorted(declared)}",
        )
    argv = [str(part) for part in spec.argv]
    if not argv:
        raise InvalidParam(f"按键 {spec.id!r} 没有可执行的命令")
    for param in spec.params:
        raw = (params or {}).get(param.name)
        if raw is None or raw == "" or raw == []:
            if param.required:
                raise InvalidParam(
                    f"缺少必填参数 {param.name!r}",
                    hint=f"按键 {spec.id!r} 需要该参数。",
                )
            continue
        value = coerce_value(param, raw)
        text = " ".join(str(item) for item in value) if isinstance(value, list) else str(value)
        reject_shell_metachars(param.name, text)
        flag = "--" + param.name.replace("_", "-")
        if param.type == "bool":
            if value:
                argv.append(flag)
        elif param.type == "list":
            for item in value:
                argv.extend([flag, str(item)])
        else:
            argv.extend([flag, str(value)])
    return argv


# --------------------------------------------------------------- 动作的步骤解析


def _substitute(text, tokens: dict, *, where: str, defer: frozenset = frozenset()) -> str:
    """把 `{占位符}` 换成已解析好的值。

    `defer` 里列出的占位符**留给执行期**再替换：它们的值由**前面的步骤**在运行时捕获
    （例如 `runs resolve` 的 stdout → `{run_dir}`）。提交时把它们判成错误是错的——
    那不是「写错了模板」，而是「还没轮到它」（实测踩到：`company.update_analysis`
    的第 4 步引用第 3 步捕获的 `{run_dir}`，提交直接 422）。
    """

    def replace(match):
        key = match.group(1)
        if not key:
            raise InvalidParam(
                f"{where} 里的 {match.group(0)!r} 是空占位符",
                hint="占位符要写明取哪个值，例如 {ticker} / {company_dir} / {run_dir}。",
            )
        if key not in tokens:
            if key in defer:
                raise _Deferred(match.group(0))
            raise InvalidParam(
                f"{where} 需要一个还没解析出来的值 {match.group(0)!r}",
                hint=f"可用的值：{sorted(tokens)}",
            )
        return str(tokens[key])

    value = text if isinstance(text, str) else str(text)
    # 枚举占位符 `{enum:a|b}` 先**摘出来**：候选来自参数声明（`Param.placeholder()`），
    # 取声明顺序的第一个。摘而不是就地替换，是因为替换出来的 `|` 会被后面的
    # 占位符替换当成新的分隔（实测踩到：`{run_dir}/inputs` 里混着枚举时整段解析失败）。
    enums: list = []

    def stash(match):
        enums.append(match.group(1).split("|")[0].strip())
        return f"\x00{len(enums) - 1}\x00"

    value = _ENUM_RE.sub(stash, value)
    try:
        for _ in range(8):  # 值里允许再嵌一层占位符（如 {run_dir} 由上一步捕获而来）
            replaced = _TOKEN_RE.sub(replace, value)
            if replaced == value:
                break
            value = replaced
    except _Deferred:
        return value      # 原样留着，执行到这一步时再替换（那时值已经有了）
    value = _ENUM_SLOT_RE.sub(lambda match: enums[int(match.group(1))], value)
    leftover = _TOKEN_RE.search(value)
    if leftover:
        raise InvalidParam(
            f"{where} 里还有没解析的占位符 {leftover.group(0)!r}",
            hint="动作的参数模板写出了无法解析的值。",
        )
    return value


class _Deferred(Exception):
    """内部信号：某个占位符要等**前面的步骤**跑完才有值，不在提交期判定。"""


def _capture_value(pattern: str, text: str, name: str) -> str:
    """从步骤输出里取一个值。取不到就是**步骤失败**，不猜、不静默。"""
    if not pattern:
        return text
    match = re.search(pattern, text or "", re.MULTILINE)
    if not match:
        raise InvalidParam(
            f"没能从这一步的输出里取到 {name!r}",
            hint=f"输出里没有匹配 {pattern!r} 的内容。",
        )
    if match.groups():
        return match.group(1).strip()
    return match.group(0).strip()


def _artifact_path(value: str, company_base: str) -> str:
    """把产物模板（`{run_dir}/inputs`）解析成绝对路径，供「产物在哪」提示与校验用。"""
    text = str(value or "")
    if text.startswith("{company_dir}"):
        tail = text[len("{company_dir}"):].lstrip("/")
        return str(Path(company_base) / tail) if tail else str(company_base)
    return text


# --------------------------------------------------------------- 任务记录

#: 单步的运行时记录（不进 `to_json`，只进 `steps` 展示）。
@dataclass
class StepRun:
    index: int
    title: str
    kind: str
    command: str = ""
    argv: list = field(default_factory=list)
    params: dict = field(default_factory=dict)
    status: str = "pending"          # pending|running|done|failed|awaiting|skipped
    exit_code: object = None
    started_at: str = ""
    finished_at: object = None
    log: deque = field(default_factory=lambda: deque(maxlen=200))
    #: 交接点专用：可复制的命令、说明、声明产物、开始时间（产物要比它新）
    handoff: dict = field(default_factory=dict)
    writes: tuple = ()


@dataclass
class Job:
    """一个任务的运行时记录（内存态；落盘用 `_public` 的脱敏副本）。"""

    id: str
    command: str
    title: str
    argv: list
    params: dict
    status: str = RUNNING
    exit_code: object = None
    started_at: str = ""
    finished_at: object = None
    error: str = ""
    log: deque = field(default_factory=lambda: deque(maxlen=200))
    cancelled: bool = False
    #: REQ-012.2：动作相关字段。`command` 形态的任务这些字段保持默认值（形状不变）。
    action: str = ""
    description: str = ""
    steps: list = field(default_factory=list)
    outputs: dict = field(default_factory=dict)
    danger: bool = False
    failure_summary: dict = field(default_factory=dict)
    handoff: dict = field(default_factory=dict)
    signal: object = None
    queued_at: object = None

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    @property
    def completed_steps(self) -> int:
        return sum(1 for step in self.steps if step.status in ("done", "failed"))


class JobRunner:
    """任务运行器。`spec_lookup(command_id)` 取白名单命令（通常直接给注册表的方法）。

    `job_lookup(action_id)` 可选：给了才认**动作**（`REQ-012.2`）；不给时行为与 `REQ-009.1`
    完全一致（只认按键）——既有调用方与测试因此一字不用改。
    """

    def __init__(
        self,
        config,
        *,
        spec_lookup,
        job_lookup=None,
        history_dir=None,
        env=None,
        clock=None,
        popen=None,
        secrets=None,
    ):
        self.config = config
        self._spec_lookup = spec_lookup
        self._job_lookup = job_lookup
        self.history_dir = (
            Path(history_dir) if history_dir else Path(config.output_root) / ".webui_jobs"
        )
        self._env = dict(os.environ if env is None else env)
        self._clock = clock or _utc_now
        self._popen = popen or subprocess.Popen
        self.secrets = collect_secrets(self._env) if secrets is None else tuple(secrets)
        #: `continue` 等校验结果的上限（秒）：够长到覆盖「查几次文件系统」，
        #: 又不至于让一次 HTTP 请求挂太久。
        self.continue_wait = float(getattr(config, "continue_wait", 1.0))
        self._jobs: dict = {}
        self._processes: dict = {}
        self._queue: deque = deque()
        self._guard = threading.RLock()
        self._wake = threading.Condition(self._guard)
        self._load_history()

    # ---------------------------------------------------------------- 对外

    def submit(self, command_id: str, params: dict | None = None) -> dict:
        """提交一个**按键**任务（`REQ-009.1` 的原路径，行为与形状不变）。"""
        try:
            spec = self._spec_lookup(command_id)
        except NotFound as exc:
            raise UnknownCommand(
                f"没有这个按键：{command_id!r}",
                hint="按键清单见 /api/v1/commands。",
            ) from exc
        argv = build_argv(spec, dict(params or {}))
        return self._enqueue(
            command=spec.id,
            title=spec.title or spec.id,
            argv=argv,
            params=dict(params or {}),
        )

    def submit_action(self, action_id: str, params: dict | None = None, *, context=None) -> dict:
        """提交一个**动作**：把步骤参数在起进程之前全部解析好（`AC-2.4`）。"""
        if self._job_lookup is None:
            raise UnknownCommand(
                f"没有这个动作：{action_id!r}",
                hint="这个进程没有装配动作注册表（job_lookup）。",
            )
        try:
            spec = self._job_lookup(action_id)
        except NotFound as exc:
            raise UnknownCommand(
                f"没有这个动作：{action_id!r}",
                hint="动作清单见 /api/v1/actions。",
            ) from exc
        values = {key: str(value) for key, value in dict(context or {}).items() if value is not None}
        steps, argv, outputs = self._resolve_steps(spec, dict(params or {}), values)
        # 上下文随任务一起留存：重试（`AC-5`）与「任务产出在哪」都要用它，
        # 而且它是**提交时**的解析结果，不该在执行过程中被改写。
        outputs["context"] = dict(values)
        return self._enqueue(
            command=spec.id,
            title=spec.title or spec.id,
            argv=argv,
            params=dict(params or {}),
            action=spec.id,
            description=spec.description,
            steps=steps,
            outputs=outputs,
            danger=bool(spec.danger),
        )

    def get(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFound(f"没有这个任务：{job_id!r}", hint="任务列表见 /api/v1/jobs。")
        return self._public(job)

    def list_jobs(self) -> list:
        return [
            self._public(job)
            for job in sorted(self._jobs.values(), key=lambda item: item.started_at, reverse=True)
        ]

    def retry(self, job_id: str) -> dict:
        """重试一个已结束的任务（`AC-5`）：按键原样重跑；动作按同参数重新编排。

        **重试 = 再提交一次**，不是在原记录上改状态——任务历史是审计记录，不改写。
        """
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFound(f"没有这个任务：{job_id!r}")
        if job.status not in TERMINAL:
            raise InvalidParam(
                f"任务 {job_id} 还在 {job.status}，不能重试",
                hint="先取消它，或等它结束。",
            )
        context = dict((job.outputs or {}).get("context")
                       or (job.handoff or {}).get("context") or {})
        if job.action:
            return self.submit_action(job.action, dict(job.params), context=context)
        return self.submit(job.command, dict(job.params))

    def cancel(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFound(f"没有这个任务：{job_id!r}")
        if job.status == QUEUED:
            with self._wake:
                try:
                    self._queue.remove(job.id)
                except ValueError:  # pragma: no cover - 竞态：刚好被放行
                    pass
                job.cancelled = True
                job.status = CANCELLED
                job.finished_at = self._clock()
            self._write_history(job)
            return self._public(job)
        if job.status == AWAITING:
            # 交接中取消：等于放弃这次交接，唤醒等待中的步骤线程。
            job.cancelled = True
            job.signal = "abandon"
            with self._wake:
                self._wake.notify_all()
            return self._public(job)
        if job.status == RUNNING:
            job.cancelled = True
            process = self._processes.get(job_id)
            if process is not None:
                try:
                    process.terminate()
                except OSError:  # pragma: no cover - 进程刚好自己退出了
                    pass
        return self._public(job)

    def continue_action(self, job_id: str, step_index=None) -> dict:
        """「我跑完了，继续」：交接步的产物**校验**（`AC-2.4`）在这里做，不通过就不继续。

        **返回校验之后的状态**（而不是发信号前的快照）：门② 第四/五/六轮都登记过
        「响应是校验前快照」——客户端拿到的 `handoff.missing` 永远是空数组，界面于是
        没法立刻说出「缺什么」。这里等一小会儿让步骤线程做完校验（成功则继续跑、失败则
        原地更新 `missing`），再把最新状态回给调用方；等不到就回当前状态（不阻塞请求）。
        """
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFound(f"没有这个任务：{job_id!r}")
        if job.status != AWAITING:
            raise InvalidParam(
                f"任务 {job_id} 现在不在等你操作（状态：{job.status}）",
                hint="只有停在交接步骤上的任务才需要这一步。",
            )
        with self._wake:
            job.signal = "continue"
            job.handoff = {**job.handoff, "attempt": int(job.handoff.get("attempt", 0)) + 1}
            self._wake.notify_all()
        # 最多等 `continue_wait` 秒看校验结果：校验很快（查几次文件系统），
        # 所以正常情况下这里就是「校验后」的状态。
        deadline = time.monotonic() + self.continue_wait
        with self._wake:
            while job.status == AWAITING and job.signal is not None and time.monotonic() < deadline:
                self._wake.wait(timeout=0.05)
        return self._public(job)

    def abandon_action(self, job_id: str) -> dict:
        """「不继续了」：任务转 `cancelled`，已完成步骤的产物保留。"""
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFound(f"没有这个任务：{job_id!r}")
        if job.status == AWAITING:
            job.cancelled = True
            with self._wake:
                job.signal = "abandon"
                self._wake.notify_all()
        return self._public(job)

    def queue(self) -> list:
        with self._guard:
            return [
                self._public(self._jobs[job_id])
                for job_id in self._queue
                if job_id in self._jobs
            ]

    def shutdown(self) -> None:
        """停止所有在跑的任务（服务退出时调用，不留孤儿进程）。"""
        with self._guard:
            processes = list(self._processes.items())
            queued = list(self._queue)
            self._queue.clear()
            self._wake.notify_all()
        for job_id in queued:
            job = self._jobs.get(job_id)
            if job is not None:
                job.status = CANCELLED
                job.cancelled = True
        for job_id, process in processes:
            with self._guard:
                job = self._jobs.get(job_id)
                if job is not None:
                    job.cancelled = True
            try:
                process.terminate()
            except OSError:  # pragma: no cover
                pass

    # ---------------------------------------------------------------- 对外/落盘副本

    def _public(self, job: Job) -> dict:
        """对外/落盘的脱敏副本：日志、参数、argv、错误原文都过一遍脱敏。

        形状对**所有**任务都稳定：`REQ-009.1` 的按键任务只是动作字段取默认值
        （`action` 为空、`steps` 为空列表），既有断言因此一字不用改。
        """
        return {
            "id": job.id,
            "command": job.command,
            "action": job.action,
            "title": job.title,
            "description": job.description,
            "argv": [redact(str(part), self.secrets) for part in job.argv],
            "params": {
                key: redact(str(value), self.secrets) for key, value in job.params.items()
            },
            "status": job.status,
            "exit_code": job.exit_code,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "queued_at": job.queued_at,
            "danger": bool(job.danger),
            "log": [redact(line, self.secrets) for line in job.log],
            "error": redact(job.error, self.secrets),
            "failure_summary": dict(job.failure_summary or {}),
            "progress": {
                "total": job.total_steps,
                "completed": job.completed_steps,
                "current": int((job.handoff or {}).get("step", -1)) if job.steps else 0,
                "title": str((job.handoff or {}).get("title", "")),
            },
            "steps": [self._step_public(step) for step in job.steps],
            "handoff": self._handoff_public(job),
            "outputs": self._outputs_public(job),
        }

    def _step_public(self, step: StepRun) -> dict:
        return {
            "index": step.index,
            "title": step.title,
            "kind": step.kind,
            "command": step.command,
            "status": step.status,
            "exit_code": step.exit_code,
            "started_at": step.started_at,
            "finished_at": step.finished_at,
            "argv": [redact(str(part), self.secrets) for part in step.argv],
            "log": [redact(line, self.secrets) for line in step.log],
            "writes": [redact(str(item), self.secrets) for item in step.writes],
        }

    def _handoff_public(self, job: Job) -> dict:
        """交接信息（`AC-2.4`）：可复制的命令、已解析路径、要校验的产物、缺什么。

        按键任务没有步骤，但**仍可能有上下文**（工作台要靠它把「上次失败的公司」认出来），
        所以没有步骤时只回 `paths`，不编造交接字段。
        """
        if not job.steps:
            # 按键任务没有步骤，但**仍可能有上下文**（工作台要靠它把「上次失败的公司」认出来）：
            # 上下文既可能在 `context` 里（内存态），也可能在已落盘的 `paths` 里（重启后）。
            context = (job.handoff or {}).get("context") or (job.handoff or {}).get("paths") or {}
            if not context:
                return {}
            return {"paths": {
                key: redact(str(value), self.secrets)
                for key, value in context.items()
                if key in ("company_dir", "run_dir", "ticker", "primary_period") and value
            }}
        if not (job.handoff or {}).get("title"):
            return {}
        payload = {
            "step": job.handoff.get("step"),
            "total": len(job.steps),
            "title": job.handoff.get("title", ""),
            "slash": job.handoff.get("slash", ""),
            "hint": job.handoff.get("hint", ""),
            "expects": [redact(str(item), self.secrets)
                        for item in (job.handoff.get("expects") or [])],
            "writes": [redact(str(item), self.secrets)
                       for item in (job.handoff.get("writes") or [])],
            "missing": list(job.handoff.get("missing") or []),
            "last_check": job.handoff.get("last_check", ""),
            "awaiting": job.status == AWAITING,
        }
        # 已解析好的路径（可复制）：只给与用户操作相关的几个键。
        context = job.handoff.get("context") or {}
        payload["paths"] = {
            key: redact(str(value), self.secrets)
            for key, value in context.items()
            if key in ("company_dir", "run_dir", "ticker", "primary_period") and value
        }
        return payload

    def _outputs_public(self, job: Job) -> dict:
        payload = {}
        for key, value in (job.outputs or {}).items():
            if key == "context":
                # 上下文一律以 `{名字: 值}` 的形态对外（内存态与落盘态同形）。
                if isinstance(value, dict):
                    payload["context"] = {
                        name: redact(str(item), self.secrets) for name, item in value.items()
                    }
                else:
                    payload["context"] = {}
            elif key == "writes":
                # **两种形态都要认**：内存里是 `{声明模板: 路径}`，落盘的公开副本是
                # `[{declared, path, exists}]`。重启后 `_load_history` 会把公开副本读回来，
                # 只认第一种会在这里 KeyError/AttributeError——而它发生在**读列表**的路径上，
                # 表现为整个 /api/v1/jobs 500（实测踩到）。
                payload["writes"] = _writes_public(value, self.secrets)
            else:
                payload[key] = redact(str(value), self.secrets)
        return payload

    def _write_history(self, job: Job) -> None:
        """任务历史落盘（AC-1.4）：面板重启后仍能查看。写不了也不能让任务本身失败。"""
        try:
            self.history_dir.mkdir(parents=True, exist_ok=True)
            (self.history_dir / f"{job.id}.json").write_text(
                json.dumps(self._public(job), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:  # pragma: no cover - 只影响「重启后仍可查看」，不影响任务执行
            pass

    def _load_history(self) -> None:
        if not self.history_dir.is_dir():
            return
        for path in sorted(self.history_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or not payload.get("id"):
                continue
            status = payload.get("status", FAILED)
            if status in (RUNNING, QUEUED, AWAITING):
                # 上次进程留下的 running/queued/awaiting 是**不可信**的：进程已随服务消失，
                # 不能继续显示「运行中」，更不能显示「在等你操作」（那个交接已经不存在了）。
                #
                # **已有 error 也要追加「被中断」**：交接中的任务往往已经带着一句
                # 「产物还没就绪」，只留那一句会让人以为是自己没跑完，而不是服务重启了
                # （门② 第三轮登记的非阻断项）。
                status = FAILED
                interrupted = "服务重启时该任务仍在运行（已中断）"
                existing = str(payload.get("error") or "").strip()
                payload["error"] = (
                    f"{existing}；{interrupted}" if existing and interrupted not in existing
                    else (existing or interrupted)
                )
            steps = [
                StepRun(
                    index=int(item.get("index", 0)),
                    title=str(item.get("title", "")),
                    kind=str(item.get("kind", "command")),
                    command=str(item.get("command", "")),
                    argv=list(item.get("argv") or []),
                    status="failed" if item.get("status") in ("running", "awaiting")
                    else str(item.get("status", "pending")),
                    exit_code=item.get("exit_code"),
                    started_at=str(item.get("started_at", "")),
                    finished_at=item.get("finished_at"),
                    log=deque(
                        list(item.get("log") or []),
                        maxlen=max(10, int(self.config.job_log_tail)),
                    ),
                    writes=tuple(item.get("writes") or ()),
                )
                for item in (payload.get("steps") or [])
                if isinstance(item, dict)
            ]
            job = Job(
                id=str(payload["id"]),
                command=str(payload.get("command", "")),
                title=str(payload.get("title", "")),
                argv=list(payload.get("argv") or []),
                params=dict(payload.get("params") or {}),
                status=status,
                exit_code=payload.get("exit_code"),
                started_at=str(payload.get("started_at", "")),
                finished_at=payload.get("finished_at"),
                error=str(payload.get("error", "")),
                action=str(payload.get("action", "")),
                description=str(payload.get("description", "")),
                steps=steps,
                outputs=dict(payload.get("outputs") or {}),
                danger=bool(payload.get("danger")),
                failure_summary=dict(payload.get("failure_summary") or {}),
                # 交接信息（含已解析路径）也要恢复：否则重启后「这家公司上次失败过」
                # 这类**跨重启**的判据（工作台待办）会凭空丢掉。
                handoff=dict(payload.get("handoff") or {}),
                log=deque(
                    list(payload.get("log") or []),
                    maxlen=max(10, int(self.config.job_log_tail)),
                ),
            )
            self._jobs[job.id] = job

    # ---------------------------------------------------------------- 入队与调度

    def _enqueue(self, *, command, title, argv, params, action="", description="",
                 steps=(), outputs=None, danger=False) -> dict:
        with self._wake:
            running = sum(1 for item in self._jobs.values() if item.status in ACTIVE)
            if running >= int(self.config.max_concurrent_jobs):
                # 刻意不用 `or 20`：队列上限为 0 是**合法配置**（等于「不排队、超限即拒」），
                # `0 or 20` 会把它悄悄变成 20——实测就是这么漏掉的。
                limit = int(getattr(self.config, "max_queued_jobs", 20))
                if len(self._queue) >= limit:
                    raise TooManyJobs(
                        f"排队任务已达上限 {limit}（当前在跑 {running} 个）",
                        hint="等一些任务结束后再提交。",
                    )
                status, queued_at = QUEUED, self._clock()
            else:
                status, queued_at = RUNNING, None
            job = Job(
                id=f"job-{uuid.uuid4().hex[:12]}",
                command=command,
                title=title,
                argv=list(argv),
                params=dict(params),
                started_at=self._clock(),
                status=status,
                action=action,
                description=description,
                steps=list(steps),
                outputs=dict(outputs or {}),
                danger=bool(danger),
                queued_at=queued_at,
                log=deque(maxlen=max(10, int(self.config.job_log_tail))),
            )
            self._jobs[job.id] = job
            if status == QUEUED:
                self._queue.append(job.id)
        self._write_history(job)
        if status == RUNNING:
            self._launch(job)
        return self.get(job.id)

    def _launch(self, job: Job) -> None:
        target = self._run_chain if job.steps else self._run
        threading.Thread(target=target, args=(job,), daemon=True, name=job.id).start()

    def _release_next(self) -> None:
        """一个任务结束后放行队首（`AC-5` 的队列）。"""
        with self._wake:
            while self._queue:
                job_id = self._queue.popleft()
                job = self._jobs.get(job_id)
                if job is None or job.status != QUEUED:
                    continue
                job.status = RUNNING
                job.queued_at = None
                break
            else:
                return
        self._write_history(job)
        self._launch(job)

    # ---------------------------------------------------------------- 单命令执行

    def _run(self, job: Job) -> None:
        process = None
        try:
            process = self._popen(
                job.argv,
                shell=False,
                cwd=str(REPO_ROOT),
                env=self._env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:  # noqa: BLE001（起不来也是任务失败，不能让线程裸崩）
            self._finish(job, FAILED, None, f"{type(exc).__name__}: {exc}")
            return
        with self._guard:
            self._processes[job.id] = process
        try:
            for line in process.stdout:  # type: ignore[union-attr]
                job.log.append(redact(line.rstrip("\n"), self.secrets))
            code = process.wait()
            if job.cancelled:
                self._finish(job, CANCELLED, code, "")
            elif code == 0:
                self._finish(job, FINISHED, code, "")
            else:
                self._finish(job, FAILED, code, f"命令以退出码 {code} 结束")
        finally:
            with self._guard:
                self._processes.pop(job.id, None)

    # ---------------------------------------------------------------- 动作（多步）

    def _resolve_steps(self, spec, params: dict, values: dict):
        """把动作的步骤声明解析成可执行的 argv（**在起进程之前**做完所有校验）。"""
        steps: list = []
        outputs: dict = {}
        first_argv: list = []
        base = str(values.get("company_dir") or "")
        #: 前面的步骤会捕获出来的值（提交时还没有，执行到那一步才替换）。
        deferred: set = set()
        for index, declared in enumerate(spec.steps):
            kind = getattr(declared, "kind_of", None) or (
                "human" if declared.__class__.__name__ == "HumanStep" else "command"
            )
            where = f"动作 {spec.id!r} 第 {index + 1} 步"
            step_defer = frozenset(deferred)
            if kind == "human":
                expects = [_substitute(item, values, where=where, defer=step_defer)
                           for item in declared.expects]
                handoff = {
                    "step": index,
                    "title": declared.title,
                    "slash": _substitute(declared.slash, values, where=where, defer=step_defer),
                    "hint": declared.hint,
                    "expects": expects,
                    "expect_kind": declared.expect_kind,
                    "writes": [_artifact_path(item, base) for item in declared.writes],
                }
                steps.append(StepRun(index=index, title=declared.title, kind="human",
                                     writes=tuple(declared.writes), handoff=handoff))
                continue
            command = declared.command
            try:
                command_spec = self._spec_lookup(command)
            except NotFound as exc:
                raise UnknownCommand(
                    f"{where} 引用了不存在的按键 {command!r}",
                    hint="动作只能编排**已注册**的按键。",
                ) from exc
            bound = {}
            for param in command_spec.params:
                if param.name in declared.bind:
                    bound[param.name] = _substitute(
                        declared.bind[param.name], values, where=where, defer=step_defer
                    )
                    continue
                # 没写 bind 的参数：只有**同名上下文值**存在时才用 `{参数名}` 兜底。
                # 凭名字硬套会让「本来该用声明默认值的非必填参数」变成缺值报错
                # （实测：一个三个参数都不填的动作直接起不来）。必填参数没有同名值时
                # 由 `build_argv` 报「缺少必填参数」——那是准确的错误，不是这里猜。
                if param.name in values:
                    bound[param.name] = _substitute(param.token, values, where=where)
            argv = build_argv(command_spec, bound)
            if not first_argv:
                first_argv = argv
            step = StepRun(
                index=index,
                title=declared.title or command_spec.title or command,
                kind="command",
                command=command,
                argv=argv,
                params=bound,
                writes=tuple(declared.writes),
                log=deque(maxlen=max(10, int(self.config.job_log_tail))),
            )
            step.handoff = {"capture": dict(declared.capture)}
            steps.append(step)
            # 本步捕获出来的值，**后面的步骤**可以用（提交时先不判定）。
            deferred.update((declared.capture or {}).keys())
            for name in (declared.capture or {}):
                outputs[name] = ""
        return steps, first_argv, outputs

    def _run_chain(self, job: Job) -> None:
        values = dict((job.outputs or {}).get("context")
                      or (job.handoff or {}).get("context") or {})
        values.update({key: value for key, value in (job.outputs or {}).items()
                       if key != "context"})
        for step in job.steps:
            if job.cancelled:
                self._finish(job, CANCELLED, job.exit_code, "")
                return
            if step.kind == "human":
                if not self._await_human(job, step, values):
                    self._finish(
                        job, CANCELLED if job.cancelled else FAILED, job.exit_code,
                        "" if job.cancelled else job.error,
                    )
                    return
                continue
            step.status = "running"
            step.started_at = self._clock()
            with self._wake:
                job.handoff = {"step": step.index, "total": len(job.steps),
                               "title": step.title, "context": values}
            self._write_history(job)
            code, output = self._run_step(job, step)
            step.exit_code = code
            step.finished_at = self._clock()
            if job.cancelled:
                step.status = "skipped"
                self._finish(job, CANCELLED, code, "")
                return
            if code != 0:
                step.status = "failed"
                job.error = f"第 {step.index + 1} 步（{step.title}）以退出码 {code} 结束"
                self._finish(job, FAILED, code, job.error)
                return
            step.status = "done"
            try:
                values.update(self._capture(step, output, job))
            except WebUIError as exc:
                step.status = "failed"
                self._finish(job, FAILED, code, f"{exc.message}（{exc.hint}）")
                return
            values.update(self._collect_writes(step, values, job))
            with self._wake:
                job.outputs = dict((job.outputs or {}), **{
                    key: value for key, value in values.items()
                    if key in (job.outputs or {}) or key.endswith("_dir") or key.endswith("_path")
                })
                job.handoff = {"step": step.index, "total": len(job.steps),
                               "title": step.title, "context": values}
        self._finish(job, FINISHED, 0, "")

    def _run_step(self, job: Job, step: StepRun):
        """跑一步子进程，边收日志边累积输出（输出给 `capture` 用）。"""
        try:
            process = self._popen(
                step.argv,
                shell=False,
                cwd=str(REPO_ROOT),
                env=self._env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:  # noqa: BLE001
            line = redact(f"{type(exc).__name__}: {exc}", self.secrets)
            step.log.append(line)
            job.log.append(f"[{step.index + 1}/{len(job.steps)}] {line}")
            return None, line
        with self._guard:
            self._processes[job.id] = process
        chunks: list = []
        try:
            for line in process.stdout:  # type: ignore[union-attr]
                text = redact(line.rstrip("\n"), self.secrets)
                step.log.append(text)
                job.log.append(f"[{step.index + 1}/{len(job.steps)}] {text}")
                chunks.append(text)
            code = process.wait()
        finally:
            with self._guard:
                self._processes.pop(job.id, None)
        return (None if job.cancelled else code), "\n".join(chunks)

    def _capture(self, step: StepRun, output: str, job: Job) -> dict:
        """按步骤声明从输出里取值（如 `runs resolve` 打出来的 run 目录）。"""
        captured = {}
        for name, pattern in (step.handoff or {}).get("capture", {}).items():
            value = _capture_value(pattern, output, name)
            captured[name] = value
            job.outputs[name] = value
        return captured

    def _collect_writes(self, step: StepRun, values: dict, job: Job) -> dict:
        """把步骤声明的产物解析成绝对路径（`AC-5` 的「产出链接到对应产物」）。"""
        resolved = {}
        for template in step.writes:
            path = _artifact_path(
                _substitute(template, values, where=step.title), values.get("company_dir", "")
            )
            resolved[template] = path
        if resolved:
            writes = dict(job.outputs.get("writes") or {})
            writes.update(resolved)
            job.outputs["writes"] = writes
        return resolved

    def _await_human(self, job: Job, step: StepRun, values: dict) -> bool:
        """交接点：置 `awaiting_agent`、等「已跑完」、**校验产物**（`AC-2.4`）。

        返回 True 表示可以继续后续步骤；False 表示这一步没通过（任务收口为失败/取消）。
        等待期间**不占并发槽**：步骤线程阻塞在条件变量上，不轮询子进程。
        """
        handoff = dict(step.handoff)
        expects = list(handoff.get("expects") or [])
        started = _utc_now()
        # 产物新鲜度用**真实墙钟**判定：`_clock` 是可注入的（测试与历史文件用它算时间戳），
        # 拿它当 mtime 基准会让「比本步开始新」变成永远成立（判据形同虚设）。
        started_ts = datetime.now(timezone.utc).timestamp()
        stamps = {path: self._mtime(path) for path in expects}
        with self._wake:
            job.status = AWAITING
            job.signal = None
            job.handoff = {**handoff, "context": dict(values), "started_at": started,
                           "attempt": 0}
            step.status = "awaiting"
            step.handoff = {**handoff, "started_at": started}
            self._wake.notify_all()
        self._write_history(job)

        deadline = datetime.now(timezone.utc).timestamp() + HANDOFF_TIMEOUT
        while True:
            with self._wake:
                if job.signal is None and not job.cancelled:
                    self._wake.wait(timeout=_HANDOFF_POLL)
                signal = job.signal
                job.signal = None
            if job.cancelled or signal == "abandon":
                step.status = "skipped"
                return False
            if signal == "continue":
                missing = self._missing_outputs(expects, stamps, started_ts)
                if missing:
                    # 措辞要区分「不在」与「没更新」：两种都会被列进 missing，
                    # 统一说「还没就绪」比「还没检测到」准确（后者会让人以为文件不存在）。
                    job.error = ("这一步的产物还没就绪（不存在，或没有比这一步开始时更新）："
                                 + "、".join(missing))
                    with self._wake:
                        job.status = AWAITING
                        # 校验结果写进**任务**的 `handoff`（对外的就是它），不是步骤的——
                        # 写在步骤上会被「本步开始时的那份快照」原样盖回去，于是
                        # `/api/v1/jobs/{id}` 的 `missing` 永远是 `[]`，界面没法说清缺什么
                        # （门② 第六轮的阻断项，这里踩过一次）。
                        job.handoff = {**(job.handoff or {}), "missing": missing,
                                       "last_check": _utc_now(), "awaiting": True}
                        self._wake.notify_all()
                    self._write_history(job)
                    continue
                with self._wake:
                    job.status = RUNNING
                    job.handoff = {**job.handoff, "missing": [], "last_check": _utc_now()}
                step.status = "done"
                step.finished_at = self._clock()
                self._write_history(job)
                return True
            if datetime.now(timezone.utc).timestamp() > deadline:
                job.error = "这一步等了很久还没有结果（任务停在交接步骤上）"
                step.status = "failed"
                return False

    def _mtime(self, path: str):
        try:
            return Path(path).expanduser().stat().st_mtime
        except OSError:
            return None

    def _missing_outputs(self, expects: list, stamps: dict, started_ts: float) -> list:
        """产物必须**存在且比本步开始时新**——「没跑就点继续」这样挡得住。

        两个比较基准取更严的一个：本步开始之前的快照 mtime（若已存在）与真实墙钟。
        只看快照会让「一开始就存在但没动过」的文件蒙混过关（旧实现真的这么漏过）。
        """
        missing = []
        for pattern in expects:
            path = Path(pattern).expanduser()
            candidates = sorted(path.parent.glob(path.name)) if _has_glob(pattern) else [path]
            fresh = []
            for item in candidates:
                if not item.exists():
                    continue
                baseline = max(started_ts, stamps.get(pattern) or 0.0)
                if item.stat().st_mtime >= baseline:
                    fresh.append(item)
            if not fresh:
                missing.append(pattern)
        return missing

    # ---------------------------------------------------------------- 收口

    def _finish(self, job: Job, status: str, exit_code, error: str) -> None:
        job.status = status
        job.exit_code = exit_code
        if error:
            job.error = redact(error, self.secrets)
        job.finished_at = self._clock()
        if status == FAILED:
            job.failure_summary = self._summarise(job)
        with self._wake:
            job.signal = None
            self._wake.notify_all()
        self._write_history(job)
        self._release_next()

    def _summarise(self, job: Job) -> dict:
        """失败摘要：**发生了什么 + 怎么办**（`AC-5`）。原始日志一个字都不删。"""
        lines = [job.error or ""] + [
            line for step in job.steps for line in (step.log or [])
        ] + list(job.log or [])
        haystack = "\n".join(lines)
        for pattern, what, how in _FAILURE_RULES:
            if pattern.search(haystack):
                return {"what": what, "how": how, "step": _failed_step_label(job),
                        "exit_code": job.exit_code, "detail": job.error or ""}
        # 兜底：最后一行非空日志往往就是真正的报错。
        tail = next((line for line in reversed(lines) if line.strip()), "")
        return {
            "what": f"命令执行失败（退出码 {job.exit_code}）" if job.exit_code is not None
            else "命令没能启动",
            "how": "展开下面的原始日志看最后几行；修掉原因后点「重试」。",
            "step": _failed_step_label(job),
            "exit_code": job.exit_code,
            "detail": job.error or tail,
        }


def _writes_public(value, secrets) -> list:
    """产物的对外形态：`[{declared, path, exists}]`（两种输入形态都接受）。"""
    items: list = []
    if isinstance(value, dict):
        items = [{"declared": name, "path": path} for name, path in sorted(value.items())]
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict):
                items.append({"declared": str(item.get("declared", "")),
                              "path": str(item.get("path", ""))})
    return [
        {"declared": item["declared"],
         "path": redact(str(item["path"]), secrets),
         "exists": Path(str(item["path"])).exists()}
        for item in items
    ]


def _failed_step_label(job: Job) -> str:
    for step in job.steps:
        if step.status == "failed":
            return f"第 {step.index + 1} 步：{step.title}"
    return ""


def _has_glob(pattern: str) -> bool:
    return any(char in pattern for char in "*?[")


# --------------------------------------------------------------- 第 6 类注册点的默认执行器
#
# 动作（`JobTypeSpec`）的默认执行器是一个**声明值**，不是函数：真正的编排在
# `JobRunner._run_chain` 里（它需要进程、历史与交接能力，而这些是内核能力）。
# 保留 `registry.job_type(kind, runner)` 的签名与语义**一字不变**——`runner` 仍然是
# 「这个任务类型由谁跑」的声明；动作类型由内核执行，所以注册时可以省掉它。
CHAIN_RUNNER = "chain"


__all__ = [
    "JobRunner", "Job", "StepRun", "build_argv", "coerce_value", "CHAIN_RUNNER",
    "RUNNING", "QUEUED", "AWAITING", "FINISHED", "FAILED", "CANCELLED", "TERMINAL",
]
