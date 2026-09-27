// kind=jobs：任务列表 + 状态 + 退出码 + 日志尾部（REQ-009.1 AC-1.1 / AC-1.4）。
import { api } from "/app.js";

function jobCard(job, refresh) {
  const card = document.createElement("div");
  card.className = "job-row";
  const head = document.createElement("div");
  head.className = "job-head";
  const title = document.createElement("strong");
  title.textContent = job.title || job.command || job.id;
  const status = document.createElement("span");
  status.className = `status-${job.status}`;
  status.textContent = job.status;
  const code = document.createElement("span");
  code.className = "panel-note";
  code.textContent = `退出码 ${job.exit_code ?? "—"}`;
  const when = document.createElement("span");
  when.className = "panel-note";
  when.textContent = `${job.started_at || ""} → ${job.finished_at || "运行中"}`;
  head.append(title, status, code, when);
  if (job.status === "running") {
    const cancel = document.createElement("button");
    cancel.type = "button";
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
  const cmd = document.createElement("p");
  cmd.className = "command-argv panel-note";
  cmd.textContent = (job.argv || []).join(" ");
  const log = document.createElement("pre");
  log.className = "job-log";
  log.textContent = (job.log || []).join("\n");
  card.append(head, cmd, log);
  return card;
}

export async function render(container, panel, data) {
  const list = document.createElement("div");
  container.append(list);
  let timer = null;

  async function refresh() {
    const payload = data || (await api(panel.endpoint)).data;
    const jobs = (payload && payload.jobs) || [];
    list.innerHTML = "";
    if (!jobs.length) {
      const empty = document.createElement("p");
      empty.className = "panel-empty";
      empty.textContent = "还没有任务。";
      list.append(empty);
    }
    for (const job of jobs) list.append(jobCard(job, refresh));
    if (!jobs.some((job) => job.status === "running") && timer) {
      clearInterval(timer);
      timer = null;
    }
  }

  await refresh();
  timer = setInterval(() => {
    if (!container.isConnected) {
      clearInterval(timer);
      return;
    }
    refresh().catch(() => clearInterval(timer));
  }, 2000);
}
