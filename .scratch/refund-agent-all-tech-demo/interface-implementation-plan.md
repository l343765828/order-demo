# Qwen 原生工具与 Schema 接口实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 Qwen3-8B 服务上提供退款 Agent 所需的原生工具往返和 json_schema，保留已验证的 Chat / JSON 行为。

**Architecture:** 使用现有 vLLM OpenAIServingChat、Qwen 模板和 Hermes parser 处理工具协议，Schema 使用同版本 vLLM 约束解码并在边界验证。服务只返回工具调用请求，退款工具由应用执行；不创建文本工具模拟协议。

**Tech Stack:** 已部署 Qwen/Qwen3-8B BF16、vLLM 0.10.2 V1、Ray Serve、Python、Pydantic、pytest；模型 revision、TP2/PP1 与 BGE 部署保持现有基线。

**Spec:** [已确认接口设计](integration-contracts.md)、[ADR-0003](../../docs/adr/0003-refund-agent-native-model-protocol.md)。本计划只覆盖模型接口与接入验收；完整退款 Agent 按 spec.md 另行实施。

## Global Constraints

- 路由保持 `/qwen3/v1/chat/completions`，模型名保持 `Qwen/Qwen3-8B`，非流式；默认关闭 thinking。
- 首轮工具选择支持 auto、none、指定函数；required 不作为 vLLM 0.10.2 V1 的已承诺能力，不静默降为 auto，不切换引擎。
- 保留总上下文8192、单序列、max_tokens默认1024且范围1–2048、请求体1MiB、最多64条消息、读取15秒、生成300秒。
- 保留普通 Chat、json_object、thinking 的已支持组合与错误语义；JSON / Schema 输出关闭 thinking。
- 保留已部署模型 revision 与 TP2 / PP1、Qwen和BGE的资源配额；不停止整个Ray Serve，不修改BGE。
- 包装层先按原始 JSON 校验可用字段、消息关联和服务额度，再用 vLLM 的 ChatCompletionRequest 解析一次。工具与 Schema 结果仍需应用侧业务校验。
- 本计划不编辑历史部署证据目录，不将测试或部署步骤写成已完成。代码实施前遵守目标仓库 AGENTS.md、文件过滤与Python注释技能；Python默认使用其 `.venv/Scripts/python.exe`。
- 集群命令固定使用 `D:/Redemption/Redemption/K8S/kubectl.exe` 和既定 kubeconfig；不记录凭证。首次连接只读 readyz；集群变更遵循目标仓库的当前明确授权要求。

## Review Focus

- assistant.content=null 的合法工具消息必须通过；截断的工具参数不能触发应用执行。
- tool_call_id 缺失、重复、悬空或与工具观察不匹配时，明确拒绝；不得伪造调用关联。
- required 或框架 any 别名不得无声改为 auto，避免调用方误以为调用已被强制执行。
- JSON Schema 类型正确不等于金额和归属正确；实际退款权限留在业务层。
- 工具请求取消、超时或生成失败后不得占住唯一序列，也不能影响BGE继续服务。

## 文件范围

以下新目录和文件均为计划创建，当前不存在也不表示已经实现：

- 新实现目录：`D:/Redemption/Redemption/K8S/qwen3-native-tools-schema-plan-20261118/`。
- 以用户提供部署目录的 `payload/qwen3_serve.py`、`payload/submit_qwen3.py`、配置和相关测试为可追溯基线，在新目录维护变更，不覆盖原部署结果和evidence。
- 服务实现：新目录 `payload/qwen3_serve.py`；保留已有服务名称和路由，复用原engine配置。
- 测试：新目录 `test_native_contract.py`、`test_schema_contract.py`、`test_http_compatibility.py` 与部署前既有回归测试。
- 测试fixture：新目录 `conftest.py` 复用基线的 service 模块加载方式，统一指向新 payload，不回退加载历史服务。
- 真实验收：新目录 `acceptance_native.py`，只执行模拟查询函数，不调用退款或其他写工具；结果写入新目录 `DEPLOYMENT-RESULT.md` 与 `evidence/`。

### Task 1：原生工具请求、响应与观察回传

**Interfaces:** 包装层的 `validate_request(body: object) -> dict` 返回校验和规范化后的 wire payload。HTTP 入口调用一次 vLLM `ChatCompletionRequest.model_validate`。服务使用 vLLM 生成的工具调用结构，应用以调用 ID 回传观察。

- [x] 核对部署版本的 `OpenAIServingChat` 签名和 Hermes parser。使用部署中的 tokenizer 做无推理检查。
- [x] 在 `test_native_contract.py` 覆盖原生工具、ID 关联、倒序观察、无效工具参数、空工具数组与截断。
- [x] 在新 `payload/qwen3_serve.py` 实现请求边界校验和观察排序，再复用 vLLM 官方类型解析。兼容 `max_tokens` / `max_completion_tokens`。
- [x] 配置 Hermes 解析器，在解析前拦截引擎的长度截断。
- [x] 运行本地工具契约与原请求回归测试。

至少固定以下测试名称与断言：`test_accepts_auto_tools` 检查模型、tools及auto不丢失；`test_accepts_linked_tool_messages` 保留原调用ID；`test_rejects_orphan_tool_result` 拒绝未关联观察；`test_required_is_explicitly_unsupported` 不降级执行。后两项可按以下契约直接编写：

```python
def test_rejects_orphan_tool_result(service):
    with pytest.raises(ValueError):
        service.validate_request({"messages": [
            {"role": "tool", "content": "{}", "tool_call_id": "missing"}
        ]})
```

### Task 2：Schema输出及失败边界

**Interfaces:** 无工具的意图、Plan/Replan请求使用 `response_format.type=json_schema`；JSON和Schema的最终content在返回前验证。DeepAgent工具循环采用Task 1的原生消息；客户端不可依赖未支持的required/any。

- [x] 在 `test_schema_contract.py` 覆盖必填字段、枚举、类型、额外字段、非有限数、截断、JSON Schema 引用与 thinking 组合。
- [x] 使用同版本 vLLM 约束解码。响应序列化一次后，用 `validate_response(response: dict, request: dict) -> None` 校验实际 HTTP 内容。
- [x] 保留 json_object 任意字段语义。无效 Schema 返回 400，无法通过 Schema 或结束校验的生成结果返回 422。
- [x] 用 LangChain SDK 的真实请求序列化与模拟 HTTP 传输验证 json_schema 和工具往返。tools 与 json_schema 分开调用。
- [x] 运行 Schema 和旧 Chat / JSON / thinking 回归。

测试名称至少包括 `test_schema_requires_declared_fields`、`test_schema_rejects_extra_fields`、`test_schema_rejects_invalid_enum`、`test_schema_rejects_truncated_output`、`test_json_object_keeps_arbitrary_fields`；失败生成不得返回可被当作成功的content。

### Task 3：HTTP回归与完整真实往返验收

**Interfaces:** 目标URL与模型名保持当前部署；测试工具采用 `get_demo_order` 只读函数，返回虚构订单事实。

- [x] 在 `test_http_compatibility.py` 覆盖 400、408、422、504 和兼容请求，保留原断连清理测试。
- [x] 编写 `acceptance_native.py`，运行时校验真实 tool_calls、同 ID 观察回传和最终回答，并保存 HTTP 证据。
- [x] 将 none、指定函数、Schema 截断、required 明确拒绝及旧 Chat / JSON / thinking 回归纳入验收脚本。
- [x] 用户授权后发布Qwen应用改造；原代码与配置哈希核对，BGE保持运行。
- [x] 真实往返及BGE并行健康/向量检查通过；断连后短请求成功，验收后近期 Worker 日志无 CUDA OOM 关键词。
- [x] 将实测范围、支持矩阵、错误语义和证据路径写入新部署记录（`D:/Redemption/Redemption/K8S/qwen3-native-tools-schema-plan-20261118/DEPLOYMENT-RESULT.md`）。

本地测试命令及依赖见发布候选说明（`D:/Redemption/Redemption/K8S/qwen3-native-tools-schema-plan-20261118/README.md`）：

```powershell
& 'D:/Redemption/Redemption/.venv/Scripts/python.exe' -m pytest 'D:/Redemption/Redemption/K8S/qwen3-native-tools-schema-plan-20261118/test_native_contract.py' 'D:/Redemption/Redemption/K8S/qwen3-native-tools-schema-plan-20261118/test_schema_contract.py' 'D:/Redemption/Redemption/K8S/qwen3-native-tools-schema-plan-20261118/test_http_compatibility.py' -q
```

期望全部通过且没有必需场景被skip；如果本地缺少所需协议依赖，先记录测试环境缺口，不能把测试收集失败改写为成功。真实GPU调用及vLLM解析必须在匹配部署版本的服务环境中完成；验收脚本仅调用明确目标URL和本地虚构工具。

## 执行与自查

2026-11-19，本地候选代码、两轴审查和验收脚本已完成。完整本地套件 80 项通过。集群内使用待发布方案所依赖的 vLLM 0.10.2、Hermes parser 和已部署 tokenizer 验证请求解析、观察渲染及 XGrammar Schema 编译。该检查未加载 GPU 推理引擎。线上切换和实际模型工具往返仍待完成。

2026-11-19，用户明确授权后完成线上切换。真实工具往返、Schema 和旧接口回归通过，详见部署记录与逐请求证据。
