"""只读报告阅读契约（REQ-015.3）；正式版本来自发布指针，不以文件排序猜测。"""
from __future__ import annotations

import base64
import difflib
import hashlib
import json
from pathlib import Path

from ..core.errors import ArtifactMissing, BadRequest, NotFound, WebUIError
from ..core.models import DatasetSpec
from ..core.security import redact, safe_join
from ..datastore import parsers
from ..render import markdown_safe

# 固定源模式覆盖索引、所有指针/台账/运行与发布元信息，以及正文摘要。
# 不把用户的路径/id插入glob；每个分片仍以公司base和已验证版本id隔离。
SOURCES = ("*", "runs/*/*", "value_reports/*/*/*", "value_reports/*/*/artifacts/*")
CATALOG_VERSION = 1
DOCUMENT_VERSION = 1

TYPES = {"business": "商业质量", "value": "价值分析", "change": "变化报告", "material": "源材料"}


def report_type(item):
    name, rel = item["name"].lower(), item["rel"].lower()
    if "change_report" in name or "变化报告" in name:
        return "change"
    if "价值分析报告" in name or (rel.startswith("value_reports/") and name == "report.md"):
        return "value"
    if name == "qualitative_report.md" or "business_analysis" in name:
        return "business"
    return "material"


def _json(base, relative):
    path = safe_join(base, *Path(relative).parts)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _history(base):
    path = safe_join(base, "history.jsonl")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def _bound_rel(base, raw):
    """指针允许既有绝对/相对路径，但必须解析到此公司 jail。"""
    if not isinstance(raw, str) or not raw:
        return ""
    path = Path(raw)
    if path.is_absolute():
        try:
            raw = path.relative_to(base).as_posix()
        except ValueError:
            return ""
    # runs 历史也可能记录 output/<company>/...，按公司边界转换，不按 basename 猜。
    prefix = base.as_posix() + "/"
    if raw.startswith(prefix):
        raw = raw[len(prefix):]
    elif raw.startswith("output/" + base.name + "/"):
        raw = raw[len("output/" + base.name + "/"):]
    try:
        path = safe_join(base, *Path(raw).parts)
        return path.relative_to(base.resolve()).as_posix()
    except (WebUIError, ValueError):
        return ""


def _official(base, artifacts, latest, value):
    by_rel = {item["rel"]: item for item in artifacts}
    result = {}
    for kind in ("business", "change"):
        key = "report" if kind == "business" else "change_report"
        bindings = latest.get("artifacts")
        raw = bindings.get(key) if isinstance(bindings, dict) else None
        rel = _bound_rel(base, raw)
        if not rel and latest.get("run_id"):
            filename = "qualitative_report.md" if kind == "business" else "change_report.md"
            rel = f"runs/{latest['run_id']}/{filename}"
        item = by_rel.get(rel)
        if item and report_type(item) == kind:
            result[kind] = item["id"]
    rel = _bound_rel(base, value.get("report"))
    item = by_rel.get(rel)
    if item and report_type(item) == "value":
        # 发布指针记录摘要；摘要缺失或不匹配时不冒充可用正式版。
        digest = value.get("report_sha256")
        if digest and hashlib.sha256(safe_join(base, *Path(rel).parts).read_bytes()).hexdigest() == digest:
            result["value"] = item["id"]
    return result


def catalog(ctx, company):
    from .companies import resolve_company, companies_dataset
    try:
        resolved = resolve_company(ctx, company)
    except NotFound:
        known = next((item for item in companies_dataset(ctx)[0]["companies"]
                      if item.get("ticker") == company), None)
        if known is None:
            raise
        return {**known, "base": None}, [], {}, {"empty": True}
    base = resolved["base"]
    try:
        data, meta = ctx.registry.datastore.get("report_reader.catalog", base=base,
                                                params={"base": str(base)})
    except ArtifactMissing:
        data, meta = {"versions": [], "official": {}}, {"empty": True}
    return resolved, data["versions"], data["official"], meta


def _source_paths(sources, base):
    """统一数据层以output为边界；阅读版本仍严格绑定到此公司jail。"""
    result = []
    for source in sources:
        source = Path(source)
        try:
            relative = source.relative_to(base)
        except ValueError as exc:
            raise BadRequest("报告数据源不属于当前公司") from exc
        result.append(safe_join(base, *relative.parts))
    return result


def parse_catalog(sources, params):
    from .companies import parse_artifacts
    base = Path(params["base"]).resolve()
    _source_paths(sources, base)
    data = parse_artifacts(sources, {"base": str(base)})
    latest, value = _json(base, "latest.json"), _json(base, "value_report.json")
    record = _json(base, "record.json")
    history = _history(base)
    run_rows = {str(row.get("run_id")): row for row in history}
    official = _official(base, data["artifacts"], latest, value)
    versions = []
    for item in data["artifacts"]:
        kind = report_type(item)
        parts = Path(item["rel"]).parts
        row, origin = {}, "未知"
        if len(parts) >= 3 and parts[0] == "runs":
            row = {**_json(base, str(Path(*parts[:2]) / "run.json")), **run_rows.get(parts[1], {})}
            origin = "run " + parts[1]
        elif len(parts) >= 4 and parts[0] == "value_reports" and item["name"] == "report.md":
            row = _json(base, str(Path(item["rel"]).parent / "manifest.json"))
            origin = "run " + str(row.get("source_run") or parts[1])
        elif official.get(kind) == item["id"]:
            row = value if kind == "value" else latest
            origin = "run " + str(row.get("source_run") or row.get("run_id") or "未知")
        status = "未知"
        if kind == "value" and _bound_rel(base, value.get("report")) == item["rel"] and official.get(kind) != item["id"]:
            status = "不可用"
        elif kind == "value" and row.get("report_sha256") and hashlib.sha256(safe_join(base, *parts).read_bytes()).hexdigest() != row["report_sha256"]:
            status = "不可用"
        elif official.get(kind) == item["id"]:
            downstream = record.get("downstream")
            stale = bool(downstream.get("stale")) if kind == "value" and isinstance(downstream, dict) else False
            if kind == "value" and latest.get("run_id") and value.get("source_run") != latest.get("run_id"):
                stale = True
            status = "不可用" if row.get("status") in ("failed", "partial") else ("过期" if stale else "有效")
        elif row.get("status"):
            status = "有效" if row["status"] == "complete" else "不可用"
        elif row.get("published_at"):
            status = "有效"
        version = {**item, "type": kind, "type_label": TYPES[kind],
                   "period": row.get("primary_period") or "未知",
                   "generated_at": row.get("created_at") or row.get("generated_at") or row.get("published_at") or "未知",
                   "data_as_of": row.get("as_of") or row.get("data_as_of") or "未知",
                   "source": origin, "status": status, "official": official.get(kind) == item["id"]}
        # 不依赖正文/路径的可见版本号；未知元信息仍显式未知。
        version["label"] = f"{version['period']} · {version['generated_at']} · {origin} · {status} · 版本 {item['id']}"
        versions.append(version)
    return {"versions": versions, "official": official}


def parse_document(sources, params):
    from .companies import _artifact_id
    base = Path(params["base"]).resolve()
    relative = params.get("relative") or ""
    target = safe_join(base, *Path(relative).parts)
    allowed = _source_paths(sources, base)
    if target not in allowed or params.get("id") != _artifact_id(relative):
        raise NotFound("所选文档不属于当前公司的报告数据源")
    if Path(relative).suffix.lower() not in (".md", ".markdown"):
        raise BadRequest("源材料不能进入 Markdown 渲染器")
    text = target.read_text(encoding="utf-8", errors="replace")
    html, toc = markdown_safe.render_document(text)
    return {"text": text, "html": html, "toc": toc}


def document(ctx, base, item):
    # 缓存命中前也验证版本的路径与id，不让派生缓存成为绕过jail的入口。
    from .companies import _artifact_id
    relative = item["rel"]
    safe_join(base, *Path(relative).parts)
    if not item["markdown"] or item["id"] != _artifact_id(relative):
        raise BadRequest("所选版本不是合法 Markdown 报告")
    return ctx.registry.datastore.get("report_reader.document", base=base,
                                     params={"base": str(base), "relative": relative, "id": item["id"]})


def contribute(registry):
    """由companies插件装配；全部派生通过既有DataStore，无核心变更。"""
    parsers.register_parser("report_reader.catalog", parse_catalog, replace=True)
    parsers.register_parser("report_reader.document", parse_document, replace=True)
    for name, version in (("report_reader.catalog", CATALOG_VERSION),
                          ("report_reader.document", DOCUMENT_VERSION)):
        if name not in {spec["name"] for spec in registry.datasets()}:
            registry.dataset(DatasetSpec(name=name, sources=SOURCES, parser=name,
                                         parser_version=version, hash_strategy="sha256"))


def payload(ctx, company, artifact_id=None, run_id=None, kind=None):
    resolved, versions, official, meta = catalog(ctx, company)
    selected = None
    if artifact_id:
        selected = next((item for item in versions if item["id"] == artifact_id), None)
        if selected is None:
            raise NotFound("此版本不属于当前公司或已不可用", hint="从当前公司的版本列表重新选择。")
        kind = selected["type"]
    elif run_id:
        selected = next((item for item in versions if item["rel"] == f"runs/{run_id}/qualitative_report.md"), None)
        kind = "business"
    kind = kind or "business"
    if kind not in TYPES:
        raise BadRequest("未知报告类型")
    if not selected and not artifact_id and not run_id:
        selected = next((item for item in versions if item["id"] == official.get(kind)), None)
    note = "" if selected else ("本次迭代报告不可用，请选择历史版本。" if run_id else "无法解析此类型的正式发布指针；可选择历史版本，未猜测最新版。")
    html, toc = "", []
    if selected and selected["markdown"]:
        rendered, document_meta = document(ctx, resolved["base"], selected)
        html, toc = rendered["html"], rendered["toc"]
        meta = {**meta, "document": document_meta,
                "cached": bool(meta.get("cached") and document_meta.get("cached"))}
    return {"types": [{"id": key, "label": label} for key, label in TYPES.items()],
            "type": kind, "versions": versions, "artifact": selected,
            "title": selected["name"] if selected else "选择报告版本", "html": html, "toc": toc,
            "notice": note, "company": {key: val for key, val in resolved.items() if key != "base"}, "meta": meta}


def compare(ctx, company, left, right):
    resolved, versions, _, _ = catalog(ctx, company)
    chosen = [next((item for item in versions if item["id"] == value), None) for value in (left, right)]
    if not all(chosen):
        raise NotFound("比较版本必须属于当前公司")
    first, second = chosen
    if left == right:
        raise BadRequest("请选择两个不同版本")
    if first["type"] != second["type"] or first["type"] == "material" or not all(item["markdown"] for item in chosen):
        raise BadRequest("只能比较同公司、同类型的 Markdown 报告")
    texts = [document(ctx, resolved["base"], item)[0]["text"].splitlines() for item in chosen]
    diff = "\n".join(difflib.unified_diff(*texts, fromfile="左版", tofile="右版", lineterm=""))
    return {"left": first, "right": second, "diff": diff, "notice": "以下仅为正文差异，不代表业务结论或新增投资判断。",
            "changes": [item for item in versions if item["type"] == "change"]}


def content(ctx, company, artifact_id):
    resolved, versions, _, _ = catalog(ctx, company)
    item = next((item for item in versions if item["id"] == artifact_id), None)
    if item is None:
        raise NotFound("此下载版本不属于当前公司")
    path = safe_join(resolved["base"], *Path(item["rel"]).parts)
    raw = path.read_bytes()
    # Base64 encoding bypasses the shared JSON response redactor. Reject a
    # credential-bearing source rather than silently rewriting its bytes.
    text = raw.decode("utf-8", errors="replace")
    if redact(text, ctx.secrets) != text:
        raise BadRequest("此材料包含已知凭据，不能打开或下载；请先修正源材料。")
    # HTML/SVG 等可执行材料只给 attachment Blob，PDF 可用原生浏览器打开。
    return {"name": item["name"], "content_type": item["content_type"], "openable": path.suffix.lower() in (".pdf", ".json", ".md", ".markdown", ".txt"),
            "base64": base64.b64encode(raw).decode("ascii")}
