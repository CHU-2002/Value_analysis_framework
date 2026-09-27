"""任务运行器（AC-1.1~AC-1.4）：白名单命令 → 子进程（`shell=False`）→ 状态 / 日志 / 历史。

这是内核能力，不含任何具体业务：命令从**注册表**里取（`CommandSpec`），内核只负责
「校验 → 起进程 → 收日志 → 落历史」。所以给项目加一个按键仍然是插件的事（AC-9）。

刻意写下来的五条：

1. **提交立即返回**（AC-1.1）：命令在后台线程里跑，HTTP 不阻塞到命令结束；
2. **拒绝路径不启动任何子进程**（AC-1.3）：未知命令、未声明参数、缺必填、shell 元字符
   全部在 `Popen` 之前判掉——先建进程再校验等于把「拒绝」变成了「跑一下再拒」；
3. **并发上限**（`config.max_concurrent_jobs`）：超限 `TOO_MANY_JOBS`（429），不排队、不静默丢弃；
4. **日志环形缓冲**（`config.job_log_tail` 行）：跑很久的命令不能让内存无上界增长；
5. **落盘与响应都脱敏**（AC-1.4）：token 既不能进日志，也不能进历史文件与响应体。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..config import REPO_ROOT
from .errors import InvalidParam, JobNotFound, NotFound, TooManyJobs, UnknownCommand
from .security import collect_secrets, redact, reject_shell_metachars

RUNNING = "running"
FINISHED = "finished"
FAILED = "failed"
CANCELLED = "cancelled"
TERMINAL = (FINISHED, FAILED, CANCELLED)


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


class JobRunner:
    """任务运行器。`spec_lookup(command_id)` 取白名单命令（通常直接给注册表的方法）。"""

    def __init__(
        self,
        config,
        *,
        spec_lookup,
        history_dir=None,
        env=None,
        clock=None,
        popen=None,
        secrets=None,
    ):
        self.config = config
        self._spec_lookup = spec_lookup
        self.history_dir = (
            Path(history_dir) if history_dir else Path(config.output_root) / ".webui_jobs"
        )
        self._env = dict(os.environ if env is None else env)
        self._clock = clock or _utc_now
        self._popen = popen or subprocess.Popen
        self.secrets = collect_secrets(self._env) if secrets is None else tuple(secrets)
        self._jobs: dict = {}
        self._processes: dict = {}
        self._guard = threading.Lock()
        self._load_history()

    # ---------------------------------------------------------------- 对外

    def submit(self, command_id: str, params: dict | None = None) -> dict:
        """校验并提交任务；校验失败在**起进程之前**抛出。"""
        try:
            spec = self._spec_lookup(command_id)
        except NotFound as exc:
            raise UnknownCommand(
                f"没有这个按键：{command_id!r}",
                hint="按键清单见 /api/v1/commands。",
            ) from exc
        argv = build_argv(spec, dict(params or {}))
        with self._guard:
            running = sum(1 for job in self._jobs.values() if job.status == RUNNING)
            if running >= int(self.config.max_concurrent_jobs):
                raise TooManyJobs(
                    f"并发任务已达上限 {self.config.max_concurrent_jobs}",
                    hint="等一个任务结束后再提交。",
                )
            job = Job(
                id=f"job-{uuid.uuid4().hex[:12]}",
                command=spec.id,
                title=spec.title or spec.id,
                argv=argv,
                params=dict(params or {}),
                started_at=self._clock(),
                log=deque(maxlen=max(10, int(self.config.job_log_tail))),
            )
            self._jobs[job.id] = job
        self._write_history(job)
        threading.Thread(target=self._run, args=(job,), daemon=True, name=job.id).start()
        return self.get(job.id)

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

    def cancel(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFound(f"没有这个任务：{job_id!r}")
        if job.status == RUNNING:
            job.cancelled = True
            process = self._processes.get(job_id)
            if process is not None:
                try:
                    process.terminate()
                except OSError:  # pragma: no cover - 进程刚好自己退出了
                    pass
        return self._public(job)

    def shutdown(self) -> None:
        """停止所有在跑的任务（服务退出时调用，不留孤儿进程）。"""
        with self._guard:
            processes = list(self._processes.items())
        for job_id, process in processes:
            with self._guard:
                job = self._jobs.get(job_id)
                if job is not None:
                    job.cancelled = True
            try:
                process.terminate()
            except OSError:  # pragma: no cover
                pass

    # ---------------------------------------------------------------- 内部

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
            job.status = FAILED
            job.error = redact(f"{type(exc).__name__}: {exc}", self.secrets)
            job.exit_code = None
            job.finished_at = self._clock()
            self._write_history(job)
            return
        with self._guard:
            self._processes[job.id] = process
        try:
            for line in process.stdout:  # type: ignore[union-attr]
                job.log.append(redact(line.rstrip("\n"), self.secrets))
            code = process.wait()
            job.exit_code = code
            if job.cancelled:
                job.status = CANCELLED
            else:
                job.status = FINISHED if code == 0 else FAILED
        finally:
            job.finished_at = self._clock()
            with self._guard:
                self._processes.pop(job.id, None)
            self._write_history(job)

    def _public(self, job: Job) -> dict:
        """对外/落盘的脱敏副本：日志、参数、argv、错误原文都过一遍脱敏。"""
        return {
            "id": job.id,
            "command": job.command,
            "title": job.title,
            "argv": [redact(str(part), self.secrets) for part in job.argv],
            "params": {
                key: redact(str(value), self.secrets) for key, value in job.params.items()
            },
            "status": job.status,
            "exit_code": job.exit_code,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "log": [redact(line, self.secrets) for line in job.log],
            "error": redact(job.error, self.secrets),
        }

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
            if status == RUNNING:
                # 上次进程留下的 running 是**不可信**的：进程已随服务消失，不能继续显示「运行中」。
                status = FAILED
                payload["error"] = payload.get("error") or "服务重启时该任务仍在运行（已中断）"
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
                log=deque(
                    list(payload.get("log") or []),
                    maxlen=max(10, int(self.config.job_log_tail)),
                ),
            )
            self._jobs[job.id] = job


__all__ = ["JobRunner", "build_argv", "coerce_value", "RUNNING", "FINISHED", "FAILED", "CANCELLED"]
