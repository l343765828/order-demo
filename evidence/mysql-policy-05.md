# 05 政策发布目录迁移验收

验收日期为 2026-11-24。首次连接目标实例后先执行 `SELECT VERSION()`，返回 **MySQL 8.0.46**。Milvus 服务端为 **3.0.2**，客户端为 pymilvus 2.6.17，MCP SDK 为 2.3.0。运行环境为 Python 3.11.7、SQLAlchemy 2.0.58、PyMySQL 1.2.4 和 Pydantic 2.14.3。Host 使用真实 Qwen3-8B，政策索引使用实际的 BGE-M3 编码和 BGE 重排服务。

## 前置条件与改造前基线

- 在 `8f1880e` 与既有工作区改动上进行实施。先核对工单 01 的 Engine 工厂、连接配置和 SQL 加载器，以及直接依赖工单 02 的命名锁、会话 Service、Checkpoint 恢复和验收记录。专题顺序中在本票之前的 03、04 已有各自的实施与验收记录。 [01 验收](mysql-core-01.md) · [02 验收](mysql-conversation-02.md) · [03 验收](mysql-refund-03.md) · [04 验收](mysql-preferences-04.md)
- 改造前运行 `python -m pytest -q --tb=short test/test_policy_catalog.py test/test_policy.py test/test_policy_evidence.py test/test_mysql_core.py test/test_mcp_protocol.py`，**46 项通过**。
- 默认 JSON 未通过配置校验。使用项目既有授权后端配置在工作区外生成临时 JSON。首次原版联验发布两份文档后，Host 因隔离库缺少 Checkpoint 表退出。执行 `cli --init-checkpoints` 后重跑，原版 `verify_policy_mcp.py` 及 `verify_policy_catalog.py` 均退出 0。原版联验中的 Host、MCP、订单及政策目录均使用隔离资源，临时启动器将旧验证脚本的会话清理限定到该隔离业务库。原版观察到有效快照、并发冲突、旧版本继续检索及同版本恢复。两次基线创建的库和集合已核实清理。

## 入口与事务

- MCP 入口按 `policy.catalog_database` 组装并复用目录 Engine。与业务库同名时共享 Engine，目录库独立时创建第二个 Engine，退出时释放各连接池。独立 `publish_policy.py --config` 沿用原 JSON 配置与输出。目录表和初始目录头由独立 PyMySQL 初始化路径执行原 DDL 和初始化语句。
- `MySQLPolicyCatalog` 管理连接并通过 `policy_catalog_repository.py` 绑定参数执行 SQL。快照从一次查询还原同修订的每份文档版本，Repository 在读取边界校验具名模型。快照、发布尝试、写入状态与生效切换分别收束事务。
- 同一物理连接跨短事务持有原名称的版本锁。发布尝试先提交 `pending`，Milvus 清理和写入结束后另行提交 `finished`。回读、召回及重排通过后，独立短事务锁住目录头，核对该文档原生效版本，并原子提交首次生效修订和目录头。事务异常回滚，回执不明时沿用同版本核对路径。

## 逐项实测

1. **目录与 MCP 契约。** 发布命令读取临时 `--config`，政策目录在独立于业务库的随机数据库中生成修订 1 和 2。真实 `search_policy` 工具发现、只读注解、输入输出 Schema 和 Bearer 认证通过，正常模式返回 3 个检索回执，模拟 Milvus 不可用时返回 `failed` 和空候选。Host 首次读取修订 2，补查期间发布新版本推进修订 3，补查仍读到修订 2 的 `RF-015`，新调查读取修订 3。未知修订返回 `failed`、无修订及空候选。Host PID 168468，独立 MCP PID 84068，四条引用的修订依次为 2、2、2、3。
2. **版本锁。** 真实 MySQL 通过独立连接查询 `IS_USED_LOCK`，确认连接 973 跨发布短事务持锁，同版本并行发布被拒绝。正常退出、切换失败和同版本重试后均可重新取得锁。将 `RELEASE_LOCK` 回执替换为 0 后，物理连接 974 被丢弃，独立连接在两秒内取得并释放原锁，随后发布使用另一物理连接。
3. **分段持久化与切换原子性。** 在 Milvus 清理与写入入口，独立 MySQL 连接读到已提交的 `pending`。回读和重排入口读到已提交的 `finished`。这些探针同时核对持锁连接的 SQLAlchemy 事务状态及 PyMySQL 的服务端事务标记，均未在事务中。注入目录头更新触发器错误 1644 后，前一条版本生效更新被回滚，目录头仍为修订 2。
4. **失败与恢复。** Milvus 部分写入 2 条后旧修订 3 仍可检索，未确认写入结束时同版本重试被拒。确认结束后清理重发，修订推进到 4 且无旧条款残留。回读校验失败时仍能检索修订 4。实际提交修订 5 后注入回执丢失，同版本重试通过历史记录返回修订 5，无重复向量写入。两个不同版本并发切换同一文档，只一项生效为修订 6。声明改变资格的 7.0 保持未生效，其已记录的资格声明继续约束重试。

整套联验退出 0。[联验回执](mysql-policy-05-integration.jsonl)记录独立的业务库和目录库、锁连接、事务探针、检索事件、冲突和重试结果。测试使用的 MySQL 库、Checkpoint、Milvus 集合均随隔离资源销毁，并核对数据库和集合已不存在。

## 命令与验证结果

`$config` 指向工作区外的授权 JSON。目录联验自行创建业务隔离库、独立目录库和配套集合，并通过独立子进程执行发布 CLI、MCP 和 Host。单独运行 MCP 脚本时，`$publishedIsolationConfig` 指向已用 `publish_policy.py --config` 发布演示政策 1.0 的隔离配置。

```powershell
.venv/Scripts/python.exe -m pytest -q --tb=short test/test_mysql_core.py test/test_policy_catalog.py test/test_mcp_protocol.py
.venv/Scripts/python.exe -X utf8 test/verify_policy_catalog.py --config $config --evidence evidence/mysql-policy-05-integration.jsonl
.venv/Scripts/python.exe -X utf8 test/verify_policy_mcp.py --config $publishedIsolationConfig
.venv/Scripts/python.exe -X utf8 test/verify_policy_mcp.py --config $publishedIsolationConfig --expect-failure
.venv/Scripts/python.exe -m pytest -q --tb=short
.venv/Scripts/python.exe -m mypy --ignore-missing-imports --check-untyped-defs config implementations/policy interfaces/policy model/policy mcp_service/policy mcp_service/server.py publish_policy.py repositories/policy_catalog_repository.py utils/database.py utils/mysql_lock.py utils/sql_loader.py
.venv/Scripts/python.exe -m compileall -q config implementations interfaces model mcp_service publish_policy.py repositories test utils
.venv/Scripts/python.exe -m pip check
git diff --check
git diff --cached --check
```

最终相关回归 **23 项通过**，全仓 **107 项通过**，mypy 检查 **27 个源文件通过**。编译、依赖、独立导入以及 Git 差异检查通过。政策 Repository、命名锁工具和配置可独立加载，未引入业务编排、工作流或应用入口。

Windows 联验处理连接关闭时记录了已有的 `WinError 10054`。联验首次运行还遇到子进程日志的编码提示，已指定 UTF-8 后重新运行独立 MCP 正常和故障验证，两次均退出 0。四项验收均有实测证据。并行审查因工作区额度未返回结果，已在当前会话复核仓库规范和工单契约。
