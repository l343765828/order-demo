# 03: 移除币种字段并兼容历史数据

**Status:** ready-for-agent

**Blocked by:** 无。

**What to build:** 订单、退款回执、MCP 结果和数据库使用整数人民币分表示金额，移除固定的币种字段。现有订单、退款记录和旧退款会话可在配套升级后继续读取与恢复。

## 验收标准

勾选反映 2026-11-28 当前工作区的复验结果。

- [x] 在独立 Agent 与 MCP 联验中执行本人查单、退款结果核对及一次授权模拟退款。新版结果无 `currency`，实付和退款整数分正确，面向消费者的人民币金额展示正确。
- [ ] 对包含历史订单和退款的隔离数据库执行迁移。迁移前核对币种，迁移后订单表及退款表均无 `currency`，并保留原标识、金额及关联。新建数据库使用相同数据契约。
- [ ] 在隔离库放入非人民币记录，确认迁移在删列前中止并报告，相关列、金额及历史记录均保留。
- [ ] 用带历史 `currency` 的真实格式快照验证会话读取、进图与消费者明确继续后的恢复。覆盖工具事件、调查观察、订单结果和退款回执，核对旧金额与原退款记录。新版写入的快照无 `currency`。

## 关联规格

[退款政策依据与单币种数据契约](../spec.md)

## Comments

### 2026-11-28 当前工作区复验

本轮按用户确认保留已实施的[兼容清理](../../../evidence/refund-demo-compatibility-cleanup.md)。工单没有前置依赖。以下结论适用于当前工作区，2026-11-25 的验收记录继续保留用于追溯。[本轮命令和实测](../../../evidence/refund-currency-03.jsonl)

- **金额与独立进程通过。** MySQL 实测 8.0.46，退款联验使用 Milvus 3.0.2 和 Qwen/Qwen3-8B。独立 MCP 的本人查单和原退款核对原始回执均无 `currency`，金额分别是整数 19900 分和 8800 分。CLI 查单显示实付 19900 分，核对原单 `MOCK-RF-1003` 显示 ¥88.00 且没有退款写调用，明确授权的模拟退款显示 ¥199.00，只有一次退款写调用。成功退款回执通过 MCP 输出 Schema 校验，数据库记录与回执一致。
- **新建库通过，旧库迁移验收未通过。** 新库订单表和退款表均无 `currency`。在带两旧列的隔离库中，纯 CNY、订单含 USD 和退款含 USD 三个样例的 `manage.py init` 均返回 0，字段和数据原样保留。当前入口没有迁移或异常币种拦截能力。
- **旧会话恢复验收未通过。** 使用历史模型和 MySQL Saver 写入带旧币种的原单与待发送检查点。当前 CLI 查看历史或提交继续消息均在状态校验处返回 1，未进入续办，原退款、消息和检查点均保留，退款提交数为零。另对新查单会话的 8 个快照逐个核对，均无 `currency`。未逐个核对新版退款会话的过程快照。
- **回归与审查。** 全量 pytest 基线和终验均为 251 项通过，两组定向测试分别通过 43 项和 9 项，mypy 检查 90 个源文件通过。独立查单全场景脚本有一处 `AssertionError`，本轮本人查单场景通过定向隔离探针复验。按规范和规格两个维度完成审查，详见本轮证据。

三项旧版验收重新开放需要另行确认恢复迁移与历史状态兼容的范围。真实服务验收使用临时库和临时集合，相关资源已清理。

### 2026-11-25 实施与验收

订单、退款回执、MCP 响应、SQL 读写和种子数据均使用原有的人民币整数字段 `paid_amount_fen` 或 `amount_fen`。升级由 `manage.py init` 执行。命令锁定订单和退款表，先核对两表仍存在的历史币种列，再删列。退款会话读取兼容旧币种字段。新增检查点使用清理后的通道数据，旧检查点仍保留原内容。

- **跨进程联验。** 先执行 `SELECT VERSION()`，实测 MySQL **8.0.46**。真实 CLI 使用独立的 Streamable HTTP MCP 进程、`Qwen/Qwen3-8B`、Milvus **3.0.2** 及现有向量检索和重排服务。三个 MCP 响应均无 `currency`。本人查单实付 **19900 分**，原退款核对 **8800 分（¥88.00）**，新获授权的模拟退款 **19900 分（¥199.00）**，全程一次退款写调用。
- **隔离库迁移。** 对旧版初始化的库逐一在退款表和订单表放入 `USD`。每次初始化均以退出码 1 报告异常表，两表币种列和历史数据保留。恢复样例为 `CNY` 后移除两列，逐行比对保留 6 笔订单、2 笔退款和 1 笔提交的标识、金额、关联及其余字段。重复初始化通过，新库取得相同表契约。另在隔离库分别验证只剩任一旧列时可重跑初始化，业务数据保持一致。
- **历史会话。** 用旧版业务模型、退款图和 MySQL Saver 保存包含工具事件、调查观察、订单结果和退款回执的真实格式快照。历史读取保留原退款单号及金额，待发送会话未自动提交。消费者明确继续后，原退款路径返回原单，待发送路径使用本轮新授权和实际政策检索完成一笔模拟退款。检查两条会话的所有新快照，含输入和过程检查点，均无 `currency`。[联验证据](../../../evidence/refund-currency-03.jsonl)

#### 验证命令

`$config` 指向本地验收配置，`$legacyRoot` 是在仓库外解出的旧版源码，基线为 `750c78d87d400bdac6b70596ddef0ccf77cf4ce2`。联验脚本核实隔离数据库和 Milvus 集合已清理。

```powershell
.venv/Scripts/python.exe -X utf8 test/verify_refund_currency.py --config $config --legacy-root $legacyRoot --evidence evidence/refund-currency-03.jsonl
.venv/Scripts/python.exe -X utf8 test/verify_mysql.py --config $config
.venv/Scripts/python.exe -X utf8 test/verify_order_query_mysql.py --config $config
.venv/Scripts/python.exe -X utf8 test/verify_checkpoint_mysql.py --config $config
$env:PYTHONUTF8 = '1'
.venv/Scripts/python.exe -X utf8 test/verify_mysql_service_regressions.py --config $config
.venv/Scripts/python.exe -m pytest -q --tb=short
.venv/Scripts/python.exe -m mypy --ignore-missing-imports --check-untyped-defs config implementations interfaces model mcp_service repositories utils workflows cli.py manage.py publish_policy.py
.venv/Scripts/python.exe -m compileall -q config implementations interfaces model mcp_service repositories utils workflows test cli.py manage.py publish_policy.py
.venv/Scripts/python.exe -m pip check
git diff --check
git diff --cached --check
```

全量 pytest **144 项通过**，mypy 检查 **89 个源文件通过**。独立加载、编译、依赖及 Git 差异检查通过。代码规范审查提出的固定字段名、代码区域和退款措辞已修正，规格审查未发现缺项。检查点列表读取出现 LangGraph 的未来版本反序列化兼容提示，实测版本读取和恢复正常。真实服务上的验证限于隔离数据，未执行部署切换，也未注入删列过程连接中断。

四项验收均已实测通过，无前置阻塞。分诊约定未定义完成状态，结论记录于此。

### 2026-11-25 开发库迁移

模型服务配置集中化重构任务结束后，按用户指示迁移开发库 `order_demo`。先执行 `SELECT VERSION()`，实测 MySQL **8.0.46**。使用仓库外的临时配置，通过 `test/launch_with_config.py` 调用新版 `manage.py init`，退出码为 0。迁移后确认订单表和退款表均无 `currency`。按主键顺序比较其余字段及退款提交记录，迁移前后的内容一致，共 5 笔订单、1 笔退款、0 笔提交。临时配置已删除。
