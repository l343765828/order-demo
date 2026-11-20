# 05 退款政策发布与检索验收

2026-11-20，Python 3.11.7，pymilvus 2.6.17、httpx 0.28.1、MCP 2.3.0、langchain-core 1.8.0、pytest 9.2.0。当时按工作机配置通过环境变量提供 Milvus 地址与令牌；凭据不记录。后续已改为 JSON 配置，见文末复测记录。真实 BGE-M3 与 reranker 分别返回 HTTP 200，Milvus 可连接。验收使用 `order_demo_refund_policy` 专用集合；未操作其他集合。

从仓库根目录在安装 `requirements-protocol.txt` 的 Python 环境运行，当前须先按 README 准备 JSON 配置；旧记录中的命令如下：

```powershell
python order-demo/publish_policy.py
python order-demo/publish_policy.py
python order-demo/test/verify_policy_store.py
python order-demo/test/verify_policy_mcp.py
python order-demo/test/verify_policy_faults.py
python -m pytest order-demo/test -q --tb=short
python -m mypy --ignore-missing-imports --check-untyped-defs order-demo/model/policy.py order-demo/mcp_service/policy.py order-demo/mcp_service/policy_backend.py order-demo/mcp_service/server.py order-demo/publish_policy.py order-demo/utils/mcp_client.py order-demo/utils/policy_tool.py
python -m compileall -q order-demo
python -m pip check
```

历史 Milvus 不可用测试使用单独进程并通过环境变量指定不可达地址，运行 `python order-demo/test/verify_policy_mcp.py --expect-failure`，收到 `failed` 和空候选，事件 `a39c193b-c9b1-4d58-b230-147ccfc9b8e8`。

- 集合实查 8 字段、非自动主键、1024 维及 FLAT/IP。两次发布后当前版本仍为 14 policy、7 faq；21 条来源和原文与源文件一致，文档向量有限、非零、归一化模长误差小于 0.0001。查询向量同样验证。现有集合 schema 或索引不符时发布拒绝覆盖。
- 通过发布后端写入仅有问答文本、没有出处的 `0.0-probe` FAQ 历史样本，标识 `d129fccce8a382e35ea7b9ab431449580af487a13f076463a82a11e9fd7edd6d`；样本保留，重复发布当前版本后历史条目仍可读。当前版召回未出现历史记录。验证脚本每次复用该条标识。
- 独立 MCP 服务未认证请求返回 401。真实查询经过 Host 固定模式的模型工具、MCP、BGE、Milvus 和重排。完整同一事件的召回原文、顺序和重排原文、顺序见 [policy-mcp-events.jsonl](policy-mcp-events.jsonl)：咨询事件 `9710cb92-df05-4b56-9149-cce60a3ef726` 召回 8 条、返回 FAQ-001、RF-003、RF-004；正式事件 `1856ba36-bf77-4696-bdcb-72ced1b89969` 召回 8 条且均为 policy，返回 RF-003、RF-004、RF-001。输入模型不能提供或覆盖 `mode`；正式模式由 Host 绑定。
- 未覆盖问题“会员积分如何兑换航空里程”仍返回政策相似候选（事件 `101741db-0cf4-4a64-be5a-b46983199efe`），检索成功不代表证据充分；06/07 须完成证据判断，不能把候选当作政策回答或办理依据。
- 经独立 MCP 进程及故障代理：零向量失败、重排 HTTP 503、重排 30 秒超时分别返回 `failed` 与空候选；零向量时重排计数 0，其余重排计数各 1。对应事件 `98f5d81b-9fe0-4dd7-a972-8f64bf6ff62c`、`3127ee2a-0142-43de-b440-db782f66a916`、`a04bd3c2-1eb3-46c8-9af7-3b6a9d1da8a4`。自动测试还覆盖 NaN、维度错误、非法范围和失败结果夹带候选。
- 本目录 pytest 32 项通过，7 个应用文件 mypy、compileall、pip check 通过。全仓库 pytest 收集时有 10 项错误：同名测试模块冲突、`agent_demo` 和 `config` 模块串用以及缺少 `langgraph.checkpoint.sqlite`，未执行全仓库测试；不将其归因于本票。03 的现有跨进程协议测试随本目录测试通过；其真实 Qwen 和故障验收记录见 `protocol-03.md`。

独立代码审阅提出的来源版本校验、失败结果约束、事件关联和 Host 固定范围问题已处理。正式咨询回答与政策依据充分性不在本票范围。

## 2026-11-20 配置文件复测

按用户要求，政策 Milvus 地址、令牌和 BGE / 重排服务地址改为从 JSON 的 `policy` 配置读取；`publish_policy.py` 与政策验证脚本支持 `--config <路径>`。旧配置没有 `policy` 时，查单配置仍可加载，政策服务不会回退读取环境变量。示例文件不包含实际凭据。

使用临时 JSON 配置和真实服务，依次执行 `python order-demo/publish_policy.py --config <临时文件>`、`python order-demo/test/verify_policy_store.py --config <临时文件>`、`python order-demo/test/verify_policy_mcp.py --config <临时文件>`、同命令加 `--expect-failure` 以及 `python order-demo/test/verify_policy_faults.py --config <临时文件>`，退出码均为 0；临时配置已清理。覆盖重复发布、实际 schema 与行数、真实 MCP 搜索、Milvus 不可达及零向量 / 重排失败 / 超时。`order-demo/test` 36 项通过；配置、政策后端、发布命令的 mypy 和 `compileall` 通过。全仓库 pytest 仍在收集时报 10 项模块冲突或缺依赖错误。

## 2026-11-20 政策存储实体改造

在 `order-demo/model/policy.py` 增加政策条目和含向量的存储记录模型。解析、发布、召回和重排链路改用具名模型，Milvus 读写边界转换，MCP 候选字段保持不变。隔离测试向真实 Milvus 写入一条随机标识记录，按完整八字段模型读回并清理该条；独立 MCP 进程经真实 BGE、Milvus 和重排服务查询成功。未将临时凭据写入仓库。

`python -m pytest order-demo/test -q --tb=short`：41 项通过。相关应用文件 mypy、compileall、pip check 通过。全仓库 pytest 收集时报 11 项错误：同名测试模块及 `agent_demo` / `config` 模块冲突、缺少 `langgraph.checkpoint.sqlite`；其中政策测试也受到 `config` 模块冲突影响，全仓库测试未执行。

审阅后补充验证：条目主键必须与文档编号、版本、条目编号的 SHA-256 一致；Milvus 投影从存储条目模型派生。使用符合该规则的隔离记录重新完成真实写入、读取、清理及独立 MCP 检索。
