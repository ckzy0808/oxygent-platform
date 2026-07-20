# 改造风险清单

## 1. 风险分级

| 级别 | 风险 | 真实证据 | 建议控制 |
| --- | --- | --- | --- |
| P0 | 凭证和请求数据泄漏 | RemoteLLM 实例持有明文 `api_key`；Web 记录完整 payload；FunctionTool 失败记录完整 arguments | Secret 引用、统一脱敏 Filter、禁止序列化密钥字段 |
| P0 | 高风险工具越权 | 顶层 user/callee 与 `MAS.call()` 不走内部 allow-list；存在 shell、Python exec、SSH、文件覆盖工具 | 外部认证、Project Workspace 边界、审批和 ExecutionGrant |
| P0 | 管理 API 无统一认证 | Prompt、脚本、上传、调试、Rating 和 `/call` 均在公共 Router | 先加可插拔 AuthN/AuthZ dependency，默认本地兼容 |
| P1 | Agent/模型/Provider/凭证耦合 | `LocalAgent.llm_model` 指向携带 URL、Key、真实 model name 的 LLM Oxy | 增加 Provider/Model Registry 与 Router Adapter |
| P1 | 无模型 fallback | 重试只发生在同一 Oxy 实例内部 | Policy 生成候选模型链，记录每次路由尝试 |
| P1 | MAS 职责过载 | 初始化、Registry、Storage、Trace、SSE、FastAPI、CLI、批处理集中于单类 | 小步抽取 Facade/Service，不改 public API |
| P1 | 多实例状态不一致 | LocalRedis、active_tasks、feedback queues、Prompt 本地版本均为进程状态 | 明确单进程开发模式；生产模式要求外部 Redis/ES |
| P1 | 共享字典竞态 | 并行请求共享 `shared_data/group_data` | 约定命名空间、不可变上下文与 Artifact Store |
| P1 | 路径边界不足 | 文件工具用字符串前缀判断 allowed_dir | 使用规范化路径包含关系并补回归测试 |
| P1 | Stdio MCP 环境泄漏 | 子进程继承整个 `os.environ` | 显式 env allow-list，凭证按工具授权注入 |
| P2 | Provider 判定脆弱 | HttpLLM 用 URL 子串和 API Key 是否存在判定 Gemini/OpenAI/Ollama | Provider Adapter 显式声明 protocol |
| P2 | Registry 重复 | MAS 名称表、OxyFactory creators、运行时 Agent type map 并存 | 引入只读描述 Registry，逐步作为单一来源 |
| P2 | 类型契约不一致 | `MAS.call()` 注解/文档称返回 OxyResponse，实际返回 output | 兼容保留并增加明确的新方法或修正文档/类型 |
| P2 | 事件无版本 | SSE payload 由多个实现直接拼 dict | 增加 event schema version 与契约测试 |
| P2 | Web 前端全局状态 | index.html 大量全局变量，残留无后端对应的 ws 逻辑 | UI 阶段前先稳定 API/事件，不在本阶段重构 UI |
| P2 | 存储 schema 无迁移 | 启动时创建多个索引，未见版本迁移层 | 增加 schema version 和幂等迁移命令 |
| P3 | Flow 隐式字段契约 | `Reflexion` 最终摘要引用 `self.llm_model`，类本身未声明该字段 | 在改造前补边界测试并显式化配置 |

## 2. 硬编码与重复实现

- `HttpLLM` 的 URL 拼接和 Provider 识别硬编码；流式解析在 HttpLLM、OpenAILLM、LiteLLM 等实现中重复。
- 各流式实现重复构造 `stream/stream_end` payload。
- `routes.py` 与 `MAS.start_web_service()` 共同依赖全局 MAS/ES 获取逻辑。
- Agent 类型、LLM 类型和危险类分别在多个静态映射中维护。
- 多个 Flow/Agent 自己实现计划、并行和最终摘要，没有统一 Task 状态模型。
- 应用 `applications/oxybank` 有独立配置、存储和认证体系；它是一个应用级实现，不应被误认为核心平台已有 Project/Role 能力。

## 3. 改造中的兼容风险

最容易破坏现有行为的点包括：

1. 改变 `OxyRequest` 的深拷贝和共享引用规则；
2. 改变 Agent 工具描述排序或许可列表，导致 LLM 决策变化；
3. 把 LLM Router 放到 `OxyRequest.call()` 外部，绕过 Node/Token/事件记录；
4. 修改 SSE 事件名或 content 字段，破坏现有 Web 和远程 `SSEOxyGent`；
5. 更改 Trace/Node 索引字段，破坏重启、历史、Rating 和前端播放；
6. 把 LocalAgent 的 `llm_model` 一次性替换成复杂对象，破坏示例和用户代码；
7. 在多 worker 下误以为 LocalRedis/内存任务字典具有跨进程一致性。

## 4. 安全处理原则

- API Key、Token、密码不进入领域对象的可序列化字段；只保存 Secret 引用。
- 日志先结构化再脱敏；Header 使用 allow-list，不记录 Authorization/Cookie。
- Code Workspace 默认拒绝工作区外访问，命令、网络、写文件和 Git 写操作分别授权。
- Artifact 内容与执行日志分离；敏感 Artifact 支持访问控制和保留期限。
- 审批是持久化决策，不使用进程内 feedback Queue 作为唯一事实来源。

## 5. 暂不处理

本阶段不改 Web UI、不替换 LocalEs、不统一 oxybank 应用内部架构、不引入新的代码 Agent 框架、不重写 MAS/Oxy/Agent。所有这些都应等待 Registry/Router/Execution Context 契约稳定后再评估。
