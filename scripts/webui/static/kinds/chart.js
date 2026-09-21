// kind=chart：数据形状 {labels:[...], series:[{name, values:[...]}]}
// 用原生 canvas 画折线/柱状 + 鼠标悬停读数。刻意不引图表库（零新增依赖）。
function niceScale(values) {
  const max = Math.max(...values, 0);
  const min = Math.min(...values, 0);
  if (max === min) return { min: min - 1, max: max + 1 };
  const pad = (max - min) * 0.1;
  return { min: min - pad, max: max + pad };
}

function draw(ctx, canvas, panel, data, hoverIndex) {
  const { width, height } = canvas;
  ctx.clearRect(0, 0, width, height);
  const labels = data.labels || [];
  const series = data.series || [];
  const type = (panel.options && panel.options.chart && panel.options.chart.type) || "line";
  const flat = series.flatMap((item) => item.values || []);
  if (!labels.length || !flat.length) {
    ctx.fillStyle = "#888";
    ctx.fillText("暂无数据", 12, 24);
    return;
  }
  const { min, max } = niceScale(flat);
  const padLeft = 64;
  const padBottom = 28;
  const plotWidth = width - padLeft - 12;
  const plotHeight = height - padBottom - 16;
  const x = (index) => padLeft + (labels.length === 1 ? plotWidth / 2 : (index * plotWidth) / (labels.length - 1));
  const y = (value) => 16 + plotHeight - ((value - min) / (max - min)) * plotHeight;

  ctx.strokeStyle = "#e3e3e3";
  ctx.fillStyle = "#888";
  ctx.font = "11px system-ui, sans-serif";
  for (let step = 0; step <= 4; step += 1) {
    const value = min + ((max - min) * step) / 4;
    const lineY = y(value);
    ctx.beginPath();
    ctx.moveTo(padLeft, lineY);
    ctx.lineTo(width - 12, lineY);
    ctx.stroke();
    ctx.fillText(value.toFixed(1), 6, lineY + 4);
  }
  const colors = ["#2f6fed", "#e8804a", "#3aa76d", "#a05ad6"];
  series.forEach((item, seriesIndex) => {
    const values = item.values || [];
    ctx.strokeStyle = colors[seriesIndex % colors.length];
    ctx.fillStyle = ctx.strokeStyle;
    if (type === "bar") {
      const barWidth = Math.max(2, plotWidth / labels.length / (series.length + 1));
      values.forEach((value, index) => {
        const barHeight = Math.abs(y(value) - y(0));
        ctx.fillRect(
          x(index) - barWidth * series.length / 2 + seriesIndex * barWidth,
          Math.min(y(value), y(0)),
          barWidth - 1,
          barHeight,
        );
      });
    } else {
      ctx.lineWidth = 2;
      ctx.beginPath();
      values.forEach((value, index) => {
        if (index === 0) ctx.moveTo(x(index), y(value));
        else ctx.lineTo(x(index), y(value));
      });
      ctx.stroke();
    }
  });
  ctx.fillStyle = "#666";
  labels.forEach((label, index) => {
    if (labels.length > 12 && index % 2 === 1) return;
    ctx.fillText(String(label), x(index) - 12, height - 8);
  });
  let legendX = padLeft;
  series.forEach((item, seriesIndex) => {
    ctx.fillStyle = colors[seriesIndex % colors.length];
    ctx.fillRect(legendX, 2, 8, 8);
    ctx.fillStyle = "#444";
    ctx.fillText(item.name || `系列 ${seriesIndex + 1}`, legendX + 12, 10);
    legendX += 110;
  });
  if (hoverIndex !== null && hoverIndex >= 0 && hoverIndex < labels.length) {
    ctx.strokeStyle = "#bbb";
    ctx.beginPath();
    ctx.moveTo(x(hoverIndex), 16);
    ctx.lineTo(x(hoverIndex), 16 + plotHeight);
    ctx.stroke();
    const lines = [
      String(labels[hoverIndex]),
      ...series.map((item) => `${item.name}: ${(item.values || [])[hoverIndex]}`),
    ];
    const boxWidth = 150;
    const boxHeight = 14 * lines.length + 8;
    const boxX = Math.min(Math.max(padLeft, x(hoverIndex) + 8), width - boxWidth - 4);
    ctx.fillStyle = "rgba(255,255,255,0.95)";
    ctx.fillRect(boxX, 16, boxWidth, boxHeight);
    ctx.strokeStyle = "#ccc";
    ctx.strokeRect(boxX, 16, boxWidth, boxHeight);
    ctx.fillStyle = "#222";
    lines.forEach((line, index) => ctx.fillText(line, boxX + 6, 30 + index * 14));
  }
}

export async function render(container, panel, data) {
  if (!data) {
    container.textContent = "暂无数据";
    return;
  }
  const canvas = document.createElement("canvas");
  canvas.width = 720;
  canvas.height = 260;
  canvas.className = "chart-canvas";
  container.append(canvas);
  const ctx = canvas.getContext("2d");
  const labelCount = (data.labels || []).length;
  let hoverIndex = null;
  canvas.addEventListener("mousemove", (event) => {
    const rect = canvas.getBoundingClientRect();
    const ratio = (event.clientX - rect.left) / rect.width;
    const padLeft = 64 / canvas.width;
    const span = 1 - padLeft - 12 / canvas.width;
    const position = (ratio - padLeft) / span;
    hoverIndex = Math.round(position * Math.max(labelCount - 1, 1));
    if (hoverIndex < 0 || hoverIndex >= labelCount) hoverIndex = null;
    draw(ctx, canvas, panel, data, hoverIndex);
  });
  canvas.addEventListener("mouseleave", () => {
    hoverIndex = null;
    draw(ctx, canvas, panel, data, null);
  });
  draw(ctx, canvas, panel, data, null);
}
