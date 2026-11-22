# order-demo 的 MySQL 存储

## 项目文档

- [需求与总体设计](.scratch/refund-agent-all-tech-demo/spec.md)涵盖业务范围、流程、异常处理和验收标准。
- [施工总览](.scratch/refund-agent-all-tech-demo/task-overview.md)列出 14 份工单、施工顺序、依赖和各项验收目标。[模型接口实施计划](.scratch/refund-agent-all-tech-demo/interface-implementation-plan.md)记录 Qwen 原生工具与 Schema 接口的实施步骤。
- 专题设计包括[数据库](.scratch/refund-agent-all-tech-demo/database-design.md)、[存储与记忆](.scratch/refund-agent-all-tech-demo/storage-memory.md)、[上下文压缩](.scratch/refund-agent-all-tech-demo/context-compression.md)、[MCP 接入与部署](.scratch/refund-agent-all-tech-demo/mcp-integration.md)、[模型选型](.scratch/refund-agent-all-tech-demo/model-selection.md)、[接口契约](.scratch/refund-agent-all-tech-demo/integration-contracts.md)和[订单号提取](.scratch/refund-agent-all-tech-demo/order-id-extraction-research.md)。
- [领域术语](GLOSSARY.md)、[架构决策](docs/adr/)和[退款政策原文](policy/refund-policy.md)记录已确认的约定。[验收记录](evidence/)保存已执行的验证及结果。

目前代码实现了查单、政策检索和订单资格咨询。施工总览也包含后续模拟退款等任务的验收目标。历史记录中的旧项目路径和部署环境信息用于追溯，当前运行方式见下文。

## 环境配置与启动

以下命令在包含 `cli.py` 的 `order-demo` 目录执行。运行 `py -3 -m pip install -r requirements-protocol.txt` 安装依赖。在仓库自带的 `config/local.json` 中填写 MySQL、Qwen 连接信息和 MCP 读取凭据。MCP 凭据须为至少 32 个 ASCII 字符。程序启动时自动读取这份配置。两个服务分别管理连接池，数据库连接使用 UTC。

先初始化数据库，再装载演示种子：

```powershell
py -3 manage.py init
py -3 manage.py seed
```

两条命令都可以重复执行，并保留已有数据。初始化会创建缺失的数据库对象，已有表结构不兼容时需另行迁移。

运行 `py -3 test/verify_mysql.py` 进行隔离集成验证。脚本会创建并清理一个随机命名的测试库，保留 `order_demo` 中的现有数据。

首次运行前初始化 checkpoint 表，然后在独立终端启动 MCP 服务：

```powershell
py -3 cli.py --init-checkpoints
py -3 -m mcp_service.server
```

在同一目录打开另一个终端，运行 `py -3 cli.py`，输入「查一下 DEMO-1001」。MCP 服务代码位于 `mcp_service/`，订单业务位于 `mcp_service/orders/`。订单查询契约位于 `model/orders/`，退款政策模型位于 `model/policy/`，退款流程模型位于 `model/refund/`。

## 退款政策发布与检索

政策源位于 `policy/refund-policy.md`。安装依赖后，在 `config/local.json` 的 `policy` 部分填写 Milvus 地址、访问凭据和模型服务地址。MCP 服务和发布命令使用同一配置文件；不提供 `policy` 时，查单仍可运行，但政策发布和检索不可用。独立发布政策：

```powershell
py -3 publish_policy.py
```

发布只管理专用集合的当前政策，重复执行使用固定条目标识，不删除历史版本。`search_policy` 的 `mode` 由 Host 选择，咨询先使用 `consultation`，必要时用 `formal` 补查一次。检索失败返回 `failed` 和空候选。

运行 `py -3 test/verify_policy_mcp.py` 可通过独立 MCP 进程验证认证及真实检索。`py -3 test/verify_policy_store.py` 会向政策集合添加并保留一条 `0.0-probe` 历史版本样本，验证当前版本过滤；请在验收环境执行。

在 CLI 输入「退款多久能到账？」可咨询通用政策，输入「DEMO-1001 能退吗？」可查询本人订单资格。政策回复附本轮核验的正式条款，资格依据 MCP 返回的实际订单状态。只问「能退吗」时，CLI 会追问订单编号。当前 CLI 支持查单和咨询，识别到明确办理请求时会提示本轮未提交。

可用 `py -3 test/verify_policy_cli.py` 运行真实 CLI 咨询验收，覆盖通用政策、订单资格、意图对照和重排故障。脚本默认读取 `config/local.json`，并创建独立 MCP 服务和隔离会话，运行后清理测试会话和 checkpoint。

安装 `pytest` 后，运行 `py -3 -m pytest -q test`，执行无需配置外部服务的测试。
