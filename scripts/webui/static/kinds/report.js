// REQ-015.3：渲染服务端已安全转换的 Markdown；所有元信息与差异用 textContent。
import { api, setHash } from "/app.js";

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function button(text, action) {
  const node = element("button", text);
  node.type = "button";
  node.onclick = action;
  return node;
}
function versionNote(item) {
  return `期次 ${item.period} · 生成 ${item.generated_at} · 数据时点 ${item.data_as_of} · ${item.source} · ${item.status}`;
}
function selectBox(label, items, value, action) {
  const wrap = element("label", label);
  const select = element("select");
  select.setAttribute("aria-label", label);
  for (const item of items) {
    const option = element("option", item.label);
    option.value = item.id;
    option.selected = value === item.id;
    select.append(option);
  }
  select.onchange = () => action(select.value);
  wrap.append(select);
  return { wrap, select };
}

export async function render(container, panel, data) {
  const root = element("div", undefined, "report-reader");
  container.append(root);
  const company = data.company.ticker || data.company.dir;
  const chosen = data.artifact;
  const route = (params) => setHash("report", { company, ...params });
  const controls = element("div", undefined, "report-controls");
  root.append(controls);
  controls.append(element("p", `公司：${data.company.display_name}`, "panel-company"));
  const type = selectBox("报告类型", data.types, data.type, (value) => route({ type: value }));
  const versions = (data.versions || []).filter((item) => item.type === data.type);
  const version = selectBox("报告版本", [{ id: "", label: "选择版本" }, ...versions], chosen?.id || "", (id) => route({ type: data.type, id }));
  controls.append(type.wrap, version.wrap);
  const generate = element("a", "生成报告");
  generate.href = `#agent?company=${encodeURIComponent(company)}`;
  controls.append(generate);
  if (data.notice) controls.append(element("p", data.notice, "report-notice"));
  if (!chosen) {
    controls.append(element("p", versions.length ? `可选历史 ${versions.length} 版；缺少元信息时显示未知。` : "尚无此类型报告。可以生成报告，或在源材料中查看已有数据。"));
    return;
  }
  root.dataset.artifact = chosen.id;
  controls.append(element("p", versionNote(chosen), "report-version-note"));
  const transferStatus = element("p", "", "report-transfer-status");
  const transfer = async (open) => {
    try {
      const response = await api(`/api/v1/companies/${encodeURIComponent(company)}/report/content?id=${encodeURIComponent(chosen.id)}`);
      const item = response.data;
      const bytes = Uint8Array.from(atob(item.base64), (ch) => ch.charCodeAt(0));
      // 仅安全阅读格式能直接打开，HTML/SVG 一律下载；JSON/Markdown按纯文本打开。
      const mime = item.content_type === "application/pdf" ? "application/pdf" : "text/plain;charset=utf-8";
      const url = URL.createObjectURL(new Blob([bytes], { type: open ? mime : "application/octet-stream" }));
      const link = element("a");
      link.href = url;
      if (open && item.openable) { link.target = "_blank"; link.rel = "noopener noreferrer"; }
      else link.download = item.name;
      root.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      transferStatus.textContent = open ? "已打开当前版本" : "已下载当前版本";
    } catch (error) { transferStatus.textContent = error.message; }
  };
  controls.append(button("下载当前版本", () => transfer(false)), transferStatus);
  if (!chosen.markdown) {
    if (["application/pdf", "application/json", "text/plain"].includes(chosen.content_type)) controls.append(button("打开当前源材料", () => transfer(true)));
    const details = element("details", undefined, "report-material");
    details.append(element("summary", "源材料技术信息（展开查看）"), element("p", `${chosen.name} · ${chosen.content_type} · ${chosen.size} 字节`));
    root.append(details);
    return;
  }
  const toolbar = element("div", undefined, "report-toolbar");
  const article = element("article", undefined, "panel-markdown report-body");
  article.innerHTML = data.html;
  const toc = element("nav", undefined, "report-toc");
  toc.setAttribute("aria-label", "报告目录");
  toc.append(element("strong", "目录"));
  for (const item of data.toc || []) {
    const jump = button(item.title, () => {
      const heading = article.querySelector(`[id="${item.id}"]`);
      heading?.scrollIntoView({ block: "start" });
      heading?.setAttribute("tabindex", "-1");
      heading?.focus({ preventScroll: true });
    });
    jump.dataset.anchor = item.id;
    jump.style.marginLeft = `${Math.max(0, item.level - 1) * 10}px`;
    toc.append(jump);
  }
  toolbar.append(button("返回顶部", () => controls.scrollIntoView({ block: "start" })));
  const focus = button("专注阅读", () => {
    const active = document.body.classList.toggle("report-focus");
    focus.textContent = active ? "退出专注" : "专注阅读";
    focus.setAttribute("aria-pressed", String(active));
  });
  focus.setAttribute("aria-pressed", "false");
  document.body.classList.remove("report-focus");
  toolbar.append(focus);
  const findLabel = element("label", "文内查找");
  const search = element("input");
  search.type = "search";
  search.setAttribute("aria-label", "文内查找");
  findLabel.append(search);
  const count = element("span", "输入关键词", "report-find-count");
  count.setAttribute("aria-live", "polite");
  let matches = [], current = -1;
  const locate = (direction) => {
    if (!matches.length) return;
    current = (current + direction + matches.length) % matches.length;
    matches.forEach((item, index) => item.classList.toggle("report-find-current", index === current));
    for (let node = matches[current].parentElement; node && node !== article; node = node.parentElement) {
      if (node.tagName === "DETAILS") node.open = true;
    }
    matches[current].scrollIntoView({ block: "center" });
    count.textContent = `${current + 1} / ${matches.length} 项`;
  };
  search.oninput = () => {
    for (const mark of article.querySelectorAll("mark[data-report-match]")) mark.replaceWith(document.createTextNode(mark.textContent));
    article.normalize();
    matches = []; current = -1;
    const needle = search.value.toLocaleLowerCase();
    if (!needle) { count.textContent = "输入关键词"; return; }
    const walker = document.createTreeWalker(article, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const text of nodes) {
      const raw = text.textContent, lower = raw.toLocaleLowerCase();
      let start = 0, found = lower.indexOf(needle);
      if (found < 0) continue;
      const fragment = document.createDocumentFragment();
      while (found >= 0) {
        fragment.append(document.createTextNode(raw.slice(start, found)));
        const mark = element("mark", raw.slice(found, found + needle.length));
        mark.dataset.reportMatch = "1"; matches.push(mark); fragment.append(mark);
        start = found + needle.length; found = lower.indexOf(needle, start);
      }
      fragment.append(document.createTextNode(raw.slice(start))); text.replaceWith(fragment);
    }
    count.textContent = matches.length ? `${matches.length} 项匹配` : "无匹配";
    if (matches.length) locate(1);
  };
  toolbar.append(findLabel, count, button("上一项", () => locate(-1)), button("下一项", () => locate(1)));
  root.append(toolbar, toc, article);
  const comparable = versions.filter((item) => item.markdown && item.id !== chosen.id);
  const comparison = element("section", undefined, "report-comparison");
  comparison.append(element("h3", "版本正文比较"));
  root.append(comparison);
  if (data.type === "material" || !comparable.length) {
    comparison.append(element("p", data.type === "material" ? "源材料不做报告正文比较。" : "只有一版，无可比版本。"));
    return;
  }
  const target = selectBox("比较版本", comparable, comparable[0].id, () => {});
  comparison.append(target.wrap);
  const result = element("div", undefined, "report-diff-result");
  comparison.append(button("比较正文", async () => {
    result.replaceChildren();
    try {
      const { data: diff } = await api(`/api/v1/companies/${encodeURIComponent(company)}/report/compare?left=${chosen.id}&right=${target.select.value}`);
      result.append(element("p", diff.notice), element("p", `左版：${versionNote(diff.left)}`), element("p", `右版：${versionNote(diff.right)}`));
      const pre = element("pre", diff.diff || "正文一致，无变化。", "report-diff");
      result.append(pre);
      for (const change of diff.changes || []) {
        const link = element("a", `已有变化报告：${change.label}`);
        link.href = `#report?company=${encodeURIComponent(company)}&id=${change.id}`;
        result.append(link);
      }
    } catch (error) { result.append(element("p", error.message)); }
  }), result);
}
