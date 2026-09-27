#!/usr/bin/env python3
"""AC-8 的浏览器实跑走查：一条命令跑完「点开每一页 + 点一个按键跑真实命令」并留证。

**为什么要有这个脚本。** `AC-8` 属「实跑」类判据：CI 全 mock，测不到「真实浏览器点开
每一页」。2026-09-27 的一次走查正是靠它抓到 **E1**——前端 `openPage` 漏转发「当前选择」，
服务端收到无 `company` 的请求，图表 / 报告 / 迭代记录三页在真实浏览器里**整页降级**，
而当时所有接口级用例都是绿的。把同一批动作固化成脚本，下次改动后可以直接重跑。

用法（控制台先用 `make gui` 起着）：

    .venv/bin/python scripts/gui_walkthrough.py --base http://127.0.0.1:8765

默认无头跑、落截图与观察记录到 `output/.webui_walkthrough/<UTC>/`（`output/` 已 gitignore，
且点号目录不会被公司的目录列表扫到）。`--headed` 会开一个**可见窗口**——
AC-8 的最终判定仍要人看着那个窗口点，本脚本只是把同一批动作自动化并留证。

退出码：0 = 全部检查通过；1 = 有页面降级 / 图表或时间线缺失 / 按键没跑成。

环境约束（写在这里省得下次再踩）：

* 浏览器要能用 `--remote-debugging-port` 起；**受限沙箱里无头启动会失败**
  （macOS 上 Edge 报 `sandbox initialization failed`），需要放宽权限再跑。
* 只用 Python 标准库与仓库既有依赖（AC-7）：CDP 的 WebSocket 是自己按 RFC 6455 写的，
  不引入 `websocket-client`、Playwright 之类的新依赖。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 只走直连：本机回环请求不该被 `HTTP_PROXY` 牵去绕一圈。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

#: 常见安装位置；`--browser` 或环境变量 `GUI_WALKTHROUGH_BROWSER` 可覆盖。
_BROWSER_CANDIDATES = (
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "microsoft-edge",
    "microsoft-edge-stable",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)


# --------------------------------------------------------------------------- 传输


def _http_json(url: str, *, method: str = "GET", timeout: float = 10.0):
    # 显式绕过代理：本机回环请求被 HTTP_PROXY 牵走会绕远甚至失败。
    request = urllib.request.Request(url, method=method)
    with _OPENER.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class _WebSocket:
    """够用就好的 WebSocket 客户端（RFC 6455）：只支持文本帧、掩码、分片与 ping。"""

    def __init__(self, url: str, timeout: float = 30.0):
        parts = urllib.parse.urlsplit(url)
        self._sock = socket.create_connection((parts.hostname, parts.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        # 刻意不发 Origin：带 Origin 时 Edge 会回 403，除非额外开 --remote-allow-origins。
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parts.hostname}:{parts.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(handshake.encode("ascii"))
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise RuntimeError("DevTools WebSocket 握手被对端关闭")
            head += chunk
        header, _, self._buffer = head.partition(b"\r\n\r\n")
        status = header.split(b"\r\n", 1)[0].decode("utf-8", "replace")
        if "101" not in status:
            raise RuntimeError(f"DevTools WebSocket 握手失败：{status}")

    # -- 收 ------------------------------------------------------------------

    def _read_exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise RuntimeError("DevTools WebSocket 连接已关闭")
            self._buffer += chunk
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def _frame(self, opcode: int, payload: bytes) -> bytes:
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", 0x80 | opcode, 0x80 | length)
        elif length < 65536:
            header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, length)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, length)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return header + mask + masked

    def recv(self) -> str:
        chunks = []
        while True:
            first, second = self._read_exact(2)
            fin, opcode = bool(first & 0x80), first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if second & 0x80 else b""
            payload = self._read_exact(length)
            if mask:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 0x9:  # ping → 必须回 pong，否则对端会掐
                self._sock.sendall(self._frame(0xA, payload))
                continue
            if opcode == 0xA:  # pong
                continue
            if opcode == 0x8:
                raise RuntimeError("DevTools WebSocket 被对端关闭")
            chunks.append(payload)
            if fin:
                return b"".join(chunks).decode("utf-8", "replace")

    # -- 发 ------------------------------------------------------------------

    def send(self, text: str) -> None:
        self._sock.sendall(self._frame(0x1, text.encode("utf-8")))


class DevTools:
    """CDP 的最小客户端：发命令、等回包、忽略事件。"""

    def __init__(self, ws_url: str):
        self._ws = _WebSocket(ws_url)
        self._seq = 0

    def send(self, method: str, **params):
        self._seq += 1
        self._ws.send(json.dumps({"id": self._seq, "method": method, "params": params}))
        while True:
            message = json.loads(self._ws.recv())
            if message.get("id") != self._seq:
                continue  # 事件（无 id）：走查不依赖任何事件
            if "error" in message:
                raise RuntimeError(f"{method} 失败：{message['error']}")
            return message.get("result", {})

    def evaluate(self, script: str):
        result = self.send(
            "Runtime.evaluate", expression=script, returnByValue=True, awaitPromise=True
        )
        outcome = result.get("result", {})
        if result.get("exceptionDetails"):
            raise RuntimeError(f"页面里这段脚本抛异常了：{outcome}")
        return outcome.get("value")

    def goto(self, url: str, *, settle: float = 2.5) -> None:
        self.send("Page.navigate", url=url)
        time.sleep(settle)

    def move(self, x: float, y: float) -> None:
        self.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y, button="none")

    def click(self, x: float, y: float) -> None:
        """真实鼠标按下/抬起（`Input` 域，等价人手的点击）。"""
        for kind in ("mousePressed", "mouseReleased"):
            self.send(
                "Input.dispatchMouseEvent",
                type=kind, x=x, y=y, button="left", buttons=1, clickCount=1,
            )

    def center_of(self, selector: str, index: int = 0):
        return self.evaluate(
            "(() => {"
            f" const nodes = [...document.querySelectorAll({json.dumps(selector)})];"
            f" const node = nodes[{index}];"
            " if (!node) return null;"
            " node.scrollIntoView({block: 'center'});"
            " const box = node.getBoundingClientRect();"
            " return {x: box.left + box.width / 2, y: box.top + box.height / 2};"
            "})()"
        )

    def click_selector(self, selector: str, index: int = 0) -> bool:
        point = self.center_of(selector, index)
        if not point:
            return False
        self.move(point["x"], point["y"])
        time.sleep(0.2)
        self.click(point["x"], point["y"])
        return True

    def type_into(self, selector: str, text: str) -> bool:
        """点中输入框后真实输入（`Input.insertText`），退回直接写 value。"""
        if not self.click_selector(selector):
            return False
        try:
            self.send("Input.insertText", text=text)
        except RuntimeError:
            self.evaluate(
                "(() => {"
                f" const node = document.querySelector({json.dumps(selector)});"
                f" node.value = {json.dumps(text)};"
                " node.dispatchEvent(new Event('input', {bubbles: true}));"
                "})()"
            )
        return True

    def screenshot(self, path: Path) -> int:
        data = self.send("Page.captureScreenshot", format="png").get("data", "")
        raw = base64.b64decode(data)
        path.write_bytes(raw)
        return len(raw)


# --------------------------------------------------------------------------- 走查


class Walkthrough:
    def __init__(self, devtools: DevTools, base: str, out_dir: Path, company: str):
        self.cdp = devtools
        self.base = base.rstrip("/")
        self.out = out_dir
        self.company = company
        self.notes: list[str] = []
        self.failures: list[str] = []
        self.pages: dict = {}

    # -- 记账 ----------------------------------------------------------------

    def note(self, text: str) -> None:
        print(text, flush=True)
        self.notes.append(text)

    def check(self, ok: bool, ok_text: str, bad_text: str) -> bool:
        if ok:
            self.note(f"  ✅ {ok_text}")
        else:
            self.note(f"  ❌ {bad_text}")
            self.failures.append(bad_text)
        return ok

    # -- 页面探针 ------------------------------------------------------------

    def panel_state(self) -> dict:
        return self.cdp.evaluate(
            """(() => {
              const panels = [...document.querySelectorAll('#panels .panel')];
              const cells = [...document.querySelectorAll('#panels td')];
              // B1 的特征是**表体被逐字符切开**：表头 3 列、表体一行 12 个单元格。
              // 所以判据是「表体单元格数 == 表头列数」，而不是「没有单字符单元格」——
              // 序号列 1/2/3 本来就该是单字符（这条走查第一版就误报过）。
              const ragged = [...document.querySelectorAll('#panels table')].map((table) => ({
                head: table.querySelectorAll('thead th').length,
                body: [...table.querySelectorAll('tbody tr')].map((row) => row.children.length),
              })).filter((item) => item.head && item.body.some((count) => count !== item.head));
              return {
                title: document.getElementById('page-title').textContent,
                ids: panels.map((p) => p.dataset.panelId),
                degraded: panels.filter((p) => p.querySelector('.panel-error,.panel-fallback'))
                                  .map((p) => p.dataset.panelId),
                canvases: document.querySelectorAll('#panels canvas').length,
                tables: document.querySelectorAll('#panels table').length,
                cells: cells.length,
                ragged: ragged,
                timeline: document.querySelectorAll('#panels .timeline-item').length,
                banner: (document.getElementById('banner').textContent || '').trim(),
                hash: location.hash,
              };
            })()"""
        )

    def open_page(self, page_id: str, query: str = "", *, settle: float = 3.0) -> dict:
        suffix = f"?{query}" if query else ""
        self.cdp.goto(f"{self.base}/#{page_id}{suffix}", settle=settle)
        state = self.panel_state()
        self.pages[page_id] = state
        where = f"#{page_id}{suffix}"
        self.note(
            f"[{state['title'] or page_id}] {where} 面板={state['ids']} "
            f"降级={state['degraded']} 表={state['tables']} 图={state['canvases']} "
            f"时间线={state['timeline']} 横幅={state['banner']!r}"
        )
        self.check(
            not state["degraded"],
            "无降级面板",
            f"{where} 有面板降级：{state['degraded']}（横幅：{state['banner']}）",
        )
        return state

    def shot(self, name: str) -> None:
        path = self.out / name
        size = self.cdp.screenshot(path)
        self.note(f"  截图 {name}（{size // 1024} KB）")

    # -- 步骤 ----------------------------------------------------------------

    def step_companies(self) -> None:
        self.note("① 公司页：应列出 output/ 下的公司")
        state = self.open_page("companies")
        self.check(state["tables"] >= 1 and state["cells"] > 0, "公司表有内容", "公司表是空的")
        self.shot("01-companies.png")

    def step_company_click_to_charts(self) -> None:
        self.note(f"② 真实点击公司「{self.company}」→ 图表页（走页面里的「当前选择」）")
        link = f"#panels a[href*='{self.company}']"
        if not self.check(
            self.cdp.click_selector(link),
            "点到了公司链接",
            f"公司页里没有指向 {self.company} 的链接",
        ):
            return
        time.sleep(3.0)
        state = self.panel_state()
        self.pages["charts"] = state
        self.note(
            f"[图表] hash={state['hash']} 面板={state['ids']} 图={state['canvases']} "
            f"降级={state['degraded']} 横幅={state['banner']!r}"
        )
        # AC-4：选择必须真的被转发到页面聚合请求上，否则这里就是整页降级（E1）。
        # hash 里的中文是百分号编码的，先还原再比。
        self.check(
            self.company in urllib.parse.unquote(state["hash"]),
            "hash 带上了当前公司",
            "点击后 hash 里没有公司",
        )
        self.check(
            not state["degraded"],
            "无降级面板（当前选择已转发到页面请求）",
            f"图表页有面板降级：{state['degraded']}（横幅：{state['banner']}）",
        )
        self.check(
            state["canvases"] >= 3,
            f"渲染出 {state['canvases']} 张图（AC-3 要求 ≥3 类）",
            f"只渲染出 {state['canvases']} 张图",
        )
        self.shot("02-charts.png")

    def step_chart_hover(self) -> None:
        self.note("③ 真实鼠标悬停第一张图：提示框应画到 canvas 上")
        canvas = self.cdp.center_of("#panels canvas")
        if not self.check(bool(canvas), "找到 canvas", "页面上没有 canvas，无法悬停"):
            return
        before = self.cdp.evaluate("document.querySelector('#panels canvas').toDataURL().length")
        self.cdp.move(canvas["x"], canvas["y"])
        time.sleep(1.0)
        after = self.cdp.evaluate("document.querySelector('#panels canvas').toDataURL().length")
        self.check(after != before, "悬停后 canvas 重绘（提示框出现）", "悬停后 canvas 没有变化")
        self.shot("03-charts-hover.png")

    def step_report(self) -> None:
        self.note("④ 报告页：Markdown 报告渲染成标题/列表/表格")
        state = self.open_page("report", f"company={urllib.parse.quote(self.company)}")
        self.note(f"  Markdown 单元格={state['cells']}")
        self.check(state["cells"] > 0, "报告里有表格单元格", "报告里没有渲染出表格")
        # B1 回归：表体曾被逐字符渲染成一堆单字符 <td>（列数对不上表头）。
        self.check(
            not state["ragged"],
            "每张表的表体列数与表头一致（B1 未回归）",
            f"有表格列数对不上表头（B1 回归）：{state['ragged']}",
        )
        self.shot("04-report.png")

    def step_runs(self) -> None:
        self.note("⑤ 迭代记录页：按时间倒序列出 run，标出当前生效 run")
        state = self.open_page("runs", f"company={urllib.parse.quote(self.company)}")
        self.check(state["timeline"] >= 2, f"时间线有 {state['timeline']} 条", "时间线不足 2 条")
        head = self.cdp.evaluate(
            "(() => { const item = document.querySelector('#panels .timeline-item');"
            " return item ? item.textContent.replace(/\\s+/g, ' ').trim().slice(0, 110) : ''; })()"
        )
        self.note(f"  最新一条：{head}")
        self.shot("05-runs.png")

    def step_command(self, command: str, params: dict, *, timeout: float) -> None:
        self.note(f"⑥ 按键页：真实点击「{command}」并跑一条真实命令")
        state = self.open_page("commands")
        buttons = self.cdp.evaluate("document.querySelectorAll('.command-button').length")
        self.check(buttons > 0, f"按键目录有 {buttons} 个按键", "按键目录是空的")

        before = self._job_ids()
        if not self.check(
            self.cdp.click_selector(f".command-button[data-command='{command}']"),
            f"点到了 {command}",
            f"按键列表里没有 {command}",
        ):
            return
        time.sleep(0.8)
        for name, value in params.items():
            self.check(
                self.cdp.type_into(f"[data-param='{name}']", value),
                f"填入 {name}={value}",
                f"表单里没有参数 {name}",
            )
        argv = self.cdp.evaluate(
            "(() => { const node = document.querySelector('.command-argv');"
            " return node ? node.textContent.trim() : ''; })()"
        )
        self.note(f"  表单回显：{argv}")
        self.shot("06-command-form.png")

        if not self.check(
            self.cdp.click_selector(".command-form button[type=submit]"),
            "点了执行",
            "找不到提交按钮",
        ):
            return
        job = self._wait_job(before, timeout=timeout)
        if not self.check(bool(job), "任务出现在任务列表里", f"{timeout:.0f}s 内没有新任务"):
            return
        exit_code = job.get("exit_code")
        self.note(f"  任务 {job['id']} 状态={job['status']} 退出码={exit_code}")
        self.note(f"  实际命令行：{' '.join(job.get('argv') or [])}")
        tail = "\n".join(job.get("log") or [])[-400:]
        self.note(f"  日志尾部：{tail!r}")
        self.check(job["status"] == "finished", "命令跑完（finished）", f"命令状态={job['status']}")
        self.check(exit_code == 0, "退出码 0", f"退出码={exit_code}")
        self.check(bool(job.get("log")), "日志有回显", "日志是空的")
        self.shot("07-command-result.png")

    def step_collect(self) -> None:
        self.note("⑦ 采集存档页：批次 / 缺口清单只读可看")
        self.open_page("collect")
        self.shot("08-collect.png")

    # -- 任务轮询 ------------------------------------------------------------

    def _job_ids(self) -> set:
        payload = _http_json(f"{self.base}/api/v1/jobs")
        return {job["id"] for job in payload["data"]["jobs"]}

    def _wait_job(self, before: set, *, timeout: float):
        deadline = time.time() + timeout
        newest = None
        while time.time() < deadline:
            payload = _http_json(f"{self.base}/api/v1/jobs")
            fresh = [job for job in payload["data"]["jobs"] if job["id"] not in before]
            if fresh:
                newest = fresh[0]
                if newest["status"] != "running":
                    return newest
            time.sleep(0.5)
        return newest


# --------------------------------------------------------------------------- 驱动


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _resolve_browser(explicit: str | None) -> str:
    for candidate in (explicit, os.environ.get("GUI_WALKTHROUGH_BROWSER")):
        if candidate:
            return candidate
    for candidate in _BROWSER_CANDIDATES:
        found = shutil.which(candidate) if "/" not in candidate else (
            candidate if Path(candidate).exists() else None
        )
        if found:
            return found
    raise SystemExit(
        "找不到 Edge/Chrome/Chromium。用 --browser 指定可执行文件，"
        "或设 GUI_WALKTHROUGH_BROWSER 环境变量。"
    )


def _wait_for_target(port: int, *, timeout: float = 30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            targets = _http_json(f"http://127.0.0.1:{port}/json/list")
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            time.sleep(0.4)
            continue
        pages = [item for item in targets if item.get("type") == "page"]
        if pages:
            return pages[0]
        time.sleep(0.4)
    return None


def _require_console(base: str) -> str:
    try:
        payload = _http_json(f"{base}/api/v1/healthz", timeout=5)
    except Exception as exc:  # noqa: BLE001（这里就是要给出可读的下一步）
        raise SystemExit(f"连不上控制台 {base}（{exc}）。先在另一个终端跑 `make gui`。") from exc
    return payload.get("data", {}).get("version", "?")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://127.0.0.1:8765", help="控制台地址")
    parser.add_argument("--company", default="600887_伊利", help="output/ 下的公司目录名")
    parser.add_argument("--out", default=None, help="证据目录（默认 output/.webui_walkthrough/<UTC>）")
    parser.add_argument("--browser", default=None, help="浏览器可执行文件")
    parser.add_argument("--debug-port", type=int, default=0, help="DevTools 端口（默认自动挑空闲端口）")
    parser.add_argument("--headed", action="store_true", help="开可见窗口（人工实跑用）")
    parser.add_argument("--command", default="runs_resolve", help="要点的按键（默认只读命令）")
    parser.add_argument(
        "--param", action="append", default=[],
        help="按键参数 key=value，可重复（默认 company_dir=output/<company>）",
    )
    parser.add_argument("--job-timeout", type=float, default=120.0, help="等任务结束的秒数")
    args = parser.parse_args(argv)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) if args.out else REPO_ROOT / "output" / ".webui_walkthrough" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    base = args.base.rstrip("/")
    version = _require_console(base)
    params = dict(item.split("=", 1) for item in args.param) or {
        "company_dir": f"output/{args.company}"
    }

    browser = _resolve_browser(args.browser)
    port = args.debug_port or _free_port()
    profile = out_dir / "browser-profile"
    print(f"控制台 {base}（框架版本 {version}）", flush=True)
    print(f"浏览器 {browser}", flush=True)
    print(f"证据目录 {out_dir}", flush=True)

    flags = ["--no-first-run", "--no-default-browser-check", "--disable-crash-reporter"]
    if not args.headed:
        flags += ["--headless=new", "--disable-gpu"]
    process = subprocess.Popen(
        [
            browser, *flags,
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*",
            "--window-size=1440,1000",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    walkthrough = None
    try:
        target = _wait_for_target(port)
        if not target:
            print("❌ 拿不到 DevTools 页面目标：浏览器可能起不来（受限沙箱里很常见）。", flush=True)
            print("   先把权限放宽，或改用 --headed 看浏览器报什么。", flush=True)
            return 1
        devtools = DevTools(target["webSocketDebuggerUrl"])
        devtools.send("Page.enable")
        devtools.send("Runtime.enable")
        print(f"已连上 DevTools（{target.get('title') or 'about:blank'}）", flush=True)

        walkthrough = Walkthrough(devtools, base, out_dir, args.company)
        walkthrough.step_companies()
        walkthrough.step_company_click_to_charts()
        walkthrough.step_chart_hover()
        walkthrough.step_report()
        walkthrough.step_runs()
        walkthrough.step_command(args.command, params, timeout=args.job_timeout)
        walkthrough.step_collect()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        time.sleep(0.5)
        subprocess.run(["pkill", "-f", f"user-data-dir={profile}"], check=False)
        shutil.rmtree(profile, ignore_errors=True)

    if walkthrough is None:
        return 1
    payload = {
        "base": base,
        "company": args.company,
        "framework_version": version,
        "browser": browser,
        "headed": args.headed,
        "command": args.command,
        "params": params,
        "generated_at": stamp,
        "failures": walkthrough.failures,
        "pages": walkthrough.pages,
        "notes": walkthrough.notes,
    }
    (out_dir / "observations.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "observations.md").write_text(
        "# AC-8 浏览器走查记录（CDP 驱动）\n\n"
        f"- 时间：{stamp}\n- 控制台：{base}（框架版本 {version}）\n"
        f"- 公司：{args.company}\n- 浏览器：{browser}"
        f"{'（可见窗口）' if args.headed else '（无头）'}\n"
        f"- 按键：{args.command} {params}\n"
        f"- 结论：{'全部检查通过' if not walkthrough.failures else '不通过'}\n\n"
        + "\n".join(f"- {line}" for line in walkthrough.notes)
        + "\n",
        encoding="utf-8",
    )

    print(flush=True)
    if walkthrough.failures:
        print(f"❌ 走查不通过：{len(walkthrough.failures)} 项", flush=True)
        for failure in walkthrough.failures:
            print(f"   - {failure}", flush=True)
        print(f"证据见 {out_dir}", flush=True)
        return 1
    print(f"✅ 走查全部检查通过；证据见 {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
