# Web 界面简体中文本地化

## 范围

本次本地化覆盖 OxyGent 现有对话界面、Project Workspace、Code Workspace、Files、Agents、Models、Workflows、Insights、Settings，以及历史记录、节点浏览器和提示词管理页面。

覆盖内容包括：

- 页面标题、一级导航、标签页和表格标题；
- 按钮、输入提示、弹窗、空状态、加载状态和错误提示；
- 工作流阶段、工程状态、模型健康状态、路由模式和角色名称；
- 功能引导、评价面板、版本管理与提示词优化界面；
- 本地无凭证演示中的项目、模型显示名、工具策略和工作流摘要。

## 保持不变的内容

以下内容属于协议、标识符或用户数据，不做翻译：

- API 路径、JSON 字段、查询参数和事件类型的内部值；
- Provider、Model、Agent、Project、Task 和 Artifact 的 ID；
- Git 分支、提交哈希、文件路径、环境变量名和凭证引用；
- OpenAI、Gemini、Ollama、Markdown、JSON、SSE、LLM 等技术名称；
- 用户输入、模型输出、提示词正文和历史对话正文。

因此，页面可以显示中文标签，同时继续向后端提交原有英文枚举值，不影响现有接口兼容性。

## 启动

在项目根目录执行：

```bash
OXYGENT_PROJECT_DEMO_PORT=18086 .conda-env/bin/python examples/platform/projects_web_demo.py
```

然后访问：

```text
http://127.0.0.1:18086/web/index.html
```

## 验证

- JavaScript 源文件通过语法检查；
- Web 产品契约、平台控制平面、工作流、洞察单元测试通过；
- 平台控制平面、工作流、洞察和代码工作区集成测试通过；
- 通过真实浏览器逐页检查主导航和主要页面的中文渲染。
