# 02: 验证 MySQL checkpoint 真实保存、重启恢复和按线程清理

**Status:** ready-for-agent

**Blocked by:** 无，可立即开工。试验使用独立验收库或隔离的验收数据，适配器在业务路径中接入 01 的共享配置。

## 目标

在目标 MySQL 版本上确认一个可用的 LangGraph checkpointer，交付可复跑的兼容性证据与复用方式。

## 范围

核对目标实例实际版本，验证 MySQL 驱动、LangGraph／DeepAgent 版本和同步或异步调用方式。复核候选 `langgraph-checkpoint-mysql` 的要求，本地记录的 MySQL 8.0.17 低于候选最低要求 8.0.19；若实例仍为 8.0.17，验证兼容的 MySQL 实现。样例图验证持久化能力，退款恢复规则由业务票验收。发现兼容性缺口时提交可复现阻塞证据，业务验收仍以兼容适配器实测通过为准。

## 涉及的契约

- Host 管理框架表，应用 `session_id` 映射为 `thread_id`；消费者与会话归属在应用载入时核验。
- checkpoint 保存图状态。运行中的 MCP Client、连接池、HTTP 连接和凭据不进入保存状态。重置需要按线程清理能力。 [数据库设计](../database-design.md) · [存储与记忆](../storage-memory.md) · [ADR-0004](../../../docs/adr/0004-refund-agent-storage-boundaries.md) · [ADR-0005](../../../docs/adr/0005-refund-conversation-compression-owner.md)

## 验收标准

- [x] 记录 `SELECT VERSION()`、适配器最低要求及实际锁定的依赖组合；证明选定实现在目标版本及所选同步／异步方式下可用。
- [x] 在真实 MySQL 初始化框架表，写入两个线程的一组图状态。终止写入进程后，由新进程读取各自状态、观察及恢复位置，且可继续保存；不同线程数据互相隔离。
- [x] 删除一个线程后，该线程的 checkpoint 及所属框架辅助记录均不可恢复；另一线程仍可读写。能够重复清理，应用七张业务表不受影响。

## 验证方式

用同一锁定依赖运行写入、独立进程恢复、按线程删除三步脚本，保留实际 MySQL 版本、适配器版本、框架表清单和断言结果。仅在内存或同一进程中读回无法满足本票。

## Comments

2026-11-19 实测：通过现有配置连接目标实例，`SELECT VERSION()` 返回 MySQL Community Server `8.0.17`。在随机命名的临时数据库测试同步 `PyMySQLSaver`，使用 `langgraph-checkpoint-mysql 3.0.0`、`langgraph 1.2.22`、`langgraph-checkpoint 4.3.2`、`PyMySQL 1.2.3`、Python 3.11；当前环境另有 `deepagents 0.7.38`，本次样例图不调用它。适配器 `setup()` 建表成功，但真实 LangGraph 图首次保存 checkpoint 时，`UPSERT_CHECKPOINTS_SQL` 在 `AS new` 处收到 MySQL 1064 语法错误，写入失败。MySQL 文档说明该行别名语法从 8.0.19 起支持，适配器上游也要求至少 8.0.19。测试库已删除并查询确认不存在，适配器安装在临时目录。未进入新进程恢复与线程清理测试。工单保持待处理，需另行验证与 8.0.17 兼容的方案，或在满足版本要求的目标环境重新运行完整验收。

来源：[适配器上游说明](https://github.com/tjni/langgraph-checkpoint-mysql) · [MySQL INSERT 语法说明](https://dev.mysql.com/doc/refman/8.0/en/insert-on-duplicate.html)

2026-11-19 升级后复测与验收：通过共享 `model.connection_config()` 连接目标实例，`SELECT VERSION()` 为 **8.0.46**；上游最低版本为 MySQL 8.0.19。Python 3.11.7、同步 `PyMySQLSaver`、PyMySQL 1.2.4、langgraph-checkpoint-mysql 3.0.0、langgraph 1.2.22、langgraph-checkpoint 4.3.2、deepagents 0.7.38；版本约束见 `order-demo/requirements-checkpoint.txt`，DeepAgent 未在样例图中调用。旧 8.0.17 与 1064 是升级前历史结果。

执行 `py -3 -m pip install -r order-demo/evidence/checkpoint-python-freeze.txt` 后设置 `MYSQL_*`，运行 `py -3 order-demo/test/verify_checkpoint_mysql.py`。真实隔离库初始化四张框架表；独立写入进程保存两个线程，退出后另一进程恢复各自观察和 `finish` 位置并继续保存；第三进程对一个线程重复清理，确认 checkpoints、checkpoint_blobs、checkpoint_writes 均无其记录，另一个线程仍可读写，七张应用表各含哨兵记录，逐行比较不变。随机隔离库已删除并查询确认。完整命令、框架表及输出见 `order-demo/evidence/checkpoint-mysql.md`。

验收结论：三项标准通过。仓库未配置完成状态，保留 `ready-for-agent`。退款业务恢复及 03 的模型／MCP 联合兼容留待相应工单。

补充检查：`python -m pip check` 无依赖冲突，`python -m compileall -q order-demo/test/verify_checkpoint_mysql.py` 通过。实际环境的完整包版本快照见 `order-demo/evidence/checkpoint-python-freeze.txt`；依赖文件固定核心组合，传递依赖以该快照记录。全仓库 `py -3 -m pytest -q` 在收集阶段报 18 项错误，涉及既有其他演示缺少 httpx、jsonschema、pymilvus、langgraph 及 MCP 模块冲突，未完成全仓库测试。

审查后补强：七张业务表均装入满足约束的哨兵，重新运行三阶段真实 MySQL 验收全部通过，临时库已确认删除。复跑命令使用完整环境快照。`py -3 -m mypy --ignore-missing-imports --check-untyped-defs order-demo/test/verify_checkpoint_mysql.py` 因系统 Python 未安装 mypy 而未执行类型检查；编译检查通过。
