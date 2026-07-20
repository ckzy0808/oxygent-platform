"""Project domain, repository, and service tests."""

from typing import Any

import pytest
from pydantic import ValidationError

from oxygent.config import Config
from oxygent.databases.db_es import LocalEs
from oxygent.platform import (
    InMemoryArtifactStore,
    LocalEsProjectRepository,
    LocalEsProjectTaskRepository,
    PlatformServices,
    Project,
    ProjectCreate,
    ProjectTask,
    ProjectTaskFromChat,
    ProjectUpdate,
    RequirementSpec,
    RequirementSpecContent,
)


def requirement(project_id: str, artifact_id: str = "requirement-1") -> RequirementSpec:
    return RequirementSpec(
        id=artifact_id,
        projectId=project_id,
        taskId="workflow-task",
        producerRole="product_manager",
        producerAgent="pm_agent",
        providerId="provider-a",
        modelId="model-a",
        content=RequirementSpecContent(summary="Initial requirement"),
    )


@pytest.mark.asyncio
async def test_project_create_update_and_empty_delete():
    services = PlatformServices()
    project = await services.create_project(
        ProjectCreate(name="Generic Project", team=["Product Manager"])
    )

    assert project.active_tasks == 0
    assert (await services.projects.get(project.id)).name == "Generic Project"

    updated = await services.update_project(
        project.id, ProjectUpdate(description="Updated context")
    )
    assert updated.description == "Updated context"
    assert len(await services.list_activity(project.id)) == 2

    await services.delete_project(project.id)
    with pytest.raises(KeyError, match="project not found"):
        await services.projects.get(project.id)


@pytest.mark.asyncio
async def test_chat_task_uses_references_without_transcript_and_is_project_isolated():
    artifacts = InMemoryArtifactStore()
    services = PlatformServices(artifacts=artifacts)
    project_a = await services.create_project(ProjectCreate(name="Project A"))
    project_b = await services.create_project(ProjectCreate(name="Project B"))
    source = artifacts.append(requirement(project_a.id))

    task = await services.create_task_from_chat(
        project_a.id,
        ProjectTaskFromChat(
            title="Clarify requirements",
            objective="Turn the latest decision into a structured task.",
            sourceTraceId="trace-123",
            attachmentReferences=["upload_1.pdf", "upload_1.pdf"],
            sourceArtifactIds=[source.id],
        ),
    )

    assert task.project_id == project_a.id
    assert task.attachment_references == ["upload_1.pdf"]
    assert task.source_trace_id == "trace-123"
    assert not hasattr(task, "transcript")
    assert (await services.projects.get(project_a.id)).active_tasks == 1
    assert await services.tasks.list(project_b.id) == []

    with pytest.raises(ValueError, match="target project"):
        await services.create_task_from_chat(
            project_b.id,
            ProjectTaskFromChat(
                title="Cross-project task",
                objective="This must be rejected.",
                sourceArtifactIds=[source.id],
            ),
        )


def test_attachment_reference_rejects_paths():
    with pytest.raises(ValidationError, match="opaque file names"):
        ProjectTaskFromChat(
            title="Unsafe",
            objective="Reject path traversal",
            attachmentReferences=["../secret.txt"],
        )


@pytest.mark.asyncio
async def test_artifact_revision_is_append_only_and_latest_filter_hides_old_revision():
    artifacts = InMemoryArtifactStore()
    services = PlatformServices(artifacts=artifacts)
    project = await services.create_project(ProjectCreate(name="Revision Project"))
    original = artifacts.append(requirement(project.id))

    revision = await services.revise_artifact(
        project.id,
        original.id,
        {"summary": "Revised requirement", "requirements": ["Keep history"]},
        producer_role="product_manager",
        producer_agent="pm_agent",
        provider_id="provider-b",
        model_id="model-b",
    )

    assert revision.id != original.id
    assert revision.revision == 2
    assert revision.supersedes_artifact_id == original.id
    assert artifacts.get(original.id).content.summary == "Initial requirement"
    assert [item.id for item in await services.list_artifacts(project.id)] == [
        revision.id,
        original.id,
    ]
    assert [
        item.id for item in await services.list_artifacts(project.id, latest_only=True)
    ] == [revision.id]


class FakeLocalEs:
    """Minimal LocalEs-compatible backend used to verify adapter boundaries."""

    def __init__(self) -> None:
        self.indices: dict[str, dict[str, dict[str, Any]]] = {}

    async def index(
        self, index_name: str, doc_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        self.indices.setdefault(index_name, {})[doc_id] = body
        return {"_id": doc_id, "result": "created"}

    async def search(self, index_name: str, body: dict[str, Any]) -> dict[str, Any]:
        documents = list(self.indices.get(index_name, {}).items())
        term = body.get("query", {}).get("term")
        if term:
            key, expected = next(iter(term.items()))
            documents = [item for item in documents if item[1].get(key) == expected]
        return {
            "hits": {
                "hits": [
                    {"_id": document_id, "_source": document}
                    for document_id, document in documents
                ]
            }
        }

    async def delete(self, index_name: str, doc_id: str) -> dict[str, Any]:
        self.indices.get(index_name, {}).pop(doc_id, None)
        return {"_id": doc_id, "result": "deleted"}


@pytest.mark.asyncio
async def test_local_es_compatible_project_and_task_adapters():
    backend = FakeLocalEs()
    projects = LocalEsProjectRepository(backend)
    tasks = LocalEsProjectTaskRepository(backend)
    project = Project(id="persistent-project", name="Persistent Project")
    task = ProjectTask(
        id="persistent-task",
        projectId=project.id,
        title="Persist task",
        objective="Verify the LocalEs-compatible boundary",
    )

    await projects.create(project)
    await tasks.create(task)

    assert (await projects.get(project.id)).name == project.name
    assert [item.id for item in await tasks.list(project.id)] == [task.id]
    assert "platform_projects" in backend.indices
    assert "platform_project_tasks" in backend.indices


@pytest.mark.asyncio
async def test_project_repository_operates_with_real_local_es(tmp_path, monkeypatch):
    monkeypatch.setattr(
        Config, "get_cache_save_dir", classmethod(lambda _cls: str(tmp_path))
    )
    backend = LocalEs()
    projects = LocalEsProjectRepository(backend)
    project = Project(id="local-es-project", name="LocalEs Project")

    await projects.create(project)

    assert (await projects.get(project.id)).name == "LocalEs Project"
    assert [item.id for item in await projects.list()] == [project.id]
    await backend.close()
