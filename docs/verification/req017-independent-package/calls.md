# 真实盲样调用与缓存清点（2026-10-17停止时）

用户最新限制：只允许gpt-6.1-sol、medium。评估者没有启动任何新模型；旧gpt-6-sol进程已不存在。run5两会话均返回1，日志末尾为repair连接超时；run4旧会话此前按协调指令中断返回130，其文件中遗留running不能视为当前有进程。只读进程检查仅匹配到检查命令本身，无financial_events或旧gpt-6-sol模型进程。

| 轮次 | A新成功响应 | B新成功响应 | donor缓存命中 | 观察 |
|---|---:|---:|---:|---|
| run2 | 10 | 4 | 0 | 旧版无统一响应缓存；分别为read/review留档，非法日期原响应当时未保存 |
| run3 | 9 | 6 | 0 | 全部读包及B的review可辨识；A review超时、B audit失败 |
| run4（含resume） | 41 | 45 | 0 | 严格响应缓存；A=9read/1review/21audit/10repair，B=5read/2review/25audit/13repair |
| run5 | 30 | 32 | 17 | 仅新audit/repair；复用14个read与3个review，审核上下文改变后重新推理 |

以上为**已保存成功响应**，不是全部实际请求或账单次数：严格缓存时代保存148个新成功响应，前两有效留档轮次另有29个，共至少177个。失败、中断及早期未保存的响应数量不能从现有日志精确重建；旧resume缓存命中未设计数器，17仅指能由origin字段证明的donor命中。不能据此编造完整调用量、token或费用。

频繁调用的可证原因：早期没有响应断点，失败重跑重复读包；每份完整年报分包通读，综合后有18/19项入选，逐项再核原文；日期/候选ID/连续引文、比较期和报表口径等失败产生修正与再次审核；新审核上下文改变后不能复用旧audit。后两轮的131个新成功响应属于audit/repair，17个属于首次read/review。缓存迁移确实免去了run5重读14个完整包，不能误称run5全部重跑。

旧模型settings未记录reasoning_effort，无法由留档证明旧调用是medium，不能追溯宣称符合用户的新限制。最新f9504ad才把显式medium和零provider自动重试纳入可核对设置；后续只能在明确通知后用指定型号逐份运行，旧sol响应不得冒充6.1响应。

两盲样所有轮次均未发布最终report_events.json，REQ-017尚未独立验收通过。冻结原件/预标注未改变。完整响应见evidence.zip内runs/blind/，逐轮清单在sources/blind/request_inventory.json；不含token或CLI认证日志，也未复制大PDF原文。
