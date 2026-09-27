// kind=form：按键目录 + 结构化参数表单 → POST /api/v1/jobs。
// 这里**没有业务逻辑**：参数形状完全来自服务端返回的 CommandSpec（它又来自各脚本 argparse 扫描）。
import { api } from "/app.js";

function field(param, control) {
  const label = document.createElement("label");
  label.textContent = param.required ? `${param.name} *` : param.name;
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
    const argv = document.createElement("p");
    argv.className = "command-argv panel-note";
    argv.textContent = `命令行：${(current.argv || []).join(" ")} …`;
    side.append(argv);
    for (const param of current.params || []) side.append(field(param, control(param)));
    const submit = document.createElement("button");
    submit.type = "submit";
    submit.textContent = current.danger ? "确认执行（会联网/覆盖）" : "执行";
    side.append(submit);
    const result = document.createElement("div");
    result.className = "command-result";
    side.append(result);

    side.onsubmit = async (event) => {
      event.preventDefault();
      submit.disabled = true;
      result.innerHTML = "";
      try {
        const job = (
          await api("/api/v1/jobs", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ command: current.id, params: collect(side) }),
          })
        ).data;
        const head = document.createElement("p");
        head.className = "command-argv";
        // 回显**实际执行的完整命令行**（含本次参数），这是「点了什么就跑什么」的凭据。
        head.textContent = `已提交 ${job.id}：${(job.argv || []).join(" ")}`;
        result.append(head);
        await follow(job.id, result);
      } catch (error) {
        const problem = document.createElement("p");
        problem.className = "panel-empty";
        problem.textContent = `提交失败：${error.message}`;
        result.append(problem);
      } finally {
        submit.disabled = false;
      }
    };
  }

  async function follow(jobId, box) {
    const status = document.createElement("p");
    const log = document.createElement("pre");
    log.className = "job-log";
    box.append(status, log);
    for (;;) {
      const job = (await api(`/api/v1/jobs/${encodeURIComponent(jobId)}`)).data;
      status.textContent = `状态：${job.status}｜退出码：${job.exit_code ?? "—"}`;
      log.textContent = (job.log || []).join("\n");
      log.scrollTop = log.scrollHeight;
      if (job.status !== "running") return job;
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
