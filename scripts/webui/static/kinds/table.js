// kind=table：服务端已经把表格渲染好了（CI 里能断言 HTML），这里只加**行为**：
// 搜索 / 排序 / 分页。刻意做在通用渲染层（AC-3.4）：公司列表、产物列表、批次列表、
// 缺口列表都用它，不每个面板各写一遍。
//
// 服务端的契约（`render/panels.py::render_table`）：
//   - 表头格子带 `data-sort-key`（可排序）与 `data-sort-type`（text/number）；
//   - 表格容器带 `data-table-controls="search,sort,page"` 与 `data-page-size`；
//   - `th` 与每行 `td` 一一对应，所以排序按**列序号**取单元格文本即可（不依赖列语义）。
const DEFAULT_PAGE_SIZE = 50;

function cellText(row, index, key) {
  const cell = row.children[index];
  if (!cell) return "";
  const own = key ? cell.dataset[key] : undefined;
  return (own !== undefined ? own : cell.textContent || "").trim();
}

function numeric(text) {
  const cleaned = String(text).replace(/[,\s]/g, "");
  if (cleaned === "" || cleaned === "—") return null;
  const value = Number(cleaned);
  return Number.isFinite(value) ? value : null;
}

function buildControls(container) {
  const box = document.createElement("div");
  box.className = "table-controls";
  const search = document.createElement("input");
  search.type = "search";
  search.placeholder = "搜索…";
  search.className = "table-search";
  search.dataset.tableSearch = "1";
  search.setAttribute("aria-label", "搜索表格内容");
  const count = document.createElement("span");
  count.className = "table-count panel-note";
  box.append(search, count);
  container.prepend(box);
  return { search, count };
}

function buildPager(container) {
  const box = document.createElement("div");
  box.className = "table-pager";
  const prev = document.createElement("button");
  prev.type = "button";
  prev.textContent = "上一页";
  prev.dataset.tablePage = "prev";
  const next = document.createElement("button");
  next.type = "button";
  next.textContent = "下一页";
  next.dataset.tablePage = "next";
  const info = document.createElement("span");
  info.className = "table-page-info panel-note";
  box.append(prev, info, next);
  container.append(box);
  return { prev, next, info };
}

export async function render(container, panel, _data) {
  const table = container.querySelector("table");
  if (!table) return;   // 空状态/降级卡片没有表格：什么都不做
  const options = (panel.options && panel.options.table) || {};
  const head = table.querySelector("thead tr");
  const body = table.querySelector("tbody");
  if (!head || !body) return;

  const headers = Array.from(head.children);
  const sortable = headers
    .map((th, index) => ({ index, key: th.dataset.sortKey, type: th.dataset.sortType || "text" }))
    .filter((item) => item.key);
  // 每行原始顺序留在 `data-original-index` 上：分页/筛选后仍能「还原默认顺序」。
  Array.from(body.children).forEach((row, index) => {
    row.dataset.originalIndex = String(index);
  });

  const state = { query: "", sort: null, direction: 1, page: 0 };
  const pageSize = Math.max(5, Number(options.page || DEFAULT_PAGE_SIZE) || DEFAULT_PAGE_SIZE);
  const searchable = options.search !== false;
  const control = searchable ? buildControls(container) : null;
  const pager = options.page === 0 ? null : buildPager(container);

  function match(row) {
    if (!state.query) return true;
    const text = Array.from(row.children).map((cell) => cell.textContent).join(" ").toLowerCase();
    return text.includes(state.query);
  }

  function compare(left, right) {
    if (!state.sort) {
      return Number(left.dataset.originalIndex) - Number(right.dataset.originalIndex);
    }
    const { index, type } = state.sort;
    const a = cellText(left, index, state.sort.key);
    const b = cellText(right, index, state.sort.key);
    if (type === "number") {
      const na = numeric(a);
      const nb = numeric(b);
      if (na === null && nb === null) return 0;
      if (na === null) return 1;       // 缺失值排在最后，不冒充 0
      if (nb === null) return -1;
      return (na - nb) * state.direction;
    }
    return a.localeCompare(b, "zh-Hans-CN") * state.direction;
  }

  function apply() {
    const rows = Array.from(body.children);
    const visible = rows.filter(match).sort(compare);
    rows.forEach((row) => { row.hidden = true; });
    const total = visible.length;
    const pages = Math.max(1, Math.ceil(total / pageSize));
    if (state.page >= pages) state.page = pages - 1;
    const start = state.page * pageSize;
    visible.slice(start, start + pageSize).forEach((row) => {
      row.hidden = false;
      body.append(row);          // 重新排序：DOM 顺序 = 显示顺序
    });
    rows.filter((row) => row.hidden).forEach((row) => body.append(row));
    if (control) {
      control.count.textContent = state.query || total !== rows.length
        ? `显示 ${Math.min(pageSize, Math.max(0, total - start))} / 命中 ${total} / 共 ${rows.length} 行`
        : `显示 ${Math.min(pageSize, total)} / 共 ${rows.length} 行`;
    }
    if (pager) {
      pager.info.textContent = `第 ${state.page + 1} / ${pages} 页`;
      pager.prev.disabled = state.page === 0;
      pager.next.disabled = state.page >= pages - 1;
    }
  }

  if (control) {
    control.search.addEventListener("input", () => {
      state.query = control.search.value.trim().toLowerCase();
      state.page = 0;
      apply();
    });
  }
  if (pager) {
    pager.prev.addEventListener("click", () => { state.page = Math.max(0, state.page - 1); apply(); });
    pager.next.addEventListener("click", () => { state.page += 1; apply(); });
  }
  for (const item of sortable) {
    const th = headers[item.index];
    th.classList.add("sortable");
    th.tabIndex = 0;
    const header = th.textContent;
    const flip = () => {
      if (state.sort && state.sort.index === item.index) state.direction = -state.direction;
      else { state.sort = item; state.direction = 1; }
      headers.forEach((node) => node.removeAttribute("data-sort-active"));
      th.dataset.sortActive = state.direction > 0 ? "asc" : "desc";
      apply();
    };
    th.addEventListener("click", flip);
    th.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); flip(); }
    });
    th.title = `按「${header}」排序`;
  }
  apply();
}
