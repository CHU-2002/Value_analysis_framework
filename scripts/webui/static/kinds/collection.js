// REQ-015.4: explicit plan/confirmation and persisted, company-bound batch results.
import { api } from "/app.js";

function node(tag, text, cls = "") {
  const el = document.createElement(tag);
  el.textContent = text;
  if (cls) el.className = cls;
  return el;
}
function select(label, entries, value) {
  const wrap = node("label", label);
  const control = document.createElement("select");
  for (const [key, title] of entries) {
    const option = node("option", title); option.value = key; control.append(option);
  }
  control.value = value;
  wrap.append(control);
  return [wrap, control];
}
function button(text, action) {
  const el = node("button", text); el.type = "button"; el.onclick = action; return el;
}
async function post(url, body) {
  return (await api(url, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) })).data;
}
function details(title, data) {
  const box = document.createElement("details"); box.append(node("summary", title));
  const pre = node("pre", JSON.stringify(data, null, 2), "job-log"); box.append(pre); return box;
}
function table(rows, columns) {
  const box = document.createElement("div"); box.className = "panel-table";
  const tbl = document.createElement("table"); const head = document.createElement("thead");
  const titles = document.createElement("tr");
  columns.forEach(([key, label]) => titles.append(node("th", label)));
  head.append(titles); tbl.append(head);
  const body = document.createElement("tbody");
  for (const row of rows) {
    const line = document.createElement("tr");
    columns.forEach(([key]) => line.append(node("td", row[key] ?? "未知")));
    body.append(line);
  }
  tbl.append(body); box.append(tbl); return box;
}

export async function render(container, panel, payload) {
  const data = payload || {};
  const company = data.company || new URLSearchParams(location.hash.split("?")[1] || "").get("company") || "";
  const route = new URLSearchParams(location.hash.split("?")[1] || "");
  const root = document.createElement("div"); root.className = "collection-workspace";
  container.append(root);
  root.append(node("p", data.guide || "先核对计划，再确认联网采集。"));
  const form = document.createElement("form"); form.className = "action-controls";
  const [scopeLabel, scope] = select("公司范围", [["current", "当前公司"], ["watchlist", "启用自选股"]], company ? "current" : "watchlist");
  const [tierLabel, tier] = select("数据档位", [["frugal", "基础必需数据"], ["bulk", "完整业务数据"]], "frugal");
  const periodLabel = node("label", "财报期次（YYYYMMDD，逗号分隔）");
  const periods = document.createElement("input"); periods.value = data.periods || "";
  periods.setAttribute("aria-label", "财报期次"); periodLabel.append(periods);
  form.append(scopeLabel, tierLabel, periodLabel);
  let metrics = route.get("chart_metrics") || "";
  const rangeLabel = node("p", metrics ? `行情历史补齐：${route.get("chart_start")}—${route.get("chart_end")}；所需指标来自图表选择。` : "财报期次与交易日期分别计算，基础档位取最新财报期。", "panel-note");
  form.append(rangeLabel);
  if (metrics) form.append(button("改用常规业务数据计划", () => { metrics = ""; rangeLabel.textContent = "已切换常规数据计划，请重新检查范围。"; invalidate(); }));
  const inspect = button("检查计划（离线）", () => check());
  form.append(inspect); root.append(form);
  const feedback = node("p", "", "panel-note"); feedback.setAttribute("role", "status"); root.append(feedback);
  const planBox = document.createElement("div"); root.append(planBox);
  const batchesBox = document.createElement("div"); root.append(node("h3", "采集进展与完成结果"), batchesBox);
  let planned = null; let revision = 0;
  function inputs() {
    return { company, scope: scope.value, tier: tier.value, periods: periods.value,
      chart_metrics: metrics, chart_start: metrics ? route.get("chart_start") || "" : "",
      chart_end: metrics ? route.get("chart_end") || "" : "" };
  }
  function invalidate() { ++revision; planned = null; planBox.replaceChildren(); feedback.textContent = "选择已变化，请重新检查计划。"; }
  form.oninput = invalidate;
  form.onsubmit = (event) => { event.preventDefault(); void check(); };
  async function check() {
    const version = ++revision; inspect.disabled = true; feedback.textContent = "正在核对本地目标计划…";
    try {
      const result = (await api(`/api/v1/research-data/plan?${new URLSearchParams(inputs())}`)).data;
      if (version !== revision || !root.isConnected) return;
      planned = result; feedback.textContent = "计划已生成；尚未产生数据源请求。"; drawPlan(result);
    } catch (error) { feedback.textContent = error.message; } finally { inspect.disabled = false; }
  }
  function drawPlan(plan) {
    planBox.replaceChildren();
    planBox.append(node("h3", `计划完备度 ${plan.complete}/${plan.total} 个目标；剩余 ${plan.remaining} 个`));
    planBox.append(node("p", `公司：${plan.companies.map((c) => `${c.display_name} ${c.ticker}`).join("、")}；期次 ${plan.inputs.periods}；档位 ${tier.options[tier.selectedIndex].textContent}`));
    planBox.append(node("p", `分母范围：${plan.denominator}`));
    planBox.append(node("p", `新增请求预估 ${plan.requests_estimate} 次；将复用 ${plan.reuse_estimate} 个已完成目标；已有存档 ${plan.existing_records} 条记录（独立统计）。`));
    planBox.append(node("p", plan.notice, "panel-note"));
    planBox.append(table(Object.entries(plan.groups).map(([group, targets]) => ({ group, targets })), [["group", "中文业务数据组"], ["targets", "目标数"]]));
    const audit = document.createElement("details"); audit.append(node("summary", "目标计划明细（状态、数据时点与下一步）"));
    audit.append(table(plan.rows, [["company_ticker", "公司"], ["group", "业务组"], ["label", "数据"], ["period", "期次"], ["state_label", "状态"], ["fetched_at", "数据获取时点"], ["next_step", "下一步"]]));
    audit.append(details("接口名与请求参数（技术详情）", plan.targets)); planBox.append(audit);
    const start = button("确认采集（联网、消耗配额）", async () => {
      if (!planned || !window.confirm(`公司范围：${plan.companies.map((c) => c.ticker).join("、")}\n期次：${plan.inputs.periods}\n档位：${plan.inputs.tier}\n业务组：${Object.keys(plan.groups).join("、")}\n新增请求预估 ${plan.requests_estimate} 次，复用 ${plan.reuse_estimate} 个目标。账号权限未验证，耗时未知。\n确认联网采集？取消不会请求数据源。`)) return;
      start.disabled = true;
      try {
        const batch = await post("/api/v1/research-data/run", { inputs: plan.inputs, digest: plan.digest, confirmed: true });
        feedback.textContent = `已提交批次 ${batch.batch_id}；请看下方进展。`; await loadBatches();
      } catch (error) { feedback.textContent = error.message; } finally { start.disabled = false; }
    });
    start.disabled = Boolean(plan.blockers && plan.blockers.length);
    if (start.disabled) planBox.append(node("p", plan.blockers.join("；"), "panel-note"));
    planBox.append(start);
  }
  let lastBatches = "";
  async function loadBatches() {
    try {
      const result = (await api(`/api/v1/research-data/batches?${new URLSearchParams({ company })}`)).data;
      if (!root.isConnected) return;
      const signature = JSON.stringify(result.batches);
      if (signature === lastBatches) return;
      lastBatches = signature;
      batchesBox.replaceChildren();
      if (!result.batches.length) batchesBox.append(node("p", "尚无采集批次。检查计划后可确认开始。"));
      for (const batch of result.batches) {
        const box = document.createElement("section"); box.className = "action-result";
        box.dataset.batch = batch.batch_id;
        box.append(node("h4", `${batch.status_label} · 已完成目标 ${batch.completed}/${batch.total} · 成功 ${batch.success}/${batch.total}`));
        const current = batch.current || {};
        if (current.dataset && batch.status === "running") box.append(node("p", `当前公司：${current.company_ticker || current.ticker || "市场公共数据"}；当前业务组：${groupFor(current.dataset)}`));
        box.append(node("p", `实际请求 ${batch.actual_requests} 次；缓存复用 ${batch.reused} 个目标；新增成功 ${batch.new}；合法空响应 ${batch.empty}；失败 ${batch.failures}；未完成 ${batch.uncompleted}。`));
        box.append(node("p", batch.notice, "panel-note"));
        box.append(table(batch.rows, [["company", "公司"], ["group", "业务组"], ["state_label", "结果"], ["fetched_at", "数据获取时点"], ["impact", "分析影响"], ["next_step", "下一步"]]));
        if (["running", "pending"].includes(batch.status)) box.append(button("暂停采集", async () => {
          try { const result = await post(`/api/v1/research-data/batches/${encodeURIComponent(batch.batch_id)}/pause`, {}); feedback.textContent = result.message; } catch (error) { feedback.textContent = error.message; }
        }));
        if (batch.can_resume) box.append(button("恢复 / 补本批缺口", async () => {
          if (!window.confirm(`恢复批次 ${batch.batch_id}：只处理原计划未完成/失败目标，会联网消耗配额；不自动重试。确认？`)) return;
          try { await post(`/api/v1/research-data/batches/${encodeURIComponent(batch.batch_id)}/resume`, { confirmed: true }); await loadBatches(); } catch (error) { feedback.textContent = error.message; }
        }));
        const boundCompanies = [...new Set(batch.rows.map((row) => row.company).filter((value) => value && value !== "市场公共数据"))];
        for (const boundCompany of boundCompanies) {
          for (const [page, label] of [["charts", "查看行情与估值（离线）"], ["report", "查看报告（采集不会自动更新）"]]) {
            const link = node("a", `${boundCompany}：${label}`);
            link.href = `#${page}?${new URLSearchParams({ company: boundCompany })}`; box.append(link, document.createElement("br"));
          }
        }
        if (company) box.append(button("从仓离线重建数据包", async () => {
          try {
            const result = await post("/api/v1/actions/data.rebuild/run", { context: { company }, params: {} });
            feedback.textContent = `已提交离线重建任务 ${result.id}；请到任务中心查看，不联网。`;
          } catch (error) { feedback.textContent = error.message; }
        }));
        box.append(details("批次标识与原始结果（技术详情）", batch)); batchesBox.append(box);
      }
    } catch (error) { feedback.textContent = error.message; }
  }
  function groupFor(dataset) {
    if (["daily", "daily_basic", "adj_factor", "trade_cal", "suspend_d", "weekly"].includes(dataset)) return "行情与估值";
    if (dataset === "yc_cb") return "利率";
    if (["top10_holders", "pledge_stat", "repurchase", "dividend"].includes(dataset)) return "股东治理与分红";
    return "财报/公司基本信息";
  }
  await loadBatches();
  if (company || scope.value === "watchlist") await check();
  const timer = setInterval(() => { if (!root.isConnected) clearInterval(timer); else void loadBatches(); }, 1800);
}
