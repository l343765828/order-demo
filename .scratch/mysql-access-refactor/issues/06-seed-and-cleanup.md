# 06 迁移种子装载并清理旧业务数据库接入

Status: ready-for-agent

Blocked by: [03 模拟退款与退款提交记录](03-refund-transactions.md)、[04 消费者跨会话记忆](04-consumer-preferences.md)、[05 政策发布目录](05-policy-publication-catalog.md)

## 目标

迁移 `manage.py seed` 的写入事务，清理旧业务连接池、游标和无调用方的辅助代码，完成专题验收。参见 [专题设计](../spec.md)。

## 范围

在种子 Service 中使用同一连接协调订单和退款 Repository。通过可区分首次插入与已存在的写入方式保留当前种子规则。完成依赖、入口生命周期及简体中文 README 更新。

## 验收标准

- [x] 空隔离库初始化后插入样例订单，且仅在 `DEMO-1003` 为本次新插入时写入配套种子退款。重跑保持原订单与退款，已有订单无退款时仍无退款。验证 SQLAlchemy MySQL 的 `FOUND_ROWS` 行数语义无法误触发配套退款。 [行数语义](https://docs.sqlalchemy.org/en/20/dialects/mysql.html#rowcount-support)
- [x] 订单和退款两次真实写入由两个 Repository 共享同一事务。成功时两者均持久化，注入第二次写入失败后第一次写入也回滚，事后使用独立连接读取数据库验证。
- [x] `manage.py init` 仍可对空环境建库、建表及补齐现有外键设定，重复执行维持既有数据。政策目录初始化和 `PyMySQLSaver` 的直接 PyMySQL 接入仍可使用。
- [x] 已复核业务路径的直接和间接导入，业务连接全部经 Core，DBUtils 无剩余调用后移除依赖。入口正确释放 Engine，独立导入公共工具、配置或适配器不加载业务编排或应用入口。
- [x] README 以简体中文更新依赖及运行说明。最终验收记录列出文件与目录、旧路径映射、事务和锁边界、依赖变化、保留的直接操作、实测命令及受限项。

## 验证方式

在隔离 MySQL 运行 `test/verify_mysql.py`、种子幂等及真实回滚回归，复跑专题各票的现有测试和独立验证脚本。业务数据验收资源仅使用本轮隔离库，必要的 MySQL、Qwen 或 Milvus 环境缺失时记录未运行场景。验收材料放在 `evidence/`。

## Comments

2026-11-24，06 实施与验收。核对了 01 至 05 的代码、配置和证据，直接依赖 03、04、05 已有验收记录。改动前全仓 pytest **107 项通过**。默认 JSON 未通过配置校验。在本机隔离 MySQL 连接后先执行 `SELECT VERSION()`，返回 **8.0.46**，并在开工源码副本复跑旧数据库验证、独立 MCP 查单和跨进程 Checkpoint，均通过。

`manage.py seed` 由 `MySQLSeedService` 管理同一 SQLAlchemy Core **2.0.58** 事务。订单采用普通 INSERT，仅在本次写入 `DEMO-1003` 成功后调用退款 Repository。 `python -X utf8 test/verify_mysql.py --config $config` 实测得到五笔样例订单和唯一配套退款，重跑保留既有数据，已有订单缺少退款时保持无退款。SQLAlchemy 的旧式空更新返回行数 1。注入真实 MySQL 退款写入错误 **1644** 后，独立连接确认前三笔订单与退款全部回滚。空库初始化、补齐复合外键、政策目录原生初始化及双进程连接池均通过。

移除业务 DBUtils 依赖和旧池助手。复跑 `verify_order_query_mysql.py`、`verify_conversation_mysql.py`、`verify_checkpoint_mysql.py`、`verify_preferences_storage_mysql.py`、`verify_mcp_protocol.py` 和 `verify_mysql_service_regressions.py`，覆盖独立 MCP 退款工具、政策目录锁及切换回滚。旧政策 CLI 验证脚本已改用独立库和集合，实测 CLI 源码副本可初始化检查点，向量端点不可用时隔离库仍会清理。全仓 pytest **110 项通过**，mypy **87 个源文件通过**，编译、依赖及 Git 差异检查通过。

本票五项验收有实测证据，`Status` 保留仓库配置的 `ready-for-agent`。先前集群的授权配置本轮不可用，集群 MySQL 版本及依赖真实 Qwen、向量服务和 Milvus 的完整场景未复跑。入口映射、命令、结果和解除条件见[验收记录](../../../evidence/mysql-seed-06.md)。

2026-11-24，集群补验完成。用户补充连接信息后，应用配置通过校验，目标集群 `SELECT VERSION()` 实测 **MySQL 8.0.46**，Milvus **3.0.2**。实际复跑 `test/verify_mysql.py`、`test/verify_order_cli.py` 和 `test/verify_policy_catalog.py`，覆盖种子并发和回滚、真实 Qwen 查单及会话恢复，以及政策检索和版本切换。

使用 `python -X utf8 test/verify_refund_submission.py --config config/local.json --evidence $output` 运行 `--group business`、`--group outcomes --investigation-probe` 和 `--group safety --investigation-probe`，分别通过 **7、19、11** 个场景。真实业务组完成实付 **19900 分** 的模拟退款。结果故障组确认提交后回执丢失可以核对原退款，提交未完成时仍待核对，重开原会话或新会话均无重复写调用。

运行 `python -X utf8 test/verify_preferences_cli.py --config config/local.json --evidence $output`，真实 CLI 在 **3 个会话**中通过 **19 轮**，核对 **38 条**有序原文。跨会话读取、更正、删除和重开恢复均通过。共 **8 次**独立 MCP 查单，退款写调用为 **0**。四条补验命令均退出 0，MySQL 隔离库和退款组 Milvus 集合均核实清理。[集群补验记录](../../../evidence/mysql-seed-06.md#2026-11-24-集群补验)
