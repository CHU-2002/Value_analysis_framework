# 覆盖需求：REQ-009.3（可扩展框架与本地数据层）—— AC-3.1 内核与启动、AC-3.2 注册表与扩展点、
# AC-3.3 声明式面板协议、AC-3.6 API 契约与稳定错误码、AC-3.7 安全中间件；
# 以及父需求 AC-9（新增 GUI 功能不得修改框架核心，由「演示插件 + 核心文件指纹」判定）
"""框架层测试：内核、注册表、面板协议、契约、安全。

两条刻意的手法：

1. **不起浏览器**：表格/时间线/指标卡由 Python 渲染成 HTML 片段，
   所以「渲染出来的东西对不对」在 CI 里可以直接断言；
2. **不开端口也能测路由**：handler 只依赖 `RequestContext`，可以直接调用；
   只有真正verify HTTP 边界（信封、404、静态资源 jail）的用例才起服务，且绑定 `127.0.0.1:0`。
"""

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from webui import SCHEMA_VERSION, __version__
from webui.config import Config, ConfigError, is_loopback, load_config
from webui.core import envelope, models, security
from webui.core.context import RequestContext
from webui.core.errors import CODE_SET, InvalidParam, PathOutsideRoot, PortInUse, WebUIError
from webui.core.registry import build_registry
from webui.core.router import find_route
from webui.core.routes import install_core_routes
from webui.core.server import WebUIServer
from webui.plugins import PluginLoadError, load_plugins

REPO_ROOT = Path(__file__).resolve().parents[1]
WEBUI_ROOT = REPO_ROOT / "scripts" / "webui"
FINGERPRINT_PATH = Path(__file__).resolve().parent / "fixtures" / "webui_core_fingerprint.json"
# 只走直连：见 http_get 的说明。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 「扩展面」：加一个功能**不许碰**这些文件。
# `models/errors/config` 不在此列——它们本来就该随框架演进（新错误码、新配置项）；
# 真正的判据是「注册与分发的实现不需要改」。
FINGERPRINTED_FILES = (
    "core/registry.py",
    "core/router.py",
    "core/routes.py",
    "core/server.py",
    "static/index.html",
    "static/app.js",
)

DEMO_PLUGIN = '''
"""演示插件：只做声明，不碰核心。"""

from webui.core import envelope
from webui.core.models import DatasetSpec, NavItem, PanelSpec, Param


def _table(ctx, company):
    return {
        "columns": [{"key": "name", "title": "指标"},
                    {"key": "value", "title": "数值", "align": "right"}],
        "rows": [{"name": "公司", "value": company}, {"name": "来源", "value": "demo"}],
    }


def _ping(ctx, **_):
    return envelope.ok({"pong": True})


def contribute(registry):
    registry.dataset(DatasetSpec(name="demo.table", sources=("data_pack_market.md",),
                                 parser="demo.table", parser_version=1))
    registry.panel(PanelSpec(
        id="demo.table", kind="table", title="演示表",
        provider=_table, params=(Param("company", type="company", required=True),),
    ))
    registry.panel(PanelSpec(id="demo.future", kind="heatmap", title="未来图表"))
    registry.nav(NavItem(id="demo", title="演示页", group="演示", order=999,
                         panels=("demo.table", "demo.future")))
    registry.route("GET", "/api/v1/demo/ping", _ping)
'''

BROKEN_PLUGIN = '''
def contribute(registry):
    raise RuntimeError("这个插件坏了")
'''


# --------------------------------------------------------------- 测试辅助


def make_config(tmp_path: Path, **overrides) -> Config:
    base = {
        "host": "127.0.0.1",
        "port": 0,
        "output_root": tmp_path / "output",
        "cache_dir": tmp_path / "output" / ".webui_cache",
        "archive_root": tmp_path / "archive",
    }
    base.update(overrides)
    return Config(**base)


def attach_config(registry, config):
    """把配置挂到注册表对象上，只为 `call_route` 方便取用（不参与任何业务逻辑）。"""
    registry.config = config
    return registry


def make_registry(config: Config):
    return attach_config(build_registry(), config)


def make_app(config: Config):
    registry = make_registry(config)
    install_core_routes(registry, config)
    return registry


def call_route(registry, method: str, path: str, **query):
    """不起 HTTP，直接调用路由 handler（handler 只依赖 RequestContext）。"""
    route, params = find_route(registry.routes(), method, path)
    assert route is not None, f"没有匹配的路由：{method} {path}"
    ctx = RequestContext(
        method=method, path=path, query=dict(query), config=registry.config, registry=registry
    )
    return route.handler(ctx, **params)


def write_plugin(directory: Path, name: str, source: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(source, encoding="utf-8")
    return path


def http_get(server: WebUIServer, path: str):
    """返回 (status, payload_or_bytes, content_type)；4xx/5xx 照常返回，不抛。

    显式禁用代理：本机回环请求不该被 `HTTP_PROXY` 环境变量牵去绕一圈
    （在有代理的机器上会让每个请求都慢几秒）。
    """
    request = urllib.request.Request(f"{server.base_url}{path}")
    try:
        with _OPENER.open(request, timeout=10) as response:
            body, status = response.read(), response.status
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        body, status = exc.read(), exc.code
        content_type = exc.headers.get("Content-Type", "")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = body
    return status, payload, content_type


def fingerprint() -> dict:
    return {
        relative: hashlib.sha256((WEBUI_ROOT / relative).read_bytes()).hexdigest()
        for relative in FINGERPRINTED_FILES
    }


# --------------------------------------------------------------- AC-3.1 内核与启动


def test_config_rejects_non_loopback_host_and_bad_port(tmp_path):
    """AC-3.1：非环回地址与非法端口都要在启动前失败（不提供放开边界的开关）。"""
    assert is_loopback("127.0.0.1") and is_loopback("localhost") and is_loopback("::1")
    assert not is_loopback("0.0.0.0") and not is_loopback("192.168.1.10")

    config_file = tmp_path / "webui.config.json"
    config_file.write_text(json.dumps({"host": "0.0.0.0"}), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config_file, env={})
    config_file.write_text(json.dumps({"port": 70000}), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config_file, env={})
    config_file.write_text(json.dumps({"host": "::1", "port": 0}), encoding="utf-8")
    assert load_config(config_file, env={}).port == 0
    config_file.write_text("{ 这不是 JSON", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config_file, env={})


def test_check_mode_assembles_without_binding_a_port(capsys):
    """AC-3.1：`--check` 只装配（配置 + 插件 + 注册表），不绑定端口。"""
    from webui.__main__ import main

    assert main(["--check", "--no-browser"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == __version__
    assert "GET /api/v1/healthz" in payload["routes"]
    assert payload["host"] == "127.0.0.1"
    assert payload["plugins"] == []


def test_cli_rejects_a_non_loopback_host_argument(capsys):
    """AC-3.1：命令行也不能把服务暴露到非环回地址。"""
    from webui.__main__ import main

    assert main(["--check", "--host", "0.0.0.0"]) == 2
    assert "不是环回地址" in capsys.readouterr().err


def test_port_in_use_is_reported_not_silently_swapped(tmp_path):
    """AC-3.1：端口被占用必须明确报错（不静默换端口）。"""
    config = make_config(tmp_path)
    first = WebUIServer(config, make_app(config))
    try:
        busy = make_config(tmp_path, port=first.port)
        with pytest.raises(PortInUse):
            WebUIServer(busy, make_app(busy))
    finally:
        first.shutdown()


def test_http_surface_serves_healthz_page_and_static(tmp_path):
    """AC-3.1 + AC-3.3：真实 HTTP 下 healthz 带版本、页面带已渲染面板、首页是静态入口。"""
    config = make_config(tmp_path)
    registry = make_app(config)
    registry.panel(
        models.PanelSpec(
            id="demo.stat",
            kind="stat",
            title="指标",
            provider=lambda ctx, **_: {"items": [{"label": "完备度", "value": "12/14"}]},
        )
    )
    registry.nav(models.NavItem(id="home", title="首页", panels=("demo.stat",)))
    with WebUIServer(config, registry) as server:
        assert server.port > 0 and server.base_url.startswith("http://127.0.0.1:")

        status, payload, content_type = http_get(server, "/api/v1/healthz")
        assert status == 200 and payload["data"]["version"] == __version__
        assert content_type.startswith("application/json")

        status, page, _ = http_get(server, "/api/v1/pages/home")
        assert status == 200 and page["data"]["schema"] == "webui.page"
        assert "12/14" in page["data"]["panels"][0]["html"]

        status, index, content_type = http_get(server, "/")
        assert status == 200 and b"<html" in index.lower()
        assert content_type.startswith("text/html")


# --------------------------------------------------------------- AC-3.2 注册表与扩展点


def test_registry_accepts_all_six_extension_points(tmp_path):
    """AC-3.2：六类注册点都能注册，且查询接口看得到。"""
    registry = make_app(make_config(tmp_path))
    with registry.contribution_from("test"):
        registry.nav(models.NavItem(id="p", title="页"))
        registry.panel(models.PanelSpec(id="p.t", kind="table"))
        registry.dataset(models.DatasetSpec(name="d", sources=("a.md",)))
        registry.command(models.CommandSpec(id="c", argv=("python", "-V")))
        registry.job_type("batch", lambda *args, **kwargs: None)
        registry.route("GET", "/api/v1/x", lambda ctx, **_: envelope.ok({}))

    assert [item.id for item in registry.nav_items()] == ["p"]
    assert registry.panel_spec("p.t").kind == "table"
    assert registry.dataset_spec("d").sources == ("a.md",)
    assert [spec.id for spec in registry.commands()] == ["c"]
    assert registry.job_types() == ("batch",)
    assert any(route.template == "/api/v1/x" for route in registry.routes())


def test_duplicate_ids_are_rejected_and_name_both_sources(tmp_path):
    """AC-3.2：同 id 重复注册必须报错并指明两个来源，不许静默覆盖。"""
    registry = make_app(make_config(tmp_path))
    with registry.contribution_from("plugin-a"):
        registry.panel(models.PanelSpec(id="dup", kind="table"))
    with registry.contribution_from("plugin-b"):
        with pytest.raises(ValueError) as excinfo:
            registry.panel(models.PanelSpec(id="dup", kind="table"))
    message = str(excinfo.value)
    assert "plugin-a" in message and "plugin-b" in message and "dup" in message


def test_demo_plugin_from_a_directory_takes_effect(tmp_path):
    """AC-3.2 / AC-9：临时目录里的插件（不碰核心）注册的导航/面板/路由/数据集自动生效。"""
    registry = make_app(make_config(tmp_path))
    report = load_plugins(registry, extra_dirs=(write_plugin(tmp_path / "plugins", "demo.py",
                                                             DEMO_PLUGIN).parent,))
    assert [error for _, error in report] == [None]

    assert [item.id for item in registry.nav_items()] == ["demo"]
    assert registry.dataset_spec("demo.table").parser_version == 1
    assert [panel["id"] for panel in registry.page_payload("demo")["panels"]] == [
        "demo.table", "demo.future",
    ]
    assert call_route(registry, "GET", "/api/v1/demo/ping")["data"] == {"pong": True}
    rendered = call_route(registry, "GET", "/api/v1/panels/demo.table", company="600887")
    assert "600887" in rendered["data"]["html"]


def test_broken_plugin_is_isolated_but_conflict_fails_startup(tmp_path):
    """AC-3.2：坏插件只让它自己失效；注册冲突则必须让启动失败。"""
    registry = make_app(make_config(tmp_path))
    plugin_dir = tmp_path / "plugins"
    write_plugin(plugin_dir, "broken.py", BROKEN_PLUGIN)
    write_plugin(plugin_dir, "demo.py", DEMO_PLUGIN)

    report = dict(load_plugins(registry, extra_dirs=(plugin_dir,)))
    assert any("这个插件坏了" in (error or "") for error in report.values())
    assert registry.has_panel("demo.table")          # 好的插件照常生效

    conflict_dir = tmp_path / "conflict"
    write_plugin(conflict_dir, "clash.py", DEMO_PLUGIN)
    with pytest.raises(PluginLoadError):
        load_plugins(registry, extra_dirs=(conflict_dir,))


def test_adding_a_feature_does_not_touch_the_core(tmp_path):
    """AC-9：加载一个完整插件前后，扩展面文件的指纹**不变**。"""
    registry = make_app(make_config(tmp_path))
    plugin_dir = write_plugin(tmp_path / "plugins", "demo.py", DEMO_PLUGIN).parent
    before = fingerprint()
    load_plugins(registry, extra_dirs=(plugin_dir,))
    assert fingerprint() == before


def test_core_fingerprint_matches_the_recorded_manifest():
    """AC-9：扩展面文件的指纹必须与记录一致。

    改了 `core/registry.py` / `router.py` / `routes.py` / `server.py` / `index.html` / `app.js`
    就要同步这份清单，并在 PR 里说明**为什么这次必须动核心**——
    这道摩擦是故意的：它让「加功能偷偷改核心」无处藏身。
    """
    recorded = json.loads(FINGERPRINT_PATH.read_text(encoding="utf-8"))
    assert fingerprint() == recorded, (
        "扩展面文件变了。若这是**框架本身的演进**，请更新 "
        f"{FINGERPRINT_PATH.relative_to(REPO_ROOT)} 并在 PR 说明理由；"
        "若这是为了加一个新功能，那说明框架有洞——先修框架，别动核心。"
    )


# --------------------------------------------------------------- AC-3.3 面板协议


def test_page_payload_renders_three_kinds_and_escapes_content(tmp_path):
    """AC-3.3：页面描述是纯数据；表格/时间线/指标卡都服务端渲染，且内容被转义。"""
    registry = make_app(make_config(tmp_path))
    registry.panel(
        models.PanelSpec(
            id="t", kind="table", title="表",
            provider=lambda ctx, **_: {
                "columns": [{"key": "k", "title": "列"}],
                "rows": [{"k": "<script>alert(1)</script>"}],
            },
        )
    )
    registry.panel(
        models.PanelSpec(
            id="tl", kind="timeline", title="时间线",
            provider=lambda ctx, **_: {"items": [{"at": "2026-09-20", "title": "增量更新",
                                                "badges": ["report-update"],
                                                "changes": ["2026H1：归母 -20.02%"]}]},
        )
    )
    registry.panel(
        models.PanelSpec(id="st", kind="stat", title="指标",
                         provider=lambda ctx, **_: {"items": [{"label": "完备度", "value": "12/14",
                                                             "state": "warn"}]})
    )
    registry.nav(models.NavItem(id="page", title="页", panels=("t", "tl", "st")))

    page = call_route(registry, "GET", "/api/v1/pages/page")["data"]
    assert page["schema_version"] == SCHEMA_VERSION
    assert [panel["kind"] for panel in page["panels"]] == ["table", "timeline", "stat"]
    html = {panel["id"]: panel["html"] for panel in page["panels"]}
    assert "<table>" in html["t"] and "&lt;script&gt;" in html["t"]   # 转义，不执行
    assert "timeline-item" in html["tl"] and "归母 -20.02%" in html["tl"]
    assert "state-warn" in html["st"]


def test_unknown_kind_degrades_to_a_readable_card(tmp_path):
    """AC-3.3：未知 kind 降级成可读卡片——不白屏、不 500。"""
    registry = make_app(make_config(tmp_path))
    registry.panel(models.PanelSpec(id="future", kind="heatmap", title="未来图"))
    registry.nav(models.NavItem(id="page", title="页", panels=("future",)))
    payload = call_route(registry, "GET", "/api/v1/panels/future")
    assert payload["ok"] is True
    panel = payload["data"]
    assert panel["render"] == "server" and panel["fallback"] is True
    assert "panel-fallback" in panel["html"] and "heatmap" in panel["html"]


def test_chart_kind_is_client_rendered_with_a_data_contract(tmp_path):
    """AC-3.3：图表交给浏览器画，服务端只回 {labels, series} 数据契约。"""
    registry = make_app(make_config(tmp_path))
    registry.panel(
        models.PanelSpec(
            id="chart", kind="chart", title="年度行情",
            options={"chart": {"type": "line"}},
            provider=lambda ctx, **_: {"labels": ["2025", "2026"],
                                      "series": [{"name": "年末收盘", "values": [28.6, 26.6]}]},
        )
    )
    panel = call_route(registry, "GET", "/api/v1/panels/chart")["data"]
    assert panel["render"] == "client"
    assert panel["data"]["labels"] == ["2025", "2026"]
    assert panel["options"]["chart"]["type"] == "line"


def test_kinds_endpoint_declares_the_fallback_policy(tmp_path):
    """AC-3.3：前端能问「服务端认识哪些 kind、未知怎么办」。"""
    data = call_route(make_app(make_config(tmp_path)), "GET", "/api/v1/kinds")["data"]
    assert {"table", "timeline", "stat"} <= set(data["server_kinds"])
    assert "chart" in data["client_kinds"]
    assert data["fallback_kind"] == "fallback"
    assert data["unknown_kind_policy"] == "degrade_to_fallback_card"


# --------------------------------------------------------------- AC-3.6 API 契约


def test_envelope_shapes_and_error_codes_are_stable():
    """AC-3.6：信封形状固定；错误码是封闭枚举，不允许把异常文本当错误码。"""
    good = envelope.ok({"a": 1}, warnings=["w"], meta={"cached": True})
    assert set(good) == {"ok", "schema_version", "data", "warnings", "meta", "error"}
    assert good["ok"] is True and good["error"] is None

    bad = envelope.failure(InvalidParam("缺参数", hint="补上"))
    assert set(bad) == {"ok", "schema_version", "data", "warnings", "meta", "error"}
    assert bad["ok"] is False and bad["data"] is None
    assert bad["error"] == {"code": "INVALID_PARAM", "message": "缺参数", "hint": "补上"}

    declared = set()
    stack = [WebUIError]
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        if cls.__dict__.get("code"):
            declared.add(cls.code)
    assert declared == set(CODE_SET), "错误码枚举与异常类必须一一对应（新增要同时改两处）"


def test_unknown_route_missing_param_and_metachars_return_their_own_codes(tmp_path):
    """AC-3.6 + AC-3.7：未知路由 404、缺必填参数 422、参数里的 shell 元字符 400。"""
    config = make_config(tmp_path)
    registry = make_app(config)
    registry.panel(
        models.PanelSpec(
            id="p", kind="table", params=(models.Param("company", required=True),),
            provider=lambda ctx, company: {"columns": [{"key": "a", "title": "A"}], "rows": []},
        )
    )
    with WebUIServer(config, registry) as server:
        status, payload, content_type = http_get(server, "/api/v1/nope")
        assert status == 404 and payload["error"]["code"] == "UNKNOWN_ROUTE"
        assert content_type.startswith("application/json")

        status, payload, _ = http_get(server, "/api/v1/panels/p")
        assert status == 422 and payload["error"]["code"] == "INVALID_PARAM"

        status, payload, _ = http_get(server, "/api/v1/panels/p?company=600887%3Brm%20-rf")
        assert status == 400 and payload["error"]["code"] == "SHELL_METACHAR"


def test_unexpected_exception_becomes_internal_without_leaking_details(tmp_path):
    """AC-3.6：未预期异常只回 INTERNAL + 请求 id，堆栈与内部细节只进服务端日志。"""

    def boom(ctx, **_):
        raise RuntimeError("内部实现细节：/secret/path")

    config = make_config(tmp_path)
    registry = make_app(config)
    registry.route("GET", "/api/v1/boom", boom)
    with WebUIServer(config, registry) as server:
        status, payload, _ = http_get(server, "/api/v1/boom")
        assert status == 500 and payload["error"]["code"] == "INTERNAL"
        serialized = json.dumps(payload, ensure_ascii=False)
        assert "内部实现细节" not in serialized and "/secret/path" not in serialized
        assert any("未处理异常" in line for line in server.logs)


# --------------------------------------------------------------- AC-3.7 安全中间件


def test_safe_join_blocks_traversal_absolute_paths_and_symlinks(tmp_path):
    """AC-3.7：`..`、绝对路径、指向树外的符号链接一律拒绝。"""
    root = tmp_path / "output"
    (root / "ok").mkdir(parents=True)
    (root / "ok" / "a.txt").write_text("hi", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - 平台不支持符号链接
        pytest.skip("本平台不支持创建符号链接")

    assert security.safe_join(root, "ok", "a.txt").read_text(encoding="utf-8") == "hi"
    for bad in (
        ("..", "outside", "secret.txt"),
        (str(outside / "secret.txt"),),
        ("link", "secret.txt"),
    ):
        with pytest.raises(PathOutsideRoot):
            security.safe_join(root, *bad)


def test_static_symlink_escape_is_rejected_over_http(tmp_path):
    """AC-3.7：静态目录里的符号链接指向仓库外 → 403（jail 到 static/ 且按真实路径判定）。"""
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        (static_dir / "leak.txt").symlink_to(outside)
    except (OSError, NotImplementedError):  # pragma: no cover
        pytest.skip("本平台不支持创建符号链接")

    config = make_config(tmp_path)
    with WebUIServer(config, make_app(config), static_dir=static_dir) as server:
        status, payload, _ = http_get(server, "/leak.txt")
        assert status == 403 and payload["error"]["code"] == "PATH_OUTSIDE_ROOT"
        index_status, _, _ = http_get(server, "/index.html")
        assert index_status == 200


def test_secrets_are_redacted_and_only_a_fingerprint_is_kept():
    """AC-3.7 / AC-4.7：日志与响应里不出现 token；只保留 8 位指纹。"""
    token = "abcdef0123456789abcdef0123456789"
    secrets_ = security.collect_secrets({"TUSHARE_TOKEN": token})
    assert security.redact(f"使用 {token} 拉取", secrets_) == "使用 *** 拉取"
    assert security.collect_secrets({"TUSHARE_TOKEN": "短"}) == ()

    fingerprint_value = security.token_fingerprint(token)
    assert len(fingerprint_value) == 8 and token not in fingerprint_value
    assert fingerprint_value == security.token_fingerprint(token)      # 稳定
    assert security.token_fingerprint("") == ""


def test_request_log_redacts_tokens(tmp_path):
    """AC-3.7：请求日志经过脱敏（token 不能靠日志泄漏）。"""
    token = "abcdef0123456789abcdef0123456789"
    config = make_config(tmp_path)
    with WebUIServer(config, make_app(config), env={"TUSHARE_TOKEN": token}) as server:
        http_get(server, f"/api/v1/nope?token={token}")
        assert server.logs, "应该有请求日志"
        assert token not in "\n".join(server.logs)
