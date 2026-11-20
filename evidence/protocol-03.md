# 03 协议兼容性验收记录

2026-11-20 配置调整：当前 Qwen 连接信息使用 `order-demo/config/local.json`，格式见 `order-demo/config/example.json`。以下为 03 当时的历史验收记录。

最新验收见下文「完整复测」。前两轮记录保留用于追溯。

2026-11-19，工作机 Windows，Python 3.11.7。工单无直接阻塞票；02 的证据记录 `langgraph==1.2.22`、`langgraph-checkpoint==4.3.2`、`deepagents==0.7.38`，但 02 未调用 DeepAgent。本次隔离环境复用这些版本，增加 `langchain-openai==1.8.0`、`mcp==2.3.0`，`pip check` 无冲突。核心版本见 `order-demo/requirements-protocol.txt`；这只是候选组合，不是完成验收的锁定组合。

复跑：在隔离 Python 3.11 环境执行 `python -m pip install -r order-demo/requirements-protocol.txt`，然后分别运行 `python order-demo/test/verify_qwen_endpoint.py`、`python order-demo/test/verify_qwen_protocol.py`、`python order-demo/test/verify_mcp_protocol.py`。默认模型入口为定稿的 Qwen URL；可用 `QWEN_BASE_URL` 覆盖。模型服务若需要密钥，通过 `QWEN_API_KEY` 为 LangChain 探针提供；不要把凭据写入文件。探针工具无业务副作用，不连接数据库。

## 实际观察

- 当前 `GET /qwen3/health` 返回 200，模型 `Qwen/Qwen3-8B` / AsyncLLM；普通 Chat 请求 HTTP 200。健康不代表原生协议可用。
- `verify_qwen_endpoint.py` 分别在同一普通请求上单独添加字段：`tools`、`tool_choice`、`max_completion_tokens` 均 HTTP 400，返回请求含不支持字段；`json_schema` HTTP 400，返回 response_format 仅支持 text 或 json_object。均未通过；进程退出码 1。LangChain `bind_tools` 真实请求也返回 400。现有运行入口与定稿中 2026-11-19 上线结果不一致，不能据此判断实际部署版本或修改集群。
- `verify_mcp_protocol.py` 启动独立 Streamable HTTP 服务，发现 `probe_echo`，检查输入字段和必填项，通过 SDK 底层 `client.session.call_tool` 调用，校验结构化结果及进程 ID，退出 Client 上下文。一次输出：`host_pid=139192 server_pid=135604 launcher_pid=114072 trace_id=067b2ee9-1495-4775-965f-1b7b97c1ed65 structured_result=valid`、`client_context_exited`。Windows venv 启动器 PID 与实际 Python 服务 PID 不同；两者均在此次运行结束后退出。此记录没有 MCP JSON-RPC 请求标识与模型调用 ID，不满足完整关联验收。
- `python -m mypy --ignore-missing-imports --check-untyped-defs` 对四个探针文件通过；`compileall` 通过；`pip check` 通过。全仓库 `python -m pytest -q` 在收集时报 7 项错误，包括不同演示目录的同名测试模块冲突及缺少 `langgraph.checkpoint.sqlite`，未运行全套测试。

## 首轮待办（历史记录）

恢复或指向满足定稿协议的实际 Qwen 服务后，重跑并扩展探针：官方 DeepAgent 子任务、真实调用 ID 和观察回传、具名 Schema、非法参数及额外字段拦截、指定函数及拒绝 required/any。还需要记录模型调用 ID、MCP JSON-RPC 请求 ID 与 trace ID 的关联；服务端计数并注入响应丢失、断连、协议头异常、SDK 自动追加回合，证明一次意图在底层和重连路径均不重发。当前 MCP 样例仅证明一次正常调用，并不证明无重发或写调用安全，不能作为退款执行客户端使用。

## 2026-11-19 Qwen 修复后复测

使用 Python 3.11.7、LangGraph 1.2.22、DeepAgent 0.7.38、langchain-openai 1.8.0、MCP 2.3.0。`verify_qwen_endpoint.py` 的五个请求均返回 HTTP 200；`verify_qwen_protocol.py` 得到真实工具调用、同一 ID 的观察回传、后续回复及 `ProbeResult(label='blue', count=3)`。

首次运行 `verify_deepagent_protocol.py` 时，真实子任务已经通过 MCP 取回观察，但 Qwen 拒绝 LangChain 生成的 assistant 消息 `name` 字段，返回 HTTP 400 `assistant 工具消息无效`。新增 `qwen_client.py`，移除 assistant 消息的可选 name，并拒绝 required/any。再次运行得到真实 `task` 委派、`probe_echo` 结果及最终回复。一次关联记录如下：模型调用 ID `chatcmpl-tool-9a47d4d936df43beb99bdba6f4c98bf7`，MCP JSON-RPC ID `3`，trace ID `68f035cd-8484-40b6-9854-359003fe09bd`，服务端 PID `139336`。通过 `client.session.call_tool` 调用，客户端上下文退出。完整严格 Schema 比对、无效参数及结果的覆盖仍需补齐。

`verify_mcp_timeout.py` 启动独立服务，服务端记录调用后延迟，客户端得到 MCP -32001 超时，等待后重新连接并列出工具；trace `2b063ad5-61cb-4f32-bdf4-9f7aeab40922` 在服务端执行记录中出现一次。该探针只覆盖服务端收到请求后的超时及新连接，不覆盖协议头异常、断连其他时序、SDK 自动追加回合及底层所有重连路径，不能据此认定全部不重发。

`pytest order-demo/test/test_qwen_client.py -q` 1 passed；相关文件 mypy 通过；`pip check` 通过。全仓库 `pytest -q` 收集阶段仍有 7 项错误，同名模块冲突及缺少 `langgraph.checkpoint.sqlite`。验收尚未全部完成。

## 2026-11-19 完整复测

在 Windows Python 3.11.7 上运行，使用与 02 相同的 LangGraph 1.2.22、checkpoint 4.3.2、DeepAgent 0.7.38，配合 LangChain 1.5.0、LangChain OpenAI 1.8.0、OpenAI SDK 3.55.1、Pydantic 2.14.3、MCP 2.3.0。独立 MCP 进程使用 Uvicorn 0.54.0，实际协商 Streamable HTTP 协议 `2026-07-28`。

从仓库根目录，用隔离环境执行以下命令。默认使用定稿中的 Qwen 地址。可用 `QWEN_BASE_URL` 和 `QWEN_API_KEY` 配置连接。故障测试与正常 MCP 探针仅连接本机临时进程，服务端日志和样例数据在临时目录。

```text
python -m pip install -r order-demo/requirements-protocol.txt pytest==9.2.0
python order-demo/test/verify_qwen_protocol.py
python order-demo/test/verify_deepagent_protocol.py
python order-demo/test/verify_mcp_protocol.py
python -m pytest order-demo/test/test_mcp_protocol.py order-demo/test/test_qwen_client.py -q -s
```

- 真实 Qwen 使用 `auto` 返回原生工具请求，`none` 接收同 ID 的观察并返回最终消息，指定函数返回该函数的原生请求。客户端拒绝 `required` 与 `any`。关闭 thinking 后独立请求 `json_schema`，返回 `ProbeResult(label='blue', count=3)`。具名结果校验拒绝额外字段和错误类型。服务端对未知请求字段、未定义的指定函数返回 400，对截断的 Schema 结果返回 422。
- Host 在工具发现时完整比对 `EchoInput` 和 `EchoResult` 的 Schema，分别拒绝服务器修改的额外属性规则和数字约束。错误参数类型、额外字段和缺失必填字段都在发送前拒绝，独立进程收到的工具 POST 数为零。服务端在一次调用后返回错误类型的结构化结果时，客户端明确拦截。
- MCP Client 在图运行期间保持打开。退出后读取其会话会报错，故障测试同时验证显式管理的 HTTP 连接关闭。探针使用无状态 HTTP，因此生命周期检查不依赖 DELETE。

每个故障单独启动服务端。服务进程在 SDK 处理前记录 JSON-RPC POST，在工具处理器中另记执行次数，重连后再列出工具并核查记录。

| 故障 | 受控接口 POST / 执行 | 观察 |
| --- | --- | --- |
| 收包后超时 | 1 / 1 | SDK 返回 `-32001`，新连接只列工具 |
| 处理后丢失响应 | 1 / 1 | 无事件 ID 的响应流结束，SDK 返回 `CONNECTION_CLOSED`（`-32000`） |
| 收到事件 ID 后断连 | 1 / 1 | SDK 发出 1 次含 `Last-Event-ID` 的 GET，收到原响应 |
| 处理完成后返回同源 307 | 1 / 1 | 受控 HTTP 客户端拒绝跳转，默认配置对照重新发出 POST，处理器共执行 2 次 |
| 请求头与参数不符 | 1 / 0 | 实际收到 `HEADER_MISMATCH`（`-32020`）。高层对照重新发现工具并发送第二个 POST |
| 服务端要求补充输入 | 1 / 1 | 底层明确拒绝继续回合。高层对照携带原 `requestState` 发第二个 POST |
| 结构化结果类型错误 | 1 / 1 | SDK 拒绝服务端结果 |

Host 使用 `controlled_mcp_http()` 管理 HTTP 连接，设置 `max_redirects=0`。MCP 2.3.0 的传输层会自行跟随同源 307/308，单独设置 `follow_redirects=False` 无法关闭这条路径。`bind_mcp_tool()` 要求一次发现返回唯一工具及完整 Schema，绑定后通过 `Client.session.call_tool` 发送。获取原响应的 SSE GET 和主动新建连接的工具发现都经过实测。该接口覆盖此处测试的单次传输行为，跨进程重启后的业务去重及退款状态恢复仍由后续工单处理。原 `verify_mcp_timeout.py` 已并入 `test_mcp_protocol.py`。

本轮真实调用样例，Host PID `145156`，MCP Server PID `143540`。DeepAgent 使用模型调用 `chatcmpl-tool-222bef3334634092948c726b5b723182` 委派给 `echo-worker`。子任务发出 `probe_echo(value="blue")`，Host 从独立 MCP 进程取得结构化观察并原样回传。检查器在实际发送给 Qwen 的 HTTP 请求体中验证了工具名称、观察正文和相同的 `tool_call_id`。

```text
model_tool_call_id = chatcmpl-tool-5d821b2fe5bf4661a137a3725fc6d004
qwen_observation_id = chatcmpl-tool-5d821b2fe5bf4661a137a3725fc6d004
mcp_json_rpc_id = 3
trace_id = 8d2565ab-3997-4078-b122-aea5219d2b29
tools/call arguments = {"value":"blue","trace_id":"8d2565ab-3997-4078-b122-aea5219d2b29"}
mcp_observation = {"value":"blue","trace_id":"8d2565ab-3997-4078-b122-aea5219d2b29","server_pid":143540,"request_id":"3"}
tools/call POST = 1
final_reply = The echo-worker successfully echoed the word "blue".
```

相关 pytest 9 项通过，10 个相关 Python 文件的 mypy 检查及 `compileall`、`pip check` 通过。全仓库 `python -m pytest -q --tb=short` 因其他演示的同名模块冲突和缺少 `langgraph.checkpoint.sqlite` 仍产生 7 项收集错误。
