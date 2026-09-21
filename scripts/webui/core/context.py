"""一次请求的上下文：handler 只看得到它——配置、注册表、查询参数、请求体。

刻意做成纯数据对象（而不是把 `BaseHTTPRequestHandler` 传进 handler）：
handler 因此可以在**不起 HTTP 服务**的情况下被直接调用与断言，
测试不用为了验一条路由去开端口。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RequestContext:
    method: str
    path: str
    query: dict = field(default_factory=dict)
    body: bytes = b""
    config: object = None
    registry: object = None
    request_id: str = ""
    secrets: tuple = ()
    # 服务端日志出口（可调用对象）。handler 在**吞掉**异常时必须用它留痕，
    # 否则 500 响应里「细节见服务端日志」就是一句空话（独立验收 D4）。
    log: object = None
