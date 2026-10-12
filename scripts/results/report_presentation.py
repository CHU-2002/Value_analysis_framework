"""REQ-016.2 读者摘要：呈现已核验事项，不代替 REQ-017 的跨材料识别。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


STATES = {"disclosed": "已披露事实", "regulator_confirmed": "监管确认事实",
          "company_explanation": "公司解释", "inference": "分析推断", "unverified": "未核实事项"}


def load_events(path: str | Path | None, context: dict) -> list[dict]:
    if path is None:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != "investment.report_events":
        raise ValueError("invalid report events schema")
    if payload.get("subject") != context["subject"] or payload.get("report_period") != context["report_period"]:
        raise ValueError("report events company or destination period mismatch")
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("invalid event")
        for key in ("title", "period", "disclosed_at", "explanation", "importance", "amount", "impact", "limits"):
            if not isinstance(event.get(key), str) or not event[key].strip():
                raise ValueError(f"event requires {key}; write 未披露/未核实 for missing facts")
        if event.get("status") not in STATES:
            raise ValueError("invalid event fact status")
        sources = event.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError("event requires original sources")
        for source in sources:
            if not isinstance(source, dict) or not all(isinstance(source.get(key), str) and source[key].strip()
                                                       for key in ("title", "date", "url", "locator")):
                raise ValueError("source requires title, date, url and locator")
            if not source["url"].startswith(("https://", "http://")):
                raise ValueError("source URL must be http(s)")
    return events


def _line(text: str) -> str:
    return text.replace("\n", " ").replace("\r", " ")


def render_reader_summary(context: dict, events: list[dict]) -> str:
    """日常中文正文；数字来自确定性结果，已核验事项由外部输入，不猜因果。"""
    subject = context["subject"]
    period, prior = context["report_period"], context["comparable_period"]
    lines = [f"# 经营变化报告 — {subject['company']} · {period}", "", "## 经营与利润发生了什么变化", "",
             f"比较期间：{period}与{prior}。下列同比按累计口径比较；不将累计数当作单季数。", ""]
    params = (context.get("period_delta") or {}).get("parameters") or {}
    for name, key in (("营业收入", "revenue_yoy_pct"), ("归母净利润", "net_profit_yoy_pct")):
        value = params.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            direction = "增长" if value > 0 else ("下降" if value < 0 else "持平")
            lines.append(f"- {name}同比{direction}{abs(value):g}%。")
        else:
            lines.append(f"- {name}同比变化尚未核实，不能判断幅度。")
    lines += ["", "## 重要事项与主要解释", ""]
    if not events:
        lines.append("尚未取得本次报告已核验的重大事项材料。收入、利润的同比变化不能单独证明变化原因；需补充原始报表附注、公告或监管文件，再核实金额及影响。")
    for event in events:
        lines += [f"### {_line(event['title'])}", "",
                  f"**{STATES[event['status']]}**；披露日期：{_line(event['disclosed_at'])}；涉及期间：{_line(event['period'])}。", "",
                  _line(event["explanation"]), "", f"为什么重要：{_line(event['importance'])}", "",
                  f"报表金额与口径：{_line(event['amount'])}", "", f"影响：{_line(event['impact'])}", "",
                  f"尚不能确定：{_line(event['limits'])}", ""]
        for source in event["sources"]:
            # 标题括号转义，保持来源可点击；正文仍由安全 Markdown renderer 消毒。
            title = _line(source["title"]).replace("[", "\\[").replace("]", "\\]")
            url = source["url"].replace("(", "%28").replace(")", "%29")
            lines.append(f"来源：[{title}]({url})，{_line(source['date'])}，{_line(source['locator'])}。")
        lines.append("")
    lines += ["", "## 证据限制与后续动作", "",
              "上述事项按各自来源日期和涉及期间解释；历史监管决定不自动构成对当前更正事项的认定。税前费用不能直接等同于归母净利润下降额；未核实税费、少数股东或其他因素的影响时，不给出因果贡献金额。"]
    degraded = context.get("degraded") or {}
    for key, label in (("prior_synthesis_dropped", "前次分析未纳入"), ("synthesis_dropped", "本次综合分析未纳入"),
                       ("prior_evidence_unavailable", "前次部分依据不可核对"), ("missing_inputs", "部分材料未取得"),
                       ("unusable_inputs", "部分材料无法读取")):
        if degraded.get(key):
            lines.append(f"- {label}，涉及结论需补充原始材料后复核。")
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="将已核验事项呈现为读者优先的变化报告")
    parser.add_argument("--context", required=True)
    parser.add_argument("--events", help="已核验 investment.report_events 文件；不执行事项发现")
    parser.add_argument("--body", help="可选：保留既有变化报告详情")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    context = json.loads(Path(args.context).read_text(encoding="utf-8"))
    if context.get("schema") != "investment.change_report_context":
        raise ValueError("invalid change report context")
    text = render_reader_summary(context, load_events(args.events, context))
    if args.body:
        text += "\n## 详细变化与证据\n\n" + Path(args.body).read_text(encoding="utf-8")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    print(f"读者报告已写入：{output}")


if __name__ == "__main__":
    main()
