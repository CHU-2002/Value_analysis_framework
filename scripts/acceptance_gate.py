#!/usr/bin/env python
"""独立验收门禁：**按子需求/大特性收口**触发，不按 PR 触发。

评审粒度（2026-09-20 使用者明确要求：「以子特性为标准评审，不要每一次 PR 都拉评审」）：
一个子需求（`REQ-NNN.S`）或一个大特性（`REQ-NNN`）的全部实现 PR 合入后**只做一次**
独立验收；中间的 PR 不拉评审，工程性修正也不单独拉。所以本门禁的触发条件是
**状态推进到 `verified`**，而不是「合 `main` 的 PR」：

- 本 PR 没有任何编号推进到 `verified` → 直接通过，不看报告（这是绝大多数 PR）；
- 本 PR 把某个编号推进到 `verified` → 必须满足：
  1. 该编号写进 PR 正文的「## 需求编号」批次（**署名**，防止「只申报一个更窄的子需求编号」
     就让父需求的 `AC-n` 永远不必被核对）；
  2. PR 正文「## 验收报告」链接 `docs/verification/` 下的报告；
  3. 报告逐条核对**该编号自己的**验收标准（父需求 `AC-n`、子需求 `AC-S.n`），
     且由独立评审者撰写、记录全量测试结果。

只改台账/文档的补录 PR 同样按这条规则：不推进状态就免报告，推进到 `verified` 就要报告——
「验收戳」本身就是验收结论，它也要有报告兜底。

独立性靠流程保证（报告里必须声明 reviewer 与「未参与实现」），CI 只能校验留痕。
模板见 docs/verification/TEMPLATE.md。
"""

import argparse
import re
import subprocess
from pathlib import Path

import req_registry
from pr_body_guard import section, strip_comments

ROOT = Path(__file__).resolve().parents[1]
VERIFICATION_DIR = ROOT / "docs" / "verification"
REQ_RE = req_registry.REQ_ID_RE
FRONT_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
PASSED_RE = re.compile(r"\d+\s+passed")
# 独立验收的触发状态：只有推进到 verified 才要求报告（implemented 只是「已合入待验收」）
REVIEW_STATUS = "verified"
# 报告按需求分节时的小标题，如 "### REQ-003 run-store 台账" / "### REQ-009.2 摘要缓存"
REQ_SECTION_RE = re.compile(rf"^#{{2,4}}\s*({req_registry.REQ_ID_RE.pattern})\b.*$", re.MULTILINE)
# 报告里举例说明「未打勾的 AC 长什么样」是正常写作，不该被判成结论：
# 扫 AC 之前先剔除围栏代码块、引用块与行内代码（REQ-007 的评审者因此被误判过）。
FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
BLOCKQUOTE_RE = re.compile(r"^[ \t]*>.*$", re.MULTILINE)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
REQUIRED_FIELDS = ("reviewer", "independence", "requirements", "full-suite")
VERIFICATION_HEADING = "验收报告"


def parse_front_matter(text: str) -> dict:
    match = FRONT_RE.match(text)
    if not match:
        return {}
    fields = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip().strip("\"'")
    return fields


def requirement_entry(req_id: str):
    """返回该编号所属的需求条目路径；不存在返回 None（子需求随父需求的文件）。"""
    return req_registry.entry_path(req_id)


def requirement_ac_ids(req_id: str) -> list:
    """从需求条目里取出该编号**自己**的 AC 编号，如父需求 ['1', '2']、子需求 ['2.1']。"""
    return req_registry.ac_ids(req_id)


def req_sections(text: str) -> dict:
    """若报告按需求分节，返回 {REQ 编号: 该节正文}；否则返回空字典。

    必须分节校验：否则 A 需求的 `- [ ]` 会连累 B 需求的同号 AC。
    """
    matches = list(REQ_SECTION_RE.finditer(text))
    if not matches:
        return {}
    sections = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(1)] = sections.get(match.group(1), "") + text[match.end() : end]
    return sections


def texts_for_requirement(texts: dict, req: str) -> list:
    """挑出「与 req 有关、且只该按 req 解释」的报告片段。

    - 报告按需求分节：只取 req 自己那一节；
    - 报告未分节：只在报告声明了 req 时，整份报告算作 req 的证据。
    """
    picked = []
    for text in texts.values():
        declared = set(REQ_RE.findall(text))
        if req not in declared:
            continue
        sections = req_sections(text)
        if sections:
            if req in sections:
                picked.append(sections[req])
        else:
            picked.append(text)
    return picked


def scannable(text: str) -> str:
    """剔除围栏代码块、引用块与行内代码，只留下真正作为结论书写的正文。"""
    return INLINE_CODE_RE.sub("", BLOCKQUOTE_RE.sub("", FENCED_CODE_RE.sub("", text)))


def checked(body: str, ac: str) -> bool:
    # 允许 `- [x] AC-1` 与 Markdown 粗体 `- [x] **AC-1**` 两种写法。
    # `AC-1` 后必须不是小数点或数字：否则子需求的 `**AC-1.2**` 会把父需求的 AC-1 判成已打勾。
    return re.search(rf"-\s*\[[xX]\]\s*\*{{0,2}}AC-{re.escape(ac)}(?![.\d])", scannable(body)) is not None


def unchecked(body: str, ac: str) -> bool:
    return re.search(rf"-\s*\[\s\]\s*\*{{0,2}}AC-{re.escape(ac)}(?![.\d])", scannable(body)) is not None


def changed_files(base: str, head: str) -> list:
    """本 PR 改动的全部文件（相对仓库根）。"""
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"git diff 失败：{proc.stderr.strip()}")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def declared_statuses(text: str) -> dict:
    """条目里声明的状态：{编号: 状态}（父需求取 front matter，子需求取小节的「- 状态：」）。"""
    found = {}
    fields = parse_front_matter(text)
    if fields.get("id") and fields.get("status"):
        found[fields["id"]] = fields["status"]
    for sub_id, body in req_registry.sub_sections(text).items():
        match = req_registry.SUB_STATUS_RE.search(body)
        if match:
            found[sub_id] = match.group(1)
    return found


def _git_show(ref: str, path: str, repo: Path) -> str:
    """`git show <ref>:<path>`；文件在该 ref 下不存在时返回空串。"""
    proc = subprocess.run(
        ["git", "show", f"{ref}:{path}"], cwd=repo, capture_output=True, text=True
    )
    return proc.stdout if proc.returncode == 0 else ""


def verified_promotions(base: str, head: str, repo: Path = ROOT) -> set:
    """本 PR 把哪些需求（含子需求）的状态推到了 `verified` —— 独立验收的触发条件。

    只认「推到 verified」这一种转变：`proposed → accepted`、状态回退、只改标题都不触发，
    避免把日常台账维护变成一次评审。
    """
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}", "--", "docs/requirements"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"git diff 失败：{proc.stderr.strip()}")
    promoted = set()
    for name in (line.strip() for line in proc.stdout.splitlines()):
        if not name.startswith("docs/requirements/REQ-") or not name.endswith(".md"):
            continue
        before = declared_statuses(_git_show(base, name, repo))
        after = declared_statuses(_git_show(head, name, repo))
        for req_id, status in after.items():
            if status == REVIEW_STATUS and before.get(req_id) != REVIEW_STATUS:
                promoted.add(req_id)
    return promoted


def added_reports(base: str, head: str) -> list:
    """本 PR 新增/修改的验收报告（排除模板）。"""
    return [
        ROOT / name
        for name in changed_files(base, head)
        if name.startswith("docs/verification/") and Path(name).name != "TEMPLATE.md"
    ]


def batch_requirements(body: str) -> list:
    """批次需求只认「## 需求编号」小节；该小节缺失时才回退到全篇扫描。

    只认那一节：PR 正文常需要在说明里提到别的需求编号（例如「REQ-005 不在本批，
    因为它未通过验收」）。若按全篇扫描，这些被显式排除的需求会被卷进批次，
    门禁就会去要求它们的 AC 打勾——那是误判。
    """
    declared = section(body, "需求编号")
    # 回退时同样剥掉 HTML 注释：模板残留注释里的 REQ-NNN 不是需求声明
    scope = declared if declared else strip_comments(body)
    return sorted(set(REQ_RE.findall(scope)))


def evaluate(body: str, reports: list, promoted=None) -> list:
    """返回问题列表；空列表表示通过。

    promoted 为本 PR 推进到 `verified` 的编号集合（见 verified_promotions）。
    **没有状态推进就没有验收主张**，因此不看报告——评审按子需求/大特性收口，不按 PR。
    一个批次可以由多份报告组成，按**并集**覆盖被验收的编号。
    """
    promoted = set(promoted or ())
    if not promoted:
        return []

    problems = []
    batch = batch_requirements(body)
    undeclared = sorted(promoted - set(batch))
    if undeclared:
        problems.append(
            f"本 PR 把以下编号的状态推到了 verified，但没有写进「## 需求编号」批次："
            f"{', '.join(undeclared)}；状态推进就是「我验收完了」的主张，必须署名并由独立评审者"
            "逐条核对它自己的 AC-n / AC-S.n"
        )
        return problems
    # 批次编号必须真实登记，否则「写一个不存在的 REQ」就能让 AC 校验无从下手。
    # 子需求与父需求分别报错：前者的父文件在、只是缺 `### REQ-NNN.S` 小节。
    unknown = [req for req in batch if req not in req_registry.registered_ids()]
    if unknown:
        missing_parents = [req for req in unknown if requirement_entry(req) is None]
        missing_subs = [req for req in unknown if requirement_entry(req) is not None]
        if missing_parents:
            problems.append(
                f"批次中的需求编号在 docs/requirements/ 下不存在：{', '.join(missing_parents)}；"
                "请先登记需求，或把编号改对"
            )
        if missing_subs:
            problems.append(
                f"批次中的子需求编号未登记：{', '.join(missing_subs)}；"
                "父需求文件里缺少对应的 `### REQ-NNN.S` 小节（见 docs/requirements/README.md §6.1）"
            )
        return problems
    verification = section(body, VERIFICATION_HEADING) or ""
    if "docs/verification/" not in verification:
        problems.append(
            f"推进状态到 verified 的 PR 必须在「## {VERIFICATION_HEADING}」里链接 "
            "docs/verification/ 下的独立验收报告（模板见 docs/verification/TEMPLATE.md）"
        )
    if not reports:
        problems.append(
            "本 PR 没有新增或修改 docs/verification/ 下的验收报告；"
            "请先由独立评审者跑全量测试并产出报告（模板见 docs/verification/TEMPLATE.md）"
        )
        return problems

    texts = {}
    for report in reports:
        text = report.read_text(encoding="utf-8")
        try:
            rel = report.relative_to(ROOT)
        except ValueError:
            rel = report
        texts[rel] = text
        fields = parse_front_matter(text)
        for field in REQUIRED_FIELDS:
            if not fields.get(field):
                problems.append(f"{rel} 的 front matter 缺少 `{field}`（模板见 docs/verification/TEMPLATE.md）")
        if fields.get("independence") and fields["independence"].lower() not in {
            "independent",
            "yes",
            "true",
        }:
            problems.append(f"{rel} 的 `independence` 必须是 independent（独立评审者未参与实现）")

    merged = "\n".join(texts.values())
    declared = set(REQ_RE.findall(merged))
    missing = sorted(req for req in promoted if req not in declared)
    if missing:
        problems.append(f"验收报告未覆盖本批需求：{', '.join(missing)}（需由独立评审者补验）")

    if not PASSED_RE.search(merged):
        problems.append("验收报告没有记录全量测试结果（需写明形如「1436 passed」的结果）")

    for req in sorted(promoted):
        scoped = texts_for_requirement(texts, req)
        if not scoped:
            problems.append(f"验收报告没有 {req} 的逐条验收内容（需为它单独分节或单独成篇）")
            continue
        for ac in requirement_ac_ids(req):
            if any(unchecked(text, ac) for text in scoped):
                problems.append(f"{req} 的 AC-{ac} 在验收报告中未打勾（仍是 `- [ ]`）")
            elif not any(checked(text, ac) for text in scoped):
                problems.append(f"{req} 的 AC-{ac} 缺少结论（需写 `- [x] AC-{ac} …`）")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="独立验收门禁（按状态推进触发，不按 PR）")
    parser.add_argument("--body-file", required=True)
    parser.add_argument("--base", default="origin/main", help="PR 的目标分支 ref")
    parser.add_argument("--head", default="HEAD", help="当前 git ref")
    args = parser.parse_args()

    body = Path(args.body_file).read_text(encoding="utf-8")
    promoted = verified_promotions(args.base, args.head)
    problems = evaluate(body, added_reports(args.base, args.head), promoted)
    if problems:
        print("独立验收门禁未通过：\n")
        for problem in problems:
            print(f"- {problem}")
        return 1
    if not promoted:
        print("独立验收门禁通过（本 PR 没有把任何需求推进到 verified，不需要独立验收报告）。")
        return 0
    print(f"独立验收门禁通过（本 PR 验收：{', '.join(sorted(promoted))}）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
