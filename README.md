# order-demo 的 MySQL 存储

运行 `py -3 -m pip install -r order-demo/requirements-protocol.txt` 安装依赖。将 `order-demo/config/example.json` 复制为 `order-demo/config/local.json`，填写 MySQL、Qwen 连接信息和 MCP 读取凭据。MCP 凭据须为至少 32 个 ASCII 字符。该本地文件已加入忽略规则，请勿提交真实凭据。两个服务分别管理连接池，数据库连接使用 UTC。

在工作区根目录运行以下命令，先初始化数据库，再装载演示种子：

```powershell
py -3 order-demo/manage.py init
py -3 order-demo/manage.py seed
```

两条命令都可以重复执行，并保留已有数据。初始化会创建缺失的数据库对象，已有表结构不兼容时需另行迁移。

运行 `py -3 order-demo/test/verify_mysql.py` 进行隔离集成验证。脚本会创建并清理一个随机命名的测试库，保留 `order_demo` 中的现有数据。

首次运行前初始化 checkpoint 表，然后在独立终端启动 MCP 服务：

```powershell
py -3 order-demo/cli.py --init-checkpoints
cd order-demo
py -3 -m mcp_service.server
```

在工作区根目录的另一个终端运行 `py -3 order-demo/cli.py`，输入「查一下 DEMO-1001」。CLI、管理命令和 MCP 服务均支持 `--config <路径>` 指定其他 JSON 文件。MCP 服务代码位于 `mcp_service/`，订单业务位于 `mcp_service/orders/`，共享的订单请求和结果契约位于 `model/order_query.py`，查单流程数据模型位于 `model/order_flow.py`。

## 退款政策发布与检索

政策源位于 `order-demo/policy/refund-policy.md`。安装 `order-demo/requirements-protocol.txt` 后，在 `order-demo/config/local.json` 的 `policy` 部分填写 Milvus 地址、访问凭据和模型服务地址，字段格式见 `order-demo/config/example.json`。该本地文件已加入忽略规则，切勿提交真实凭据。MCP 服务和发布命令使用同一配置文件；不提供 `policy` 时，查单仍可运行，但政策发布和检索不可用。独立发布政策：

```powershell
py -3 order-demo/publish_policy.py
```

发布只管理专用集合的当前政策，重复执行使用固定条目标识，不删除历史版本。发布命令及政策验收脚本均支持 `--config <路径>` 指定其他 JSON 配置文件。MCP 服务读取同一配置文件。`search_policy` 的 `mode` 由 Host 选择，咨询先使用 `consultation`，必要时用 `formal` 补查一次。检索失败返回 `failed` 和空候选。

运行 `py -3 order-demo/test/verify_policy_mcp.py` 可通过独立 MCP 进程验证认证及真实检索。`py -3 order-demo/test/verify_policy_store.py` 会向政策集合添加并保留一条 `0.0-probe` 历史版本样本，验证当前版本过滤；请在验收环境执行。

在 CLI 输入「退款多久能到账？」可咨询通用政策，输入「DEMO-1001 能退吗？」可查询本人订单资格。政策回复附本轮核验的正式条款，资格依据 MCP 返回的实际订单状态。只问「能退吗」时，CLI 会追问订单编号。当前 CLI 支持查单和咨询，识别到明确办理请求时会提示本轮未提交。

可用 `py -3 order-demo/test/verify_policy_cli.py --config <路径>` 运行真实 CLI 咨询验收，覆盖通用政策、订单资格、意图对照和重排故障。脚本创建独立 MCP 服务和隔离会话，运行后清理测试会话和 checkpoint。
