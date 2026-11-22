# MySQL checkpoint 验收记录

2026-11-20 配置调整：当前 MySQL 连接信息在 `config/local.json` 中配置。以下为 02 当时的历史验收记录。

2026-11-19；目标实例 `SELECT VERSION()`：8.0.46。`langgraph-checkpoint-mysql` 上游要求 MySQL >= 8.0.19；历史 8.0.17 和 1064 错误见工单原记录。

复跑：Python 3.11，安装 `py -3 -m pip install -r order-demo/evidence/checkpoint-python-freeze.txt` 以复现本次完整环境（核心依赖另见 `order-demo/requirements-checkpoint.txt`），通过环境变量提供 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`，运行 `py -3 order-demo/test/verify_checkpoint_mysql.py`。账号需要创建、删除临时数据库的权限。脚本创建随机隔离库，在 `finally` 中删除并核实删除；不打印凭据。验证直接使用公共 `model.connection_config()`，通过独立子进程依次写入、恢复、清理。样例只保存字符串状态，不保存连接或凭据。

```text
SELECT VERSION(): 8.0.46
Python: 3.11.7
PyMySQL: 1.2.4
langgraph: 1.2.22
langgraph-checkpoint: 4.3.2
langgraph-checkpoint-mysql: 3.0.0
deepagents: 0.7.38
write: both threads saved and paused before finish
resume: independent process recovered both observations and positions, then saved
tables: checkpoint_blobs, checkpoint_migrations, checkpoint_writes, checkpoints, consumer_preferences, conversation_messages, conversation_sessions, conversation_summaries, orders, refund_dispatches, refunds
cleanup: repeated deletion removed thread a and auxiliary rows; thread b read/write and seven business tables unchanged
disposable database removed and verified
```

使用同步 `PyMySQLSaver`；图在 `finish` 前中断，新进程核验两个线程各自的状态、观察和下一节点后继续。清理前确认三个带 `thread_id` 的框架表均存在目标线程记录，重复删除后逐表确认零行，另一个线程继续写入；七张应用表在清理前后逐行比较，每张表均含隔离哨兵记录。`checkpoint_migrations` 为全局迁移表，不按线程删除。DeepAgent 版本已记录但本样例图未调用；这不证明退款业务恢复或 03 的模型／MCP 组合兼容。
