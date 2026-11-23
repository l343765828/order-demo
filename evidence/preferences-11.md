# 11 在聊天中查看、更正和删除显式长期偏好

验收日期为 2026-11-23。连接目标 MySQL 后首先执行 `SELECT VERSION()`，实测 **8.0.46**。开工前只读核对了 `order_demo` 中 `DEMO-1001` 的实付 19900 分和 `consumer_preferences` 表，并复核[工单 04 的真实查单验收](order-cli.md)。本票联验使用随机命名的 MySQL 隔离库、真实 Qwen3-8B 和本机独立 Streamable HTTP MCP 进程。验收配置保存在工作区外的临时文件中。

## 复跑命令

`$issue11Config` 指向现有的本地验收 JSON，提供已授权的 MySQL 和 Qwen 连接。脚本生成临时 MCP 读取凭据，初始化、装载和删除自己创建的隔离库。

```powershell
.venv/Scripts/python.exe -X utf8 test/verify_preferences_cli.py --config $issue11Config --evidence evidence/preferences-11-integration.json
.venv/Scripts/python.exe -X utf8 -m pytest -q --tb=short
.venv/Scripts/python.exe -X utf8 -m mypy --ignore-missing-imports --check-untyped-defs config/demo.py config/limits.py model/refund/preference.py model/refund/flow.py implementations/refund/preferences.py implementations/refund/preference_intent.py implementations/refund/intent.py implementations/refund/decision.py interfaces/refund/preference.py workflows/refund.py workflows/refund_nodes.py implementations/refund/host.py implementations/orders/reply.py
.venv/Scripts/python.exe -m compileall -q config implementations interfaces model mcp_service workflows test utils
.venv/Scripts/python.exe -m pip check
git diff --check
git diff --cached --check
```

## 实施与验证边界

- 首版受控键为 `response_detail`，值为 `brief` 和 `detailed`，用于订单状态答复。详略影响排版，全部订单事实和金额沿用同一已核验回执。
- 意图节点从当前原文提取明确的偏好命令及当轮表达要求。原始消息照常归档，业务模型读取分离后的本轮业务文字。扩大意图模型 Schema 的早期探针收到 Qwen 422，部署服务拒绝未正常结束的 JSON。最终实现沿用原有业务意图 Schema，将偏好解析和持久化交给固定应用逻辑。
- 固定节点以 Host 的可信消费者身份查询并维护当前值。显式写入失败时独立报告偏好失败，混合查单仍可使用 MySQL 中可读取的当前值。新轮重新计算有效详略，历史和待执行 checkpoint 不能重放偏好写入。
- 故障仅注入隔离库。使用 `SIGNAL SQLSTATE '45000'` 的行触发器拒绝写入，使用临时重命名偏好表触发真实读取错误。真实工具路径使用查单 MCP，政策问题分离及调查工具权限由自动化测试覆盖。摘要后的读取边界由工单 12 验收。

## 验收结果

- 联验退出码为 0。19 次 CLI 调用跨三个会话，每轮由独立 Host 进程执行。另一会话读到并使用简短偏好，同键更正后只读到详细，删除后原会话和新会话都读到空值。MySQL 原文和 CLI `--history` 逐条一致，共 38 条。已等待执行的历史偏好节点在自动化测试中拒绝重放。
- 临时详细答复后下一轮继续使用已保存的简短值。普通详略陈述、他人会话访问和他人偏好请求都未修改偏好。其他消费者夹具始终保有自己的值。8 次跨进程查单结果一致，金额为 19900 分，订单、退款、退款提交记录在验证前后逐行一致，MCP 退款写入调用为 0。
- 两次保存或更正失败、一次删除失败和一次读取失败均有真实 `failed` 结果，CLI 对应显示无法确认变更或无法读取。混合查单写失败时继续使用 MySQL 中仍可读取的详细值。隐式读取失败时查单使用默认排版。
- 运行版本为 Python 3.11.7、PyMySQL 1.2.4、LangGraph 1.2.22、langgraph-checkpoint-mysql 3.0.0、DeepAgent 0.7.38、MCP 2.3.0、LangChain OpenAI 1.8.0。已按服务端日志 PID 核对跨进程调用，独立 MCP 进程和隔离库均已清理。[逐轮回执](preferences-11-integration.json)
- 全仓 pytest 98 项通过，13 个源文件通过 mypy。编译、依赖、适配器独立导入及工作区和暂存区差异检查通过。联验中 LangGraph 提示历史消息和来源类型尚未注册，当前版本仍成功读取全部 checkpoint。
