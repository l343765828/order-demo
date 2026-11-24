# 04 消费者跨会话记忆迁移验收

验收日期为 2026-11-24。连接目标实例后先执行 `SELECT VERSION()`，实测 **MySQL 8.0.46**。运行环境为 Windows、Python 3.11.7、SQLAlchemy Core 2.0.58、PyMySQL 1.2.4、LangGraph 1.2.22、PyMySQLSaver 3.0.0、DeepAgents 0.7.38、MCP 2.3.0 和真实 Qwen3-8B。

## 前置条件与改动前基线

开工基线为 `8f1880e` 加当前工作区已有改动和暂存文件。按专题顺序核对了 01 的共用 Engine、SQL 加载器和 MCP 查单入口，02 的会话 Service、命名锁和原生 Checkpoint 接入，以及 03 的退款提交与执行 Service。04 直接依赖 02。CLI 在开工时已由同一 Engine 组装会话和提交 Service，偏好通过单独的 DBUtils 池访问。 [01 验收](mysql-core-01.md) · [02 验收](mysql-conversation-02.md) · [03 验收](mysql-refund-03.md)

- 全仓 pytest **103 项通过**，偏好与 Core 相关回归 **26 项通过**。旧版 CLI 在隔离库通过 19 轮偏好联验，验证 38 条原文、8 次独立 MCP 查单和 0 次退款写调用，隔离库已清理并核实。
- 新增的 `ManagePreferences` 隔离验证先用旧实现运行，旧连接路径无法接受共用 SQLAlchemy Engine，预期失败。该次隔离库已清理并核实。

## 入口与事务边界

CLI 将同一主库 Engine 传给会话、退款提交及偏好 Service，并在退出时释放 Engine。Host 由 `ManagePreferences` 接口调用已绑定可信消费者的 `MySQLPreferences`。查看使用短连接，结束读取事务后返回 `listed`。设置和删除各使用一次独立事务，确认提交并归还连接后返回 `updated` 或 `deleted`。数据库或存储校验出错时沿用 `failed`，CLI 仍按读取或变更的实际结果生成回复。

偏好 Repository 在 Service 传入的连接上执行独立 SQL，以命名参数绑定消费者、键和值，读取使用 `mappings()` 和既有 `ConsumerPreference` 模型验证结果。设置按组合主键原子插入或覆盖，更新时间继续取 MySQL UTC 微秒时间。表定义、偏好命令、工作流节点、模型提示词、MCP Schema 和恢复路径均沿用已有契约。

## 复跑命令

`$config` 指向工作区外的授权验收 JSON，`$legacy` 指向开工前应用源码副本，`$baseline` 指向工作区外的基线结果文件。各真实验证使用随机隔离库，CLI 联验另派生本机 MCP 端口和临时凭据。

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest -q --tb=short test/test_preferences.py
.venv/Scripts/python.exe -X utf8 -m pytest -q --tb=short test/test_preferences.py test/test_mysql_core.py
.venv/Scripts/python.exe -X utf8 -m pytest -q --tb=short
.venv/Scripts/python.exe -X utf8 test/verify_preferences_cli.py --config $config --evidence $baseline
.venv/Scripts/python.exe -X utf8 test/verify_preferences_cli.py --config $config --legacy-root $legacy --evidence evidence/mysql-preferences-04-integration.json
.venv/Scripts/python.exe -X utf8 test/verify_preferences_storage_mysql.py --config $config
.venv/Scripts/python.exe -X utf8 test/verify_conversation_mysql.py --config $config
.venv/Scripts/python.exe -X utf8 test/verify_checkpoint_mysql.py --config $config
.venv/Scripts/python.exe -m mypy --ignore-missing-imports --check-untyped-defs cli.py implementations/refund/host.py implementations/refund/preferences.py interfaces/refund/preference.py model/refund/preference.py repositories/preference_repository.py
.venv/Scripts/python.exe -m compileall -q cli.py config implementations interfaces model mcp_service repositories workflows test utils
.venv/Scripts/python.exe -m pip check
git diff HEAD --check
git diff --cached --check
```

无 `--legacy-root` 的 CLI 基线命令在改动前运行。

## 验收结果

| 工单验收项 | 实测结果 |
| --- | --- |
| 读取、更新、删除限定认证消费者，状态、回复及缺失行为一致 | 双消费者分别读写同一偏好键，独立连接确认对方记录保持原值，包括消费者编号含引号、下划线和百分号的夹具。当前值缺失时读取返回 `listed` 和空值，重复删除返回 `deleted`。真实 CLI 拒绝访问其他消费者会话和偏好，19 轮的偏好状态、回复、有效详略及恢复标志与基线逐轮一致。 |
| 写入、删除在返回前结束事务，故障沿用业务失败语义并归还连接 | 更新和删除成功返回后，独立 MySQL 连接立即能看到新值或空记录。故障探针在事务连接内分别看到已执行的更新和删除，独立连接仍看到旧值。提交前执行 MySQL `SIGNAL SQLSTATE '45000'` 后，两次均返回 `failed`，旧值保留。每次正常、异常或用旧无效值触发存储校验的调用结束时，Engine 借出连接数均为 0。CLI 另经真实触发器和临时表名故障验证失败回复，查单仍使用可读取的当前偏好或默认排版。 |
| 跨会话使用当前偏好，历史原文和旧 Checkpoint 不重新启用旧值 | 开工前源码副本的 Host 在同一隔离库写入首条偏好、原文和 Checkpoint。新版 Host 在新会话读取该值，并接续旧会话。三个会话共 19 轮，验证更正、读取更正后的值、删除、重开旧会话与新会话。删除后旧会话读取为空，查单采用默认详略，38 条原文通过 SQL 和 CLI 逐条核对。相关 pytest 覆盖暂停在偏好节点的历史 Checkpoint 不重放写入。 |

真实 CLI 联验退出码为 0，8 次独立 Streamable HTTP MCP 查单均返回同一份订单事实及实付 **19900 分**，退款写调用为 0。隔离库经查询确认清理，独立 MCP 进程已退出。 [逐轮回执](mysql-preferences-04-integration.json)

会话验证复跑通过，覆盖会话锁、原文原子回滚及流程入口失败时保留用户原文。Checkpoint 验证通过，覆盖三个独立进程写入、恢复和按线程清理。两组隔离库均已清理并核实。LangGraph 对历史消息和来源类型仍发出已有的反序列化注册警告，当前版本读取、接续与清理均通过。

偏好 pytest **21 项通过**，偏好与 Core 合计 **26 项通过**，全仓 pytest **103 项通过**。mypy **6 个文件通过**，编译、依赖和 Git 差异检查通过。独立导入偏好 Repository、配置、SQL 加载器和 Engine 工厂时未加载应用入口、业务用例或工作流。工单契约和仓库规范分别通过静态审查。跨版本复跑使用开工前源码副本，`--legacy-root` 由复跑者传入该副本路径。

本票三项验收均已实测。外部联验覆盖真实 MySQL、Qwen3-8B 和独立查单 MCP。政策目录和 Milvus 联验由专题工单 05 覆盖。
