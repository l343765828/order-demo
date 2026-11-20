# 04 本人订单查询验收

2026-11-20 配置与目录调整：当前启动方式和配置格式见 `order-demo/README.md`。MCP 服务入口已移至 `mcp_service.server`，配置集中于 `config` 目录。以下为 04 当时的历史验收记录。

从工作区根目录安装 `python -m pip install -r order-demo/requirements-protocol.txt`。通过环境变量注入 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`。首次搭建使用 `python order-demo/manage.py init` 和 `python order-demo/manage.py seed` 初始化专用库，随后运行 `python order-demo/cli.py --init-checkpoints` 初始化框架表。这些命令可显式重复执行。

将相同的随机读取凭据通过 `ORDER_MCP_READ_TOKEN` 注入两个进程，长度至少 32 个 ASCII 字符。启动独立服务 `python order-demo/order_mcp.py`。本地默认监听 `127.0.0.1:8765`，可用 `ORDER_MCP_PORT` 指定服务端口，并用 `ORDER_MCP_URL` 调整 Host 的目标地址。Host 调用已部署的 Qwen3-8B，`QWEN_BASE_URL` 可覆盖默认配置，需要认证时使用 `QWEN_API_KEY`。

```powershell
python order-demo/cli.py
python order-demo/cli.py --session <会话ID> --history
python order-demo/cli.py --session <会话ID> --message "查一下 DEMO-1001"
```

在交互提示符输入“查一下 DEMO-1001”。标准输出显示会话 ID 和订单结果，标准错误输出可关联的 JSON 事件。每轮只执行一个查单计划步骤，最多发送一次 `get_order`。超过本票输入预算时保留原文，并提示开启新会话。会话压缩由工单 12 处理。

复跑真实验收使用 `python order-demo/test/verify_order_cli.py`。脚本使用 01 的种子订单、随机端口、临时凭据和两个隔离会话。脚本结束时只清理自己的消息、会话和 checkpoint。

2026-11-19，Windows，Python 3.11.7。连接目标 MySQL 后执行 `SELECT VERSION()` 得到 **8.0.46**，并确认五笔种子存在。使用 PyMySQL 1.2.4、同步 PyMySQLSaver 3.0.0、LangGraph 1.2.22、langgraph-checkpoint 4.3.2、DeepAgent 0.7.38、LangChain OpenAI 1.8.0、MCP 2.3.0。

- 真实 CLI Host PID `113812`，独立 MCP Server PID `124684`，重新打开的 Host PID `113312`。会话 `4149b798-74ac-4fc0-8c2c-293295440846`，本轮 trace `8ca5dbd0-79f9-453e-8d4b-c3c5e4c4b332`。
- LangGraph 节点按 `intent → plan → execute → observe → replan` 执行。实际计划只有一步 `inspect_order(DEMO-1001)`，依赖为空。DeepAgent 委派 `chatcmpl-tool-32b0d33fa9304c458930725ab4c0420c` 到 OrderAgent。ReAct 调用和回传观察共用模型调用 ID `chatcmpl-tool-c65753f9225c4b938cd090afab8cca9c`。同一工具事件 `ec810dfd-44f0-488c-9b50-58e8bfb1bd15` 关联服务端 MCP 请求 `3` 与外层观察。Replan 返回 `finish`。
- CLI 显示「订单 DEMO-1001：已支付，未发货，无模拟退款记录，实付 19900 分。」查 `DEMO-2001` 和 `DEMO-9999` 均显示「未找到可操作的订单。」关闭 MCP 进程后显示「订单服务暂时不可用，请稍后再试。」
- 跨进程验收中，普通客户端名与 thread ID 无法通过认证（HTTP 401）。三种非法工具入参均拒绝，未执行查单。认证后列出的工具只有 `get_order`。带 thread ID 的认证请求不能读取他人订单。
- 在新 Host 进程中成功读取同会话的两条原文及前轮 checkpoint，随后继续发送查询。全组结束后 MySQL 保存了八条有序原文，角色按 `user`／`assistant` 交替。另一会话看不到原记录，测试会话归属切到另一消费者后，使用原会话 ID 读取历史或发送消息均退出码 2。服务端四次业务读调用，退款写调用 **0**，`refunds` 和 `refund_dispatches` 的记录数与开始时一致。测试会话及 checkpoint 已清理。
- 固定模型负例验证了监督层只收到 `task`，专用 OrderAgent 只收到 `get_order`。默认子 Agent 和 Shell 调用被拒绝。非法依赖、错单、额外消费者参数、模型伪造成功及并行超预算均不产生额外业务调用。

`python -m pytest order-demo/test -q` 为 **23 passed**。变更涉及的十个 Python 文件通过 `mypy --ignore-missing-imports --check-untyped-defs`，`python -m compileall -q order-demo` 和 `python -m pip check` 通过。全仓 `python -m pytest -q` 收集阶段仍有七项既有错误，涉及其他演示的同名测试模块和缺少 `langgraph.checkpoint.sqlite`。
