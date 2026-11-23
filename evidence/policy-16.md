# 16 建立政策发布目录并支持多文档版本切换

验收日期为 2026-11-23。目标 MySQL 在建隔离库前执行 `SELECT VERSION()`，返回 **8.0.46**。Milvus 服务端返回 **3.0.2**。使用 Python 3.11.7、PyMySQL 1.2.4、pymilvus 2.6.17、MCP 2.3.0、DeepAgent 0.7.38 和 LangGraph 1.2.22。验证使用真实 Qwen3-8B、BGE-M3、Milvus、BGE reranker 和独立的 Streamable HTTP MCP 进程。Host 查询本人订单时使用现有 `DEMO-1001` 种子数据，测试会话使用独立标识。

## 复跑命令

`$issue16Config` 指向本地验收 JSON。该文件提供已授权的后端连接。验收脚本自动派生随机命名的政策数据库和 Milvus 集合，以及用于本地独立 MCP 进程的读取凭据。

```powershell
.venv/Scripts/python.exe -X utf8 test/verify_policy_catalog.py --config $issue16Config --evidence evidence/policy-16-integration.jsonl
.venv/Scripts/python.exe -m pytest -q --tb=short
.venv/Scripts/python.exe -m mypy --ignore-missing-imports --check-untyped-defs config implementations/policy interfaces/policy model/policy mcp_service/policy mcp_service/server.py publish_policy.py utils/mysql.py utils/checkpoint.py model/__init__.py
.venv/Scripts/python.exe -m compileall -q config implementations interfaces model mcp_service workflows test utils
.venv/Scripts/python.exe -m pip check
git diff --check
git diff --cached --check
```

实测发布入口为 `publish_policy.py --config <隔离配置> --source <原件> --version <版本> --publisher issue16-verification --qualification unchanged`。改变资格的探针使用 `--qualification changed`，并再次用同一原件尝试 `unchanged`。

## 前置条件和基线

- 工单 15 的 Host 已从本轮 MCP 回执取得修订号、补查并核验引用。接入本票前，全仓测试 63 项通过。生产基线集合的 `DEMO-REFUND-POLICY` 1.0 强一致回读得 21 条，字段和原文与仓库权威源一致，真实召回和重排通过。[工单 15 的验收记录](policy-15.md)
- 使用实际基线集合做只读迁移演练，目录建在随机 MySQL 隔离库。同一原件错误标记为改变资格后的直接重分类被拒绝。将基线原件副本的日期标头修正后，条目集合仍与已有索引完全一致，登记为修订 1，Milvus 删除及重写调用均为零。[基线登记回执](policy-16-baseline.jsonl)

## 验收观察

- 初次发布产生修订 1 和 2。演示政策有 21 条，补充政策有 3 条。两份来源中的 `RF-003` 同时进入真实 MCP 重排结果，同正文仍保留两个文档身份。Host 普通咨询展示独立 FAQ 原文，资格答复按真实订单状态解释并引用演示政策 1.0 的 `RF-003`。
- 同一 Host 调查的首次成功回执携带修订 2。探针先用真实 Milvus 查询返回空候选，再在 Host 发出的正式条款请求进入目录读取前暂停。此时发布补充政策 2.0，生成修订 3。恢复后的正式补查仍读取修订 2，答复引用旧版 `RF-015`。新调查读取修订 3，删除的 `RF-015` 退出检索，演示政策仍为 1.0。不存在的修订 99 返回 `failed` 和空候选。
- 部分写入实际落入 2 条后注入异常，旧修订 3 仍检索到补充政策 2.0。未确认前次发布结束时，重试拒绝清理。确认结束后使用修正原件全量重发同版本，原件删除的条目没有残留，切换为修订 4。全量写入后的回读故障发生时，旧修订 4 仍检索到补充政策 3.0。
- 注入 MySQL 提交已成功、调用方未收到回执的情况。重跑命令的发布逻辑核对到修订 5，已生效版本不再清理或重写，历史版本仍可回查。持有同版本锁时第二次调用被拒绝。两个不同版本竞争切换同一文档，只有一个激活为修订 6，另一项报告同文档并发冲突。
- 同一个 Host 进程在更新前后引用补充政策的 `FAQ-001`，标题、版本及条目编号格式一致。MCP 工具事件与 Host 回执能按检索事件 ID 对账。声明改变资格的 7.0 保存发布者和确认，保持未生效。同一原件改传 `unchanged` 仍被拒绝。 [完整联验回执](policy-16-integration.jsonl)

早期实测发现 MySQL 会把显式修订整合进 `COALESCE` 时转换为 `Decimal`。改为按无符号整数读取，修订类型回归通过。Windows MCP 联验中，长时间空闲的连接关闭时曾记录 `WinError 10054`，后续请求及上述断言均成功。

全仓 pytest 68 项通过，26 个源文件通过 mypy。编译、依赖、独立导入和 Git 差异检查通过。联验创建的数据库、集合、会话及 checkpoint 已清理。真实基础设施的验证范围是本机 Host、独立 MCP 进程及后端隔离数据。
