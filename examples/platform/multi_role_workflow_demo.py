"""Run the phase-one, four-role Artifact workflow with environment configuration."""

import asyncio
import json
import os

from oxygent import Config, MAS
from oxygent.platform import build_environment_workflow_bundle


def build_platform():
    bundle = build_environment_workflow_bundle(workflow_is_master=True)
    return (
        bundle.oxy_space,
        bundle.artifacts,
        bundle.control_plane.usage,
        bundle.control_plane.traces,
    )


async def main() -> None:
    Config.set_server_auto_open_webpage(False)
    oxy_space, artifact_store, usage_store, trace_store = build_platform()
    idea = os.getenv(
        "OXYGENT_PLATFORM_IDEA",
        "Build a project-centered multi-role Agent collaboration platform.",
    )
    async with MAS(oxy_space=oxy_space) as mas:
        response = await mas.chat_with_agent(
            payload={"query": idea, "project_id": "multi-role-demo"}
        )
        print(json.dumps(response.output, ensure_ascii=False, indent=2))
        print(
            json.dumps(
                {
                    "artifactCount": len(artifact_store.list()),
                    "modelUsage": [
                        usage.model_dump(mode="json", by_alias=True)
                        for usage in usage_store.list()
                    ],
                    "routeDecisions": [
                        trace.model_dump(mode="json", by_alias=True)
                        for trace in trace_store.route_decisions()
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
