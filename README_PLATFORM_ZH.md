# OxyGent 多角色、多模型 Agent 平台

本仓库是在开源 OxyGent 基础上扩展的项目中心化、多角色、多模型 Agent 协作平台。它保留了 OxyGent 原有的 Chat、Agent、Tool、Memory、Web、CLI、批处理、MCP 和 SSE 能力，并增加了项目工作区、结构化角色工作流、模型路由、代码实现、真实验证、独立审查和人工审批能力。

> 当前最终分支：`main`  
> 参考提交：`4d15840`  


## 1. 主要能力

- 保留 OxyGent 原有通用对话、Agent、Tool、MCP 和流式输出能力；
- Projects 项目中心及已有项目导入；
- Product Manager、Solution Architect、Technical Lead、Reviewer 四角色顺序协作；
- 每个角色独立配置 Provider、API Key、模型和路由策略；
- OpenAI-compatible、OpenAI Responses、Gemini 和 Ollama Provider Adapter；
- RequirementSpec、ArchitectureDecision、TaskGraph、ReviewReport 等结构化 Artifact；
- Provider 健康状态、模型 fallback、路由原因、调用追踪和 Token 统计；
- 项目文件夹或 ZIP 上传及源码分析；
- 基于 Aider 的代码生成和代码迭代；
- 修改文件内容、代码变化和验证结果展示；
- Reviewer 三级审查：完全通过、基本合格、需要修改；
- 审查意见和真实验证错误一键返回 Aider；
- 真实执行 pytest、Maven、npm 等固定参数验证命令；
- 人工 Request Revision、Approve、Apply、Download 和 Discard；


## 2. 推荐环境

### 2.1 macOS/Linux

- Python 3.10；
- PyCharm；
- Conda 或 Miniconda；
- Git；
- Aider 0.86.2；
- Chrome 或 Edge。

按目标项目需要额外安装：

- Java 项目：JDK 17、Maven；
- 前端项目：Node.js 20/22、npm；
- Python 项目：pytest 和目标项目自身依赖。

### 2.2 Windows

为了完整支持 Aider、MCP、进程超时终止和真实验证，Windows 推荐使用：

```text
Windows 10/11 + WSL2 Ubuntu + Windows 版 PyCharm + WSL Python 解释器
```

当前部分子进程管理逻辑采用 POSIX 进程组。原生 Windows Python 可以启动部分能力，但要获得更接近 macOS/Linux 的完整行为，建议使用 WSL2。

## 3. 获取仓库

当前仓库为公开仓库。

### 3.1 PyCharm 克隆

在 PyCharm 欢迎页选择 `Get from VCS`，填写：

```text
https://github.com/ckzy0808/oxygent-platform.git
```

通过浏览器登录有权限的 GitHub 账号，然后完成 Clone。

### 3.2 命令行克隆

```bash
git clone https://github.com/ckzy0808/oxygent-platform.git
cd oxygent-platform
git branch --show-current
git log -1 --oneline
```

当前分支应为 `main`。

## 4. 创建 Python 3.10 环境

不要复制其他电脑上的 `.conda-env` 或 `.venv`。虚拟环境必须在新电脑重新创建。

### 4.1 Conda

```bash
conda create --prefix ./.conda-env python=3.10 -y
conda activate ./.conda-env
python --version
```

### 4.2 PyCharm 解释器

进入：

```text
Settings → Project → Python Interpreter → Add Interpreter
```

选择项目中的解释器：

macOS/Linux/WSL：

```text
项目目录/.conda-env/bin/python
```

原生 Windows：

```text
项目目录\.conda-env\python.exe
```

## 5. 安装依赖

在项目根目录的 PyCharm Terminal 中执行：

```bash
conda activate ./.conda-env
python -m pip install --upgrade --force-reinstall pip setuptools wheel
python -m pip install -r requirements.txt
python -m pip install aider-chat==0.86.2
```

检查：

```bash
python -m aider --version
python -c "import oxygent; import oxygent.platform; print('OxyGent platform import OK')"
```

如果出现 `_distutils_hack` 错误，重新执行：

```bash
python -m pip install --upgrade --force-reinstall pip setuptools wheel
```

> `requirements.txt` 当前未直接声明 Aider，因此必须单独安装 `aider-chat==0.86.2`。

## 6. 安装真实验证工具

按需要检查：

```bash
git --version
node --version
npm --version
java -version
mvn -version
```

Ubuntu/WSL 可安装：

```bash
sudo apt update
sudo apt install -y git curl build-essential openjdk-17-jdk maven
```

Node.js 建议通过 NVM 安装。Windows 修改 PATH 后，需要完全退出并重新打开 PyCharm。

## 7. API Key 安全配置

### 7.1 安全原则

严禁把真实 API Key、Token 或密码写入：

- `.env` 或示例配置并提交到 Git；
- Python、JavaScript 或 HTML；
- README、测试 fixture 或截图；
- Provider 普通配置对象；
- Git 提交信息或运行日志。

如果 Key 曾出现在聊天、代码、截图或 Git 历史中，应立即撤销并生成新 Key。

### 7.2 当前凭证处理方式

平台保存的是凭证引用，而不是明文 Key：

```text
ProviderProfile
→ credentialReference
→ env:ENVIRONMENT_VARIABLE
→ EnvironmentCredentialResolver
→ 运行时读取真实 Key
```

### 7.3 不要只创建 `.env`

仓库中提供了：

```text
examples/platform/.env.multi_role.example
```

但当前 `projects_web_demo.py` 不会自动调用 `load_dotenv()`。仅创建根目录 `.env` 不能保证被读取。推荐在 PyCharm Run Configuration 的 `Environment variables` 中配置。

## 8. 四角色共用一个模型

首次运行推荐使用同一个 Provider、Key 和模型：

```text
DEFAULT_LLM_API_KEY=替换为自己的新Key
DEFAULT_LLM_BASE_URL=替换为真实API地址
DEFAULT_LLM_MODEL_NAME=替换为真实模型名称
DEFAULT_LLM_PROVIDER_TYPE=openai-compatible
```

启动时平台会将同一模型映射给：

```text
Product Manager
Solution Architect
Technical Lead
Reviewer
```

四角色共用同一 Provider 时，Reviewer 不能排除生产者 Provider：

```text
OXYGENT_REVIEWER_EXCLUDE_PRODUCER_PROVIDER=0
```

### Provider 类型

`openai-compatible` 适用于 OpenAI Chat Completions 兼容接口，Base URL 通常是 API 根地址，例如 `https://host/v1`。

`openai-responses` 适用于 Responses API。当前 Adapter 会直接向配置的完整 `baseUrl` 发送 POST 请求。

常见错误：

```text
400：协议、模型名称或参数不匹配
401/403：Key 无效或没有权限
404：Base URL 层级错误
429：额度或速率限制
Timeout：网关或模型响应过慢
```

## 9. 四角色使用不同模型

先启用真实工作流：

```text
OXYGENT_ENABLE_REAL_WORKFLOW=1
```

Product Manager：

```text
OXYGENT_PM_PROVIDER_ID=pm-provider
OXYGENT_PM_PROVIDER_TYPE=openai-compatible
OXYGENT_PM_BASE_URL=https://provider-a.example.com/v1
OXYGENT_PM_API_KEY=替换为自己的Key
OXYGENT_PM_MODEL=模型名称
OXYGENT_PM_TIMEOUT=120
```

Solution Architect：

```text
OXYGENT_ARCHITECT_PROVIDER_ID=architect-provider
OXYGENT_ARCHITECT_PROVIDER_TYPE=openai-compatible
OXYGENT_ARCHITECT_BASE_URL=https://provider-b.example.com/v1
OXYGENT_ARCHITECT_API_KEY=替换为自己的Key
OXYGENT_ARCHITECT_MODEL=模型名称
OXYGENT_ARCHITECT_TIMEOUT=120
```

Technical Lead：

```text
OXYGENT_LEAD_PROVIDER_ID=lead-provider
OXYGENT_LEAD_PROVIDER_TYPE=openai-compatible
OXYGENT_LEAD_BASE_URL=https://provider-c.example.com/v1
OXYGENT_LEAD_API_KEY=替换为自己的Key
OXYGENT_LEAD_MODEL=模型名称
OXYGENT_LEAD_TIMEOUT=180
```

Reviewer：

```text
OXYGENT_REVIEWER_PROVIDER_ID=reviewer-provider
OXYGENT_REVIEWER_PROVIDER_TYPE=openai-compatible
OXYGENT_REVIEWER_BASE_URL=https://provider-d.example.com/v1
OXYGENT_REVIEWER_API_KEY=替换为自己的Key
OXYGENT_REVIEWER_MODEL=模型名称
OXYGENT_REVIEWER_TIMEOUT=120
OXYGENT_REVIEWER_EXCLUDE_PRODUCER_PROVIDER=1
```

如果 Reviewer 与其他角色共用 Provider，将最后一项设为 `0`。

## 10. 配置 PyCharm 启动项

进入：

```text
Run → Edit Configurations → Add New Configuration → Python
```

填写：

```text
Name: OxyGent Real Four-Role GPT
Script path: 项目目录/examples/platform/projects_web_demo.py
Working directory: 项目根目录
Python interpreter: 项目目录/.conda-env/bin/python
```

推荐环境变量：

```text
PYTHONUNBUFFERED=1
OXYGENT_PROJECT_DEMO_PORT=18086
OXYGENT_CODE_WORKSPACE_ROOT=/tmp/oxygent-code-worktrees

DEFAULT_LLM_API_KEY=替换为自己的新Key
DEFAULT_LLM_BASE_URL=替换为真实API地址
DEFAULT_LLM_MODEL_NAME=替换为真实模型名称
DEFAULT_LLM_PROVIDER_TYPE=openai-compatible

OXYGENT_AIDER_PROVIDER_TIMEOUT_SECONDS=300
OXYGENT_AIDER_TIMEOUT_SECONDS=420
OXYGENT_AIDER_REASONING_EFFORT=low
OXYGENT_SEED_DEMO_DATA=0
```

原生 Windows 可将工作区改为：

```text
OXYGENT_CODE_WORKSPACE_ROOT=C:\OxyGentData\code-workspaces
```

`Working directory` 必须是项目根目录，否则可能出现：

```text
ModuleNotFoundError: No module named 'oxygent.platform'
```

## 11. 启动网站

在 PyCharm 右上角选择 `OxyGent Real Four-Role GPT`，点击 Run。

控制台正常应显示：

```text
Application startup complete
```

如果端口配置为 18086，访问：

| 页面 | 地址 |
| --- | --- |
| Chat | <http://127.0.0.1:18086/web/index.html> |
| Projects | <http://127.0.0.1:18086/web/projects.html> |
| Code | <http://127.0.0.1:18086/web/code.html> |
| Files | <http://127.0.0.1:18086/web/files.html> |
| Agents | <http://127.0.0.1:18086/web/agents.html> |
| Models | <http://127.0.0.1:18086/web/models.html> |
| Workflows | <http://127.0.0.1:18086/web/workflows.html> |
| Insights | <http://127.0.0.1:18086/web/insights.html> |
| Settings | <http://127.0.0.1:18086/web/settings.html> |

不要通过 PyCharm 的 `localhost:63342` 静态服务器打开 HTML，否则后端 API 不存在，页面可能显示 `Failed to fetch`。

## 12. 标准使用流程

### 12.1 新建项目

1. 打开 Projects；
2. 创建项目并输入产品 Idea；
3. 启动四角色工作流；
4. Product Manager 生成 RequirementSpec；
5. Solution Architect 生成 ArchitectureDecision；
6. Technical Lead 生成 TaskGraph；
7. Reviewer 生成 ReviewReport；
8. 在 Artifacts 和 Workflows 查看结构化结果。

```text
Idea
→ Product Manager
→ RequirementSpec
→ Solution Architect
→ ArchitectureDecision
→ Technical Lead
→ TaskGraph
→ Reviewer
→ ReviewReport
```

### 12.2 导入已有项目

上传前建议排除：

```text
.git/
node_modules/
target/
dist/
build/
.venv/
.idea/
coverage/
大型二进制文件
```

在 Projects 上传文件夹或 ZIP，等待文件统计和项目分析，然后输入改造需求并启动完整工作流。

### 12.3 Aider 代码实现

```text
RequirementSpec + ArchitectureDecision + TaskGraph + ReviewReport
→ Code Stage
→ Aider
→ 本地 Aider Proxy
→ Technical Lead 模型
→ 修改隔离项目副本
→ 网页显示生成或修改后的代码
```

Aider 默认使用 Technical Lead 的模型配置，不需要单独配置另一个 Key。

建议使用明确需求：

```text
目标：增加用户名模糊搜索。

验收标准：
1. 支持按用户名查询；
2. 空输入返回全部数据；
3. 不改变现有鉴权逻辑；
4. 增加对应测试。

允许修改：backend/src/main、frontend/src
禁止修改：数据库密码、认证密钥、部署配置。
```

## 13. Reviewer 审查

审查结果分为：

```text
绿色：完全通过
黄色：基本合格，可由人工判断是否继续
红色：需要修改
```

红色或人工决定修改时，可以把结构化审查意见返回 Aider。Aider 应基于上一次产出的代码继续迭代，而不是重新从原始项目开始。

## 14. 真实运行验证

平台通过固定 argv 执行真实命令并读取退出码，例如：

```text
["python", "-m", "pytest", "-q"]
["mvn", "test"]
["npm", "run", "build"]
```

结果判断：

```text
exit code = 0：通过
exit code != 0：失败
timeout：超时
缺少工具：blocked/infrastructure
```

常用命令：

```bash
python -m pytest -q
mvn test
mvn package
npm install
npm run build
```

如果验证失败，可以将真实 stdout、stderr 和退出码返回 Aider，让其基于当前修改版本继续修复。网络下载失败、Maven Central 中断或 npm registry 不可用通常属于基础设施问题，不应直接认定为代码错误。

## 15. 审批流程

推荐顺序：

```text
代码实现
→ 人工查看代码
→ 真实运行验证
→ Reviewer 独立审查
→ Request Revision 或 Approve
→ Apply / Download / Discard
```

`Approve changes` 和 `Apply to branch` 是两个独立动作。Approve 表示人工认可，Apply 才表示应用结果。

## 16. Token 统计

每次模型调用记录：

- Project、Task、Run；
- Role、Agent；
- Provider、Model；
- Input Tokens、Output Tokens；
- Latency、Status、Failure Reason；
- Fallback Used、Created At。

模型返回标准 `usage` 时优先使用真实 Usage；服务商不返回 Usage 时可能采用估算。Aider 通过平台代理调用模型，因此也进入 Token 记录。

## 17. 测试

完整测试：

```bash
python -m pytest
```

平台核心测试：

```bash
python -m pytest \
  tests/unittest/test_platform_credentials.py \
  tests/unittest/test_platform_routing.py \
  tests/unittest/test_platform_provider_adapters.py \
  tests/unittest/test_platform_control_plane.py \
  tests/unittest/test_platform_projects.py \
  tests/unittest/test_workflow_runtime.py \
  tests/unittest/test_workflow_artifact_parsing.py \
  tests/unittest/test_code_stage.py \
  tests/unittest/test_code_workspace.py \
  tests/unittest/test_diff_verification.py \
  tests/unittest/test_approval_lifecycle.py \
  tests/unittest/test_token_metering.py
```

最小检查：

```bash
python -c "import oxygent; import oxygent.platform; print('OK')"
python -m aider --version
python examples/platform/projects_web_demo.py
```

## 18. 常见问题

### 端口被占用

macOS/Linux：

```bash
lsof -nP -iTCP:18086 -sTCP:LISTEN
```

Windows：

```powershell
netstat -ano | findstr :18086
```

也可以修改：

```text
OXYGENT_PROJECT_DEMO_PORT=18087
```

### 页面显示 Failed to fetch

- 确认 PyCharm 后端仍在运行；
- 确认使用 `127.0.0.1:18086`；
- 不要使用 `localhost:63342`；
- 检查浏览器端口与后端端口是否一致。

### Aider 一直转圈

```text
OXYGENT_AIDER_PROVIDER_TIMEOUT_SECONDS=300
OXYGENT_AIDER_TIMEOUT_SECONDS=420
OXYGENT_AIDER_REASONING_EFFORT=low
```

首次测试只修改少量文件，不要一次要求重构整个大型项目。

### Maven 下载失败

```bash
mvn -U test
mvn -U dependency:go-offline
```

`Premature end of Content-Length` 通常是网络或缓存中的不完整依赖，不一定是代码错误。

### Token 一直为 0

- 检查模型请求是否成功；
- 检查 Provider 是否返回 Usage；
- 检查 Aider 是否通过平台代理；
- 检查请求是否在产生结果前超时；
- 刷新 Models Usage 和 Insights 页面。

### 重启后数据消失

当前部分 Project、Artifact、Trace、Usage 和 Code Stage 状态使用内存存储。停止服务后部分数据可能丢失。重要结果应及时下载、导出或备份。

## 19. 相对原始 OxyGent 的新增文件

当前版本相对 `upstream/main` 共改动 128 个文件，增加约 27,374 行。

### 19.1 架构和实施文档

```text
docs/refactor/01-current-architecture.md
docs/refactor/02-agent-call-flow.md
docs/refactor/03-llm-provider-flow.md
docs/refactor/04-tool-and-permission-system.md
docs/refactor/05-web-and-event-protocol.md
docs/refactor/06-storage-and-trace.md
docs/refactor/07-refactor-risks.md
docs/refactor/08-target-architecture.md
docs/refactor/09-implementation-roadmap.md
docs/refactor/10-phase-1-multi-model-platform.md
docs/refactor/11-product-ui-code-workspace-plan.md
docs/refactor/12-pr1-navigation-shell.md
docs/refactor/13-pr2-projects-and-artifacts.md
docs/refactor/14-pr3-agents-and-models.md
docs/refactor/15-pr4-workflow-timeline.md
docs/refactor/16-pr5-repository-git-worktree.md
docs/refactor/17-pr6-diff-verification.md
docs/refactor/18-pr7-approval-apply-discard.md
docs/refactor/19-pr8-insights-cost-statistics.md
docs/refactor/20-zh-cn-localization.md
docs/refactor/21-real-web-workflow.md
docs/refactor/screenshots/
```

### 19.2 平台示例和入口

```text
examples/platform/.env.multi_role.example
examples/platform/README.md
examples/platform/multi_role_workflow_demo.py
examples/platform/projects_web_demo.py
```

### 19.3 多角色多模型后端

```text
oxygent/platform/__init__.py
oxygent/platform/api.py
oxygent/platform/approvals.py
oxygent/platform/artifacts.py
oxygent/platform/code_stage.py
oxygent/platform/coding.py
oxygent/platform/common.py
oxygent/platform/control_plane.py
oxygent/platform/credentials.py
oxygent/platform/insights.py
oxygent/platform/profiles.py
oxygent/platform/projects.py
oxygent/platform/provider_adapters.py
oxygent/platform/registries.py
oxygent/platform/routing.py
oxygent/platform/services.py
oxygent/platform/tracing.py
oxygent/platform/usage.py
oxygent/platform/verification.py
oxygent/platform/workflow.py
oxygent/platform/workflow_runtime.py
```

### 19.4 新增页面

```text
oxygent/web/agents.html
oxygent/web/code.html
oxygent/web/files.html
oxygent/web/insights.html
oxygent/web/models.html
oxygent/web/projects.html
oxygent/web/settings.html
oxygent/web/workflows.html
```

### 19.5 新增样式和脚本

```text
oxygent/web/css/app-shell.css
oxygent/web/css/chat-enterprise.css
oxygent/web/css/chat-modes.css
oxygent/web/css/code-workspace.css
oxygent/web/css/control-plane.css
oxygent/web/css/insights.css
oxygent/web/css/projects.css
oxygent/web/css/workflow-timeline.css
oxygent/web/css/workspace.css

oxygent/web/js/agents-page.js
oxygent/web/js/api-client.js
oxygent/web/js/app-shell.js
oxygent/web/js/chat-modes.js
oxygent/web/js/code-page.js
oxygent/web/js/files-page.js
oxygent/web/js/insights-page.js
oxygent/web/js/models-page.js
oxygent/web/js/projects-page.js
oxygent/web/js/workflow-page.js
oxygent/web/js/workspace-page.js
```

### 19.6 品牌资源

```text
oxygent/web/image/brand/cosco-shipping-logo.jpg
```

### 19.7 新增测试

```text
tests/integration/test_approval_api.py
tests/integration/test_basic_role_workflow.py
tests/integration/test_code_stage_lifecycle.py
tests/integration/test_code_workspace_api.py
tests/integration/test_diff_verification_api.py
tests/integration/test_insights_api.py
tests/integration/test_platform_control_plane_api.py
tests/integration/test_platform_project_api.py
tests/integration/test_workflow_timeline_api.py

tests/unittest/test_approval_lifecycle.py
tests/unittest/test_code_stage.py
tests/unittest/test_code_workspace.py
tests/unittest/test_diff_verification.py
tests/unittest/test_insights.py
tests/unittest/test_platform_control_plane.py
tests/unittest/test_platform_credentials.py
tests/unittest/test_platform_projects.py
tests/unittest/test_platform_provider_adapters.py
tests/unittest/test_platform_routing.py
tests/unittest/test_projects_web_demo.py
tests/unittest/test_web_product_shell.py
tests/unittest/test_workflow_artifact_parsing.py
tests/unittest/test_workflow_runtime.py
tests/unittest/test_workflow_timeline.py
```

## 20. 在原 OxyGent 文件上的修改

```text
.gitignore
oxygent/mas.py
oxygent/oxy/llms/base_llm.py
oxygent/web/history.html
oxygent/web/index.html
oxygent/web/js/flowchart.js
oxygent/web/js/mermaid-sdk-gantt.js
oxygent/web/js/prompt-manager.js
oxygent/web/js/swimlane.js
oxygent/web/js/timeline.js
oxygent/web/node.html
oxygent/web/prompts.html
tests/unittest/test_token_metering.py
```

这些修改用于接入平台路由、Token Usage、中文导航、新工作区入口和旧页面兼容。原 OxyGent 许可证及版权声明保持不变。

## 21. 验收清单

- [ ] 成功克隆 `main`；
- [ ] Python 3.10 环境可用；
- [ ] `oxygent.platform` 可以导入；
- [ ] Aider 0.86.2 可以运行；
- [ ] API Key 只配置在本机环境变量；
- [ ] 网站通过 18086 启动；
- [ ] Chat 可以使用；
- [ ] 可以新建和导入 Project；
- [ ] 四角色工作流可以生成四类 Artifact；
- [ ] Agents 和 Models 显示真实配置；
- [ ] Token 记录会增长；
- [ ] Aider 能生成或修改代码；
- [ ] Code 页面能显示代码结果；
- [ ] Reviewer 能返回三级审查结果；
- [ ] 验证命令返回真实退出码；
- [ ] 审查意见和验证错误可以返回 Aider；
- [ ] 可以完成人工审批并下载结果。

## 22. 许可证

本项目继承并保留原 OxyGent 仓库中的许可证、版权和第三方声明。详见：

- [LICENSE](LICENSE)
- [NOTICE_Third_Party.md](NOTICE_Third_Party.md)
- [SECURITY.md](SECURITY.md)

