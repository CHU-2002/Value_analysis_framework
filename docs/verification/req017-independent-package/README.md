# REQ-017 紧凑独立证据包

本目录自包含，不依赖旧399文件的展开提交。旧数据和历史均未删除或改写；本新增提交只提供紧凑交付形式。真实模型实跑暂停。

- [阶段报告](status.md)：f9504ad离线门禁与尚未通过的AC。
- [缺陷摘要](findings-summary.md)：通用问题及复验边界。
- [调用量摘要](calls.md)：可辨成功响应、缓存命中及不能精确重建的部分。
- [冻结预标注](blind-annotations.md)：原始字节不变，SHA256仍为`9f9fecbf143d51e397385e9d6bc60c9fe20193d6499747b913441a4f74903049`；实现方在最终冻结前请勿读取具体公司/线索。
- [完整ZIP](evidence.zip)：全部已保存的历史响应、逐轮记录、原始日志、已完成官方样例、来源与预标注；不含当前运行中的sol61-medium单例或认证文件。
- [SHA256清单](SHA256SUMS)：外置文件与整个ZIP的摘要。

## ZIP内部定位

| 内容 | 逻辑路径 |
|---|---|
| 完整逐轮缺陷记录 | `verification/findings-full.md` |
| 全部AC阶段报告 | `verification/status-full.md` |
| 原始冻结预标注 | `verification/blind-annotations.md` |
| 盲样逐轮模型原始响应/审核/排除/状态/指纹 | `runs/blind/blind_{a,b}_run{1..5}/` |
| 已完成的最终官方样例及其完整链 | `runs/official/yili-r6/`、`runs/official/wuliangye-r7/` |
| 官方例历史各轮记录 | `runs/official/` |
| 原始错误和独立门禁日志 | `logs/` |
| 原件、来源URL/SHA和材料清单 | `sources/blind/`、`runs/official/examples/` |
| 逐文件摘要及物理存放路径 | `MANIFEST.json` |

1737个逻辑文件中，字节完全相同的副本只存一次（798份独立内容）。每个原逻辑路径都在`MANIFEST.json`保留`stored_as`、SHA256及字节数，可逐字节重建；若ZIP中没有该逻辑文件名，按`stored_as`定位。没有ZIP中再套重复ZIP，也没有修改原始模型答案。

在本目录运行`shasum -a 256 -c SHA256SUMS`可验证外部包；内部每一逻辑文件已按MANIFEST的SHA逐项读回验证。历史命令只记录已发生行为，不代表现在获准运行旧型号。阶段报告不是独立验收通过报告，不能用于推进verified。
