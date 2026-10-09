// kind=jobs：任务中心（REQ-009.1 的任务生命周期 + REQ-012.2 的 AC-5）。
//
// 约定：**失败给人话 + 原始日志两份**（`failure_summary` 是服务端算的，日志一个字不删）；
// 队列、进行中、历史三块都列；离开页面任务照常在服务端跑，回来还能看到。
import { api } from "/app.js";
import { mountHandoff } from "/kinds/handoff.js";

const STATUS_LABEL = {
  queued: "排队中",
  running: "进行中",
  awaiting_agent: "等你操作",
  finished: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

function progressBar(job) {
  const progress = job.progress || {};
  if (!progress.total) return null;
  const bar = document.createElement("div");
  bar.className = "job-progress";
  const fill = document.createElement("span");
  fill.style.width = `${Math.round((progress.completed / progress.total) * 100)}%`;
  fill.dataset.jobProgress = `${progress.completed}/${progress.total}`;
  bar.append(fill);
  return bar;
}

function failureBox(job) {
  const summary = job.failure_summary || {};
  const box = document.createElement("div");
  box.className = "job-failure";
  const what = document.createElement("p");
  what.className = "job-failure-what";
  what.textContent = `发生了什么：${summary.what || job.error || "任务失败"}`;
  const how = document.createElement("p");
  how.className = "job-failure-how";
  how.textContent = `怎么办：${summary.how || "展开下面日志看最后几行，修掉原因后点「重试」。"}`;
  box.append(what, how);
  if (summary.step) {
    const where = document.createElement("p");
    where.className = "panel-note";
    where.textContent = `停在哪一步：${summary.step}`;
    box.append(where);
  }
  return box;
}

function outputsBox(job) {
  const outputs = job.outputs || {};
  if (outputs.artifacts_endpoint && ["finished", "failed", "cancelled"].includes(job.status)) {
    const box = document.createElement("div");
    box.className = "job-artifacts";
    const path = outputs.artifacts_endpoint.replace("{job_id}", encodeURIComponent(job.id));
    api(path).then(({ data }) => {
      box.textContent = data.message || "";
      for (const link of data.links || []) {
        const a = document.createElement("a");
        a.textContent = link.label;
        a.href = link.href;
        a.dataset.jobOutputLink = "1";
        box.append(a, document.createElement("br"));
      }
    }).catch((error) => { box.textContent = `产物暂时无法读取：${error.message}`; });
    return box;
  }
  const writes = outputs.writes || [];
  if (!writes.length) return null;
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
    // 产物可直接点开看（数据包/报告都在产物目录下，走既有的报告页）。
    const href = artifactHref(job);
    if (item.exists && href) {
      const open = document.createElement("a");
      open.href = href;
      open.dataset.jobOutputLink = "1";
      open.textContent = " 打开报告";
      li.append(open);
    }
  }
  return list;
}

// 产出链接要用「这家公司」拼报告页的 URL。**读取顺序是关键**：
// `handoff.paths` 只在交接中的任务上有值（终态任务的 handoff 是空的），
// 而 `outputs.context` 是**提交时**就留存下来的（重试也用同一份）。
// 原先只读 `handoff.paths.ticker`，于是「有产出」与「有 ticker」两个条件互斥，
// 产出链接是**死代码**（门② 第四轮实测：22 个产出块、0 个 <a>）。
function selectionCompany(job) {
  const paths = (job.handoff || {}).paths || {};
  const context = (job.outputs || {}).context || {};
  return paths.ticker || context.ticker || paths.company_dir || context.company_dir || "";
}

// 报告页只认 ticker（`AC-1` 的 URL 契约）；`company_dir` 是目录名，作为兜底不算理想，
// 但比不显示链接好——服务端解析出来的 ticker 正常情况下总是有的。
function artifactHref(job) {
  const ticker = (job.outputs || {}).context?.ticker
    || ((job.handoff || {}).paths || {}).ticker;
  return ticker ? `#report?company=${encodeURIComponent(ticker)}` : "";
}

function logDetails(job, options = {}) {
  const details = document.createElement("details");
  details.className = "job-log-details";
  if (job.status === "failed" && options.autoOpen !== false) details.open = true;
  const summary = document.createElement("summary");
  summary.textContent = "原始日志（真实命令行与输出，未删改）";
  details.append(summary);
  const argv = document.createElement("p");
  argv.className = "command-argv panel-note";
  argv.textContent = (job.argv || []).join(" ");
  details.append(argv);
  const steps = job.steps || [];
  if (steps.length) {
    const list = document.createElement("ol");
    list.className = "job-steps";
    for (const step of steps) {
      const item = document.createElement("li");
      item.className = `job-step status-${step.status}`;
      item.dataset.stepStatus = step.status;
      item.textContent = `${step.title || step.command}（${STATUS_LABEL[step.status] || step.status}）`;
      if ((step.log || []).length) {
        const inner = document.createElement("details");
        const innerSummary = document.createElement("summary");
        innerSummary.textContent = "这一步的输出";
        const pre = document.createElement("pre");
        pre.className = "job-log";
        pre.textContent = step.log.join("\n");
        inner.append(innerSummary, pre);
        item.append(inner);
      }
      list.append(item);
    }
    details.append(list);
  }
  const log = document.createElement("pre");
  log.className = "job-log";
  log.textContent = (job.log || []).join("\n");
  details.append(log);
  return details;
}

function jobCard(job, refresh, options = {}) {
  const card = document.createElement("div");
  card.className = "job-row";
  card.dataset.jobId = job.id;
  card.dataset.jobStatus = job.status;
  const head = document.createElement("div");
  head.className = "job-head";
  const title = document.createElement("strong");
  title.textContent = job.title || job.command || job.id;
  const status = document.createElement("span");
  status.className = `status-${job.status}`;
  status.textContent = STATUS_LABEL[job.status] || job.status;
  head.append(title, status);
  if (job.progress && job.progress.total) {
    const steps = document.createElement("span");
    steps.className = "panel-note";
    steps.textContent = `${job.progress.completed}/${job.progress.total} 步`;
    head.append(steps);
  }
  const code = document.createElement("span");
  code.className = "panel-note";
  code.textContent = `退出码 ${job.exit_code ?? "—"}`;
  const when = document.createElement("span");
  when.className = "panel-note";
  when.textContent = `${job.started_at || ""} → ${job.finished_at || "进行中"}`;
  head.append(code, when);
  if (job.status === "running" || job.status === "queued") {
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.dataset.jobCancel = job.id;
    cancel.textContent = "取消";
    cancel.onclick = async () => {
      cancel.disabled = true;
      try {
        await api(`/api/v1/jobs/${encodeURIComponent(job.id)}/cancel`, { method: "POST" });
      } finally {
        cancel.disabled = false;
        refresh();
      }
    };
    head.append(cancel);
  }
  if (["failed", "cancelled"].includes(job.status)) {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.dataset.jobRetry = job.id;
    retry.textContent = "重试";
    retry.onclick = async () => {
      retry.disabled = true;
      try {
        await api(`/api/v1/jobs/${encodeURIComponent(job.id)}/retry`, { method: "POST" });
      } finally {
        retry.disabled = false;
        refresh();
      }
    };
    head.append(retry);
  }
  card.append(head);
  const bar = progressBar(job);
  if (bar) card.append(bar);
  if (job.status === "failed") card.append(failureBox(job));
  const outputs = outputsBox(job);
  if (outputs) card.append(outputs);
  if (job.handoff && job.handoff.awaiting) {
    // **从状态重建**交接面板，而不是只在「刚点执行」的回调里渲染它：
    // 门② 第六轮实测——刷新或重新进入页面后，等待中的任务既看不到可复制的命令、
    // 也找不到继续/放弃的入口；点继续而产物不合格时界面还不说缺什么。
    // 谁拿到 awaiting 的 job，谁就把交接面板挂出来（与动作面板共用 `kinds/handoff.js`）。
    mountHandoff(card, job, refresh);
  }
  card.append(logDetails(job, options));
  return card;
}

export async function render(container, panel, data) {
  const list = document.createElement("div");
  container.append(list);
  let timer = null;

  async function refresh() {
    // **每次刷新都要重新取**：原先写成 `data || await api(...)`，也就是首屏那份载荷
    // 一旦存在就永远优先——面板于是**再也不会更新**（门② 第六轮：点「我跑完了，继续」
    // 被拦下之后，界面永远显示校验前的话术，因为渲染用的还是首屏载荷）。
    // 闭包里的 `data` 只作为取不到时的兜底。
    let payload = null;
    try {
      payload = (await api(panel.endpoint)).data;
    } catch (error) {
      payload = data;
    }
    payload = payload || data;
    const jobs = (payload && payload.jobs) || [];
    const queue = (payload && payload.queue) || [];
    list.innerHTML = "";
    const active = jobs.filter((job) => ["running", "awaiting_agent"].includes(job.status));
    const queued = queue.length ? queue : jobs.filter((job) => job.status === "queued");
    const history = jobs.filter((job) => !["running", "awaiting_agent", "queued"].includes(job.status));
    const section = (heading, items, options = {}) => {
      if (!items.length && !options.always) return;
      const title = document.createElement("div");
      title.className = "job-section";
      title.textContent = `${heading}（${items.length}）`;
      list.append(title);
      if (!items.length) {
        const empty = document.createElement("p");
        empty.className = "panel-empty";
        empty.textContent = options.emptyText || "没有任务。";
        list.append(empty);
        return;
      }
      for (const job of items) list.append(jobCard(job, refresh, options));
    };
    section("进行中", active, { always: true, emptyText: "现在没有在跑的任务。" });
    section("队列", queued);
    section("历史", history);
    if (!jobs.length) {
      const empty = document.createElement("p");
      empty.className = "panel-empty";
      empty.textContent = "还没有任务。去「可以做的事」里发起一个动作。";
      list.append(empty);
    }
    // **空闲时放慢，而不是停掉**：原先 `clearInterval` 之后，任务页在空闲时被打开过
    // 就再也不刷新了——之后新起的任务不会出现（门② 第八轮登记的 F3，设计文档那句
    // 「只在有 running 任务时轮询」的实际含义比字面弱）。改成自适应间隔：
    // 有在跑/排队的任务时 2s，空闲时 10s（**永远不会停**，所以之后新起的任务最迟 10s 内出现）。
  }

  await refresh();
  // 当前间隔用**闭包变量**记：`setInterval` 在浏览器里返回 number，而 ES 模块恒为严格模式，
  // 给 number 挂属性会抛 `TypeError: Cannot create property '__period' on number`
  // （门② 第八轮用真实 Edge 实测过）——那一抛会打断整个任务面板的渲染。
  let period = 2000;
  const tick = () => {
    if (!container.isConnected) {
      clearInterval(timer);
      timer = null;
      return;
    }
    refresh()
      .then(() => {
        // 从卡片自己的 `data-job-status` 判断忙不忙（`jobCard` 每个卡片都写了它）。
        // 不用 `closest('.job-section')`：那是**标题**节点、不是卡片的祖先，判断会永远为假。
        const busy = [...list.querySelectorAll("[data-job-status]")]
          .some((card) => ["running", "queued"].includes(card.dataset.jobStatus));
        const wanted = busy ? 2000 : 10000;
        if (timer && period !== wanted) {
          clearInterval(timer);
          period = wanted;
          timer = setInterval(tick, wanted);
        }
      })
      .catch(() => { /* 一次失败不致命：下一拍再试 */ });
  };
  timer = setInterval(tick, period);
}
