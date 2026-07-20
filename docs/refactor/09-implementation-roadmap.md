# 渐进式实施路线图

## 1. 迁移分类

| 现有模块 | 策略 | 说明 |
| --- | --- | --- |
| `Oxy.execute` 生命周期 | 直接保留 | 是统一追踪、重试、Hook 和事件基础 |
| `OxyRequest/OxyResponse` | 保留并扩展兼容上下文 | 新 ID 放入可选 execution context，不破坏旧字段 |
| Chat/ReAct/Parallel/Workflow Agent | 暂时不动 | 通过 Router/Execution Adapter 接入新架构 |
| `BaseLLM` 与各 LLM 实现 | 包装成 Adapter | Provider Registry 描述它们，先不重写流式实现 |
| `LocalAgent.llm_model` | 保留接口但改变指向 | 从具体 LLM 名逐步指向 ModelRouter Oxy 名 |
| MAS 名称注册表 | 保留并包装 | Agent Registry 提供描述和版本，MAS 仍负责运行实例 |
| `MAS.start_web_service` | 保留接口，逐步抽取实现 | 先提取 App Factory，再拆管理服务 |
| SSE 事件 | 直接保留并版本化 | 旧事件字段保持兼容 |
| Trace/Node/History | 保留并投影 | 新 ExecutionTrace/ModelInvocation 与旧索引并存 |
| LocalEs/MemoryEs/JES | 直接保留 | 新 Repository 先适配 BaseEs |
| LocalRedis/Redis | 直接保留 | 标明 LocalRedis 只支持单进程开发 |
| Vearch 工具检索 | 暂时不动 | 不等同于 Artifact/Memory Store |
| Prompt Manager | 保留接口但加 Project/权限包装 | 热更新能力继续复用 |
| `OxyFactory`/运行时类型映射 | 最终收敛 | 过渡期由 Registry 汇总，避免一次性删除 |
| 进程内 feedback Queue | 包装成 Adapter，最终不作事实来源 | Approval 必须持久化 |
| Web UI | 暂时不动 | API 和事件稳定后再做 Chat/Projects/Code Workspace |
| 遗留 `ws` 前端逻辑 | 最终废弃 | 当前后端没有 WebSocket endpoint |

## 2. 分阶段路线

### Phase 0：基线与安全护栏

- 固化本审计文档和架构决策记录；
- 增加 Secret/日志脱敏测试；
- 为 `OxyRequest.call`、SSE schema、Trace/Node 字段和 Agent→LLM 调用补契约测试；
- 修复文件路径边界判断和高风险工具默认暴露文档；
- 明确本地单进程与生产多实例模式。

验收：现有单元测试、格式检查、最小启动不回归；日志测试证明 Authorization、Cookie、API Key 不出现。

### Phase 1：Registry 与 Model Router（推荐第一个代码改造 PR）

- 新增纯领域 DTO：`ProviderSpec`、`ModelSpec`、`ModelCandidate`、`RoleModelPolicy`；
- 新增内存实现的 Provider/Model/Policy Registry 接口；
- 新增 `ModelRouter(BaseLLM)` Adapter；
- Router 按候选顺序调用现有 LLM Oxy，并记录 routing attempt；
- Agent 仍只配置一个 `llm_model` 字符串；示例通过把它指向 Router 验证兼容；
- 不增加数据库表、不改 UI、不改 Chat/ReAct 核心类。

验收：主选成功不触发备用；可重试失败触发备用；非可重试失败不盲切；每次尝试记录模型/Provider/耗时/Token/错误；关闭 Router 后旧配置行为完全不变。

### Phase 2：Project、Role 与 Agent Definition

- 建立 Project Repository、Role Registry、Agent Definition/Instance；
- Role 使用数据模板而非硬编码业务枚举；
- 引入 `ExecutionContext(project_id, role_id, agent_instance_id)`；
- 通过 Adapter 将 Project Agent 实例注册到 MAS；
- Prompt key 和工具策略增加 Project 作用域。

### Phase 3：Artifact Store 与 Workflow/Task DAG

- 建立 WorkflowDefinition/Run、Task、Dependency 和状态机；
- Artifact Store 支持版本、类型、来源和 Review 状态；
- 现有 Workflow/Parallel/PlanAndSolve 作为 executor 或迁移参考，不立即删除；
- 支持幂等调度、失败重试、取消和从持久化状态恢复。

### Phase 4：Approval 与 Code Workspace

- 将 feedback 适配成 Approval 交互通道；
- 建立持久化审批、风险动作和审计日志；
- 接入 Git 仓库、受限工作树、Diff、测试与 Review Artifact；
- 所有写操作通过 Project Policy 和 Approval Service。

### Phase 5：API 与 UI

- 抽取 `create_app(mas, services)`；
- 提供版本化 Projects/Workflow/Artifact/Workspace API；
- 现有 Chat API 和 SSE 保持兼容；
- 最后实现 Chat、Projects、Code Workspace 三个前端入口。

## 3. 推荐第一个 PR

推荐先做“模型目录与无侵入路由骨架”，而不是 Project CRUD。

范围：

- Registry/Policy 的协议和内存实现；
- `ModelRouter` 作为新 LLM 类型；
- 失败分类和 routing attempt 数据结构；
- MockLLM 驱动的单元/集成测试；
- 一个不含真实凭证的示例配置。

不在范围：

- UI、数据库迁移、Project 页面、Artifact、DAG、Git、审批；
- 修改所有 Agent；
- 替换 HttpLLM/OpenAILLM/LiteLLM；
- 改变现有 SSE 事件和 Trace schema。

主要风险：Router 内部调用产生递归、重复重试导致延迟放大、Token 重复累计、错误分类不正确、候选 LLM 权限校验不一致。应通过明确的内部调用标记、总尝试上限和每候选测试控制。

## 4. 建议 PR 验收标准

1. 未配置 Router 时，现有 Agent 与 LLM 行为、测试和事件不变。
2. Agent 只需把 `llm_model` 改为 Router 注册名即可启用策略。
3. 主选成功时只执行一次 Provider 调用。
4. 主选出现配置为可回退的错误时按顺序调用备用。
5. 认证/输入错误不会自动回退，除非策略显式允许。
6. 每次尝试有独立 attempt 记录，最终 Token 汇总不重复。
7. Router 不记录 API Key、Authorization、Cookie 或完整敏感 Header。
8. 并发请求之间候选状态和指标隔离。
9. 单元测试、Ruff format check 和最小 MAS 启动通过。

## 5. 本阶段验证记录

2026-07-20 使用仓库本地 Python 3.10.20 环境执行：

```bash
.conda-env/bin/python -m pytest tests/unittest -q
.conda-env/bin/python -m ruff format --check .
.conda-env/bin/python -m ruff check .
.conda-env/bin/python -m compileall -q oxygent
git diff --check
```

结果：

- 单元测试首次在受限网络中为 `339 passed, 13 skipped, 5 failed`；5 个失败全部来自 `tests/unittest/test_tool/test_train_ticket_tools.py` 对 12306 的真实网络访问。允许联网后单独复跑为 `5 passed`，因此完整单元测试基线为 `344 passed, 13 skipped`。
- `compileall` 和 `git diff --check` 通过。
- Ruff format check 未通过：仓库已有 40 个 Python 文件需要格式化；本阶段未批量修改这些无关文件。
- Ruff static check 未通过：仓库已有 132 个问题，主要为未使用导入/变量、非顶部导入、单行多语句和裸 `except`；本阶段仅记录基线。
- 使用 `MemoryEs + LocalRedis + MockLLM + ChatAgent` 的程序化 MAS 调用返回 `MAS_SMOKE_OK`。
- 临时在 `127.0.0.1:18080` 启动 FastAPI/Uvicorn，`POST /chat` 返回 HTTP 200 和 `WEB_SMOKE_OK`；随后主动停止服务。验证未使用或输出真实凭证。

注意：CI 当前执行的是会直接改写文件的 `ruff format .`，不是 `ruff format --check .`，因此 CI 配置本身不会把格式漂移作为失败门禁。建议在单独 PR 中改为 check 模式并先清理现有格式基线。
