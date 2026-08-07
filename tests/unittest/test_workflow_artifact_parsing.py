"""Strict parsing tests for model-produced Artifact content."""

import pytest
from pydantic import ValidationError

from oxygent.platform import BasicRoleWorkflow


def test_artifact_parser_accepts_fenced_json_and_populates_schema_fields():
    content = BasicRoleWorkflow._parse_content(
        "product_manager",
        """```json
        {
          "summary": "Clear requirements",
          "requirements": ["Keep the workflow traceable"],
          "constraints": ["No raw credentials"],
          "acceptanceCriteria": ["The Artifact validates"]
        }
        ```""",
    )

    assert content.summary == "Clear requirements"
    assert content.requirements == ["Keep the workflow traceable"]
    assert content.acceptance_criteria == ["The Artifact validates"]


def test_artifact_parser_unwraps_named_content_object():
    content = BasicRoleWorkflow._parse_content(
        "technical_lead",
        {
            "taskGraph": {
                "summary": "Implementation plan",
                "tasks": [
                    {
                        "id": "task-1",
                        "title": "Implement parser",
                        "description": "Validate model output",
                        "dependsOn": [],
                    }
                ],
            }
        },
    )

    assert content.tasks[0].title == "Implement parser"


def test_artifact_parser_rejects_unstructured_or_unknown_fields():
    with pytest.raises(ValueError, match="JSON object"):
        BasicRoleWorkflow._parse_content("reviewer", "Looks good to me")

    with pytest.raises(ValidationError):
        BasicRoleWorkflow._parse_content(
            "reviewer",
            {"summary": "Review", "privateReasoning": "must not be stored"},
        )
