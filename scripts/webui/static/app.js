// 前端 shell：取导航、取页面描述、维护「当前选择」、按 kind 分发渲染。
// 这里**没有任何业务判断**；加新面板 = 插件注册，加新可视化类型 = registerPanelKind。
//
// REQ-012.1 的增量（三处，都是「搬数据」不是「做业务」）：
//   1. 导航是**树**：`children` 由服务端下发，这里只缩进；
//   2. 「当前公司」是**全局上下文**：URL 的 `company=` 优先，其次 localStorage；
//      切换公司只改 hash，不重新加载页面；
//   3. 声明了 `requires` 的页面在缺上下文时显示**可读空状态**，而不是让面板各自报错。
import { render as renderActions } from "/kinds/actions.js";
import { render as renderChart } from "/kinds/chart.js";
import { render as renderFallback } from "/kinds/fallback.js";
import { render as renderForm } from "/kinds/form.js";
import { render as renderJobs } from "/kinds/jobs.js";
import { render as renderTable } from "/kinds/table.js";

const kindRegistry = new Map();
export function registerPanelKind(kind, renderer) {
  kindRegistry.set(kind, renderer);
}

// 客户端渲染的 kind（服务端渲染的 kind 直接给 html 片段，不需要前端渲染器）
registerPanelKind("chart", renderChart);
registerPanelKind("fallback", renderFallback);
registerPanelKind("form", renderForm);
registerPanelKind("jobs", renderJobs);
registerPanelKind("table", renderTable);   // 服务端已渲染好表格，这里只加搜索/排序/分页
registerPanelKind("actions", renderActions);

// 「当前选择」是一袋查询参数（company / period / run / id …），由页面链接携带。
// shell 不解释它们的含义——那属于插件，这里只负责转交（AC-9：核心不含业务）。
const selection = {};
// 记住上次选择（REQ-012.1 的 AC-1）：键名固定，值统一是 ticker。
const COMPANY_KEY = "webui.selection.company";
// 公司级页面的空状态（缺上下文时不渲染面板，也不发请求）。
let pageNeedsCompany = false;

function parseHash() {
  const raw = window.location.hash.replace(/^#/, "");
  const [pageId, query = ""] = raw.split("?", 2);
  return { pageId, query };
}

function applySelection(query) {
  // 规则：**URL 显式给了 company 就以 URL 为准**（刷新/前进后退/复制链接都一致）；
  // 没给就保留当前上下文（它是全局的，切换页面不该把它丢掉——实测踩到：点侧栏
  // 回工作台后公司变回「（未选择）」，公司级动作全部变成禁用）。
  // 「冷开公司级页面」由 `mountPanel` 的 `requires` 空状态负责，与这里无关。
  const params = new URLSearchParams(query);
  const company = params.get("company");
  if (company) {
    selection.company = company;
    rememberCompany(company);
  }
  for (const [key, value] of params.entries()) selection[key] = value;
}

function rememberCompany(ticker) {
  if (!ticker) return;
  try { window.localStorage.setItem(COMPANY_KEY, ticker); } catch (_) { /* 隐私模式 */ }
}

function forgetCompany() {
  delete selection.company;
  try { window.localStorage.removeItem(COMPANY_KEY); } catch (_) { /* 隐私模式 */ }
}

function rememberedCompany() {
  try { return window.localStorage.getItem(COMPANY_KEY) || ""; } catch (_) { return ""; }
}

function setHash(pageId, params) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== "" && value !== null && value !== undefined) query.set(key, value);
  }
  const text = query.toString();
  window.location.hash = text ? `${pageId}?${text}` : pageId;
}

async function api(path, options = {}) {
  // 「当前选择」在**传输层**统一转发：靠每个调用点自己记得带上它靠不住——
  // `openPage` 漏过一次，带 company 的图表/报告/迭代记录就整页降级（实跑走查 E1）。
  const target = path.startsWith("/") ? withSelection(path) : path;
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  const response = await fetch(target, { ...options, headers });
  const payload = await response.json();
  if (!payload.ok) {
    const error = payload.error || {};
    // 面向用户的错误（AC-10）：主视觉给人话（message + hint），错误码只作为技术细节附在后面。
    const problem = new Error(error.message || "请求失败");
    problem.hint = error.hint || "";
    problem.code = error.code || "ERROR";
    throw problem;
  }
  // warnings 要往上传：逐面板降级只在这里留痕，丢掉它们等于把故障藏起来（复验 N7）。
  return { data: payload.data, warnings: payload.warnings || [] };
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

// 数据新鲜度徽标（AC-3.3 / AC-6）：`panel.meta` 由内核从面板数据里提到载荷顶层，
// 前端只读这一处。「刷新视图（离线重算）」与「重新拉取（联网）」因此在界面上分得清：
// 看到「命中缓存」说明这次没重算，看到「重新拉取」按钮才需要联网。
export function freshnessNote(meta) {
  if (!meta || meta.degraded) return null;
  const parts = [];
  if (meta.cached) parts.push("命中缓存（没有重新解析）");
  else if (meta.fingerprint || meta.dataset) parts.push("本次重新解析");
  if (meta.generated_at) parts.push(`数据生成时间 ${meta.generated_at}`);
  if (meta.framework_version) parts.push(`产出格式 ${meta.framework_version}`);
  if (!parts.length) return null;
  const note = document.createElement("p");
  note.className = "panel-freshness panel-note";
  note.dataset.freshness = meta.cached ? "cached" : "fresh";
  note.textContent = parts.join(" · ");
  return note;
}

async function mountPanel(panel) {
  const { card, body } = panelCard(panel);
  const freshness = freshnessNote(panel.meta);
  if (freshness) card.append(freshness);
  if (panel.render === "server" && panel.html) {
    body.innerHTML = panel.html;
    const renderer = kindRegistry.get(panel.kind);
    if (renderer) {
      // 服务端渲染的面板也可以有**行为**（表格的搜索/排序/分页在通用渲染层里，AC-3.4）。
      try { await renderer(body, panel, panel.data); } catch (_) { /* 行为失败不影响已渲染的内容 */ }
    }
    return card;
  }
  const renderer = kindRegistry.get(panel.kind) || kindRegistry.get("fallback");
  try {
    let data = panel.data;
    if (data === undefined && panel.endpoint) {
      data = (await api(panel.endpoint)).data;
    }
    await renderer(body, panel, data);
  } catch (error) {
    body.innerHTML = "";
    body.append(problemCard(error));
  }
  return card;
}

// 面板级失败的**面向用户**文案（AC-10）：说清发生了什么 + 怎么办，错误码折叠在最后。
function problemCard(error) {
  const box = document.createElement("div");
  box.className = "panel-problem";
  const title = document.createElement("p");
  title.className = "panel-problem-title";
  title.textContent = "这块内容暂时看不到";
  const detail = document.createElement("p");
  detail.className = "panel-problem-detail";
  detail.textContent = error.message || "加载失败";
  box.append(title, detail);
  if (error.hint) {
    const hint = document.createElement("p");
    hint.className = "panel-problem-hint";
    hint.textContent = `下一步：${error.hint}`;
    box.append(hint);
  }
  if (error.code) {
    const details = document.createElement("details");
    details.className = "panel-technical";
    const summary = document.createElement("summary");
    summary.textContent = "技术细节";
    const code = document.createElement("code");
    code.textContent = error.code;
    details.append(summary, code);
    box.append(details);
  }
  return box;
}

// 缺上下文时的正常空状态（REQ-012.1 的 AC-1.1：冷开公司级页面**不出降级卡**）。
//
// 文案与可用的入口由**服务端**给（`page.empty_state` 的 message / hint / supports）：
// 核心只比对 `requires` 与查询串，所以新增一个「需要其它上下文」的页面不用改前端。
function needsContextCard(empty) {
  const state = empty || {};
  const box = document.createElement("div");
  box.className = "panel-empty-state";
  box.dataset.emptyState = state.reason || "context";
  const title = document.createElement("p");
  title.className = "empty-title";
  title.textContent = state.message || "这一页还需要先选好上下文";
  const detail = document.createElement("p");
  detail.className = "empty-detail";
  detail.textContent = state.hint || "";
  const supports = state.supports || [];
  const actions = document.createElement("p");
  actions.className = "empty-actions";
  if (supports.includes("company_picker")) {
    const pick = document.createElement("button");
    pick.type = "button";
    pick.dataset.emptyAction = "company-picker";
    pick.textContent = "选公司";
    pick.onclick = () => {
      const selector = document.getElementById("company-select");
      if (selector) selector.focus();
    };
    actions.append(pick);
  }
  if (supports.includes("home_link")) {
    const home = document.createElement("a");
    home.href = "#home";
    home.dataset.emptyAction = "home";
    home.textContent = "去工作台";
    actions.append(home);
  }
  box.append(title, detail, actions);
  return box;
}

async function openPage(pageId) {
  banner("");
  const { data: page, warnings } = await api(`/api/v1/pages/${encodeURIComponent(pageId)}`);
  pageNeedsCompany = (page.requires || []).includes("selection.company");
  document.getElementById("page-title").textContent = page.title;
  document.getElementById("page-desc").textContent = page.description || "";
  // 降级留痕要让人看见：否则页面「看起来正常」而实际有面板没渲染出来（复验 N7）。
  if (warnings.length) banner(warnings.join("；"), "warn");
  const container = document.getElementById("panels");
  container.innerHTML = "";
  // 服务端已经把「缺上下文」判成空状态（不渲染面板、也不产生 warnings），前端照着画即可。
  const emptyState = page.empty_state
    || (pageNeedsCompany && !selection.company
        ? { reason: "selection.company", message: "先选一家公司",
            hint: "这一页展示的是某一家公司的内容。用右上角的「当前公司」选一家。",
            supports: ["company_picker", "home_link"] }
        : null);
  document.body.dataset.needsCompany = emptyState ? "1" : "";
  if (emptyState) {
    // 缺上下文是**正常空状态**：不渲染面板、不发面板请求，也就不可能出降级卡（关掉 E2）。
    container.append(needsContextCard(emptyState));
  } else {
    for (const panel of page.panels) container.append(await mountPanel(panel));
  }
  updateBreadcrumb(page);
  for (const link of document.querySelectorAll("#nav a")) {
    link.classList.toggle("active", link.dataset.page === pageId);
  }
}

function updateBreadcrumb(page) {
  const node = document.getElementById("breadcrumb");
  if (!node) return;
  const parts = [page.group || "", page.title || ""].filter(Boolean);
  if ((page.requires || []).includes("selection.company")) {
    const name = document.getElementById("company-current");
    if (name && name.textContent) parts.push(name.textContent);
  }
  node.textContent = parts.join(" / ");
}

// --------------------------------------------------------------- 导航树与公司选择器

function navLink(item) {
  const link = document.createElement("a");
  link.href = `#${item.id}`;
  link.dataset.page = item.id;
  link.textContent = item.title;
  if ((item.requires || []).length) link.dataset.requires = item.requires.join(",");
  link.addEventListener("click", (event) => {
    event.preventDefault();
    setHash(item.id, selectionFor(item));
  });
  return link;
}

// 点进一个页面时带哪些参数：公司级页面带当前公司，其余不带（AC-1：不需要经过某个页面的链接）。
function selectionFor(item) {
  if ((item.requires || []).includes("selection.company") && selection.company) {
    return { company: selection.company };
  }
  return {};
}

function renderNav(items, container, depth = 0) {
  for (const item of items) {
    const link = navLink(item);
    link.style.paddingLeft = `${12 + depth * 12}px`;
    container.append(link);
    if ((item.children || []).length) renderNav(item.children, container, depth + 1);
  }
}

async function fillCompanySelector() {
  const select = document.getElementById("company-select");
  if (!select) return;
  const { data } = await api("/api/v1/companies");
  const companies = (data.companies || []);
  const remembered = rememberedCompany();
  const options = [{ value: "", label: "（未选择）" }];
  for (const item of companies) {
    options.push({ value: item.ticker || item.dir, label: item.display_name || item.name });
  }
  const current = selection.company || remembered || "";
  select.innerHTML = "";
  for (const option of options) {
    const node = document.createElement("option");
    node.value = option.value;
    node.textContent = option.label;
    if (option.value === current) node.selected = true;
    select.append(node);
  }
  if (!companies.length) {
    select.innerHTML = "";
    const node = document.createElement("option");
    node.value = "";
    node.textContent = "还没有公司数据";
    select.append(node);
    select.disabled = true;
  }
  // 上次选择在 URL 里没有公司时补进 URL——刷新与前进后退后状态一致（AC-1）。
  if (!selection.company && current) {
    selection.company = current;
    const page = parseHash().pageId || defaultPage;
    setHash(page, { ...selection, company: current });
  }
  updateCurrentCompanyLabel();
}

function updateCurrentCompanyLabel() {
  const label = document.getElementById("company-current");
  const select = document.getElementById("company-select");
  if (!label) return;
  // 控件要跟**当前选择**保持一致：切页时 URL 上可能没有 company，但上下文仍在内存里
  // （见 `applySelection`）。那时若只改标签、不同步控件，用户会看到「（未选择）」而实际
  // 上下文还在，公司级动作被误判成缺上下文——实测踩到：点侧栏回任务页后动作全部禁用。
  if (select && select.value !== (selection.company || "")) {
    select.value = selection.company || "";
  }
  const text = select && select.selectedIndex >= 0 ? select.options[select.selectedIndex].textContent : "";
  label.textContent = selection.company ? (text || selection.company) : "（未选择）";
}

function wireCompanySelector() {
  const select = document.getElementById("company-select");
  if (!select) return;
  select.addEventListener("change", () => {
    const value = select.value;
    if (value) {
      selection.company = value;
      rememberCompany(value);
    } else {
      forgetCompany();
    }
    updateCurrentCompanyLabel();
    // 切换公司 = 改 hash 的一个参数，**不重新加载页面**（hashchange 已经接好），
    // 渲染统一交给 hashchange 那一条路径——这里不要再自己调一次 openPage，
    // 否则同一页会被渲染两次（第二次还会带上还没生效的旧上下文）。
    const page = parseHash().pageId || defaultPage;
    setHash(page, { ...selection, company: value });
    if (parseHash().pageId === page && (window.location.hash || "").includes("company=")) {
      // hash 完全没变（例如从「未选择」切到「未选择」）：hashchange 不会触发，手动兜一次。
      reload();
    }
  });
}

function reload() {
  const { pageId } = parseHash();
  openPage(pageId || defaultPage).catch((error) => banner(problemText(error), "error"));
}

function problemText(error) {
  const parts = [error.message || "请求失败"];
  if (error.hint) parts.push(`下一步：${error.hint}`);
  return parts.join("；");
}

let defaultPage = "home";

async function boot() {
  try {
    const { data } = await api("/api/v1/nav");
    const items = data.items || [];
    defaultPage = data.default_page || (items[0] && items[0].id) || "home";
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
        groups.set(group, []);
      }
      groups.get(group).push(item);
    }
    for (const [, groupItems] of groups) renderNav(groupItems, nav);
    if (!items.length) {
      nav.textContent = "还没有注册任何页面。";
      return;
    }
    wireCompanySelector();
    try {
      await fillCompanySelector();
    } catch (error) {
      // 选择器取数失败不该让整个控制台起不来：导航与页面照常，横幅说明原因。
      banner(problemText(error), "warn");
    }
    const requested = parseHash().pageId;
    const known = items.some((item) => item.id === requested);
    if (!requested || !known) {
      // 未知页面 → 落到工作台并提示（而不是悄悄落到第一个业务面板）。
      if (requested) banner("没有这一页，已转到工作台。", "warn");
      setHash(defaultPage, selection.company ? { company: selection.company } : {});
      if (parseHash().pageId === requested) await openPage(defaultPage);
    }
    window.addEventListener("hashchange", () => {
      const next = parseHash();
      if (!next.pageId) return;
      pageNeedsCompany = false;
      applySelection(next.query);
      updateCurrentCompanyLabel();
      openPage(next.pageId).catch((error) => banner(problemText(error), "error"));
    });
    pageNeedsCompany = false;
    applySelection(parseHash().query);
    const first = items.some((item) => item.id === parseHash().pageId)
      ? parseHash().pageId
      : defaultPage;
    await openPage(first);
  } catch (error) {
    banner(`控制台初始化失败：${problemText(error)}`, "error");
  }
}

export { api, selection, openPage, problemCard, setHash, reload };
boot();
