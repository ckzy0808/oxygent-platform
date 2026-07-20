# Storage、Memory、Trace 与 Prompt

## 1. 存储选择

`MAS.init_db()` 根据配置选择 ES 兼容实现：

- 配置 `es`：`JesEs`，包装官方同步 Elasticsearch Client，并通过线程执行；
- `storage.es_engine == "MemoryEs"`：纯内存；
- 其他情况：`LocalEs`，JSON 文件持久化到 `{cache.save_dir}/local_es_data`。

Redis 配置存在时使用 `JimdbApRedis`，否则使用进程内 `LocalRedis`。Vearch 仅在配置存在时创建，并用于工具描述向量检索。

`DBFactory` 是单例且只允许创建一种 class type；MAS 同时直接构造 MemoryEs、LocalRedis、JimdbApRedis 和 Vearch，因此它并不是统一的存储依赖容器。

## 2. ES 索引

`MAS.init_db()` 创建以下索引：

| 索引 | 写入来源 | 用途 |
| --- | --- | --- |
| `{app}_trace` | `BaseAgent._pre/_post_save_data` | 顶层 Trace、原始 payload、共享数据、结果、指标 |
| `{app}_node` | `Oxy._pre/_post_save_data` | 每个 Agent/LLM/Tool 节点、调用关系、状态、输入输出和 extra |
| `{app}_history` | `BaseAgent._post_save_data` | 按 session 保存 query/answer，供短期记忆读取 |
| `{app}_message` | `MAS.send_message`，可选 | SSE 消息；stream 分批合并为 `merged_stream` |
| `{app}_prompt` | `PromptManager` | 当前 Prompt |
| `{app}_prompt_history` | `PromptManager` | Prompt 版本归档 |
| `{app}_rating` | `routes.py` | 用户评分明细 |
| `{app}_rating_stats` | `routes.py` | Trace 评分聚合 |

索引名称只由 `Config.app.name` 隔离，没有 Project ID 或租户分区。

## 3. Trace 模型

一个顶层调用创建 `current_trace_id` 和 `group_id`。多轮请求用 `from_trace_id` 链接，读取前序 Trace 的 `group_id/group_data`。`root_trace_ids` 用于构造会话根集合。每个 Oxy 节点具有 `node_id/father_node_id/pre_node_ids/latest_node_ids/parallel_id/call_stack`，能够表达运行时调用树与部分并行依赖。

重启机制以 `reference_trace_id + restart_node_id + restart_node_order + input_md5` 查找旧节点：目标节点可使用用户覆盖输出，目标节点之前的匹配节点可重放。它是 Trace 重放，不是通用 Workflow checkpoint。

## 4. Memory

`schemas.Memory` 是进程内消息列表，提供追加、截断和 OpenAI 风格 dict 转换。`LocalAgent._get_history()` 从 `{app}_history` 查询历史问答并重建 user/assistant 消息；`ReActAgent` 在单次执行中维护独立 `react_memory`。

当前没有独立的长期记忆实体、语义记忆策略或 Project Artifact Memory。Vearch 当前服务于工具检索，不保存 Agent 会话记忆。

## 5. Trace 指标

`shared_data._metrics` 保存查询开始时间、first response time 和累积 Token Usage。单模型统计按 Provider 返回的 `model_name` 聚合。指标随着共享数据写入 Trace，但没有独立、可高效查询的调用统计表，也没有模型路由尝试明细。

建议目标 `ExecutionTrace` 增加 append-only 的 `ModelInvocation`：`project_id/workflow_id/task_id/role_id/agent_id/provider_id/model_id/attempt/latency/state/token_usage/error_class/routing_reason`。旧 Node 记录继续保留，用 Adapter 双写或异步投影。

## 6. Prompt 管理和热更新

`LocalAgent` 可设置 `use_live_prompt` 和 `prompt_key`，默认 key 为 `{agent_name}_prompt`。初始化与 `reload_prompt()` 通过 `get_dynamic_prompt()` 解析内容，失败时回退代码内静态 Prompt。

`PromptManager` 负责保存、查询、历史、回滚、搜索和缓存；`DynamicAgentManager` 维护 prompt key 到 Agent 的映射并调用 Agent reload；`VersionSyncCoordinator` 在 ES 可用时轮询版本变化并重载。日志显示 Redis Pub/Sub 同步尚未实现，未配置 ES 时跨实例一致性不保证。

`PromptOptimizer` 通过 MAS 中的一个 LLM 生成优化建议。Prompt API 和优化 API 当前属于同一无统一鉴权的 Router，应在产品化前纳入 Project/Role 权限。

## 7. 一致性与容量风险

- LocalEs 是单机 JSON 文件、内存缓存和延迟刷盘，适合本地开发，不适合多进程并发写和大规模查询。
- LocalRedis 是进程内 deque，多 worker/多实例不可共享。
- `shared_data/group_data/original_payload` 以 JSON 文本保存，缺少字段级加密、敏感信息清理和可查询 schema。
- Node/Trace/History 存在数据重复，写入由后台任务完成；虽然多轮调用会等待前一 Trace 后台任务，但进程崩溃仍可能丢最后一批写入。
- `stream_dict` 在 MAS 内存中聚合流，异常终止可能留下缓存项。
- ES schema 由启动时创建/覆盖 mapping 文件，缺少显式 schema version 和迁移工具。
- Project、Workflow、Task、Artifact、Approval 不应继续塞进 `shared_data`，应建立独立 Repository 和生命周期。

## 8. 保留策略

保留 `BaseEs` 接口、Node/Trace/History 格式和 LocalEs/MemoryEs 开发体验；新增领域 Repository 接口。第一阶段可用现有 ES abstraction 实现 Repository，后续再决定生产存储，不需要先引入 ORM 或新数据库框架。
