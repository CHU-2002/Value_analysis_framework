# REQ-014 研发实跑记录

日期：2026-10-09。环境：macOS，Python 3.12.13，Microsoft Edge 无头浏览器。
真实来源是本项目的 `output` 公司产物；初轮写沙箱仓与缓存，任务历史按默认位置写项目
`output/.webui_jobs`。最终走查使用真实产物原样快照，全部运行产物写沙箱。所有轮次均不使用数据源 token，
不触碰真实 `~/turtle_archive`。本记录是研发自测，独立验收另由未参与实现的 agent 执行。

## 命令

在仓库根启动新服务（另一个终端执行走查；复现时将沙箱目录换成一个新的目录）：

```bash
WEBUI_ARCHIVE_ROOT="$PWD/output/.live_req014_after2/archive" \
WEBUI_CACHE_DIR="$PWD/output/.live_req014_after2/cache" \
.venv/bin/python -m scripts.webui --no-browser --port 8876
.venv/bin/python scripts/watchlist_walkthrough.py \
  --base http://127.0.0.1:8876 --out output/.live_req014_after2/browser
```

旧界面对照：基线 `bf063fee33d842cc96438da705802168b7949ac3` 的独立 worktree 起服务，
使用同一真实产物源与另一沙箱仓（端口 8874），同一脚本加 `--probe-only`。

```bash
.venv/bin/python scripts/watchlist_walkthrough.py \
  --base http://127.0.0.1:8874 --out output/.live_req014_before/browser --probe-only
```

最终走查使用 `WEBUI_OUTPUT_ROOT="$PWD/output/.live_req014_final/products"`（真实公司目录的原样快照），
仓、缓存分别指向同一沙箱的 `archive` / `cache`；端口 8877，脚本证据目录为
`output/.live_req014_final/browser`。所有任务历史也在沙箱内（任务 T6）。

## 实际观察

- 旧版退出码 **1**，4 条失败：引导未指向添加/导入入口，添加/导入/移除动作元素均不存在。
- 最终 `make verify`：**1900 passed / 3 skipped**，覆盖率 **76.95%**；追溯、scope、回归门禁全部通过。
  Scope：48 文件 / 1903 用例，在 52 / 2000 预算内；3 个 skip 是未配置 token 的既有 API integration 用例。
- 新版退出码 **0**，`failures: []`；三种代码写法添加工商银行均归到同一标的，清单与选择器在当前页刷新，
  工作台和公司列表也能看到尚未有产物的新公司。
- 真实产物首次导入：**导入 1 家、跳过 15 项**；每项跳过原因在卡片显示。
  既有伊利公司有记录，11 家早期公司没有记录，其余目录不符合公司产物约定或缺少记录。
- 重复导入：**导入 0 家、跳过 16 项**，其中伊利明确显示已在清单里。
- 移除前确认说明保留产物；取消不改清单，确认后伊利从数据清单消失。
  产物保留逐文件断言见 `tests/test_watchlist_ui.py`，独立验收另做真实文件哈希复核。
- 首轮走查在结果摘要出现时过早读取页面，修正为等待刷新结束，问题已登记需求任务 T1。
  独立预检发现的 GET 行契约、子进程离线证明与异常文案问题登记 T2/T3/T5，并补回归。独立干净 worktree 还发现四个测试借用了本机真实 dotenv；已通过共享凭据来源隔离
  与四个显式 mock 凭据 fixture 修复，登记 T7，未修改产品预检。
- 初次受限环境全量测试有 13 条本机 HTTP 绑定权限失败；允许本机绑定后原样跑门禁全部通过。
  这项运行环境约束登记 T4，未修改任何测试或门槛。

## 截图与观察原文

- [旧版入口缺失](REQ-014-development/before-empty.png)
- [新版空清单与真实入口](REQ-014-development/after-empty.png)
- [真实导入及逐项跳过原因](REQ-014-development/imported.png)
- [移除结果](REQ-014-development/removed.png)
- [旧版 observations](REQ-014-development/before-observations.json)
- [新版 observations](REQ-014-development/after-observations.json)
