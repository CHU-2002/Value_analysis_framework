// 未知/未实现的前端 kind 一律降级成可读卡片：不白屏、不报错页（AC-3.3）。
export async function render(container, panel, data) {
  const box = document.createElement("div");
  box.className = "panel-fallback";
  const title = document.createElement("p");
  title.className = "panel-fallback-title";
  title.textContent = `无法渲染面板：${panel.title || panel.id}`;
  const detail = document.createElement("p");
  detail.className = "panel-fallback-detail";
  detail.textContent =
    panel.render === "client" && data === undefined
      ? `此面板需要更新的前端（kind=${panel.kind}）`
      : `没有为 kind=${panel.kind} 注册渲染器`;
  const note = document.createElement("p");
  note.className = "panel-note";
  note.textContent = `面板 id：${panel.id}；接口 ${panel.endpoint || "（无）"} 仍可直接读取。`;
  box.append(title, detail, note);
  container.append(box);
}
