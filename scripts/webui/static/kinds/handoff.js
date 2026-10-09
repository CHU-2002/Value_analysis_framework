// 人机交接（REQ-012.2 的 AC-2.4）的**共享**渲染器：任务中心与动作面板都用它。
//
// 为什么要独立成一个模块：门② 第六轮抓到，交接面板原先只由 `actions.js::follow()` 渲染，
// 而 `follow()` 只在「刚点执行」与「点重试」时被调用——于是
//   ① 刷新或重新进入页面后，等待中的任务**找不到继续/放弃的入口**；
//   ② 点「我跑完了，继续」而产物不合格时，面板整体消失，界面**一个字都不说缺什么**。
// 交接是服务端状态（`awaiting_agent`），界面必须**从状态重建**，而不是只在某一次点击的
// 回调里存在。所以：谁能拿到一个 awaiting 的 job，谁就调用这里。
import { api } from "/app.js";

const PATH_LABELS = {
  company_dir: "公司目录",
  run_dir: "本次 run",
  ticker: "标的",
  primary_period: "期次",
};

function copyText(text, button) {
  if (!navigator.clipboard) {
    button.textContent = "请手动复制";
    return;
  }
  navigator.clipboard.writeText(text).then(
    () => { button.textContent = "已复制"; },
    () => { button.textContent = "请手动复制"; },
  );
}

function problem(error) {
  const text = document.createElement("p");
  text.className = "panel-problem-detail";
  text.textContent = error.hint ? `${error.message}；下一步：${error.hint}` : error.message;
  return text;
}

/** 一个 awaiting 任务 → 交接面板（含「缺什么」与继续/放弃）。 */
export function handoffPanel(job, refresh) {
  const box = document.createElement("div");
  box.className = "handoff";
  box.dataset.handoffFor = job.id;
  box.dataset.handoffStep = String((job.handoff || {}).step ?? "");
  const handoff = job.handoff || {};

  const title = document.createElement("p");
  title.className = "handoff-title";
  title.textContent = `需要你手动跑一步：${handoff.title || ""}`;
  box.append(title);

  if (handoff.hint) {
    const hint = document.createElement("p");
    hint.className = "panel-note";
    hint.textContent = handoff.hint;
    box.append(hint);
  }
  if (handoff.slash) {
    const row = document.createElement("p");
    row.className = "handoff-command";
    const code = document.createElement("code");
    code.textContent = handoff.slash;
    const copy = document.createElement("button");
    copy.type = "button";
    copy.dataset.handoffCopy = "1";
    copy.textContent = "复制命令";
    copy.onclick = () => copyText(handoff.slash, copy);
    row.append(code, copy);
    box.append(row);
  }

  const paths = handoff.paths || {};
  const keys = Object.keys(paths);
  if (keys.length) {
    const list = document.createElement("dl");
    list.className = "handoff-paths";
    for (const key of keys) {
      const dt = document.createElement("dt");
      dt.textContent = PATH_LABELS[key] || key;
      const dd = document.createElement("dd");
      const code = document.createElement("code");
      code.textContent = paths[key];
      dd.append(code);
      list.append(dt, dd);
    }
    box.append(list);
  }
  if ((handoff.expects || []).length) {
    const expects = document.createElement("p");
    expects.className = "panel-note";
    expects.textContent = "继续前会检查这些产物是否存在且是这次跑出来的："
      + handoff.expects.join("、");
    box.append(expects);
  }

  // 「缺什么」是**一等信息**：它必须在界面上（含点继续失败之后），不能只活在接口里。
  const missing = handoff.missing || [];
  const missingNode = document.createElement("p");
  missingNode.className = missing.length ? "handoff-missing" : "handoff-missing panel-note";
  missingNode.dataset.handoffMissing = "1";
  if (missing.length) {
    missingNode.textContent = "这些产物还没就绪（可能不存在，或没有比这一步开始时更新）："
      + missing.join("、");
  } else {
    const list = (handoff.expects || []).length
      ? `这一步应该产出：${(handoff.expects || []).join("、")}。`
      : "这一步应当产出分析记录。";
    missingNode.textContent = `${list}跑完上面那条命令再点「我跑完了，继续」；`
      + "没有产物就点继续会被拦下，我会告诉你缺哪一项。";
  }
  box.append(missingNode);

  // 反馈区：**从状态渲染**（`handoff.missing` 由服务端在校验失败时写入并持久化）。
  // 若只在点击回调里写这段文字，随后的一次面板刷新就会把它抹掉（实测就是如此）。
  const feedback = document.createElement("p");
  feedback.className = "handoff-feedback";
  feedback.dataset.handoffFeedback = "1";
  if (missing.length) {
    feedback.dataset.handoffBlocked = "1";
    feedback.textContent = "还没检测到这一步的产物，任务仍停在这一步："
      + missing.join("、")
      + (handoff.last_check ? `（校验时间 ${handoff.last_check}）` : "");
  } else {
    feedback.hidden = true;
  }
  box.append(feedback);

  const actions = document.createElement("p");
  actions.className = "handoff-actions";
  const done = document.createElement("button");
  done.type = "button";
  done.dataset.handoffContinue = "1";
  done.textContent = "我跑完了，继续";
  done.onclick = async () => {
    done.disabled = true;
    feedback.hidden = true;
    try {
      const next = (await api(
        `/api/v1/jobs/${encodeURIComponent(job.id)}/continue`, { method: "POST" },
      )).data;
      // 服务端语义：产物不合格就**停在原地**，响应里会带最新的 `handoff.missing`。
      // 界面要把它说出来（门② 第六轮：此前这一步失败后界面一个字都没有）。
      if (next && next.status === "awaiting_agent") {
        const still = (next.handoff || {}).missing || [];
        feedback.hidden = false;
        feedback.dataset.handoffBlocked = "1";
        feedback.textContent = still.length
          ? `还没检测到这一步的产物，任务仍停在这一步：${still.join("、")}`
          : "任务仍停在这一步：产物还没就绪。";
      }
      // 注意：上面只是**即时**反馈；刷新后由 `missing` 重新渲染（见函数开头），
      // 所以这段文字不会因为一次重绘就消失。
    } catch (error) {
      feedback.hidden = false;
      box.append(problem(error));
    } finally {
      done.disabled = false;
      if (refresh) refresh();
    }
  };
  const give = document.createElement("button");
  give.type = "button";
  give.dataset.handoffAbandon = "1";
  give.textContent = "放弃这次";
  give.onclick = async () => {
    give.disabled = true;
    try {
      await api(`/api/v1/jobs/${encodeURIComponent(job.id)}/abandon`, { method: "POST" });
    } finally {
      if (refresh) refresh();
    }
  };
  actions.append(done, give);
  box.append(actions);
  return box;
}

/** 把容器里已有的交接面板换掉（同 id 只留一个）。 */
export function mountHandoff(container, job, refresh) {
  container.querySelectorAll(`[data-handoff-for="${job.id}"]`).forEach((node) => node.remove());
  const panel = handoffPanel(job, refresh);
  container.append(panel);
  return panel;
}

export { copyText as _copyText, problem as _problem };
