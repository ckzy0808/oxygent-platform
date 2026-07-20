"""Run the Project/Artifact UI with local, credential-free demo data."""

import asyncio
import os

from oxygent import MAS, oxy
from oxygent.platform import (
    PlatformServices,
    ProjectCreate,
    ProjectTaskFromChat,
    RequirementSpec,
    RequirementSpecContent,
    build_platform_router,
)


async def demo_response(_request):
    return "The existing OxyGent Chat remains available beside Project Workspace."


async def build_services() -> PlatformServices:
    services = PlatformServices()
    project = await services.create_project(
        ProjectCreate(
            name="Agent Platform Workspace",
            description="A generic workspace for structured multi-role collaboration.",
            repository="Not linked",
            team=[
                "Product Manager",
                "Solution Architect",
                "Technical Lead",
                "Reviewer",
            ],
        )
    )
    requirement = services.artifacts.append(
        RequirementSpec(
            projectId=project.id,
            taskId="demo-workflow-task",
            producerRole="product_manager",
            producerAgent="pm_agent",
            providerId="provider-a",
            modelId="model-a",
            content=RequirementSpecContent(
                summary="Keep role outputs structured and traceable",
                requirements=[
                    "Preserve the existing Chat experience",
                    "Pass structured Artifacts between roles",
                ],
                acceptanceCriteria=[
                    "Every task references its Project",
                    "Artifact revisions never overwrite earlier versions",
                ],
            ),
        )
    )
    await services.create_task_from_chat(
        project.id,
        ProjectTaskFromChat(
            title="Review the Project workspace foundation",
            objective="Verify Project isolation, Artifact provenance, and Chat handoff.",
            sourceTraceId="demo-trace-reference",
            attachmentReferences=["workspace_notes.md"],
            sourceArtifactIds=[requirement.id],
        ),
    )
    return services


async def main() -> None:
    services = await build_services()
    llm = oxy.MockLLM(name="projects_demo_llm", func_mock_process=demo_response)
    agent = oxy.ChatAgent(
        name="projects_demo_agent",
        llm_model="projects_demo_llm",
        is_master=True,
    )
    port = int(os.getenv("OXYGENT_PROJECT_DEMO_PORT", "18080"))
    async with MAS(name="projects_web_demo", oxy_space=[llm, agent]) as mas:
        await mas.start_web_service(
            host="127.0.0.1",
            port=port,
            routers=[build_platform_router(services)],
            welcome_message="Project Workspace demo is ready.",
        )


if __name__ == "__main__":
    asyncio.run(main())
