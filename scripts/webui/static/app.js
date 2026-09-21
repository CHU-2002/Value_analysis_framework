// 前端 shell：只做四件事——取导航、取页面描述、维护「当前选择」、按 kind 分发渲染。
// 这里**没有任何业务判断**；加新面板 = 插件注册，加新可视化类型 = registerPanelKind。
import { render as renderChart } from "/kinds/chart.js";
import { render as renderFallback } from "/kinds/fallback.js";

const kindRegistry = new Map();
export function registerPanelKind(kind, renderer) {
  kindRegistry.set(kind, renderer);
}

// 客户端渲染的 kind（服务端渲染的 kind 直接给 html 片段，不需要前端渲染器）
registerPanelKind("chart", renderChart);
registerPanelKind("fallback", renderFallback);
registerPanelKind("form", renderFallback);
registerPanelKind("jobs", renderFallback);

const selection = { company: "", period: "", run: "" };

async function api(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  const payload = await response.json();
  if (!payload.ok) {
    const error = payload.error || {};
    throw new Error(`${error.code || "ERROR"}: ${error.message || "请求失败"}`);
  }
  return payload.data;
}

function banner(message, kind = "warn") {
  const node = document.getElementById("banner");
  node.textContent = message;
  node.className = `banner banner-${kind}`;
  node.hidden = !message;
}

function withSelection(endpoint) {
  const url = new URL(endpoint, window.location.origin);
  for (const [key, value] of Object.entries(selection)) {
    if (value && !url.searchParams.has(key)) url.searchParams.set(key, value);
  }
  return url.pathname + url.search;
}

function panelCard(panel) {
  const card = document.createElement("section");
  card.className = `panel panel-${panel.size || "full"}`;
  card.dataset.panelId = panel.id;
  card.dataset.kind = panel.kind;
  const head = document.createElement("header");
  head.className = "panel-head";
  head.textContent = panel.title || panel.id;
  card.append(head);
  if (panel.description) {
    const note = document.createElement("p");
    note.className = "panel-note";
    note.textContent = panel.description;
    card.append(note);
  }
  const body = document.createElement("div");
  body.className = "panel-body";
  card.append(body);
  return { card, body };
}

async function mountPanel(panel) {
  const { card, body } = panelCard(panel);
  if (panel.render === "server" && panel.html) {
    body.innerHTML = panel.html;
    return card;
  }
  const renderer = kindRegistry.get(panel.kind) || kindRegistry.get("fallback");
  try {
    let data = panel.data;
    if (data === undefined && panel.endpoint) data = await api(withSelection(panel.endpoint));
    await renderer(body, panel, data);
  } catch (error) {
    body.innerHTML = "";
    const problem = document.createElement("p");
    problem.className = "panel-empty";
    problem.textContent = `面板加载失败：${error.message}`;
    body.append(problem);
  }
  return card;
}

async function openPage(pageId) {
  banner("");
  const page = await api(`/api/v1/pages/${encodeURIComponent(pageId)}`);
  document.getElementById("page-title").textContent = page.title;
  document.getElementById("page-desc").textContent = page.description || "";
  const container = document.getElementById("panels");
  container.innerHTML = "";
  for (const panel of page.panels) container.append(await mountPanel(panel));
  for (const link of document.querySelectorAll("#nav a")) {
    link.classList.toggle("active", link.dataset.page === pageId);
  }
}

async function boot() {
  try {
    const { items } = await api("/api/v1/nav");
    const nav = document.getElementById("nav");
    nav.innerHTML = "";
    const groups = new Map();
    for (const item of items) {
      const group = item.group || "其他";
      if (!groups.has(group)) {
        const title = document.createElement("div");
        title.className = "nav-group";
        title.textContent = group;
        nav.append(title);
        groups.set(group, true);
      }
      const link = document.createElement("a");
      link.href = `#${item.id}`;
      link.dataset.page = item.id;
      link.textContent = item.title;
      link.addEventListener("click", (event) => {
        event.preventDefault();
        window.location.hash = item.id;
      });
      nav.append(link);
    }
    if (!items.length) {
      nav.textContent = "还没有注册任何页面。";
      return;
    }
    const requested = window.location.hash.replace(/^#/, "");
    const first = items.some((item) => item.id === requested) ? requested : items[0].id;
    window.addEventListener("hashchange", () => {
      const pageId = window.location.hash.replace(/^#/, "");
      if (pageId) openPage(pageId).catch((error) => banner(error.message, "error"));
    });
    await openPage(first);
  } catch (error) {
    banner(`控制台初始化失败：${error.message}`, "error");
  }
}

export { api, selection, openPage };
boot();
