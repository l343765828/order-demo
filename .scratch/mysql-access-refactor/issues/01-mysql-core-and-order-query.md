# 01 搭建 MySQL Core 基础设施并迁移本人查单

Status: ready-for-agent

Blocked by: 无

## 目标

通过正常 MCP 入口完成本人查单，建立后续工单可复用的 Engine、SQL 加载和 Repository 约定。参见 [专题设计](../spec.md)。

## 范围

- 盘点应用和测试中的直接 PyMySQL、DBUtils、游标、连接池、提交与回滚。记录调用方、旧事务边界和归属工单，在代码改动前跑当前可用基线。
- 添加 SQLAlchemy 2.x 依赖。在 `utils/database.py` 与 `utils/sql_loader.py` 提供连接工厂及 SQL 加载器，在配置中集中维护连接池参数。
- 新增 `repositories/order_repository.py`、`repositories/sql/orders/get_order.sql` 和订单 JOIN 的具名查询结果，复用现有枚举和完整订单实体。订单 Service 管理只读连接和业务结果转换，MCP 入口注入 Service 并保持现有线程调度和事件日志。
- 当前阶段中仍调用旧 MySQL 接入的退款和政策路径沿用原依赖，由后续工单迁移。

## 验收标准

- [x] 已记录当前 PyMySQL／DBUtils 调用清单和回归基线，区分业务访问、管理初始化、Checkpoint 与验证脚本。
- [x] Engine 在现有 JSON 配置加载后创建，处理带特殊字符的密码，使用 UTC 和 UTF-8，启用连接存活检查，按进程复用并在退出时释放。查单结束归还连接。可独立加载基础设施模块。
- [x] SQL 加载器按固定根目录读取 UTF-8 文件，同一路径复用 `TextClause`。Repository 通过注入的 `Connection` 绑定参数并验证数据库结果，连接和事务由 Service 管理。
- [x] 本人存在订单返回 `FOUND`，且已退款和未退款结果准确。他人订单和不存在订单统一返回 `UNAVAILABLE`。数据库故障返回 `FAILED`，不包含凭据或 SQL 异常详情。包含引号等特殊字符的参数按原值绑定。
- [x] MCP 工具发现、输入输出 Schema、Bearer 认证、消费者隔离、日志和现有写权限与基线一致。同步查询仍在现有线程调度中运行。

## 验证方式

运行 `test/test_order_query.py`、SQL 加载器和连接生命周期测试、`test/verify_order_query_mysql.py` 与 MCP 契约回归。使用隔离库验证参数绑定和数据库故障。有外部环境时复跑 `test/verify_order_cli.py`。在 `evidence/` 记录基线、复跑命令、结果及环境受限项。

## Comments

2026-11-24，工单 01 实施与验收。改动前基线为 `8f1880e`，无直接依赖。`SELECT VERSION()` 实测 **8.0.46**，改动前 `python -m pytest -q` **98 通过**，工单相关回归 **38 通过**。原 `verify_order_query_mysql.py` 从演示库只读核对五笔种子和本人订单结果，`verify_mcp_protocol.py` 独立进程往返通过。

本人查单已从 DBUtils 迁移为入口注入 `MySQLOrderQueryService`，通过 SQLAlchemy Core **2.0.58** 与 PyMySQL **1.2.4** 读取订单。在临时 JSON 指定的随机隔离库执行 `python -X utf8 test/verify_order_query_mysql.py --config <验收配置>`，验证 `FOUND`、`UNAVAILABLE`、`FAILED`、退款关联、真实故障恢复、特殊字符绑定、连接归还和存活检查。独立 MCP **2.3.0** 的工具发现、结构化响应、Bearer 认证、请求日志及写权限与基线一致，工作线程调度经契约测试复核。

`python -X utf8 test/verify_order_cli.py --config <验收配置>` 通过真实 Qwen3-8B、独立 MCP 和 MySQL 查到本人实付 **19900 分**，并验证他人和不存在结果一致、断连提示、会话恢复和零次退款写调用。两套验收隔离库均已清理并核实。最终 `python -m pytest -q` **103 通过**，mypy、compileall、pip check 和差异检查通过。

五项验收全部通过。临时验收配置未进入仓库。`Status` 沿用 `ready-for-agent`，本地分诊标签未配置完成状态。命令、调用盘点、连接和事务边界及实测详情见[验收记录](../../../evidence/mysql-core-01.md)。
