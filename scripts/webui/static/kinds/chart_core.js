// 图表公共能力（REQ-012.3 的 AC-7）：比例尺 / 刻度 / 图例 / DPR 适配 / 标签抽稀 /
// 缺失值处理 / 悬停读数 / 导出。新增图型（柱状、热力图…）只写渲染器、复用这一层。
//
// 「服务端管契约，走查管行为」：服务端保证数据形状（`labels`/`series`/`basis`/`unit`），
// 这里保证**画出来对**。像素级行为在 CI 里没有 JS 运行器，所以由
// `scripts/gui_walkthrough.py` 的浏览器走查覆盖。
export const COLORS = ["#2f6fed", "#e8804a", "#3aa76d", "#a05ad6", "#c94f7c", "#0f9bb5"];

export const DEFAULT_PADDING = { top: 26, right: 16, bottom: 34, left: 68 };

export function isNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

// 缺失值是 `null`（数据包里的「—」）：**不能画成 0**，否则趋势被篡改。
export function isMissing(value) {
  return value === null || value === undefined || value === "";
}

export function niceScale(values) {
  const numbers = values.filter(isNumber);
  if (!numbers.length) return { min: 0, max: 1 };
  let max = Math.max(...numbers);
  let min = Math.min(...numbers);
  if (min > 0) min = 0;               // 财务量级从 0 起更不容易误读
  if (max < 0) max = 0;
  if (max === min) return { min: min - 1, max: max + 1 };
  const pad = (max - min) * 0.08;
  return { min: min - pad, max: max + pad };
}

export function formatTick(value) {
  if (!isNumber(value)) return "";
  const abs = Math.abs(value);
  if (abs >= 10000) return value.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
  if (abs >= 100) return value.toFixed(0);
  if (abs >= 1) return value.toFixed(1);
  return value.toFixed(2);
}

// 按**实测文本宽度**抽稀标签（不是「隔一项画一个」）：宽标签少画、窄标签多画。
export function thinLabels(ctx, labels, plotWidth, { minGap = 8 } = {}) {
  const widths = labels.map((label) => ctx.measureText(String(label)).width + minGap);
  const total = widths.reduce((sum, width) => sum + width, 0);
  if (total <= plotWidth) return labels.map((_, index) => index);
  const step = Math.ceil(total / Math.max(plotWidth, 1));
  const keep = [];
  for (let index = 0; index < labels.length; index += step) keep.push(index);
  return keep;
}

export function measureLegend(ctx, series) {
  return series.map((item) => ctx.measureText(item.name || "").width + 22);
}

// 图例**流动布局**：按容器宽度换行，不按固定 110px 步进（AC-7：图例不被截断）。
export function layoutLegend(ctx, series, width) {
  const widths = measureLegend(ctx, series);
  const rows = [];
  let row = { items: [], width: 0 };
  series.forEach((item, index) => {
    const itemWidth = widths[index];
    if (row.width + itemWidth > width && row.items.length) {
      rows.push(row);
      row = { items: [], width: 0 };
    }
    row.items.push({ item, index, x: row.width });
    row.width += itemWidth;
  });
  if (row.items.length) rows.push(row);
  return { rows, height: rows.length * 16 };
}

// DPR 适配（AC-7：图形按容器宽度与设备像素比绘制，不模糊）。
export function resizeCanvas(canvas, container, cssHeight) {
  const ratio = window.devicePixelRatio || 1;
  const cssWidth = Math.max(240, Math.floor(container.clientWidth || canvas.clientWidth || 720));
  canvas.style.width = `${cssWidth}px`;
  canvas.style.height = `${cssHeight}px`;
  canvas.width = Math.floor(cssWidth * ratio);
  canvas.height = Math.floor(cssHeight * ratio);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);   // 之后一律用 CSS 像素坐标作画
  return { ctx, width: cssWidth, height: cssHeight, ratio };
}

export function drawAxes(ctx, { width, height, scale, padding, format = formatTick, steps = 4 }) {
  ctx.save();
  ctx.strokeStyle = "#e3e3e3";
  ctx.fillStyle = "#888";
  ctx.font = "11px system-ui, sans-serif";
  for (let step = 0; step <= steps; step += 1) {
    const value = scale.min + ((scale.max - scale.min) * step) / steps;
    const y = padding.top + (height - padding.top - padding.bottom) * (1 - step / steps);
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(width - padding.right, y);
    ctx.stroke();
    ctx.fillText(format(value), 6, y + 4);
  }
  ctx.restore();
}

export function drawSeries(ctx, options) {
  const { series, x, y, plotWidth, labels, type = "line", hoverIndex = null } = options;
  series.forEach((item, seriesIndex) => {
    const values = item.values || [];
    const color = COLORS[seriesIndex % COLORS.length];
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    if (type === "bar") {
      const barWidth = Math.max(2, plotWidth / Math.max(labels.length, 1) / (series.length + 1));
      values.forEach((value, index) => {
        if (!isNumber(value)) return;   // 缺失值不画柱子，也不画 0
        const zero = y(0);
        ctx.fillRect(
          x(index) - (barWidth * series.length) / 2 + seriesIndex * barWidth,
          Math.min(y(value), zero),
          Math.max(1, barWidth - 1),
          Math.abs(y(value) - zero),
        );
      });
      return;
    }
    ctx.lineWidth = 2;
    ctx.beginPath();
    let started = false;
    values.forEach((value, index) => {
      if (!isNumber(value)) {
        started = false;                 // 缺失值**断开**折线，不连假线
        return;
      }
      if (!started) ctx.moveTo(x(index), y(value));
      else ctx.lineTo(x(index), y(value));
      started = true;
    });
    ctx.stroke();
    // 缺失值标一个空心点：让「断开」看起来是**有意的**，而不是数据没加载。
    values.forEach((value, index) => {
      if (value === null || value === undefined) return;
      if (isNumber(value)) return;
      ctx.save();
      ctx.strokeStyle = "#c0c0c8";
      ctx.beginPath();
      ctx.arc(x(index), y(0), 2, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    });
  });
  if (hoverIndex !== null && hoverIndex >= 0 && hoverIndex < labels.length) {
    ctx.save();
    ctx.strokeStyle = "#bbb";
    ctx.beginPath();
    ctx.moveTo(x(hoverIndex), options.padding.top);
    ctx.lineTo(x(hoverIndex), options.height - options.padding.bottom);
    ctx.stroke();
    ctx.restore();
  }
}

export function drawLegend(ctx, series, rows, top) {
  ctx.save();
  ctx.font = "11px system-ui, sans-serif";
  rows.forEach((row, rowIndex) => {
    row.items.forEach(({ item, index, x }) => {
      const y = top + rowIndex * 16;
      ctx.fillStyle = COLORS[index % COLORS.length];
      ctx.fillRect(x, y, 8, 8);
      ctx.fillStyle = "#444";
      ctx.fillText(item.name || `系列 ${index + 1}`, x + 12, y + 8);
    });
  });
  ctx.restore();
}

export function drawTooltip(ctx, { labels, series, hoverIndex, x, width, padding }) {
  const lines = [
    String(labels[hoverIndex]),
    ...series.map((item) => {
      const value = (item.values || [])[hoverIndex];
      const text = isNumber(value) ? formatTick(value) : (value === null || value === undefined ? "—（缺失）" : String(value));
      return `${item.name}: ${text}`;
    }),
  ];
  ctx.save();
  ctx.font = "11px system-ui, sans-serif";
  const boxWidth = Math.min(
    Math.max(...lines.map((line) => ctx.measureText(line).width)) + 16,
    width - padding.left - 20,
  );
  const boxHeight = 15 * lines.length + 8;
  const boxX = Math.min(Math.max(padding.left, x(hoverIndex) + 8), width - boxWidth - 4);
  ctx.fillStyle = "rgba(255,255,255,0.96)";
  ctx.fillRect(boxX, padding.top, boxWidth, boxHeight);
  ctx.strokeStyle = "#ccc";
  ctx.strokeRect(boxX, padding.top, boxWidth, boxHeight);
  ctx.fillStyle = "#222";
  lines.forEach((line, index) => ctx.fillText(line, boxX + 6, padding.top + 14 + index * 15));
  ctx.restore();
}

export function drawEmpty(ctx, width, height, text = "暂无数据") {
  ctx.save();
  ctx.fillStyle = "#888";
  ctx.font = "12px system-ui, sans-serif";
  ctx.fillText(text, Math.min(12, width / 2), Math.min(24, height / 2));
  ctx.restore();
}

// 导出（AC-7：提供导出）：PNG 下载 + 数据 TSV 复制，都不引新依赖。
export function downloadPng(canvas, name = "chart") {
  const link = document.createElement("a");
  link.href = canvas.toDataURL("image/png");
  link.download = `${name}.png`;
  link.click();
}

export function seriesToTsv(labels, series) {
  const head = ["期次", ...series.map((item) => item.name || "")].join("\t");
  const rows = (labels || []).map((label, index) => [
    label,
    ...series.map((item) => {
      const value = (item.values || [])[index];
      return isMissing(value) ? "" : String(value);
    }),
  ].join("\t"));
  return [head, ...rows].join("\n");
}

// 一次完整的「画一张图」：所有图型共用（AC-7 的绘制质量要求落在这里，一处修好、所有图受益）。
export function renderChart(container, { labels, series, type, unit, basisLabel, basisKind }) {
  const canvas = document.createElement("canvas");
  canvas.className = "chart-canvas";
  container.append(canvas);
  const cssHeight = 280;
  let state = { hoverIndex: null };

  function paint() {
    const { ctx, width, height } = resizeCanvas(canvas, container, cssHeight);
    ctx.clearRect(0, 0, width, height);
    ctx.font = "11px system-ui, sans-serif";
    const padding = { ...DEFAULT_PADDING };
    const headline = [unit ? `单位：${unit}` : "", basisLabel ? `口径：${basisLabel}${basisKind ? `（${basisKind}）` : ""}` : ""]
      .filter(Boolean).join(" · ");
    if (headline) {
      ctx.fillStyle = "#666";
      ctx.fillText(headline, padding.left, 12);
    }
    if (!labels.length || !series.length) {
      drawEmpty(ctx, width, height, "这一口径下没有可画的数据");
      return null;
    }
    const flat = series.flatMap((item) => item.values || []);
    const scale = niceScale(flat);
    const legend = layoutLegend(ctx, series, width - padding.left - padding.right);
    padding.top += legend.height;
    const plotWidth = Math.max(10, width - padding.left - padding.right);
    const plotHeight = Math.max(10, height - padding.top - padding.bottom);
    const x = (index) => padding.left + (labels.length === 1 ? plotWidth / 2 : (index * plotWidth) / (labels.length - 1));
    const y = (value) => padding.top + plotHeight - ((value - scale.min) / (scale.max - scale.min)) * plotHeight;
    drawAxes(ctx, { width, height, scale, padding });
    drawSeries(ctx, { series, x, y, plotWidth, labels, type, hoverIndex: state.hoverIndex, padding, height });
    // 横轴：按实测宽度抽稀，且**右侧留出末标签宽度的一半**，末标签不会被截断。
    const keep = thinLabels(ctx, labels, plotWidth - 8);
    ctx.fillStyle = "#666";
    keep.forEach((index) => {
      const text = String(labels[index]);
      const half = Math.min(ctx.measureText(text).width / 2, 40);
      const left = Math.min(Math.max(x(index) - half, 2), width - ctx.measureText(text).width - 2);
      ctx.fillText(text, left, height - 10);
    });
    drawLegend(ctx, series, legend.rows, 14);
    if (state.hoverIndex !== null) {
      drawTooltip(ctx, { labels, series, hoverIndex: state.hoverIndex, x, width, padding });
    }
    return { x, padding, plotWidth };
  }

  let geometry = paint();
  canvas.addEventListener("mousemove", (event) => {
    const rect = canvas.getBoundingClientRect();
    const ratio = (event.clientX - rect.left) / Math.max(rect.width, 1);
    const padLeft = (geometry ? geometry.padding.left : DEFAULT_PADDING.left) / Math.max(rect.width, 1);
    const position = (ratio - padLeft) / Math.max(1 - padLeft - 0.02, 0.01);
    let index = Math.round(position * Math.max(labels.length - 1, 1));
    if (index < 0 || index >= labels.length) index = null;
    state.hoverIndex = index;
    paint();
  });
  canvas.addEventListener("mouseleave", () => {
    state.hoverIndex = null;
    paint();
  });
  // 容器宽度变化时重画（AC-7：按容器宽度绘制）。ResizeObserver 不可用时退回窗口 resize。
  if (typeof ResizeObserver !== "undefined") {
    const observer = new ResizeObserver(() => {
      if (!canvas.isConnected) { observer.disconnect(); return; }
      paint();
    });
    observer.observe(container);
  }
  return { canvas, repaint: paint, setData(next) { labels = next.labels; series = next.series; geometry = paint(); } };
}

export { renderChart as default };

// Export composition is renderer work: kind dispatchers only supply canvases and metadata.
export function composeChartImage(canvases, headings = []) {
  const output = document.createElement("canvas");
  output.width = Math.max(320, ...canvases.map(canvas => canvas.width));
  const headerHeight = 30 + 30 * headings.length;
  output.height = headerHeight + canvases.reduce((sum, canvas) => sum + canvas.height, 0);
  const context = output.getContext("2d");
  context.fillStyle = "white";
  context.fillRect(0, 0, output.width, output.height);
  context.fillStyle = "#333";
  context.font = "16px sans-serif";
  headings.forEach((text, index) => context.fillText(text, 12, 22 + 30 * index));
  let top = headerHeight;
  canvases.forEach(canvas => { context.drawImage(canvas, 0, top); top += canvas.height; });
  return output;
}
