// kind=actions：意图化动作按钮 + 服务端预检（禁用 + 理由）+ 危险动作确认层 + 进度。
// 前端**不做任何判断**：能不能跑、缺什么、会发生什么、要不要确认，全部由服务端算
// （`GET /api/v1/actions`），这样预检可以被 CI 断言（AC-4 / AC-3 的可判定形式）。
import { api, selection } from "/app.js";

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

  const button = document.createElement("button");
  button.type = "button";
  button.className = `action-button${action.danger ? " danger" : ""}`;
  button.textContent = action.enabled ? (action.danger ? "执行（需确认）" : "执行") : "暂不可用";
  button.disabled = !action.enabled;
  button.dataset.actionRun = action.id;
  card.append(button);

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

  button.onclick = async () => {
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
        body: JSON.stringify({ params: {}, context }),
      })).data;
      await follow(job, result, refresh);
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
      box.querySelectorAll(".handoff").forEach((node) => node.remove());
      box.append(handoffPanel(current, () => refresh && refresh()));
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

function handoffPanel(job, refresh) {
  const box = document.createElement("div");
  box.className = "handoff";
  const handoff = job.handoff || {};
  const title = document.createElement("p");
  title.className = "handoff-title";
  title.textContent = `需要你手动跑一步：${handoff.title || ""}`;
  box.append(title);
  if (handoff.hint) {
    const hint = document.createElement("p");
    hint.className = "panel-note";
    hint.textContent = handoff.hint;
    box.append(hint);
  }
  if (handoff.slash) {
    const row = document.createElement("p");
    row.className = "handoff-command";
    const code = document.createElement("code");
    code.textContent = handoff.slash;
    const copy = document.createElement("button");
    copy.type = "button";
    copy.textContent = "复制命令";
    copy.onclick = () => copyText(handoff.slash, copy);
    row.append(code, copy);
    box.append(row);
  }
  const paths = handoff.paths || {};
  const keys = Object.keys(paths);
  if (keys.length) {
    const list = document.createElement("dl");
    list.className = "handoff-paths";
    for (const key of keys) {
      const dt = document.createElement("dt");
      dt.textContent = { company_dir: "公司目录", run_dir: "本次 run", ticker: "标的", primary_period: "期次" }[key] || key;
      const dd = document.createElement("dd");
      const code = document.createElement("code");
      code.textContent = paths[key];
      dd.append(code);
      list.append(dt, dd);
    }
    box.append(list);
  }
  if ((handoff.expects || []).length) {
    const expects = document.createElement("p");
    expects.className = "panel-note";
    expects.textContent = `继续前会检查这些产物是否存在且是这次跑出来的：${handoff.expects.join("、")}`;
    box.append(expects);
  }
  if ((handoff.missing || []).length) {
    const missing = document.createElement("p");
    missing.className = "handoff-missing";
    // 「找不到」与「找到了但没更新」是两回事：服务端只回缺了哪几项，
    // 措辞要说清两种可能，别让用户以为文件不在（独立验收抓到的措辞问题）。
    missing.textContent = "这些产物还没就绪（可能不存在，或没有比这一步开始时更新）："
      + handoff.missing.join("、");
    box.append(missing);
  }
  const row = document.createElement("p");
  row.className = "handoff-actions";
  const done = document.createElement("button");
  done.type = "button";
  done.dataset.handoffContinue = "1";
  done.textContent = "我跑完了，继续";
  done.onclick = async () => {
    done.disabled = true;
    try {
      await api(`/api/v1/jobs/${encodeURIComponent(job.id)}/continue`, { method: "POST" });
    } catch (error) {
      box.append(problem(error));
    } finally {
      done.disabled = false;
      refresh();
    }
  };
  const give = document.createElement("button");
  give.type = "button";
  give.dataset.handoffAbandon = "1";
  give.textContent = "放弃这次";
  give.onclick = async () => {
    give.disabled = true;
    try {
      await api(`/api/v1/jobs/${encodeURIComponent(job.id)}/abandon`, { method: "POST" });
    } finally {
      refresh();
    }
  };
  row.append(done, give);
  box.append(row);
  return box;
}

function jobDetails(job) {
  const box = document.createElement("div");
  box.className = "job-details";
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
    groups.get(group).append(actionCard(action, () => reloadPanel(container, panel)));
  }
  if (!actions.length) {
    const empty = document.createElement("p");
    empty.className = "panel-empty";
    empty.textContent = "还没有可用的动作。";
    container.append(empty);
  }
}

async function reloadPanel(container, panel) {
  const payload = (await api(panel.endpoint || "/api/v1/actions")).data;
  container.innerHTML = "";
  await render(container, panel, payload);
}
