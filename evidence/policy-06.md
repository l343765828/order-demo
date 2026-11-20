# 06 政策和订单资格咨询验收

2026-11-20，Windows，Python 3.11.7。目标 MySQL 的 `SELECT VERSION()` 返回 **8.0.46**。使用 PyMySQL 1.2.4、同步 PyMySQLSaver 3.0.0、LangGraph 1.2.22、DeepAgent 0.7.38、LangChain OpenAI 1.8.0、MCP 2.3.0、pymilvus 2.6.17。CLI 使用真实 Qwen3-8B，独立 MCP 进程连接真实 MySQL、BGE-M3、Milvus 和 BGE reranker。验收配置使用系统临时文件，验收后删除。

从工作区根目录复验：

```powershell
.venv/Scripts/python.exe order-demo/test/verify_policy_cli.py --config <JSON配置路径> --evidence order-demo/evidence/policy-06-final-events.jsonl
.venv/Scripts/python.exe -m pytest order-demo/test -q --tb=short
.venv/Scripts/python.exe -m mypy --ignore-missing-imports --check-untyped-defs order-demo/model/order_flow.py order-demo/model/policy_evidence.py order-demo/order_agent.py order-demo/order_flow.py order-demo/policy_answer.py order-demo/cli.py
.venv/Scripts/python.exe -m compileall -q order-demo
.venv/Scripts/python.exe -m pip check
```

- 首轮真实 CLI 检索到了 FAQ-004 和适用正式条款，回复却提示依据不足。原因是 Host 要求模型准确重填全部已知出处。现由模型选择相关候选，Host 从当前发布源读取 FAQ 声明，从本轮实际检索结果逐一核对正式出处，缺少条款时最多补查一次。
- 完整真实 CLI 验收退出码为 0，覆盖 16 轮、15 个隔离会话，实际观察到 7 次订单读取和 15 次政策搜索。逐轮关联的工具、MCP 请求、候选与原文、选择和最终回复见 [JSONL](policy-06-final-events.jsonl)。
- 「退款多久能到账？」首次咨询事件 `6621cb58-27c9-4361-9101-a90bbdd34337` 的首位为 FAQ-004，正式补查事件 `d8b725a1-8ed7-4f2e-ae77-734b019f7c70` 返回缺失的 RF-001。原问与同义问法都采用同一 FAQ 原文，引用《演示退款政策与常见问题》RF-001、RF-008。积分换里程问题按依据不足回复。故障代理强制将不相关但附有正式出处的 FAQ-002 排到首位，事件 `f69cec9b-5a1a-447d-9066-f4f6ae232e3d`，回复仍是当前政策文档未说明。
- DEMO-1001 已支付、未发货且未退款，引用 RF-003；1002 未支付、1004 已发货，均引用 RF-004；2001 只显示未找到可操作的订单。「能退 DEMO-1001 吗」走资格咨询，条件式请求识别为办理，「这单退款？」走澄清。只问「能退吗」时追问订单号，同会话补上 1001 后才进行订单读取和政策检索。
- 隔离代理分别返回重排 HTTP 503 和超过 30 秒的响应，政策事件 `a1d1a141-b6b2-4a5d-8ec0-3131b719bb2d` 和 `41c7af9c-9e73-4a54-b4c7-482ae734d98e` 均为失败且无候选，CLI 提示暂时无法核实。超时后，代理尝试写迟到响应时记录了 Windows 连接已中止的 `WinError 10053`，该故障用例及整组验收通过。保持故障配置时纯查单仍返回实付 19900 分。
- 退款写调用为 0，退款表与提交表记录数一致，验收 checkpoint 中没有退款授权或办理待办。15 个隔离会话及 checkpoint 已清理。
- 应用测试 50 项通过，包含检索事件、过期版本、未知条款、伪造摘录、FAQ 缺少正式支持及条款与未支付事实冲突。六个应用 Python 文件的 mypy、compileall 和 pip check 通过。全仓 pytest 收集阶段报 12 项错误，原因是其他演示的同名测试与配置模块冲突，以及缺少 SQLite checkpoint 依赖。
