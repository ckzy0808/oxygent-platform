"""Contract tests for the additive static product navigation shell."""

from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from httpx import ASGITransport, AsyncClient

import oxygent


WEB_ROOT = Path(oxygent.__file__).parent / "web"
PRODUCT_PAGES = {
    "chat": "index.html",
    "projects": "projects.html",
    "code": "code.html",
    "files": "files.html",
    "agents": "agents.html",
    "models": "models.html",
    "workflows": "workflows.html",
    "insights": "insights.html",
    "settings": "settings.html",
}


class IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name == "id" and value:
                self.ids.add(value)


def read_web_file(name: str) -> str:
    return (WEB_ROOT / name).read_text(encoding="utf-8")


def test_product_pages_are_additive_static_documents():
    for page_id, filename in PRODUCT_PAGES.items():
        document = read_web_file(filename)
        assert f'data-oxygent-page="{page_id}"' in document
        assert "./css/app-shell.css" in document
        assert "./js/app-shell.js" in document


def test_legacy_chat_contract_remains_present_with_mode_entry():
    document = read_web_file("index.html")
    collector = IdCollector()
    collector.feed(document)

    legacy_ids = {
        "chatbox",
        "message_input",
        "message_send",
        "fileAddtional",
        "agent_log",
        "timeline-section",
        "tracegraph-section",
    }
    assert legacy_ids.issubset(collector.ids)
    assert "function send_message()" in document
    assert "../sse/chat?payload=" in document
    assert "message_handler(event.data)" in document
    assert "if (ws && ws.readyState === WebSocket.OPEN)" in document

    assert {"chat-mode-shell", "code-mode-panel"}.issubset(collector.ids)
    for mode in ("general", "research", "data", "code"):
        assert f'data-chat-mode="{mode}"' in document
    assert "转换为项目任务" in document
    assert 'id="convert-project-task"' in document
    assert "./js/api-client.js" in document
    assert "projectTaskAttachmentReferences" in document


def test_enterprise_chat_skin_keeps_core_actions_and_brand_assets():
    document = read_web_file("index.html")
    script = read_web_file("js/chat-modes.js")
    stylesheet = read_web_file("css/chat-enterprise.css")
    logo = WEB_ROOT / "image" / "brand" / "cosco-shipping-logo.jpg"

    assert "中国远洋海运集团有限公司" in document
    assert "中远海运智能协作中心" in document
    assert "多角色 · 多模型协作" in document
    assert "让专业智能体协同推进复杂任务" in document
    assert "智能体概览" in document
    assert "精简视图" in document
    assert "./image/brand/cosco-shipping-logo.jpg" in document
    assert "./css/chat-enterprise.css" in document
    assert logo.is_file()
    assert logo.stat().st_size > 0

    for label in ("航运研究", "供应链风险", "经营分析", "项目任务拆解"):
        assert label in document
    assert "data-chat-prompt" in document
    assert "mountChatStarters" in script
    assert "MutationObserver" in script
    assert 'body[data-oxygent-page="chat"]' in stylesheet
    assert "#message_send" in stylesheet


def test_navigation_contains_the_required_information_architecture():
    script = read_web_file("js/app-shell.js")
    for page_id, filename in PRODUCT_PAGES.items():
        assert f"'{page_id}'" in script
        assert f"'{filename}'" in script


def test_workspace_skeleton_contains_project_model_and_code_sections():
    script = read_web_file("js/workspace-page.js")
    for label in (
        "概览",
        "创意",
        "需求",
        "架构",
        "任务",
        "产物",
        "团队",
        "活动",
        "服务商",
        "路由策略",
        "代码仓库",
        "代码任务",
        "变更",
        "审查",
        "验证",
        "仓库上下文",
        "任务时间线",
        "变更与验证",
    ):
        assert label in script


def test_projects_and_files_use_additive_platform_api_clients():
    projects = read_web_file("projects.html")
    files = read_web_file("files.html")
    client = read_web_file("js/api-client.js")
    conversion = read_web_file("js/chat-modes.js")

    assert "./js/projects-page.js" in projects
    assert "./js/files-page.js" in files
    assert "../api/v1/platform" in client
    assert "createTaskFromChat" in client
    assert "startProjectWorkflow" in client
    assert "导入现有项目" in read_web_file("js/projects-page.js")
    assert "智能体项目理解" in read_web_file("js/projects-page.js")
    assert "analyzeSourceWorkspace" in client
    assert "node_modules" in client
    assert "项目源码过大" in client
    assert "启动四角色工作流" in read_web_file("js/projects-page.js")
    assert "尚未配置真实模型" in read_web_file("js/projects-page.js")
    assert "不会复制完整对话内容" in conversion


def test_agents_and_models_use_sanitized_control_plane_views():
    agents = read_web_file("agents.html")
    models = read_web_file("models.html")
    agent_script = read_web_file("js/agents-page.js")
    model_script = read_web_file("js/models-page.js")
    client = read_web_file("js/api-client.js")

    assert "./js/agents-page.js" in agents
    assert "./js/models-page.js" in models
    assert "为何选择此模型？" in agent_script
    assert "不会展示模型的私有推理过程" in agent_script
    assert "credentialMask" in model_script
    assert "请输入密钥引用，切勿填写 API Key 的实际值" in model_script
    assert "testProvider" in client


def test_workflow_timeline_uses_unified_events_and_hides_private_reasoning():
    document = read_web_file("workflows.html")
    script = read_web_file("js/workflow-page.js")
    client = read_web_file("js/api-client.js")

    assert "./js/workflow-page.js" in document
    assert "./css/workflow-timeline.css" in document
    for phase in (
        "需求",
        "架构",
        "计划",
        "实现",
        "验证",
        "审查",
        "审批",
    ):
        assert phase in script
    assert "执行详情" in script
    assert "不会展示模型的私有推理过程" in script
    assert "listWorkflowRuns" in client
    assert "listWorkflowEvents" in client
    assert "workflowEventStreamUrl" in client
    assert "new EventSource" in script


def test_insights_uses_live_token_usage_and_safe_route_views():
    document = read_web_file("insights.html")
    script = read_web_file("js/insights-page.js")
    client = read_web_file("js/api-client.js")

    assert "./js/insights-page.js" in document
    assert "./css/insights.css" in document
    for section in ("概览", "用量", "可靠性"):
        assert section in script
    assert "精确计量" in script
    assert "预估成本" not in script
    assert "window.setTimeout(function () { loadInsights(true); }, 2000)" in script
    assert "成功率" in script
    assert "不会展示模型的私有推理过程" in script
    assert "getInsightsSummary" in client
    assert "getInsightsBreakdown" in client
    assert "listInsightRuns" in client


def test_code_page_is_a_project_first_aider_workflow_stage():
    document = read_web_file("code.html")
    script = read_web_file("js/code-page.js")
    client = read_web_file("js/api-client.js")
    chat = read_web_file("js/chat-modes.js")

    assert "./js/code-page.js" in document
    assert "./css/code-workspace.css" in document
    for label in (
        "项目工作流 · 代码实现阶段",
        "上传已有项目",
        "新建空白项目",
        "前序工作流产物",
        "开始代码实现",
        "生成或修改的文件",
        "下载最终完整项目 ZIP",
        "查看 Aider 生成和修改的真实代码",
        "修改后代码",
        "按反馈继续修改",
        "真实运行验证",
        "模型独立审查",
        "最终人工审批",
        "一键按审查意见返回修改",
        "无需修改，进入最终审批",
        "一键根据验证错误修复",
        "完全通过",
        "基本合格",
        "需要修改",
        "一键按建议继续优化",
    ):
        assert label in script
    assert "importSourceWorkspace" in client
    assert "startCodeStageRun" in client
    assert "getCodeStageRun" in client
    assert "getCodeStageChanges" in client
    assert "getCodeStageFileChange" in client
    assert "verifyCodeStage" in client
    assert "reviewCodeStage" in client
    assert "overrideCodeStageReview" in client
    assert "verificationRevisionInstructions" in script
    assert "startCodeStageReviewRevision" in script
    assert "startCodeStageReviewRevision" in client
    assert "stderrPreview" in script
    assert "stdoutPreview" in script
    assert "真实退出码" in script
    assert "approveCodeStage" in client
    assert "codeStageDownloadUrl" in client
    assert "webkitdirectory" in script
    for legacy_label in ("注册代码仓库", "配置认证", "Worktree 受保护", "统一差异"):
        assert legacy_label not in script
    assert "每个代码任务都在独立的 Git 工作树中运行" in read_web_file("index.html")
    assert "mountCodeSelectors" in chat


def test_primary_and_legacy_pages_declare_simplified_chinese():
    for filename in (
        *PRODUCT_PAGES.values(),
        "history.html",
        "node.html",
        "prompts.html",
    ):
        assert 'lang="zh-CN"' in read_web_file(filename)


@pytest.mark.asyncio
async def test_all_product_pages_are_served_by_existing_static_mount():
    app = FastAPI()
    app.mount("/web", StaticFiles(directory=WEB_ROOT), name="web")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for filename in PRODUCT_PAGES.values():
            response = await client.get(f"/web/{filename}")
            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]
