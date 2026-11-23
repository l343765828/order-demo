# 15 移除 Host 的政策文件依赖

验收日期为 2026-11-23。目标 MySQL 连接后首先执行 `SELECT VERSION()`，返回 **8.0.46**，随后确认 `order_demo.orders` 有 5 条种子。Milvus SDK 返回服务端版本 **3.0.2**，演示政策专用集合可读。使用 Python 3.11、PyMySQL 1.2.4、pymilvus 2.6.17、MCP 2.3.0、DeepAgent 0.7.38 和 LangGraph 1.2.22。正常检索连接真实 Qwen3-8B、BGE-M3、Milvus 与 BGE reranker。

## 复跑命令

从仓库根目录执行。将变量 `$issue15Config` 指向经授权的本地 JSON，测试会另建 MCP 服务配置、复制一份不含政策 Markdown 的 Host 运行目录，并用真实服务创建隔离会话。

```powershell
.venv/Scripts/python.exe test/verify_policy_mcp.py --config $issue15Config
.venv/Scripts/python.exe test/verify_policy_cli.py --config $issue15Config --smoke --evidence evidence/policy-15-smoke.jsonl
.venv/Scripts/python.exe test/verify_policy_cli.py --config $issue15Config --ticket15-extra --evidence evidence/policy-15-ranking.jsonl
.venv/Scripts/python.exe test/verify_policy_cli.py --config $issue15Config --faults-only --evidence evidence/policy-15-faults.jsonl
.venv/Scripts/python.exe -m pytest -q --tb=short
.venv/Scripts/python.exe -m mypy --ignore-missing-imports --check-untyped-defs config/demo.py model/policy/search.py model/policy/evidence.py model/refund/flow.py implementations/policy/answer.py implementations/policy/evidence.py implementations/policy/source.py implementations/refund/investigation.py mcp_service/policy/search.py mcp_service/policy/backend.py mcp_service/server.py workflows/refund_nodes.py
.venv/Scripts/python.exe -m compileall -q config implementations interfaces model mcp_service workflows test
.venv/Scripts/python.exe -m pip check
git diff --check
```

## 依赖和基线

- 工单 05 的版本预过滤、合并召回及统一重排实现存在。独立 MCP 复验成功，未认证请求返回 401。按 `consultation` 查询时同一结果包含 FAQ 与正式条款，按 `formal` 查询时召回和重排结果均为正式条款。三个查询均返回目录修订号 `1`。历史验收详见 [工单 05 证据](policy-05.md)。
- 工单 06 的 PolicyAgent、资格规则和只读调查流程存在。改动前全应用测试 57 项通过。真实冒烟轮次复验咨询和本人资格两个入口。历史验收详见 [工单 06 证据](policy-06.md)。

## 本轮观察

- 无政策 Markdown 的 Host 经独立 MCP 读取政策。「退款多久能到账？」展示 FAQ-004 的答案并引用《演示退款政策与常见问题》1.0 版 FAQ-004。「DEMO-1001 能退吗？」先查本人订单，按已支付、未发货且尚未退款的状态计算资格，引用 1.0 版 RF-003。[正常业务回执](policy-15-smoke.jsonl)
- 在现有集合写入两条随机主键的隔离记录，一条是 1.0 版只有问答的 FAQ，另一条是同编号的历史版。用重排代理调整真实候选的次序。问答 FAQ 排在正式条款之后的第二位时，首轮直接展示该 FAQ 的答案及出处。FAQ-003 排首位但正文没有回答已发货能否退款时，采用第二位 RF-004 原文。两例均只有一次检索，历史版未进入候选或答复。[排序与补查回执](policy-15-ranking.jsonl)
- 早期注入试验中，Qwen 对「退款金额是否可以自行指定？」错误选择了只说明撤销的 FAQ-006，事件编号为 `b8dda3f6-0ec5-41ea-bad5-ceb7708b75f5` 和 `da629337-1a35-47c9-86fc-4f83a30e390d`。对照实际模型请求后，将选答输入精简为问题与全部候选的类型和原文，首先要求判断材料是否充足。真实 Qwen 直接复验在同组三条不适用候选下返回 `insufficient`，加入适用的 RF-005 后选中 RF-005。
- 同样的三条不适用候选经完整 CLI 复测后，Host 用消费者问题在目录修订 1 内正式条款语义补查一次，取得并展示 RF-005 原文。积分换里程的补查仍无适用条款，CLI 提示当前政策文档未说明。[排序与补查回执](policy-15-ranking.jsonl)
- 按实际回执核对事件、文档、版本、条目、类型、标题和原文。逐项变更事件、文档、版本、编号、类型或原文后，引用核验拒绝采信。将已通过普通咨询核验的 FAQ 改作资格依据时也被拒绝。[正常业务回执](policy-15-smoke.jsonl) · [排序与补查回执](policy-15-ranking.jsonl)
- 空回执探针只为首轮咨询的实际 Milvus 查询追加无匹配主键条件。独立 MCP 返回成功、零候选和目录修订 1，传回修订 1 的正式补查得到三条当前版条款，修订 2 返回检索失败。[空回执与修订请求](policy-15-ranking.jsonl)
- 重排代理返回 HTTP 503 或超过客户端时限时，政策工具返回 `failed`、空候选和空目录修订号。CLI 提示暂时无法核实政策。保持故障配置，纯查单仍返回 DEMO-1001 实付 19900 分。超时后故障代理尝试写迟到响应时记录 Windows `WinError 10053`，两条故障用例均达到预期。[故障回执](policy-15-faults.jsonl)

全部验收轮次的退款写工具调用为 0，退款和提交记录数在验收前后相同。隔离会话、checkpoint 和两条隔离政策记录均已清理。

## 本地检查

全仓 pytest 63 项通过。12 个相关应用模块通过 mypy，`compileall`、`pip check` 和 `git diff --check` 通过。单独导入政策后端及演示配置时，未加载退款用例或工作流。
