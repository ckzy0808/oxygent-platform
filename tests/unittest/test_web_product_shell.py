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
    assert "Convert to Project Task" in document
    assert 'id="convert-project-task"' in document
    assert "./js/api-client.js" in document
    assert "projectTaskAttachmentReferences" in document


def test_navigation_contains_the_required_information_architecture():
    script = read_web_file("js/app-shell.js")
    for page_id, filename in PRODUCT_PAGES.items():
        assert f"'{page_id}'" in script
        assert f"'{filename}'" in script


def test_workspace_skeleton_contains_project_model_and_code_sections():
    script = read_web_file("js/workspace-page.js")
    for label in (
        "Overview",
        "Ideas",
        "Requirements",
        "Architecture",
        "Tasks",
        "Artifacts",
        "Team",
        "Activity",
        "Providers",
        "Routing Policies",
        "Repositories",
        "Code Tasks",
        "Changes",
        "Reviews",
        "Verification",
        "Repository Context",
        "Task Timeline",
        "Changes and Verification",
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
    assert "the full transcript is not copied" in conversion


def test_agents_and_models_use_sanitized_control_plane_views():
    agents = read_web_file("agents.html")
    models = read_web_file("models.html")
    agent_script = read_web_file("js/agents-page.js")
    model_script = read_web_file("js/models-page.js")
    client = read_web_file("js/api-client.js")

    assert "./js/agents-page.js" in agents
    assert "./js/models-page.js" in models
    assert "Why this model?" in agent_script
    assert "Private model reasoning is never displayed" in agent_script
    assert "credentialMask" in model_script
    assert "Enter a secret reference, never an API key value" in model_script
    assert "testProvider" in client


def test_workflow_timeline_uses_unified_events_and_hides_private_reasoning():
    document = read_web_file("workflows.html")
    script = read_web_file("js/workflow-page.js")
    client = read_web_file("js/api-client.js")

    assert "./js/workflow-page.js" in document
    assert "./css/workflow-timeline.css" in document
    for phase in (
        "Requirement",
        "Architecture",
        "Plan",
        "Implementation",
        "Verification",
        "Review",
        "Approval",
    ):
        assert phase in script
    assert "Execution details" in script
    assert "Private model reasoning is never displayed" in script
    assert "listWorkflowRuns" in client
    assert "listWorkflowEvents" in client


def test_code_workspace_uses_isolated_repository_apis_and_scope_contract():
    document = read_web_file("code.html")
    script = read_web_file("js/code-page.js")
    client = read_web_file("js/api-client.js")
    chat = read_web_file("js/chat-modes.js")

    assert "./js/code-page.js" in document
    assert "./css/code-workspace.css" in document
    for label in (
        "Repository Context",
        "Task Timeline",
        "Change Contract",
        "Worktree protected",
        "Mutation remains gated",
    ):
        assert label in script
    assert "listRepositorySources" in client
    assert "createCodeTask" in client
    assert "readRepositoryFile" in client
    assert "Every Code Task runs in an isolated Git worktree" in read_web_file(
        "index.html"
    )
    assert "mountCodeSelectors" in chat


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
