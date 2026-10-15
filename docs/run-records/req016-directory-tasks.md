# REQ-016 目录与批次实现任务

- T1（完成）：全市场目录独立于自选股保存，采用 additive `directory_snapshot` 表（`CREATE TABLE IF NOT EXISTS`）；现有 schema 版本不变，旧数据库首次访问自动增加表，旧记录与清单不变。每次刷新有独立标识和内容 SHA256，逐交易所记覆盖与排除，旧快照永不覆盖。计划、批次固定引用由采集编排层保存。
- T2（完成）：北京市场后缀 `BJ` 加入自选股和 A 股接口过滤；若显式计划交易日历，按沪/深/北选择 SSE/SZSE/BSE。扫描调用点的默认 SSE 入参不得覆盖此市场选择；显式用户入参仍可指定交易所。
- T3（完成）：真实规模预检发现原 `append_batch` 每个目标重复序列化全 targets 并删除重写所有 completed，累计写量 O(n²)。新增 `changed_keys` 增量协议：首次保存固定目标，每个完成目标在一个 SQLite 事务内写入，运行中的轻量更新不重写兼容 JSON，暂停、失败、终态保存完整 JSON。默认全量调用仍兼容旧调用者，恢复优先读 SQLite。
- T4（环境记录）：2026-10-12 沙箱 `output/.live_req016_directory` 第一次真实目录刷新因受限网络 DNS 不能解析 `api.waditu.com`；三交易所各重试 5 次后返回覆盖未知，目录数量 0，快照与缺口保留且不能确认全市场采集。随后通过自动审批取得网络访问，重复刷新成功：快照 `a107a05c417d4762bb7145d74260f442`，5573 家（SSE 2320 / SZSE 2904 / BSE 349），三交易所覆盖 confirmed，缺口列表为空，退出码 0。内容摘要 `bbc8736398aaf763b49f7981d5880a04c3c79a23b304714d66d4546c55006d74`，获取时间 `2026-10-12T15:17:19.023752+00:00`。token 仅从主仓 `.env` 读取，未复制到工作树、日志或产物；真实 HOME 存档未写入。

验证：`/Users/everett.li/Project/Turtle_investment_framework/.venv/bin/python -m pytest -q tests/test_data_directory.py tests/test_data_pull.py tests/test_data_store.py`：67 passed。主线整合后统一生成 scope 与运行完整门禁。

接口：`Directory(store).refresh(client)` 是唯一联网动作；`latest()` / `get(id)` / `list()` / `compare(old_id,new_id)` 读历史。快照包含 `snapshot_id/source/fetched_at/content_sha256/companies/count/coverage/exclusions/gaps`，公司项含 `ticker/display_name/market/exchange/list_status/metadata`。覆盖键 SSE/SZSE/BSE，状态 confirmed/unknown；缺口为字符串列表。

命令：`python -m scripts.datalayer directory refresh|list|show|diff --store 沙箱`；show 可指定 `--snapshot-id`，diff 必须指定 `--old` / `--new`。refresh 与公司数据采集分开，查询命令不实例化数据源客户端。

## 实跑记录

环境：macOS / 项目 `.venv` Python / Tushare stock_basic；网络权限经自动审批批准。工作树没有 `.env`，故显式从主仓读取 token；不是临时环境补丁。可复制命令（在实现工作树运行）：

```sh
/Users/everett.li/Project/Turtle_investment_framework/.venv/bin/python - <<'PY'
import json
from scripts.datalayer.security import resolve_token
from scripts.datalayer.store import DataStore
from scripts.datalayer.directory import Directory
from tushare_collector import TushareClient
store = DataStore('output/.live_req016_directory')
token = resolve_token(dotenv_path='/Users/everett.li/Project/Turtle_investment_framework/.env')
snapshot = Directory(store).refresh(TushareClient(token, store=store))
print(json.dumps({key: snapshot[key] for key in ('snapshot_id', 'fetched_at', 'count', 'coverage', 'gaps')}, ensure_ascii=False))
PY
```

实际退出码 0，输出 `count=5573`，`coverage={SSE: confirmed/2320, SZSE: confirmed/2904, BSE: confirmed/349}`，`gaps=[]`。目录刷新仅发出三个 stock_basic 请求，没有创建公司数据采集批次。完整快照位于工作树隔离沙箱 `output/.live_req016_directory/store.db`；外部请求原始记录写同一仓，全市场批次实跑由主线另行完成。

## T4 全市场批次实跑耗时问题与保守整期预取

2026-10-12，owner 已确认 44584 个目标的真实全市场沙箱批次。实跑中逐公司串行路径在数分钟内仅完成百余目标；按实际节奏整体耗时过长。保留原目标、接口、期次和报告类型，增加 `market_bulk.prefetch` 正式模块：

- `stock_basic` 从已获取的同数据源目录原始记录按 ticker 分区，不追加请求；目标所需字段齐全、行可按全部语义筛选才保存。
- 利润表、资产负债表、现金流量表、财务指标仅对实际 VIP 客户端使用原有 `_invoke` VIP 路由，按整期+报告类型分页取数；权限不足或非 VIP 配置保留逐目标路径。
- 每页保留全部行，连续分页到空末页，不以短页推断完整；跨页重复行、分页上限、字段缺失、限流/错误、暂停、请求预算不足均回退。整期中没有某公司/报告类型不等于合法空响应，不能写 `empty`。
- 入仓保留原目标 `dataset/period/params`，只写目标集合中且未成功的公司；不会自动创建新目标或改变批次成功状态。接口返回远程实际请求计数与预取目标数，主线把这些调用加入原批次请求统计后由既有 PullBatch 续跑命中存档。
- 后续补充日线整日预取：按原窗口每个日历日调用官方 daily 的 trade_date 参数并完整分页，空日必须源接口实际返回；整个窗口完成后一次 groupby(ts_code) 分区，避免每公司扫描全市场全年帧。未完整覆盖窗口、日期不匹配、缺字段、重复 offset、暂停、预算不足均整组不写仓。始终没有返回行的公司继续逐目标确认，不写 empty。需求 AC、门禁、预算上限均未改变。

官方说明：[利润表](https://tushare.pro/document/2?doc_id=33)、[财务指标](https://tushare.pro/document/2?doc_id=79)；整期接口权限另有要求，不以配置推断实际权限；权限由数据源返回确认。

本次实现只运行 mock；真实批次暂停与接线由主线处理。`prefetch(store, targets, access, completed=..., should_pause=..., max_requests=...)` 不生成计划、不负责用户确认。


T4 补充：`whole_market=True` 是已确认全市场批次的正式执行选项；普通官方客户端 `_vip_mode=False` 仅表示未配置 broker，不能推断账号没有 5000 积分。金融白名单 4 项可在此选项下通过唯一网络门面 `_invoke` 显式访问 `income_vip/balancesheet_vip/cashflow_vip/fina_indicator_vip`；权限不足由真实响应确认并回退，绝不修改客户端配置。报告包含 `source_apis` 与失败的 `official_alias` 便于追溯，入仓仍用原基本数据集和原目标参数。

`stock_basic_reused` 单独记录目录原始数据分区复用数量，分区继承源 `fetched_at/token_fingerprint/batch_id/tier_label/quota_profile`，不会把旧目录转换为本批新增。金融与日线真实预取记录使用正在执行的批次标识；主线根据批次来源区分本批预取成功与旧数据复用。

官方整日接口：[A股日线 daily](https://tushare.pro/document/2?doc_id=27)。实现阶段仍只 mock，未额外调用真实数据源。

T4 分页边界补充（已修）：数据源可能把请求 limit=6000 截到实际上限5000。若用 `page * requested_limit` 推导 offset，会跳过中间行而无法靠末页判断发现。预取现在按每页实际返回行数递增 offset，仍必须取得空末页，并保留重复页/行、最大页数、预算护栏。既有组合测试以请求6000、实际返回2行再1行模拟不同源上限，断言请求 offset=0,2,3，并确认中间公司和全部行均入仓；没有增加测试用例预算。

## T8 / E3 完整整日来源检查点（2026-10-13，已修）

真实全市场预取原先把完整日响应保存在内存，暂停或窗口后半段出错时，恢复会重新请求先前已成功的日期，浪费已确认请求预算。现在每个交易日期对应的整日响应在完整分页到空、字段和日期验证通过后立即保存到原始仓：`ticker='' / dataset='daily' / period='latest'`，参数为原 `trade_date/fields`（不含分页 limit/offset）。金融整期同样保存全局来源，使用原报告期列防止期次键冲突。

来源检查点以原始仓 meta 内容摘要标识“已完整结束分页”，不同于未完整页或单次普通接口响应。恢复只 exact find 同请求、同 token 指纹、同批次且字段完整的检查点；未完整分页、失败、不同 token 或不兼容字段不复用。来源记录获取时间和批次标识保持不变。`checkpoint_reused` 统计恢复使用的完整源响应数量，可写入批次预取报告。

全局 empty 只表示该日期/该报告期接口真实完整返回空；不能直接把所有公司写为合法空。公司日线仍须原窗口所有日期完整取得后才分区保存 ok；窗口内始终没有行的公司继续逐目标确认。既有组合用例新增后半日失败→恢复的流程：第一日源检查点保留，恢复只请求第二日，再次恢复零源请求，source fetched_at 不变，不产生任何公司假 empty；更换 token 不命中检查点。没有增加测试条数。
