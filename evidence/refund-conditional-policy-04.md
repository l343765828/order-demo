# 工单 04 协调政策发布与开发环境验收

验收日期为 2026-11-28。程序版本为 `main` 的 `c4e213e130b868500283a1e8a87eba2710eadb96` 加工作区已有应用改动，包含工单 01–03 的退款实现。本轮复用这些实现，修改正式政策、验收入口和说明。实测应用文件指纹为 `ce43fa77a78c03fa224b9ecfc388e87bb6143eb6820de4c4be1d5d09a6a170d3`。指纹按 Git 跟踪的现存应用 Python、SQL 和依赖清单路径排序，依次写入 UTF-8 路径、一个 NUL 和文件的 SHA-256 二进制摘要，再计算总 SHA-256，共 125 个文件，排除 `test/` 和配置副本。前置 03 的五项验收均已通过。 [前置工单记录](refund-conditional-policy-03.md)

## 正式政策与目标切换

- `1.1` 保留 `1.0` 的文档 ID、14 条正式条款和 7 条 FAQ，修订 RF-013，明确由可信订单事实和统一规则判断资格，已授权的合格办理无需预先取得政策依据。政策用于咨询和解释，依据失败或冲突时保留已确认结果。两类条款前提等待之后的明确授权。原文件 `1.0` 保留供快照复核。 [1.1 原件](../policy/refund-policy-1.1.md) [1.0 原件](../policy/refund-policy.md)
- 本轮目标通过现有 `config/local.json` 选定。MySQL 业务库与政策目录均为 `order_demo`，Milvus 集合为 `order_demo_refund_policy`。首条显式数据库查询 `SELECT VERSION()` 返回 MySQL `8.0.46`，Milvus 返回 `3.0.2`。隔离库按现有入口建表和初始化检查点，逐表 `SHOW CREATE TABLE` 对比。11 张业务和检查点表只有 `refunds` 多出非唯一索引 `fk_refund_dispatch (idempotency_key, order_id)`。
- 索引差异触发 ADR-0012 暂停。收到用户确认后复查目标本地 MCP `8765` 无监听，未发现针对目标配置的 Agent 进程。具全局连接可见性的账号查询 `information_schema.PROCESSLIST` 和 `INNODB_TRX`，业务库其他连接与关联事务均为 0。删除经确认的索引后，11 张表的 `SHOW CREATE TABLE` 全部与新建库一致。
- 仍在停流窗口内执行 `manage.py reset`。重置前目标提交表已空。重置后五笔种子订单和样例退款 `MOCK-RF-1003`（8800 分）齐全，提交、会话、偏好和检查点等八张数据表为空，22 条检查点迁移记录逐行一致。隔离 MySQL 另植入各状态提交，验证 `pending`、`unconfirmed`、`succeeded`、`closed_no_refund` 均清空，政策目录和其他演示哨兵逐行保存。
- 当前目标配置和 Git 中上一程序版本的默认配置均未设置 `refund_policy_events_dir`。旧流程只向配置的目录写入对应幂等键的 JSONL，目标配置因此无可清理文件。仓库 `evidence/` 中的历史证据继续保留。若运行过另一个未提供的旧配置，需要用该配置确认实际目录后再核对其文件。
- 重置前目标库没有两张政策目录表，也没有额外业务表。Milvus 重置前后 `FLAT/IP` 索引定义及已有 22 条记录（含向量）逐行一致，前后记录 SHA-256 均为 `639c185da435c94e467ad57b1ae75ccfcbf5280888325a98c216f170bc410315`。发布入口随后新建目录并启用 `DEMO-REFUND-POLICY` `1.1`，修订号为 `1`，发布确认 `unchanged`，21 条记录强一致回读与原件匹配。发布后原有 22 条仍保持相同摘要。
- `1.1` 原件及目录记录的 SHA-256 为 `18e87cf3296387390ad5109609682f56ab1185f81fbb94eb4902a7a3dcbe5b36`。单独隔离目录实际发布 `1.0` 后切换 `1.1`，目录修订 `1` 与 `2` 分别保留正确快照。按各快照强一致读回 21 个条目并逐条与对应原件核对。目标库直接从没有活动目录切换到 `1.1`，隔离库另覆盖有历史发布时的切换。

```powershell
.venv/Scripts/python.exe -X utf8 manage.py --config config/local.json reset
.venv/Scripts/python.exe -X utf8 publish_policy.py --config config/local.json --source policy/refund-policy-1.1.md --version 1.1 --publisher demo-maintainer --qualification unchanged
.venv/Scripts/python.exe -X utf8 test/launch_with_config.py mcp_service.server config/local.json
.venv/Scripts/python.exe -X utf8 test/launch_with_config.py cli config/local.json --message '查一下 DEMO-1001'
.venv/Scripts/python.exe -X utf8 test/launch_with_config.py cli config/local.json --message '这笔订单可以退款吗？'
.venv/Scripts/python.exe -X utf8 test/launch_with_config.py cli config/local.json --session <上一轮会话编号> --message 'DEMO-1001'
.venv/Scripts/python.exe -X utf8 test/launch_with_config.py cli config/local.json --message '如果能退，就帮我退 DEMO-1098'
.venv/Scripts/python.exe -X utf8 test/launch_with_config.py cli config/local.json --message '如果能退，就帮我退 DEMO-1001'
.venv/Scripts/python.exe -X utf8 test/launch_with_config.py cli config/local.json --message '退款多久能到账？'
```

目标核对使用 PyMySQL 和仓库连接工具执行 `SELECT VERSION()`、`SHOW CREATE TABLE`、`SELECT status, COUNT(*) FROM refund_dispatches GROUP BY status` 及表行对比。经确认的结构调整命令为 `ALTER TABLE refunds DROP INDEX fk_refund_dispatch`。通过 `MySQLPolicyCatalog.snapshot()` 和 `PolicyBackend.read_version()` 核对目录及原文。目标 MCP 与 CLI 复用 `test/launch_with_config.py`，目标待核对测试以 `test/verify_refund_submission.py` 的种子辅助方法创建专用 `DEMO-1098`，使用独立授权来源会话与 `pending` 记录。CLI 事件按 `trace_id` 对照 MCP 结构化事件及业务库实际记录。

### 目标新会话实测

| 场景 | 实测观察 | 追踪标识 |
| --- | --- | --- |
| 新会话查 `DEMO-1001` | 已支付、未发货，实付 19900 分。MCP 查单一次，零次政策检索和提交。 | `06502047-80bf-442a-a74b-ea500775b418` |
| 资格咨询缺号 | 追问要咨询的编号，零次工具调用。 | `b9903260-d989-4df9-8e15-1f1275b8fc7d` |
| 同会话只补 `DEMO-1001` | 符合资格、仅作解释，查单一次，零次政策检索和提交。 | `7f74a501-db9b-452e-953d-f89305126c7f` |
| 新会话申请待核对订单 `DEMO-1098` | 查单一次、结果核对一次且未找到原结果。保留同一个 `pending`，零次提交。随后撤除该专用订单、提交和授权来源会话。 | `6b2900a7-7f8c-4767-8adb-ff2b5558ab09` |
| 默认开发配置办理 `DEMO-1001` | 默认配置缺写凭据，回复退款服务暂时不可用，零次提交。 | `cdfdbc74-95fa-4ef9-b152-9b389ee7e030` |
| 目标库临时写配置办理 `DEMO-1001` | 两次查单、一次退款提交、零次政策检索。回执 `MOCK-RF-1f28bf8c0e394e979b0aa095521f46b6` 与唯一新退款记录一致，金额 19900 分，提交为 `succeeded`。 | `0949c6cd-e1d1-42db-b37b-ad23873bdadc` |
| 新会话咨询到账 | 实际 CLI 答复引用 `1.1` `FAQ-004`，事件中的版本、目录修订与原文匹配。首轮探针对显示文本按整条原文比较，FAQ 标题及内部条款索引会在答复排版时省略。随后通过 CLI `--history`、原始会话记录和政策原件，核对实际答案正文及引文一致。 | 会话 `f13ad9b6-9791-438e-98ba-3f32d468fe41` |

办理实测以版本库配置为基础，在临时配置副本中用 `secrets.token_urlsafe(40)` 分别生成临时读、写凭据，Host 与 MCP 传入同一副本。MySQL、Milvus 和政策目录继续指向已验收的开发目标。版本库配置保持原有读写设置。

另用独立 MCP 的 `search_policy` 调用，传入查询「退款政策与执行结果的依据，办理是否需要预先取得政策依据？」与 `formal` 模式，核对回复中的文档 ID、标题、`1.1` 版本、条款 ID、类型和全文均与 RF-013 原件一致。MCP 实收一次检索，目录修订为 `1`，追踪 `305b1565-59ab-4f6f-bcc8-60e995fc111c`，检索事件 `5a60455a-1747-47ca-876b-35d03ce4e715`。临时验收服务结束后退出。

## 调用链和状态

| 路径 | 本轮流转与调用次数 |
| --- | --- |
| 普通查单 | `INTENT → PLAN → EXECUTE → OBSERVE → REPLAN`，MCP 查单一次，政策检索和退款提交各零次。 |
| 只读资格补号 | 缺号一轮在 `INTENT` 追问，不发工具调用。只补号时接续同一会话的资格咨询，`INTENT` 模型为零次，查单一次，政策检索和退款提交各零次。 |
| 普通合格办理 | 确定性规则判断资格，无用于资格确认的模型调用。查单与提交前复核后保存带授权的 `pending`，受控发送一次，成功后变为 `succeeded`。政策检索和旧核验 JSONL 写入均为零次。旧 `1.0` 路径在提交前要求检索和核验事件持久化。 |
| 提交前拒绝 | 依据可信订单状态直接拒绝，零次退款服务调用、零条新退款。按原因追加一个正式政策步骤，必要时同快照补查一次。 |
| 事务内拒绝 | 一次退款服务调用，锁定后复查最新订单，拒绝提交成为 `closed_no_refund`，零条新退款。事务退出后才按确认原因检索政策。 |
| 结果待核对 | 读原结果，已确认的原退款沿用原退款号，查无原结果时保留 `pending` 或 `unconfirmed`。同订单新会话零次退款提交。 |

## 验证命令

```powershell
$verificationDir = Join-Path ([IO.Path]::GetTempPath()) 'refund-cutover-04'
$null = New-Item -ItemType Directory -Force -Path $verificationDir
.venv/Scripts/python.exe -X utf8 -m pytest -q test/test_policy.py -k cutover_policy --tb=short
.venv/Scripts/python.exe -X utf8 -m pytest -q test/test_policy.py test/test_policy_catalog.py test/test_policy_evidence.py test/test_demo_reset.py test/test_order_id_extraction.py test/test_order_flow.py test/test_refund_submission.py --tb=short
$env:OPENBLAS_NUM_THREADS = '1'
.venv/Scripts/python.exe -X utf8 -m pytest -q test
.venv/Scripts/python.exe -m mypy --ignore-missing-imports --check-untyped-defs config implementations interfaces model mcp_service repositories utils workflows cli.py manage.py publish_policy.py
.venv/Scripts/python.exe -m py_compile test/test_policy.py test/verify_mysql.py test/verify_refund_submission.py
.venv/Scripts/python.exe -m pip check
git -c core.safecrlf=false diff --check
git -c core.safecrlf=false diff --cached --check -- . ':(exclude)policy/refund-policy-1.1.md'
git -c core.safecrlf=false -c core.whitespace=-blank-at-eol diff --cached --check -- policy/refund-policy-1.1.md
.venv/Scripts/python.exe -X utf8 test/verify_mysql.py --config config/local.json
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config config/local.json --group all --evidence (Join-Path $verificationDir 'all.jsonl')
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config config/local.json --group explicit-policy --evidence (Join-Path $verificationDir 'explicit-policy.jsonl')
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config config/local.json --group qualification --evidence (Join-Path $verificationDir 'qualification.jsonl')
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config config/local.json --group outcomes --evidence (Join-Path $verificationDir 'outcomes.jsonl')
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config config/local.json --group outcomes --investigation-probe --evidence (Join-Path $verificationDir 'outcomes-probe.jsonl')
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config config/local.json --group qualification --investigation-probe --evidence (Join-Path $verificationDir 'qualification-probe.jsonl')
.venv/Scripts/python.exe -X utf8 test/launch_with_config.py cli config/local.json --session f13ad9b6-9791-438e-98ba-3f32d468fe41 --history
```

开工基线 pytest 296 项、mypy 90 个应用源文件通过。发布条款的定向红灯为缺少 `1.1` 正式原件，添加后通过。首轮完整隔离组在发布快照、合格办理和直接并发验收完成后遇到 CLI 超时。主动咨询组首个通用咨询通过，订单依据一轮 CLI 超时。重试与全量回归期间同时观察到 Windows 提交内存余量不足、OpenBLAS 分配失败、Python 子进程 `MemoryError` 或无法创建线程。独立故障样本改为顺序运行，真实竞争用例继续并发验证。

最终定向 pytest 204 项通过。全量 pytest 限制 OpenBLAS 为一个线程后通过 297 项，mypy 通过 90 个应用源文件，`py_compile` 和 `pip check` 通过。已复核 README 标题、正文和操作说明为简体中文。政策原件标头延续 `1.0` 的 Markdown 换行格式，三个行末的双空格已单独核对并随发布原件保留。

### 隔离端到端观察

| 场景 | 观察 |
| --- | --- |
| 发布、普通办理和直接 MCP | `all` 组在隔离目录确认两份快照。真实 CLI 合格办理一次、政策零次。独立 MCP 用同一幂等键并发收到两次请求，只产生一条退款，后续返回原回执。 |
| 新版咨询来源 | `explicit-policy` 组通用到账咨询引用 `1.1` 正式发布原件的 `FAQ-004`，目录修订和原文一致，退款调用和新退款均为零。 |
| 禁用政策后端的冷启动 | 独立进程使用无 `policy` 配置的临时副本。真实 CLI 查单、合格资格咨询及只读补号照常，首次获授权办理一次。后续重复请求返回原退款，只有历史指代时重新追问编号。 |
| 冷启动拒绝及政策降级 | 未支付与已发货订单，两轮查资格、两轮明确办理分别返回真实业务原因。每轮最多一次失败政策调用，无退款提交。通用咨询说明暂时无法核实依据。调查探针整组退出码为 0。 |
| 提交前最新状态改变 | 首次查单未发货、提交前复核读到已发货，零次写调用、零条新退款。 |
| 事务内才发现订单已发货 | 首查与复核读取未发货，MCP 在事务内读取到已发货后拒绝。一次退款调用、零条新退款，提交结束为 `closed_no_refund`，退出事务后一次失败政策检索。 |
| 丢失成功回执 | 一次提交后只读核对发现原退款，回复原回执，库中一条退款。 |
| 丢失拒绝回执 | 一次提交后只读核对仍查无原结果，保存 `unconfirmed`，跨会话再请求仍查原结果，零次追加提交。 |
| 写入事务失败 | MySQL 约束拒绝插入、插入后强制回滚两例均一次退款调用、零条持久化退款，提交关闭。提交成功但连接在回执前断开时查到原退款，回复原结果。提交前断开且查无结果时进入待核对。 |
| 两个 Host 提交竞争 | 两个独立会话竞争同一订单，MCP 实收一次退款写调用，库中一条提交和一条退款。另一请求走只读核对并返回待核对。锁超时、释放后受控提交及退款查询归属检查也通过。 |

调查探针只替换实模的只读工具选择，按原路径访问真实隔离 MCP 和 MySQL。故障注入与观测只作用于隔离库。资格规则、授权核验、提交、事务及回执处理使用应用代码。

`qualification` 和 `outcomes` 的纯实模运行均以退出码 0 完成，两组也分别通过调查探针复验。结果覆盖冷启动三项、前置与事务内拒绝、重复办理、并发、事务失败、回执丢失及跨会话核对。隔离脚本确认清理各自创建的 MySQL 库和相关 Milvus 集合。完整 `all` 和 `explicit-policy` 命令曾在后续政策问答处超时，前面已通过的场景保留证据，验收项通过实际目标、成功的隔离分组和全量 pytest 汇总。重新获取两组单次全绿结果需在具备足够 Windows 提交内存、模型能在 CLI 时限内应答的环境单独运行。

## 审查

- 规范审查核对固定状态使用和新文件暂存，新增断言复用退款、提交和政策检索枚举。正式 `1.1` 原件按约定暂存。
- 规格审查核对同一配置贯穿重置、发布、启动和实测。已修正 README，使三类入口可使用同一个外部配置，政策入口指向 `1.1`。实际运行版本增加工作区源码指纹。目标切换的结构差异由用户确认后处理。
- 工单 02 的独立订单号提取实模对照沿用其固定基线，当前工单验证新流程的订单号来源、缺号、补号及授权回归。
