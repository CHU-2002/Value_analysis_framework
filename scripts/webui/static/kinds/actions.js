// kind=actions：意图化动作按钮 + 服务端预检（禁用 + 理由）+ 危险动作确认层 + 进度。
// 前端**不做任何判断**：能不能跑、缺什么、会发生什么、要不要确认，全部由服务端算
// （`GET /api/v1/actions`），这样预检可以被 CI 断言（AC-4 / AC-3 的可判定形式）。
import { api, selection, refreshCompanyViews } from "/app.js";
import { mountHandoff } from "/kinds/handoff.js";

function actionCard(action, refresh) {
  const card = document.createElement("div");
  card.className = "action-card";
  card.dataset.actionId = action.id;
  card.dataset.enabled = action.enabled ? "1" : "0";

  const title = document.createElement("p");
  title.className = "action-title";
  title.textContent = action.title;
  card.append(title);

  if (action.description) {
    const note = document.createElement("p");
    note.className = "panel-note";
    note.textContent = action.description;
    card.append(note);
  }

  const effects = document.createElement("p");
  effects.className = "action-effects panel-note";
  const bits = [];
  if (action.effects && action.effects.network) bits.push("需要联网");
  else bits.push("离线执行");
  if (action.effects && action.effects.quota) bits.push("消耗数据源调用配额");
  else bits.push("不消耗配额");
  if (action.effects && (action.effects.writes || []).length) {
    bits.push(`会写入：${action.effects.writes.join("、")}`);
  }
  // 预估（`AC-4`：执行前说明「预计耗时/调用量」）。数字由**服务端**算，这里只展示；
  // 服务端算不出来时不给这个键，界面就不显示预估（不编数字）。
  const estimate = action.estimate || {};
  if (estimate.requests) bits.push(`预计 ${estimate.requests} 次请求`);
  if (estimate.records) bits.push(`预计读取 ${estimate.records} 条记录`);
  if (estimate.seconds) bits.push(`预计约 ${estimate.seconds} 秒`);
  effects.textContent = bits.join(" · ");
  card.dataset.actionEstimate = estimateText(estimate);
  card.append(effects);
  if (estimate.detail) {
    const detail = document.createElement("p");
    detail.className = "action-estimate-detail panel-note";
    detail.dataset.actionEstimateDetail = "1";
    detail.textContent = `预估依据：${estimate.detail}`;
    card.append(detail);
  }

  const form = document.createElement("form");
  form.className = "action-inputs";
  const controls = {};
  for (const field of action.inputs || []) {
    const label = document.createElement("label");
    label.textContent = field.label;
    const input = document.createElement(field.choices ? "select" : "input");
    input.name = field.name;
    input.dataset.actionInput = field.name;
    input.required = !!field.required;
    if (field.choices) {
      for (const choice of field.choices) {
        const option = document.createElement("option");
        option.value = choice.value;
        option.textContent = choice.label;
        input.append(option);
      }
    } else {
      input.type = "text";
      input.placeholder = field.placeholder || "";
      input.maxLength = field.name === "name" ? 80 : 120;
    }
    controls[field.name] = input;
    label.append(input);
    form.append(label);
  }
  card.append(form);

  const button = document.createElement("button");
  button.type = "submit";
  button.className = `action-button${action.danger ? " danger" : ""}`;
  button.textContent = action.enabled ? (action.danger ? "执行（需确认）" : "执行") : "暂不可用";
  button.disabled = !action.enabled;
  button.dataset.actionRun = action.id;
  form.append(button);

  if (!action.enabled && (action.blockers || []).length) {
    const blocked = document.createElement("ul");
    blocked.className = "action-blockers";
    for (const blocker of action.blockers) {
      const item = document.createElement("li");
      item.textContent = blocker;
      blocked.append(item);
    }
    card.append(blocked);
  }

  const result = document.createElement("div");
  result.className = "action-result";
  card.append(result);

  form.onsubmit = async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const params = Object.fromEntries(Object.entries(controls).map(([name, input]) => [name, input.value]));
    // 取消 = 不提交、无副作用。确认层把预估也带上（决定就是在这一刻做的）。
    const confirm = { ...(action.confirm || {}), estimate: action.estimate || {} };
    if (action.danger && !(await confirmDialog(confirm))) return;
    button.disabled = true;
    result.innerHTML = "";
    try {
      const context = {};
      if (selection.company) context.company = selection.company;
      const job = (await api("/api/v1/actions/" + encodeURIComponent(action.id) + "/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ params, context }),
      })).data;
      const completed = await follow(job, result, refresh);
      if (action.id.startsWith("universe.") && completed.status === "finished") {
        await refreshCompanyViews(card);
        await refresh({ id: action.id, job: completed });
      }
    } catch (error) {
      result.append(problem(error));
    } finally {
      button.disabled = !action.enabled;
    }
  };
  return card;
}

// 预估的可断言文本（走查与 DOM 断言都读它；空预估给空串）。
function estimateText(estimate) {
  if (!estimate) return "";
  const bits = [];
  if (estimate.requests) bits.push(`预计 ${estimate.requests} 次请求`);
  if (estimate.records) bits.push(`预计读取 ${estimate.records} 条记录`);
  if (estimate.seconds) bits.push(`预计约 ${estimate.seconds} 秒`);
  return bits.join(" · ");
}

function confirmDialog(confirm) {
  const config = confirm || {};
  return new Promise((resolve) => {
    const layer = document.createElement("div");
    layer.className = "confirm-layer";
    layer.dataset.confirm = "1";
    const box = document.createElement("div");
    box.className = "confirm-box";
    const title = document.createElement("p");
    title.className = "confirm-title";
    title.textContent = config.title || "确认执行这个动作？";
    const body = document.createElement("p");
    body.className = "confirm-body";
    body.textContent = config.body || "";
    box.append(title, body);
    const estimate = estimateText(config.estimate || {});
    if (estimate) {
      const numbers = document.createElement("p");
      numbers.className = "confirm-estimate";
      numbers.dataset.confirmEstimate = "1";
      numbers.textContent = `${estimate}。`;
      box.append(numbers);
    }
    const row = document.createElement("p");
    row.className = "confirm-actions";
    const ok = document.createElement("button");
    ok.type = "button";
    ok.dataset.confirmAccept = "1";
    ok.textContent = config.confirm_label || "确认执行";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.dataset.confirmCancel = "1";
    cancel.textContent = "取消";
    cancel.onclick = () => { layer.remove(); resolve(false); };
    ok.onclick = () => { layer.remove(); resolve(true); };
    row.append(ok, cancel);
    box.append(row);
    layer.append(box);
    document.body.append(layer);
  });
}

export async function follow(job, box, refresh) {
  const head = document.createElement("p");
  head.className = "action-status";
  box.append(head);
  let current = job;
  for (;;) {
    head.textContent = statusLine(current);
    if (current.handoff && current.handoff.awaiting) {
      // 交接面板统一由 `kinds/handoff.js` 渲染（任务中心与这里共用同一份实现）。
      mountHandoff(box, current, () => refresh && refresh());
      break;
    }
    if (current.status !== "running" && current.status !== "queued") break;
    if (!box.isConnected) break;      // 离开页面任务继续跑，但页面不再打请求（AC-5 的服务端编排）
    await new Promise((resolve) => setTimeout(resolve, 800));
    current = (await api(`/api/v1/jobs/${encodeURIComponent(current.id)}`)).data;
  }
  head.textContent = statusLine(current);
  box.append(jobDetails(current));
  if (current.status === "failed") box.append(retryButton(current, box, refresh));
  return current;
}

function statusLine(job) {
  const progress = job.progress || {};
  const steps = progress.total ? ` · ${progress.completed}/${progress.total} 步` : "";
  const current = progress.title && job.status === "running" ? ` · 正在：${progress.title}` : "";
  const status = job.status === "awaiting_agent" ? "等你操作" : job.status;
  return `状态：${status}${steps}${current}`;
}

function jobDetails(job) {
  const box = document.createElement("div");
  box.className = "job-details";
  if ((job.action || "").startsWith("universe.")) {
    for (const line of job.log || []) {
      const start = line.indexOf('{"watchlist_result":');
      if (start < 0) continue;
      try {
        const report = JSON.parse(line.slice(start)).watchlist_result;
        const text = document.createElement("p");
        text.className = "watchlist-summary";
        text.textContent = report.message;
        box.append(text);
        for (const item of report.items || []) {
          const skipped = document.createElement("p");
          skipped.textContent = `${item.company}：${item.reason}`;
          box.append(skipped);
        }
      } catch (_) { /* 日志不是结构化结果时仍可在技术详情查看。 */ }
    }
    return box;
  }
  const outputs = job.outputs || {};
  const writes = outputs.writes || [];
  if (writes.length) {
    const list = document.createElement("ul");
    list.className = "job-outputs";
    for (const item of writes) {
      const li = document.createElement("li");
      const code = document.createElement("code");
      code.textContent = item.path;
      li.append(code);
      if (!item.exists) {
        const note = document.createElement("span");
        note.className = "panel-note";
        note.textContent = "（还没生成）";
        li.append(note);
      }
      list.append(li);
    }
    box.append(list);
  }
  if (job.error) {
    const error = document.createElement("p");
    error.className = "job-error";
    error.textContent = job.error;
    box.append(error);
  }
  const details = document.createElement("details");
  details.className = "job-log-details";
  if (job.status === "failed") details.open = true;    // 失败自动展开（AC-5）
  const summary = document.createElement("summary");
  summary.textContent = "原始日志（真实命令行与输出，未删改）";
  details.append(summary);
  const argv = document.createElement("p");
  argv.className = "command-argv panel-note";
  argv.textContent = (job.argv || []).join(" ");
  details.append(argv);
  const log = document.createElement("pre");
  log.className = "job-log";
  log.textContent = (job.log || []).join("\n");
  details.append(log);
  box.append(details);
  return box;
}

function retryButton(job, box, refresh) {
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.jobRetry = job.id;
  button.textContent = "重试";
  button.onclick = async () => {
    button.disabled = true;
    try {
      const next = (await api(`/api/v1/jobs/${encodeURIComponent(job.id)}/retry`, { method: "POST" })).data;
      box.innerHTML = "";
      await follow(next, box, refresh);
    } catch (error) {
      box.append(problem(error));
    }
  };
  return button;
}

function problem(error) {
  const text = document.createElement("p");
  text.className = "panel-problem-detail";
  text.textContent = error.hint ? `${error.message}；下一步：${error.hint}` : error.message;
  return text;
}

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = "已复制";
  } catch (_) {
    button.textContent = "请手动复制";
  }
}

export async function render(container, panel, data) {
  const payload = data || (await api(panel.endpoint || "/api/v1/actions")).data;
  const actions = (payload && payload.actions) || [];
  const groups = new Map();
  for (const action of actions) {
    const group = action.group || "动作";
    if (!groups.has(group)) {
      const heading = document.createElement("div");
      heading.className = "action-group-title";
      heading.textContent = group;
      container.append(heading);
      const grid = document.createElement("div");
      grid.className = "action-grid";
      container.append(grid);
      groups.set(group, grid);
    }
    // 预检结果是**服务端**算的（AC-4）：刷新一次就是重新问一次服务端，前端不复算。
    groups.get(group).append(actionCard(action, (completed) => reloadPanel(container, panel, completed)));
  }
  if (!actions.length) {
    const empty = document.createElement("p");
    empty.className = "panel-empty";
    empty.textContent = "还没有可用的动作。";
    container.append(empty);
  }
}

async function reloadPanel(container, panel, completed) {
  const payload = (await api(panel.endpoint || "/api/v1/actions")).data;
  container.innerHTML = "";
  await render(container, panel, payload);
  if (completed) {
    const card = [...container.querySelectorAll("[data-action-id]")].find(node => node.dataset.actionId === completed.id);
    if (card) {
      const result = card.querySelector(".action-result");
      const status = document.createElement("p");
      status.textContent = statusLine(completed.job);
      result.append(status, jobDetails(completed.job));
    }
  }
}
