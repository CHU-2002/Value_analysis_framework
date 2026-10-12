# REQ-016 目录与批次实现任务

- T1（完成）：全市场目录独立于自选股保存，采用 additive `directory_snapshot` 表（`CREATE TABLE IF NOT EXISTS`）；现有 schema 版本不变，旧数据库首次访问自动增加表，旧记录与清单不变。每次刷新有独立标识和内容 SHA256，逐交易所记覆盖与排除，旧快照永不覆盖。计划、批次固定引用由采集编排层保存。
- T2（完成）：北京市场后缀 `BJ` 加入自选股和 A 股接口过滤；若显式计划交易日历，按沪/深/北选择 SSE/SZSE/BSE。扫描调用点的默认 SSE 入参不得覆盖此市场选择；显式用户入参仍可指定交易所。
- T3（完成）：真实规模预检发现原 `append_batch` 每个目标重复序列化全 targets 并删除重写所有 completed，累计写量 O(n²)。新增 `changed_keys` 增量协议：首次保存固定目标，每个完成目标在一个 SQLite 事务内写入，运行中的轻量更新不重写兼容 JSON，暂停、失败、终态保存完整 JSON。默认全量调用仍兼容旧调用者，恢复优先读 SQLite。
- T4（环境记录）：2026-10-12 沙箱 `output/.live_req016_directory` 第一次真实目录刷新因受限网络 DNS 不能解析 `api.waditu.com`；三交易所各重试 5 次后返回覆盖未知，目录数量 0，快照与缺口保留且不能确认全市场采集。随后通过自动审批取得网络访问，重复刷新成功：快照 `a107a05c417d4762bb7145d74260f442`，5573 家（SSE 2320 / SZSE 2904 / BSE 349），三交易所覆盖 confirmed，缺口列表为空，退出码 0。内容摘要 `bbc8736398aaf763b49f7981d5880a04c3c79a23b304714d66d4546c55006d74`，获取时间 `2026-10-12T15:17:19.023752+00:00`。token 仅从主仓 `.env` 读取，未复制到工作树、日志或产物；真实 HOME 存档未写入。

验证：`/Users/everett.li/Project/Turtle_investment_framework/.venv/bin/python -m pytest -q tests/test_data_directory.py tests/test_data_pull.py tests/test_data_store.py`：67 passed。主线整合后统一生成 scope 与运行完整门禁。

接口：`Directory(store).refresh(client)` 是唯一联网动作；`latest()` / `get(id)` / `list()` / `compare(old_id,new_id)` 读历史。快照包含 `snapshot_id/source/fetched_at/content_sha256/companies/count/coverage/exclusions/gaps`，公司项含 `ticker/display_name/market/exchange/list_status/metadata`。覆盖键 SSE/SZSE/BSE，状态 confirmed/unknown；缺口为字符串列表。

命令：`python -m scripts.datalayer directory refresh|list|show|diff --store 沙箱`；show 可指定 `--snapshot-id`，diff 必须指定 `--old` / `--new`。refresh 与公司数据采集分开，查询命令不实例化数据源客户端。
