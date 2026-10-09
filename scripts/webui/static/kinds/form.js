// kind=form：按键目录 + 结构化参数表单 → POST /api/v1/jobs。
// 这里**没有业务逻辑**：参数形状完全来自服务端返回的 CommandSpec（它又来自各脚本 argparse 扫描）。
import { api } from "/app.js";

function field(param, control, labels = {}) {
  const label = document.createElement("label");
  const name = labels[param.name] || param.name;
  label.textContent = param.required ? `${name} *` : name;
  if (param.help) label.title = param.help;
  label.append(control);
  return label;
}

function control(param) {
  if (param.type === "bool") {
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(param.default);
    input.dataset.param = param.name;
    input.dataset.kind = "bool";
    return input;
  }
  if (param.type === "enum" && (param.choices || []).length) {
    const select = document.createElement("select");
    select.dataset.param = param.name;
    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = param.required ? "（必选）" : "（默认）";
    select.append(blank);
    for (const choice of param.choices) {
      const option = document.createElement("option");
      option.value = choice;
      option.textContent = choice;
      select.append(option);
    }
    if (param.default) select.value = param.default;
    return select;
  }
  const input = document.createElement("input");
  input.type = param.type === "int" || param.type === "float" ? "number" : "text";
  input.dataset.param = param.name;
  input.dataset.kind = param.type;
  if (param.type === "list") input.placeholder = "可重复：用空格或逗号分隔";
  if (param.default !== null && param.default !== undefined && param.default !== false) {
    input.value = param.default;
  }
  if (param.required) input.required = true;
  return input;
}

function collect(form) {
  const params = {};
  for (const input of form.querySelectorAll("[data-param]")) {
    const name = input.dataset.param;
    if (input.dataset.kind === "bool") {
      if (input.checked) params[name] = true;
      continue;
    }
    const value = input.value.trim();
    if (value !== "") params[name] = value;
  }
  return params;
}

export async function render(container, panel, data) {
  const payload = data || (await api(panel.endpoint)).data;
  const commands = (payload && payload.commands) || [];
  const layout = document.createElement("div");
  layout.className = "command-layout";
  const list = document.createElement("div");
  list.className = "command-list";
  const side = document.createElement("form");
  side.className = "command-form";
  layout.append(list, side);
  container.append(layout);

  let current = null;

  function drawForm() {
    side.innerHTML = "";
    if (!current) {
      const note = document.createElement("p");
      note.className = "panel-note";
      note.textContent = "从左边的分组里选一个按键。";
      side.append(note);
      return;
    }
    const title = document.createElement("h3");
    title.textContent = current.title || current.id;
    side.append(title);
    const command = current;
    const notice = document.createElement("p");
    notice.textContent = command.description || "";
    side.append(notice);
    const argv = document.createElement("p");
    argv.className = "command-argv panel-note";
    argv.textContent = `命令行：${(current.argv || []).join(" ")} …`;
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "完整命令行（技术细节）";
    details.append(summary, argv);
    side.append(details);
    for (const param of current.params || []) {
      const input = control(param);
      if (command.placeholder) input.placeholder = command.placeholder;
      side.append(field(param, input, command.field_labels));
    }
    const submit = document.createElement("button");
    submit.type = "submit";
    submit.textContent = command.preflight_endpoint ? "生成报告（消耗模型额度）" :
      (current.danger ? "确认执行（会联网/覆盖）" : "执行");
    side.append(submit);
    const reason = document.createElement("p");
    reason.className = "panel-note";
    side.append(reason);
    const result = document.createElement("div");
    result.className = "command-result";
    side.append(result);

    let ready = !command.preflight_endpoint;
    let preview = "";
    let revision = 0;
    let pending = false;
    async function preflight() {
      if (!command.preflight_endpoint) return;
      const version = ++revision;
      ready = false;
      preview = "";
      submit.disabled = true;
      reason.textContent = "正在检查…";
      try {
        const query = new URLSearchParams({ command: command.id, ...collect(side) });
        const check = (await api(`${command.preflight_endpoint}?${query}`)).data;
        if (version !== revision || !side.contains(submit)) return;
        ready = Boolean(check.ready);
        preview = check.command_line || "";
        argv.textContent = preview ? `命令行：${preview}` : "命令行尚未就绪";
        notice.textContent = `${command.description || ""} ${check.notice || ""}`;
        reason.textContent = check.reason || "检查通过，确认后开始生成。";
      } catch (error) {
        if (version !== revision || !side.contains(submit)) return;
        reason.textContent = `检查失败：${error.message}`;
      }
      submit.disabled = !ready || pending;
    }
    side.oninput = () => { void preflight(); };
    void preflight();

    side.onsubmit = async (event) => {
      event.preventDefault();
      if (!ready || pending) return;
      const params = collect(side);
      if (command.preflight_endpoint && !window.confirm(
        `会消耗模型额度，可能需要较长时间。\n将执行：${preview}\n确认生成报告？`
      )) return;
      pending = true;
      submit.disabled = true;
      result.innerHTML = "";
      try {
        const job = (
          await api("/api/v1/jobs", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ command: command.id, params }),
          })
        ).data;
        const head = document.createElement("p");
        head.textContent = `已提交 ${job.id}`;
        result.append(head);
        const execution = document.createElement("details");
        const executionTitle = document.createElement("summary");
        executionTitle.textContent = "实际执行命令";
        const actualArgv = document.createElement("pre");
        actualArgv.className = "command-argv";
        actualArgv.textContent = (job.argv || []).join(" ");
        execution.append(executionTitle, actualArgv);
        result.append(execution);
        await follow(job.id, result, command);
      } catch (error) {
        const problem = document.createElement("p");
        problem.className = "panel-empty";
        problem.textContent = `提交失败：${error.message}`;
        result.append(problem);
      } finally {
        pending = false;
        if (side.contains(submit)) {
          if (command.preflight_endpoint) await preflight();
          else submit.disabled = false;
        }
      }
    };
  }

  async function follow(jobId, box, command) {
    const status = document.createElement("p");
    const log = document.createElement("pre");
    log.className = "job-log";
    const logs = document.createElement("details");
    const heading = document.createElement("summary");
    heading.textContent = "原始日志";
    logs.append(heading, log);
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "取消任务";
    cancel.onclick = async () => {
      await api(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
    };
    box.append(status, cancel, logs);
    for (;;) {
      const job = (await api(`/api/v1/jobs/${encodeURIComponent(jobId)}`)).data;
      status.textContent = `状态：${job.status}｜退出码：${job.exit_code ?? "—"}`;
      log.textContent = (job.log || []).join("\n");
      log.scrollTop = log.scrollHeight;
      if (!["running", "queued"].includes(job.status)) {
        cancel.remove();
        if (job.status === "failed") {
          const failure = document.createElement("p");
          failure.textContent = "生成失败：请展开日志确认原因，检查登录态和公司数据后手动重新提交。";
          box.append(failure);
        }
        if (command.artifacts_endpoint) {
          const path = command.artifacts_endpoint.replace("{job_id}", encodeURIComponent(jobId));
          const outputs = (await api(path)).data;
          const artifacts = document.createElement("div");
          artifacts.className = "job-artifacts";
          if (outputs.message) artifacts.textContent = outputs.message;
          for (const link of outputs.links || []) {
            const a = document.createElement("a");
            a.textContent = link.label;
            a.href = link.href;
            artifacts.append(a, document.createElement("br"));
          }
          box.append(artifacts);
        }
        return job;
      }
      // 面板被替换 / 页面切走就停止轮询：长任务不该让离开的页面继续打请求。
      if (!box.isConnected) return job;
      await new Promise((resolve) => setTimeout(resolve, 800));
    }
  }

  const groups = new Map();
  for (const command of commands) {
    const group = command.group || "其他";
    if (!groups.has(group)) {
      const heading = document.createElement("div");
      heading.className = "command-group-title";
      heading.textContent = group;
      list.append(heading);
      groups.set(group, []);
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = `command-button${command.danger ? " danger" : ""}`;
    button.textContent = command.title || command.id;
    button.dataset.command = command.id;
    button.onclick = () => {
      current = command;
      for (const other of list.querySelectorAll(".command-button")) {
        other.classList.toggle("active", other === button);
      }
      drawForm();
    };
    list.append(button);
    groups.get(group).push(button);
  }
  drawForm();
}
