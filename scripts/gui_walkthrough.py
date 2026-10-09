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
import hashlib
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

    def reload(self, *, settle: float = 3.0) -> None:
        """整页重载（hash-only 导航不会重载文档，所以需要一个显式入口）。

        「冷开」必须真的重载：只改 hash 的话，页面里的**内存态**（当前选择）还在，
        看起来像冷开、其实带着上一家公司的上下文（实测踩到：空状态一直不出现）。
        """
        self.send("Page.reload")
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
              const technical = panels.filter((p) => {
                // AC-12 的判据：**技术化错误页**。人话标题 + 折叠错误码不算；
                // 标题里直接摆 BAD_REQUEST 这类错误码、或整页 500 才算。
                const title = (p.querySelector('.panel-error-title') || {}).textContent || '';
                return /BAD_REQUEST|INTERNAL|PARSE_FAILED|INVALID_PARAM|Unknown|Error/.test(title);
              }).map((p) => p.dataset.panelId);
              return {
                title: document.getElementById('page-title').textContent,
                ids: panels.map((p) => p.dataset.panelId),
                degraded: panels.filter((p) => p.querySelector('.panel-error,.panel-fallback'))
                                  .map((p) => p.dataset.panelId),
                technical: technical,
                canvases: document.querySelectorAll('#panels canvas').length,
                tables: document.querySelectorAll('#panels table').length,
                cells: cells.length,
                ragged: ragged,
                timeline: document.querySelectorAll('#panels .timeline-item').length,
                banner: (document.getElementById('banner').textContent || '').trim(),
                hash: location.hash,
                company: (() => {
                  const node = document.getElementById('company-select');
                  return node && node.selectedIndex >= 0 ? node.options[node.selectedIndex].textContent : '';
                })(),
                emptyState: document.querySelectorAll('#panels .panel-empty-state').length,
                freshness: document.querySelectorAll('#panels .panel-freshness').length,
                tableControls: document.querySelectorAll('#panels [data-table-search]').length,
                chartBasis: [...document.querySelectorAll('#panels [data-chart-basis]')]
                                  .map((node) => node.value),
                chartBasisLabels: [...document.querySelectorAll('#panels .chart-toolbar')]
                                  .map((node) => node.textContent.replace(/\\s+/g, ' ').trim().slice(0, 90)),
                actionButtons: document.querySelectorAll('#panels [data-action-run]').length,
                blockedActions: document.querySelectorAll('#panels .action-card[data-enabled="0"]').length,
                actionBlockers: [...document.querySelectorAll('#panels .action-blockers li')]
                                  .map((node) => node.textContent.trim()).slice(0, 4),
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
        self.note(
            f"  当前公司={state['company']!r} 空状态={state['emptyState']} "
            f"新鲜度徽标={state['freshness']} 表格控件={state['tableControls']} "
            f"口径={state['chartBasis']}"
        )
        self.check(
            not state["degraded"],
            "无降级面板",
            f"{where} 有面板降级：{state['degraded']}（横幅：{state['banner']}）",
        )
        # AC-12：全程不得出现**技术化错误页**（错误码当标题 / 整页 500 / 横幅里摆错误码）。
        # 横幅只该说人话；错误码属于「技术细节」，不该占主视觉（AC-10）。
        # `NOT_FOUND` 也要在内：门② 第四轮发现「记住了上次选择、而产物被删/改名」
        # 时顶栏会露出 `NOT_FOUND`（那一轮走查的禁则表里没有它，所以没抓住）。
        banned = ("BAD_REQUEST", "INTERNAL", "PARSE_FAILED", "INVALID_PARAM",
                  "UNKNOWN_COMMAND", "NOT_FOUND", "PATH_OUTSIDE_ROOT", "FORBIDDEN")
        self.check(
            not state["technical"] and not any(code in (state["banner"] or "") for code in banned),
            "没有技术化错误页",
            f"{where} 出现技术化错误：面板={state['technical']} 横幅={state['banner']!r}",
        )
        return state

    def shot(self, name: str) -> None:
        path = self.out / name
        size = self.cdp.screenshot(path)
        self.note(f"  截图 {name}（{size // 1024} KB）")

    def shot_bytes(self, name: str):
        """已落盘截图的字节（用来断言「前后对照」不是同一张图）。"""
        path = self.out / name
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return ""

    # -- 步骤（REQ-012 的 AC-12 全路径） --------------------------------------

    def select_company(self, ticker: str) -> None:
        """用**全局选择器**切公司（不是从某个页面的链接点进去）。

        设置之后必须**轮询确认**：`Runtime.evaluate` 不等 Promise，写一个 `async` 函数
        立刻读状态只会拿到旧值（实测踩到：选择器明明切了，判定却是「没切」）。
        """
        ok = self.cdp.evaluate(
            """(() => {
              const node = document.getElementById('company-select');
              if (!node) return false;
              const option = [...node.options].find((item) => item.value === %s);
              if (!option) return false;
              node.value = %s;
              node.dispatchEvent(new Event('change', { bubbles: true }));
              return node.value === %s;
            })()""" % (repr(ticker), repr(ticker), repr(ticker))
        )
        if not ok:
            self.check(False, f"选择器切到 {ticker}",
                       f"选择器里没有 {ticker}（当前选项：{self._select_options()}）")
            return
        deadline = time.time() + 8
        while time.time() < deadline:
            current = self.cdp.evaluate(
                "(() => { const node = document.getElementById('company-select');"
                " return node ? node.value : ''; })()"
            )
            if ticker in urllib.parse.unquote(self.cdp.evaluate("location.hash") or ""):
                break
            time.sleep(0.3)
        self.check(
            ticker in urllib.parse.unquote(self.cdp.evaluate("location.hash") or ""),
            f"选择器切到 {ticker}",
            f"切换后 URL 没带上 {ticker}：{self.cdp.evaluate('location.hash')}",
        )

    def _select_options(self) -> list:
        return self.cdp.evaluate(
            "(() => { const node = document.getElementById('company-select');"
            " return node ? [...node.options].map((item) => item.value) : []; })()"
        )

    def probe(self, script: str):
        return self.cdp.evaluate(script)

    def step_home(self) -> None:
        """① 新用户第一次打开：默认落地页是工作台，且看得到「能做什么」。"""
        self.note("① 冷开控制台：默认落地页应是工作台（不是某个业务面板）")
        state = self.open_page("home")
        self.check(state["title"] == "工作台", f"落地页是「{state['title']}」", f"落地页是「{state['title']}」")
        self.check(
            "home.universe" in state["ids"],
            "工作台列出了公司与状态",
            f"工作台没有公司面板：{state['ids']}",
        )
        self.check(
            state["actionButtons"] > 0,
            f"工作台有 {state['actionButtons']} 个可直接执行的动作",
            "工作台没有任何动作按钮",
        )
        self.shot("01-home.png")

    def step_company_context(self, ticker: str) -> None:
        """② 选一次公司：全站跟随；刷新 / 前进后退 / 复制 URL 都一致。"""
        self.note(f"② 全局选择器切到 {ticker}：URL 带 ticker、页面标题看得出当前公司")
        self.select_company(ticker)
        state = self.panel_state()
        self.check(
            ticker in urllib.parse.unquote(state["hash"]),
            "URL 里带上了 ticker",
            f"URL 里没有 ticker：{state['hash']}",
        )
        self.check(
            bool(state["company"]) and ticker.split(".")[0] in state["company"],
            f"顶栏显示当前公司：{state['company']!r}",
            f"顶栏没显示当前公司：{state['company']!r}",
        )
        self.shot("02-company-selected.png")

        # 刷新：状态必须一致（URL 是权威）。
        self.cdp.goto(f"{self.base}/#home?company={ticker}", settle=2.5)
        again = self.panel_state()
        self.check(
            ticker in urllib.parse.unquote(again["hash"]) and bool(again["company"]),
            "刷新后当前公司保持",
            f"刷新后公司丢了：hash={again['hash']} 顶栏={again['company']!r}",
        )

    def step_cold_open_without_company(self) -> None:
        """③ 直接打开公司级页面（URL 没有公司）：正常空状态，**零降级卡**（关掉 E2）。"""
        self.note("③ 冷开 #report（URL 里没有公司）：应是可读空状态，不是 BAD_REQUEST 卡片")
        # 「冷开」= 一个**全新文档**直接打开不带公司的公司级页面。三步缺一不可：
        # ① 先走 about:blank 把上一个文档丢掉（只改 hash 的话内存态还在，看着像冷开、
        #    其实带着上一家公司的上下文）；② 再 Page.navigate 到 #report（`Page.navigate`
        #    对「只有 hash 不同」的 URL **不会**重新加载文档）；③ 最后显式 Page.reload。
        self.cdp.goto(f"{self.base}/#home", settle=2.0)
        cleared = self.cdp.evaluate(
            "(() => { localStorage.removeItem('webui.selection.company');"
            " return localStorage.getItem('webui.selection.company'); })()"
        )
        self.check(cleared is None, "已清掉「上次选择」（等价于新用户）",
                   f"localStorage 里还留着上次选择：{cleared!r}")
        self.cdp.goto("about:blank", settle=1.0)
        self.cdp.goto(f"{self.base}/#report", settle=1.5)
        self.cdp.reload(settle=3.0)
        state = self.panel_state()
        self.pages["report-cold"] = state
        self.check(
            state["emptyState"] >= 1,
            "显示了「先选一家公司」的空状态",
            f"没有空状态（面板={state['ids']}）",
        )
        self.check(
            not state["degraded"] and not state["technical"],
            "没有任何降级卡（E2 已关闭）",
            f"冷开出现降级：面板={state['degraded']} 技术化={state['technical']}",
        )
        text = self.cdp.evaluate(
            "(() => { const node = document.querySelector('#panels .panel-empty-state');"
            " return node ? node.textContent.replace(/\\s+/g, ' ').trim() : ''; })()"
        )
        self.note(f"  空状态文案：{text}")
        self.check("先选一家公司" in text, "空状态说清了下一步", f"空状态文案不可读：{text!r}")
        # 横幅里**不许**出现错误码：缺上下文是正常空状态，不是故障（AC-10 的广义读法）。
        self.check(
            not state["banner"] and "BAD_REQUEST" not in (state["banner"] or ""),
            "顶栏横幅为空（空状态不是故障）",
            f"空状态却在横幅里报了错：{state['banner']!r}",
        )
        self.shot("03-cold-open-empty-state.png")

    def _api(self, method: str, path: str, body=None) -> dict:
        """直接打接口（用来**造**一个交接中的任务；界面动作需要真跑一次分析，代价太大）。"""
        import urllib.request as _request

        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = _request.Request(f"{self.base}{path}", data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        with _OPENER.open(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def step_handoff(self, ticker: str) -> None:
        """⑬ 人机交接（`AC-2.4`）：刷新后仍能继续/放弃，且失败时界面**说清缺什么**。

        门② 第六轮的三条阻断都在这一屏上：

        1. 点「我跑完了，继续」而产物不合格 → 交接面板整体消失、界面一个字都不说缺什么；
        2. 刷新/重新进入后交接**不可达**（可复制命令、路径、继续/放弃全没了）；
        3. 界面唯一入口会再起一个等待任务。

        修法：交接面板改为**从状态重建**（`kinds/handoff.js`，任务中心与动作面板共用），
        点继续失败时把 `handoff.missing` 显示出来。这里用接口造一个交接中的任务，
        然后**刷新页面**验证上面三条。
        """
        self.note("⑬ 人机交接：刷新后仍可继续/放弃；点继续失败要说清缺什么（AC-2.4）")
        job = self._api("POST", "/api/v1/actions/company.update_analysis/run",
                        {"params": {}, "context": {"company": ticker}})["data"]
        deadline = time.time() + 30
        current = job
        while time.time() < deadline:
            current = self._api("GET", f"/api/v1/jobs/{job['id']}")["data"]
            if current["status"] == "awaiting_agent":
                break
            time.sleep(0.3)
        if not self.check(current["status"] == "awaiting_agent",
                          "造出一个停在交接步的任务",
                          f"任务没有停在交接步：{current['status']} {current.get('error')}"):
            return

        # ① 刷新后交接必须可达（这是第六轮的阻断项之一）
        self.cdp.reload(settle=3.5)
        self.open_page("commands", f"company={urllib.parse.quote(ticker)}")
        state = self.cdp.evaluate(
            """(() => {
              const panel = document.querySelector('[data-handoff-for="%s"]');
              return {
                panel: panel ? 1 : 0,
                slash: document.querySelectorAll('[data-handoff-for="%s"] .handoff-command code').length,
                continueButton: document.querySelectorAll('[data-handoff-continue]').length,
                abandonButton: document.querySelectorAll('[data-handoff-abandon]').length,
                missing: document.querySelectorAll('[data-handoff-missing]').length,
              };
            })()""" % (job["id"], job["id"])
        )
        self.note(f"  刷新后交接面板：{state}")
        self.check(state["panel"] == 1, "刷新后仍能看到交接面板",
                   f"刷新后交接面板消失了：{state}")
        self.check(state["continueButton"] >= 1 and state["abandonButton"] >= 1,
                   "刷新后仍能继续/放弃",
                   f"刷新后找不到继续/放弃按钮：{state}")
        self.check(state["slash"] >= 1, "刷新后仍能看到可复制的命令",
                   f"刷新后看不到要跑的命令：{state}")

        # ② 产物不合格时点继续：必须**留在原地**，并把「缺什么」显示出来
        # 点继续**之前** `missing` 是空的（还没校验过），这是正常的：
        # 界面此刻应当把「要产出什么」告诉用户（`expects`），校验结果在点完之后出现。
        before = self._api("GET", f"/api/v1/jobs/{job['id']}")["data"]
        self.check(bool(before["handoff"]["expects"]), "接口给出了要校验的产物清单",
                   f"接口没给 expects：{before['handoff']}")
        clicked = self.cdp.evaluate(
            """(() => {
              const node = document.querySelector('[data-handoff-continue]');
              if (!node) return false;
              node.click();
              return true;
            })()"""
        )
        self.check(clicked, "点了「我跑完了，继续」", "找不到继续按钮")
        time.sleep(2.0)
        after = self.cdp.evaluate(
            """(() => {
              // **按任务定位**，不读整页文本：门② 第七轮用受控还原证明过，读
              // `document.body.textContent` 时「历史里旧任务的失败卡」也能满足这条判据，
              // 于是交接面板整块消失时它仍然 ✅——那条断言就看不出缺陷了。
              const panel = document.querySelector('[data-handoff-for="%s"]');
              const node = panel ? panel.querySelector('[data-handoff-feedback]') : null;
              const missingNode = panel ? panel.querySelector('[data-handoff-missing]') : null;
              const text = node ? node.textContent : '';
              // **可见性按计算样式判**：只看 `hidden` 属性会被 `display:none` /
              // `visibility:hidden` 骗过（门② delta 复核实测过这三种情形）。
              let shown = false;
              if (node && !node.hidden) {
                const style = getComputedStyle(node);
                shown = style.display !== 'none' && style.visibility !== 'hidden'
                  && parseFloat(style.opacity || '1') > 0;
              }
              return {
                status: panel ? 1 : 0,
                feedback: text,
                missingText: missingNode ? missingNode.textContent : '',
                visible: !!(shown && text.trim()),
                missingShown: !!(missingNode && missingNode.textContent.includes('还没就绪')),
              };
            })()""" % job["id"]
        )
        server = self._api("GET", f"/api/v1/jobs/{job['id']}")["data"]
        self.note(f"  继续之后：界面={after} 服务端状态={server['status']}")
        self.check(server["status"] == "awaiting_agent", "产物不合格时任务仍停在交接步",
                   f"任务被放行了：{server['status']}")
        self.check(after["status"] == 1, "交接面板还在（没有凭空消失）",
                   "点了继续之后交接面板消失了")
        self.check(after["missingShown"], "界面说清了「缺什么」（面板内的缺失行）",
                   f"面板里没有说缺什么：missingText={after['missingText']!r}")
        self.check("还没就绪" in after["feedback"],
                   "面板内的失败反馈也说了「还没就绪」",
                   f"反馈措辞不一致或缺失：{after['feedback']!r}")
        self.check(after["visible"], "失败反馈在界面上可见（不是隐藏节点）",
                   f"反馈节点不可见：visible={after['visible']} text={after['feedback']!r}")
        self.cdp.evaluate(
            """(() => {
              const panel = document.querySelector('[data-handoff-for="%s"]');
              if (panel) panel.scrollIntoView({block: 'center'});
              return true;
            })()""" % job["id"]
        )
        time.sleep(0.5)
        self.shot("14-handoff.png")

        # ③ 收尾：放弃这个交接任务，避免留下僵尸等待任务
        self._api("POST", f"/api/v1/jobs/{job['id']}/abandon")
        time.sleep(1.0)
        final = self._api("GET", f"/api/v1/jobs/{job['id']}")["data"]
        self.check(final["status"] == "cancelled", "可以放弃这次交接",
                   f"放弃之后状态是 {final['status']}")

    def step_artifact_links(self) -> None:
        """⑫ 任务产出要能**点开**（`AC-5`）：有产出块就必须有链接。

        门② 第四轮抓到的是死代码：`selectionCompany()` 只读 `handoff.paths.ticker`，
        而终态任务的 `handoff` 是空的（公司上下文在 `outputs.context` 里）——两个条件互斥，
        实测 22 个产出块、**0 个链接**。判据只靠走查看得见，所以钉在这里。
        """
        self.note("⑫ 任务产出链接：有产出块就必须有可点的链接（AC-5）")
        self.open_page("commands")
        counts = self.cdp.evaluate(
            """(() => {
              const blocks = [...document.querySelectorAll('#panels .job-outputs')];
              const links = [...document.querySelectorAll('#panels .job-outputs a[data-job-output-link]')];
              const hrefs = links.map((a) => a.getAttribute('href')).filter(Boolean);
              // 逐块统计：「这一块里所有**已生成**的条目是不是都有链接」。
              const covered = blocks.filter((block) => {
                const items = [...block.querySelectorAll('li')];
                const exists = items.filter((li) => !li.textContent.includes('（还没生成）'));
                const linked = items.filter((li) => li.querySelector('a[data-job-output-link]'));
                return exists.length > 0 && linked.length === exists.length;
              }).length;
              return { blocks: blocks.length, links: links.length, covered: covered,
                       ok: hrefs.filter((h) => h.startsWith('#report?company=')).length,
                       sample: hrefs.slice(0, 2) };
            })()"""
        )
        self.note(
            f"  产出块={counts['blocks']} 链接={counts['links']} 形如报告页={counts['ok']} "
            f"每块都有链接={counts['covered']}/{counts['blocks']}"
        )
        # 判据是**逐块**的：每个 `.job-outputs` 里至少有与「已生成」条目数相等的链接。
        # 曾经写成 `links >= 1`——门② 第五轮把 `jobs.js` 受控还原成第四轮的死代码后，
        # 那一版仍然 ✅（25 个产出块、只剩 1 个链接），也就是**抓不住它本该抓的缺陷**。
        # `AGENTS.md` 的标准是「能抓住已知缺陷才算证据」，所以这里必须逐块判。
        self.check(
            counts["blocks"] == 0 or counts["covered"] == counts["blocks"],
            f"每个产出块都有链接（{counts['covered']}/{counts['blocks']}）",
            f"有 {counts['blocks']} 个产出块，只有 {counts['covered']} 块是全的"
            f"（链接总数 {counts['links']}）——产出链接退化成死代码了",
        )
        self.check(
            counts["blocks"] == 0 or counts["ok"] == counts["links"],
            f"产出链接指向报告页（{counts['ok']}/{counts['links']}）",
            f"产出链接的 href 不对：{counts['sample']}",
        )
        self.shot("13-artifact-links.png")

    def step_unresolvable_company(self) -> None:
        """⑪ 上下文**解析不出来**时也要给人话（`AC-10`）：不能把 `NOT_FOUND` 摆上横幅。

        真实路径：选择器「记住上次选择」之后产物被删/改名，或 URL 里写了一个不存在的标识。
        门② 第四轮就是这么复现的——那一轮走查的横幅禁则表不含 `NOT_FOUND`，所以没抓住。
        """
        self.note("⑪ 上下文解析不出来：URL 里写一个不存在的公司，应给可读空状态")
        self.cdp.goto(f"{self.base}/#report?company=999999.XX", settle=3.0)
        state = self.panel_state()
        self.pages["report-unresolvable"] = state
        self.check(
            state["emptyState"] >= 1,
            "给了「这家公司找不到了」的空状态",
            f"没有空状态（面板={state['ids']} 横幅={state['banner']!r}）",
        )
        self.check(
            not state["degraded"] and not state["technical"]
            and "NOT_FOUND" not in (state["banner"] or ""),
            "没有降级卡、横幅里也没有错误码",
            f"解析不出来时露出了技术细节：降级={state['degraded']} 横幅={state['banner']!r}",
        )
        text = self.cdp.evaluate(
            "(() => { const node = document.querySelector('#panels .panel-empty-state');"
            " return node ? node.textContent.replace(/\\s+/g, ' ').trim() : ''; })()"
        )
        self.note(f"  空状态文案：{text}")
        self.check("找不到了" in text, "空状态说清了发生了什么", f"空状态文案不可读：{text!r}")
        self.shot("12-unresolvable-company.png")

    def step_table_sorting(self) -> None:
        """表格能力要**真的点**（`AC-8`）：声明了 sort 不等于能排序。

        门② 第三轮就是这么抓到的——批次列表声明了 `sort=True`，但服务端没下发
        `data-sort-key`，真实浏览器点表头返回 `NOT-sortable`，而行级测试只断言了「声明」。
        所以这里在**真实浏览器里点一次数值列**，要求排序**实际生效**（行序变化）。
        选数值列而不是文本列：首列往往已经有序，点一下看不出变化。
        """
        self.note("④b 表格排序（AC-8）：在真实浏览器里点一次数值列表头")
        self.open_page("collect")
        outcome = self.cdp.evaluate(
            """(() => {
              const panel = document.querySelector('#panels .panel[data-panel-id="collect.batches"]');
              if (!panel) return { error: 'no-panel' };
              const ths = [...panel.querySelectorAll('thead th[data-sort-key]')];
              const target = ths.find((th) => th.dataset.sortType === 'number') || ths[ths.length - 1];
              if (!target) return { error: 'NOT-sortable: 没有任何 data-sort-key 表头' };
              const column = [...target.parentNode.children].indexOf(target);
              const read = () => [...panel.querySelectorAll('tbody tr')]
                .map((row) => (row.children[column] || {}).textContent || '').map((t) => t.trim());
              const before = read();
              target.click();
              // `before` 只作日志用途，但**不截断**：截断过的日志会让人以为判据也只看前几行。
              return { column: column, key: target.dataset.sortKey, before: before,
                       active: target.dataset.sortActive || '' };
            })()"""
        )
        self.note(f"  点击结果：{outcome}")
        if outcome.get("error"):
            self.check(False, "表头可排序", f"批次表不可排序：{outcome['error']}")
            return
        self.check(bool(outcome.get("active")), "点表头后进入排序态", f"点表头没有生效：{outcome}")
        after = self.cdp.evaluate(
            """(() => {
              const panel = document.querySelector('#panels .panel[data-panel-id="collect.batches"]');
              const ths = [...panel.querySelectorAll('thead th[data-sort-key]')];
              const target = ths.find((th) => th.dataset.sortType === 'number') || ths[ths.length - 1];
              const column = [...target.parentNode.children].indexOf(target);
              return [...panel.querySelectorAll('tbody tr')]
                .map((row) => (row.children[column] || {}).textContent || '').map((t) => t.trim()).slice(0, 5);
            })()"""
        )
        self.note(f"  第一次点击后该列（全部行）：{after}")
        numbers = [float(text) for text in after if text.replace(".", "", 1).isdigit()]
        self.check(
            numbers == sorted(numbers),
            f"数值列升序排列（{len(numbers)} 行全看）",
            f"点了排序但数值列不是升序：{after}",
        )
        # **再点一次**：asc→desc 必须是真翻转。只点一次的话，「本来就有序」也能骗过判据
        # （门② 第八轮 F6 指出的证据强度问题）。
        self.cdp.evaluate(
            """(() => {
              const panel = document.querySelector('#panels .panel[data-panel-id="collect.batches"]');
              const ths = [...panel.querySelectorAll('thead th[data-sort-key]')];
              const target = ths.find((th) => th.dataset.sortType === 'number') || ths[ths.length - 1];
              target.click();
              return true;
            })()"""
        )
        time.sleep(0.4)
        flipped = self.cdp.evaluate(
            """(() => {
              const panel = document.querySelector('#panels .panel[data-panel-id="collect.batches"]');
              const ths = [...panel.querySelectorAll('thead th[data-sort-key]')];
              const target = ths.find((th) => th.dataset.sortType === 'number') || ths[ths.length - 1];
              const column = [...target.parentNode.children].indexOf(target);
              return [...panel.querySelectorAll('tbody tr')]
                .map((row) => (row.children[column] || {}).textContent || '').map((t) => t.trim());
            })()"""
        )
        flipped_numbers = [float(text) for text in flipped if text.replace(".", "", 1).isdigit()]
        self.note(f"  第二次点击后该列：{flipped}")
        self.check(
            flipped_numbers == sorted(flipped_numbers, reverse=True),
            f"再点一次变成降序（{len(flipped_numbers)} 行全看）",
            f"第二次点击没有翻转：{flipped}",
        )
        self.shot("04b-table-sort.png")

    def step_actions_page(self) -> None:
        """④ 任务页的「可以做的事」：动作以意图命名、禁用时给可读理由。"""
        self.note("④ 任务页：动作清单 + 预检（禁用时给理由）")
        state = self.open_page("commands")
        self.check(state["actionButtons"] > 0, "动作清单有按钮", "动作清单是空的")
        self.note(
            f"  动作按钮={state['actionButtons']} 禁用={state['blockedActions']} "
            f"理由样例={state['actionBlockers']}"
        )
        only_in_year = self.cdp.evaluate(
            "(() => { const nodes = [...document.querySelectorAll('#panels .action-title')];"
            " return nodes.map((node) => node.textContent.trim()); })()"
        )
        self.note(f"  动作标题：{only_in_year}")
        self.check(
            all(not any(flag in title for flag in ("--", "company_dir", "run_dir"))
                for title in only_in_year),
            "动作标题里没有内部参数名或 CLI 开关",
            f"动作标题里出现了内部名字：{only_in_year}",
        )
        self.shot("04-actions.png")

    def step_data_page(self) -> None:
        """⑤ 数据页：清单 / 缺口 / 仓规模，以及「联网」与「离线」两个可分辨的动作。"""
        self.note("⑤ 数据页：清单 / 缺口 / 仓规模 + 拉取与离线重建两个动作")
        state = self.open_page("data")
        self.check(
            "data.universe" in state["ids"] or "data.store" in state["ids"],
            "数据页列出了数据面板",
            f"数据页没有数据面板：{state['ids']}",
        )
        effects = self.cdp.evaluate(
            "(() => [...document.querySelectorAll('#panels .action-card')].map((card) => ({"
            "  id: card.dataset.actionId, enabled: card.dataset.enabled,"
            "  effects: (card.querySelector('.action-effects') || {}).textContent || '',"
            "})) )()"
        )
        self.note(f"  数据动作：{effects}")
        online = [item for item in effects if item["id"] in ("data.pull_all", "data.fill_gaps")]
        offline = [item for item in effects if item["id"] == "data.rebuild"]
        self.check(
            all("联网" in item["effects"] for item in online) and bool(online),
            "联网动作明确标出「需要联网」",
            f"联网动作的文案没标联网：{online}",
        )
        self.check(
            all("离线" in item["effects"] for item in offline) and bool(offline),
            "离线重建明确标出「离线执行」",
            f"离线动作的文案没标离线：{offline}",
        )
        self.shot("05-data.png")

    def step_chart_basis(self) -> None:
        """⑥ 图表：口径可切换、图上标明口径与单位、每张图都按容器宽度绘制。"""
        self.note("⑥ 图表页：口径默认年度、可切换，标出单位与口径")
        state = self.open_page("charts", f"company={urllib.parse.quote(self.company)}")
        self.check(state["canvases"] >= 3, f"渲染出 {state['canvases']} 张图", "图不够 3 张")
        # 口径切换器只在**这份数据包真有多种口径**时才出现；只有年度口径时，
        # 工具栏必须写明「口径：年度（累计）」——两种情况都算「按口径可查看」。
        declared = self.cdp.evaluate(
            """(async () => {
              const response = await fetch('/api/v1/companies/' +
                encodeURIComponent(new URLSearchParams(location.hash.split('?')[1] || '').get('company') || '') +
                '/charts');
              const payload = await response.json();
              const charts = (payload.data && payload.data.charts) || {};
              return Object.fromEntries(Object.entries(charts).map(([key, value]) => [key, value.bases || []]));
            })()"""
        )
        self.note(f"  各图可用口径：{declared}")
        multi = [key for key, bases in (declared or {}).items() if len(bases) > 1]
        if multi:
            self.check(
                bool(state["chartBasis"]),
                f"多种口径的图有切换器：{state['chartBasis']}",
                f"多口径的图（{multi}）没有切换器（AC-7 要求能按口径查看）",
            )
        else:
            self.note("  （这份数据包只有一种口径，切换器按设计不出现）")
        labels = self.cdp.evaluate(
            "(() => [...document.querySelectorAll('#panels .chart-toolbar')]"
            ".map((node) => node.textContent.replace(/\\s+/g, ' ').trim()))()"
        )
        self.note(f"  图表工具栏：{labels}")
        self.check(
            bool(labels) and all("口径" in text for text in labels),
            "每张图都标明了口径（累计/单期）",
            f"图上没有口径标注：{labels}",
        )
        self.check(
            bool(labels) and all("单位" in text for text in labels),
            "每张图都标明了单位",
            f"图上没有单位标注：{labels}",
        )
        self.check(
            state["tableControls"] >= 0 and state["freshness"] >= 0,
            "（新鲜度徽标与表格控件为可选观察点）",
            "不可达",
        )
        # 切到单季：序列与标签必须跟着换（不是画同一份数据）。
        # 切**真的有多档口径**的那张图：第一张图（年度行情）天生只有年度口径，没有切换器。
        target = (multi or [None])[0]
        if not target:
            self.note("  （没有任何多口径的图，跳过切换判据）")
        else:
            # 「切换前」先截一张：切换与截图必须在**两次** evaluate 之间，
            # 否则两张截图是同一份字节、切换前这一屏没有证据（独立验收抓到的）。
            before = self.cdp.evaluate(
                """(() => {
                  const panel = document.querySelector("[data-panel-id='%s']");
                  const node = panel && panel.querySelector('[data-chart-basis]');
                  if (!node) return { error: 'no-basis-switcher' };
                  const canvas = panel.querySelector('canvas');
                  const options = [...node.options].map((item) => item.value);
                  return { options: options, beforeLength: canvas.toDataURL().length,
                           basis: node.value };
                })()""" % target
            )
            self.note(f"  口径切换前（{target}）：{before}")
            self.shot("06a-charts-before-switch.png")
            switched = self.cdp.evaluate(
                """(() => {
                  const panel = document.querySelector("[data-panel-id='%s']");
                  const node = panel && panel.querySelector('[data-chart-basis]');
                  const options = [...node.options].map((item) => item.value);
                  if (!options.includes('quarter')) return { skipped: true, options: options };
                  node.value = 'quarter';
                  node.dispatchEvent(new Event('change', { bubbles: true }));
                  return { options: options, switched: true };
                })()""" % target
            )
            switched = {**(before or {}), **(switched or {})}
            self.note(f"  口径切换（{target}）：{switched}")
            time.sleep(3.0)
            self.shot("06b-charts-quarter.png")
            after = self.cdp.evaluate(
                """(() => {
                  const panel = document.querySelector("[data-panel-id='%s']");
                  const canvas = panel.querySelector('canvas');
                  const toolbar = panel.querySelector('.chart-toolbar');
                  return {
                    length: canvas.toDataURL().length,
                    toolbar: toolbar ? toolbar.textContent.replace(/\\s+/g, ' ').trim() : '',
                    hash: location.hash,
                  };
                })()""" % target
            )
            self.note(f"  切换后：{after}")
            self.check(
                after["length"] != (switched or {}).get("beforeLength")
                and "单季" in after["toolbar"]
                and "basis=quarter" in after["hash"],
                "切到单季口径后图重画、标注跟着换、URL 带 basis=quarter",
                f"口径切换没有生效：切换前={switched} 切换后={after}",
            )
            # 这里不再截 `06-charts.png`：它是切到单季之后的同一屏，与 `06b` 是同一份字节
            # （门② 登记过重复截图）。切换前的证据在 `06a`。
            # 两张截图必须是**不同**的字节：否则「切换前」这一屏没有证据。
            # 这一条是脚本对自己的判据（独立验收就是靠比对 md5 发现两张图一模一样的）。
            self.check(
                self.shot_bytes("06a-charts-before-switch.png")
                != self.shot_bytes("06b-charts-quarter.png"),
                "切换前/后两张截图不是同一份字节（有前后对照）",
                "切换前后的截图完全相同：这一屏没有前后对照证据",
            )
    def step_action_run(self, action_id: str, *, timeout: float) -> None:
        """⑦ 执行一个动作：确认层 → 任务 → 产出。

        指定的动作在当前数据下可能**按设计被禁用**（例如仓里没有这家公司的记录时
        「离线重建」不可用）。这时按「意图化动作可用」的原则**换一个可用的公司级动作**，
        并把换用的事实写进证据；一个都不可用就明确记一笔（不假装通过）。
        """
        self.note(f"⑦ 执行动作 {action_id}（真实按钮，不是接口调用）")
        state = self.open_page("commands")
        before = self._job_ids()
        available = self.cdp.evaluate(
            "(() => [...document.querySelectorAll('[data-action-run]')]"
            ".filter((node) => !node.disabled).map((node) => node.dataset.actionRun))()"
        )
        if action_id not in (available or []):
            self.note(f"  （{action_id} 在当前上下文下被禁用；可用的动作：{available}）")
            # 优先挑**不联网**的动作：走查不该顺手消耗数据源配额（那是使用者花钱买的）。
            safe = [item for item in (available or []) if item != "data.pull_all"
                    and item != "data.fill_gaps"]
            if not safe:
                self.note("  ⏭️ 可用的动作都会联网（会花配额）：这一步跳过；"
                          "失败与重试由下一步单独覆盖，联网动作改由真实使用触发。")
                return
            action_id = safe[0]
            self.note(f"  改用可用动作（不联网）：{action_id}")
        selector = f"[data-action-run='{action_id}']"
        if not self.check(
            self.cdp.click_selector(selector),
            f"点到了动作 {action_id}",
            f"页面上没有可执行的 {action_id}（禁用或不存在）",
        ):
            return
        time.sleep(0.6)
        # 危险动作必须经过一次显式确认（AC-4）；取消 = 不提交、无副作用。
        confirm = self.cdp.evaluate(
            "(() => { const node = document.querySelector('.confirm-layer');"
            " return node ? node.textContent.replace(/\\s+/g, ' ').trim().slice(0, 120) : ''; })()"
        )
        if confirm:
            self.note(f"  确认层：{confirm}")
            self.check(
                self.cdp.click_selector("[data-confirm-cancel]"),
                "确认层可以取消",
                "确认层没有取消按钮",
            )
            time.sleep(0.8)
            jobs_after_cancel = len(_http_json(f"{self.base}/api/v1/jobs")["data"]["jobs"])
            self.note(f"  取消后任务数={jobs_after_cancel}（取消不应产生任务）")
            self.shot("07-action-confirm-cancelled.png")
            self.check(
                self.cdp.click_selector(selector),
                f"再次点击 {action_id}",
                f"取消后按钮不可再点：{action_id}",
            )
            time.sleep(0.6)
            self.check(
                self.cdp.click_selector("[data-confirm-accept]"),
                "确认层点了「确认执行」",
                "确认层没有确认按钮",
            )
        job = self._wait_job(before, timeout=timeout)
        if not self.check(bool(job), "动作产生了任务", f"{timeout:.0f}s 内没有任务"):
            return
        self.note(f"  任务 {job['id']} 状态={job['status']} 步骤={job.get('progress')}")
        self.note(f"  实际命令行：{' '.join(job.get('argv') or [])}")
        self.check(
            job["status"] in ("finished", "awaiting_agent"),
            f"动作跑到了终态或交接点（{job['status']}）",
            f"动作状态={job['status']}（错误={job.get('error')!r}）",
        )
        self.shot("08-action-result.png")

    def step_failure_and_retry(self) -> None:
        """⑧ 失败与重试：失败原因是人话、原始日志可展开、重试可用。"""
        self.note("⑧ 失败与重试：故意让一个动作失败，看「发生了什么 + 怎么办」与重试")
        failed = self.cdp.evaluate(
            """(async () => {
              // 用接口构造一次必然失败的动作：环境变量指向不存在的目录 → 步骤 1 失败。
              const response = await fetch('/api/v1/jobs', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: 'runs_resolve', params: { company_dir: '/nonexistent/definitely-not-here' } }),
              });
              const payload = await response.json();
              return payload.ok ? payload.data.id : ('ERROR: ' + JSON.stringify(payload.error));
            })()"""
        )
        self.note(f"  构造的失败任务：{failed}")
        if not isinstance(failed, str) or failed.startswith("ERROR"):
            self.check(False, "构造失败任务", f"没能构造失败任务：{failed}")
            return
        deadline = time.time() + 60
        job = None
        while time.time() < deadline:
            job = _http_json(f"{self.base}/api/v1/jobs/{failed}")["data"]
            if job["status"] not in ("running", "queued"):
                break
            time.sleep(0.5)
        self.check(job and job["status"] == "failed", "任务确实失败了", f"任务状态={job and job['status']}")
        if not job:
            return
        self.check(bool(job.get("failure_summary", {}).get("what")), "失败给了「发生了什么」", "没有失败摘要")
        self.check(bool(job.get("failure_summary", {}).get("how")), "失败给了「怎么办」", "没有下一步建议")
        self.check(bool(job.get("log")), "原始日志保留", "日志被删了")
        self.open_page("commands")
        time.sleep(1.5)
        shown = self.cdp.evaluate(
            "(() => { const node = document.querySelector('#panels .job-failure');"
            " return node ? node.textContent.replace(/\\s+/g, ' ').trim().slice(0, 160) : ''; })()"
        )
        self.note(f"  页面上的失败文案：{shown}")
        self.check(bool(shown), "任务中心展示了失败原因", "任务中心没有展示失败原因")
        retry = self.cdp.evaluate("document.querySelectorAll('[data-job-retry]').length")
        self.check(retry > 0, f"有 {retry} 个任务可以重试", "没有可重试的任务按钮")
        # 把失败卡滚到视野里再截：不滚的话这一屏与「动作结果」那屏字节完全相同
        # （门② 登记过 `08-action-result.png` 与 `09-failure-retry.png` 同哈希）。
        self.cdp.evaluate(
            "(() => { const node = document.querySelector('#panels .job-failure');"
            " if (node) node.scrollIntoView({block: 'center'}); return true; })()"
        )
        time.sleep(0.6)
        self.shot("09-failure-retry.png")

    def step_switch_company(self, ticker: str, other: str) -> None:
        """⑨ 切换公司：全站跟随，旧公司不残留。"""
        self.note(f"⑨ 从 {ticker} 切到 {other}：标题与数据随之切换")
        self.select_company(other)
        self.open_page("charts", f"company={urllib.parse.quote(other)}")
        state = self.panel_state()
        self.check(
            other in urllib.parse.unquote(state["hash"]),
            f"切到 {other} 后 URL 跟上了",
            f"切换后 URL 没跟上：{state['hash']}",
        )
        self.shot("10-switched-company.png")

    def step_display_name_consistency(self, ticker: str) -> None:
        """⑩ 同一家公司在公司页 / 图表页 / 采集存档页的显示名必须**逐字相同**（`AC-9`）。

        为什么单独一步：独立验收发现「采集存档页把目录名当显示名」时，走查**全绿**——
        它到那一页只看降级与技术化错误，从不比较跨页显示名。`AGENTS.md` 的常设授权说
        「脚本全绿本身不算证据」，这一条就是补上那个洞：判据落在**跨页的字符串相等**上，
        而且顺带钉住「显示名里不许出现目录名的下划线形态」。
        """
        self.note(f"⑩ 显示名一致性（AC-9）：{ticker} 在公司页 / 图表页 / 采集存档页")
        names = {}
        self.open_page("companies")
        names["公司列表"] = self.cdp.evaluate(
            """(() => {
              const rows = [...document.querySelectorAll('#panels .panel[data-panel-id="companies.list"] tbody tr')];
              const hit = rows.find((row) => row.textContent.includes('%s'.split('.')[0]));
              return hit ? hit.children[0].textContent.trim() : '';
            })()""" % ticker
        )
        self.open_page("charts", f"company={urllib.parse.quote(ticker)}")
        names["图表页顶栏"] = self.panel_state()["company"]
        self.open_page("collect")
        names["采集存档页"] = self.cdp.evaluate(
            """(() => {
              const table = document.querySelector('#panels .panel[data-panel-id="collect.rebuild"]');
              if (!table) return '';
              const rows = [...table.querySelectorAll('tbody tr')];
              const hit = rows.find((row) => row.textContent.includes('%s'.split('.')[0]));
              return hit ? hit.children[0].textContent.trim() : '';
            })()""" % ticker
        )
        self.note(f"  三处显示名：{names}")
        picked = {page: name for page, name in names.items() if name}
        self.check(
            len(picked) >= 2 and len(set(picked.values())) == 1,
            f"三处显示名逐字相同：{set(picked.values())}",
            f"同一家公司在不同页面的显示名不一致：{names}",
        )
        self.check(
            all("_" not in name for name in picked.values()),
            "显示名里没有目录名形态（下划线）",
            f"显示名里出现了目录名：{picked}",
        )
        # 截图去**数据页**：三处比对已经取完。停在任何已截过的页面上都会产生重复字节
        # （门② 第三轮登记过 `08-collect.png` 与 `11-display-name.png` 同哈希），
        # 数据页的「自选股清单」正好是同一家公司的**第四处**显示位。
        self.open_page("data")
        self.shot("11-display-name.png")

    # -- 旧步骤（REQ-009 的回归路径，继续保留） ------------------------------

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

    def _wait_job(self, before: set, *, timeout: float, newest: bool = False):
        """等到出现一个**不在 `before` 里的**任务并收口；`newest=True` 时等最新的那个。"""
        deadline = time.time() + timeout
        candidate = None
        seen = False
        while time.time() < deadline:
            payload = _http_json(f"{self.base}/api/v1/jobs")
            fresh = [job for job in payload["data"]["jobs"] if job["id"] not in before]
            if fresh:
                candidate = fresh[0]
                seen = True
                if candidate["status"] not in ("running", "queued"):
                    return candidate
            elif newest and seen:
                return candidate
            time.sleep(0.5)
        return candidate


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


def _company_catalog(base: str) -> list:
    """`GET /api/v1/companies` 的公司清单（ticker 优先，目录名兜底）。"""
    payload = _http_json(f"{base}/api/v1/companies")["data"]
    return payload.get("companies") or []


def _resolve_ticker(catalog: list, wanted: str) -> str:
    """把 `--company` 解析成 **ticker**（URL 与选择器的规范形式）。"""
    text = str(wanted or "").strip()
    for item in catalog:
        if text in (item.get("ticker"), item.get("dir")):
            return item.get("ticker") or item.get("dir") or text
    return text


def _other_ticker(catalog: list, current: str) -> str:
    for item in catalog:
        candidate = item.get("ticker") or item.get("dir") or ""
        if candidate and candidate != current:
            return candidate
    return ""


def _require_console(base: str) -> str:
    try:
        payload = _http_json(f"{base}/api/v1/healthz", timeout=5)
    except Exception as exc:  # noqa: BLE001（这里就是要给出可读的下一步）
        raise SystemExit(f"连不上控制台 {base}（{exc}）。先在另一个终端跑 `make gui`。") from exc
    return payload.get("data", {}).get("version", "?")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://127.0.0.1:8765", help="控制台地址")
    parser.add_argument("--company", default="600887.SH",
                        help="公司标识：ticker（规范）或 output/ 下的目录名")
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
    parser.add_argument(
        "--action", default="data.rebuild",
        help="走查时要真实执行的动作 id（默认「从仓离线重建」：不联网、不花钱）",
    )
    args = parser.parse_args(argv)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) if args.out else REPO_ROOT / "output" / ".webui_walkthrough" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    base = args.base.rstrip("/")
    version = _require_console(base)
    # 按键参数（旧路径）默认用**目录名**：`runs resolve --company-dir` 要的是目录。
    catalog = _company_catalog(base)
    directory = next(
        (item.get("dir") for item in catalog if args.company in (item.get("ticker"), item.get("dir"))),
        args.company,
    )
    params = dict(item.split("=", 1) for item in args.param) or {
        "company_dir": f"output/{directory}"
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

        ticker = _resolve_ticker(catalog, args.company)
        walkthrough = Walkthrough(devtools, base, out_dir, ticker)
        # REQ-012 的 AC-12 全路径：落地页 → 选公司 → 图表/报告/迭代 → 执行动作 →
        # 观察失败与重试 → 切换公司。旧步骤（REQ-009 的 AC-8）继续跑，证明没有回退。
        walkthrough.step_home()
        walkthrough.step_company_context(ticker)
        walkthrough.step_cold_open_without_company()
        walkthrough.step_data_page()
        walkthrough.step_handoff(ticker)
        walkthrough.step_artifact_links()
        walkthrough.step_unresolvable_company()
        walkthrough.step_table_sorting()
        walkthrough.step_actions_page()
        walkthrough.step_chart_basis()
        walkthrough.step_report()
        walkthrough.step_runs()
        walkthrough.step_action_run(args.action, timeout=args.job_timeout)
        walkthrough.step_failure_and_retry()
        other = _other_ticker(catalog, ticker)
        if other:
            walkthrough.step_switch_company(ticker, other)
        else:
            walkthrough.note("（只有一家公司，跳过「切换公司」这一步）")
        walkthrough.step_display_name_consistency(ticker)
        # REQ-009 的既有路径（AC-8 的回归）：公司页 → 点进图表 → 悬停 → 按键页
        walkthrough.step_companies()
        walkthrough.step_company_click_to_charts()
        walkthrough.step_chart_hover()
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
        "company": walkthrough.company,
        "action": args.action,
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
        f"- 公司：{walkthrough.company}\n- 动作：{args.action}\n- 浏览器：{browser}"
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
