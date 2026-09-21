"""HTTP 服务：只监听环回地址；`/api/v1/**` 走注册表，其余走静态资源。

失败一律走统一信封：**已知错误**（`WebUIError`）按自己的错误码与状态码返回；
**未知异常**只回 `INTERNAL` + 请求 id，堆栈只进服务端日志——不把路径与内部细节漏给页面（AC-3.6）。
"""

from __future__ import annotations

import errno
import os
import sys
import threading
import traceback
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import __version__
from . import envelope
from .context import RequestContext
from .errors import BindFailed, PathOutsideRoot, PortInUse, UnknownRoute, WebUIError
from .router import find_route
from .security import collect_secrets, redact, safe_join

# 请求日志与异常堆栈的内存上限：长时间运行不能靠无界 list 吃内存（D4）。
LOG_BUFFER_LINES = 500

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
JSON_CONTENT_TYPE = "application/json; charset=utf-8"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}
MAX_STATIC_BYTES = 5 * 1024 * 1024


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"turtle-webui/{__version__}"

    # ------------------------------------------------------------ 入口

    def do_GET(self):  # noqa: N802（BaseHTTPRequestHandler 的命名约定）
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802
        self._dispatch("POST")

    # ------------------------------------------------------------ 分发

    def _dispatch(self, method: str) -> None:
        from urllib.parse import urlparse

        parsed = urlparse(self.path)
        path = parsed.path
        request_id = uuid.uuid4().hex[:8]
        if path.startswith("/api/"):
            body, status, content_type = self._api(method, path, parsed.query, request_id)
        else:
            body, status, content_type = self._static(method, path)
        self._send(status, body, content_type)

    def _api(self, method: str, path: str, raw_query: str, request_id: str):
        from urllib.parse import parse_qs

        try:
            route, params = find_route(self.server.registry.routes(), method, path)
            if route is None:
                raise UnknownRoute(
                    f"没有这条接口：{method} {path}",
                    hint="可用路由见 /api/v1/registry。",
                )
            query = {
                key: values[-1] for key, values in parse_qs(raw_query, keep_blank_values=True).items()
            }
            ctx = RequestContext(
                method=method,
                path=path,
                query=query,
                config=self.server.config,
                registry=self.server.registry,
                request_id=request_id,
                secrets=self.server.secrets,
                log=self._log,
                # 真实绑定端口随请求传下去：不再把它挂在注册表上（复验 N8：
                # 同一个 registry 起第二个服务会污染第一个的 healthz）。
                bound_port=self.server.server_address[1],
            )
            payload = route.handler(ctx, **params)
            return envelope.dumps(payload), 200, JSON_CONTENT_TYPE
        except WebUIError as exc:
            return envelope.dumps(envelope.failure(exc)), exc.status, JSON_CONTENT_TYPE
        except Exception:  # noqa: BLE001（守住边界：任何未预期异常都不许漏成 500 堆栈）
            self._log(f"[{request_id}] 未处理异常 {method} {path}\n{traceback.format_exc()}")
            return (
                envelope.dumps(
                    envelope.failure(
                        code="INTERNAL",
                        message="内部错误",
                        hint=f"请求 id：{request_id}（细节见服务端日志）",
                    )
                ),
                500,
                JSON_CONTENT_TYPE,
            )

    def _static(self, method: str, path: str):
        if method != "GET":
            return (
                envelope.dumps(envelope.failure(code="BAD_REQUEST", message="静态资源只支持 GET")),
                405,
                JSON_CONTENT_TYPE,
            )
        relative = path.lstrip("/") or "index.html"
        parts = [part for part in relative.split("/") if part]
        try:
            target = safe_join(self.server.static_dir, *parts)
        except PathOutsideRoot as exc:
            return envelope.dumps(envelope.failure(exc)), exc.status, JSON_CONTENT_TYPE
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            return (
                envelope.dumps(
                    envelope.failure(
                        code="NOT_FOUND",
                        message=f"静态资源不存在：{relative}",
                        hint="前端入口是 /。",
                    )
                ),
                404,
                JSON_CONTENT_TYPE,
            )
        data = target.read_bytes()
        if len(data) > MAX_STATIC_BYTES:
            return (
                envelope.dumps(envelope.failure(code="BAD_REQUEST", message="静态资源过大")),
                413,
                JSON_CONTENT_TYPE,
            )
        return data, 200, CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")

    # ------------------------------------------------------------ 底层

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        # **统一响应出口脱敏**（复验 N1）：凭据不但不能进日志，也不能进响应体——
        # 面板数据、降级卡片、错误 hint 都可能夹带 token。
        if self.server.secrets and (
            content_type.startswith("text/") or content_type.startswith("application/json")
        ):
            try:
                text = body.decode("utf-8")
            except UnicodeDecodeError:
                # 非 UTF-8 的文本资源：宁可原样返回，也不能用 replace 解码把字节改坏（复验 P2）。
                text = None
            if text is not None:
                body = redact(text, self.server.secrets).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # 数据新鲜度由我们自己的指纹与 meta 管，交给浏览器缓存会两边打架。
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _log(self, message: str) -> None:
        logger = getattr(self.server, "log_sink", None)
        text = redact(message, getattr(self.server, "secrets", ()))
        if logger is not None:
            logger(text)
        else:  # pragma: no cover - 兜底
            print(text)

    def log_message(self, fmt, *args):  # noqa: A003（覆盖父类，默认实现往 stderr 打）
        self._log(f"{self.address_string()} {fmt % args}")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self, address, handler_class, *, config, registry, secrets, log_sink=None,
        static_dir=None,
    ):
        self.config = config
        self.registry = registry
        self.secrets = secrets
        self.log_sink = log_sink
        self.static_dir = Path(static_dir) if static_dir else STATIC_DIR
        super().__init__(address, handler_class)


class WebUIServer:
    """把 HTTP 服务的生命周期收成一个对象：起、停、拿真实端口。"""

    def __init__(
        self, config, registry, *, env=None, log_sink=None, static_dir=None, echo_logs=True
    ):
        self.config = config
        self.registry = registry
        self.secrets = collect_secrets(dict(os.environ if env is None else env))
        # 环形缓冲：日志不能无上界地吃内存（独立验收 D4）。
        self.logs: deque = deque(maxlen=LOG_BUFFER_LINES)
        self.echo_logs = echo_logs

        def default_sink(text: str) -> None:
            """写内存 + 写 stderr：500 响应的 hint 说「见服务端日志」，就得真的有日志。"""
            self.logs.append(text)
            if self.echo_logs:
                print(text, file=sys.stderr, flush=True)

        sink = log_sink or default_sink
        try:
            self._httpd = _Server(
                (config.host, config.port),
                _Handler,
                config=config,
                registry=registry,
                secrets=self.secrets,
                log_sink=sink,
                static_dir=static_dir,
            )
        except OSError as exc:
            # 只有「地址已被占用」才叫端口占用；地址不可用（例如 IPv6 环回）时
            # 让用户去换端口是错的建议（独立验收 D5）。
            if exc.errno in (errno.EADDRINUSE, errno.EACCES):
                raise PortInUse(
                    f"端口 {config.port} 无法绑定（已被占用或无权限）",
                    hint=f"换一个 --port，或先停掉占用该端口的进程（不静默换端口）。{exc}",
                ) from exc
            raise BindFailed(
                f"地址 {config.host}:{config.port} 无法绑定",
                hint=f"这不是端口占用问题，换端口不会解决：{exc}",
            ) from exc
        self._thread: threading.Thread | None = None
        # `BaseServer.shutdown()` 在 `serve_forever()` 从未启动时会**永久阻塞**
        # （它等的是一个只有 serve_forever 退出时才 set 的事件）。所以自己记状态。
        self._serving = False

    @property
    def port(self) -> int:
        """真实端口（`--port 0` 时由内核分配）。"""
        return self._httpd.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://{self.config.host}:{self.port}"

    def start_background(self) -> threading.Thread:
        if self._thread is None:
            self._serving = True
            self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
            self._thread.start()
        return self._thread

    def serve_forever(self) -> None:
        self._serving = True
        self._httpd.serve_forever()

    def shutdown(self) -> None:
        if self._serving:
            self._httpd.shutdown()
            self._serving = False
        self._httpd.server_close()

    def __enter__(self) -> "WebUIServer":
        self.start_background()
        return self

    def __exit__(self, *exc_info) -> None:
        self.shutdown()
