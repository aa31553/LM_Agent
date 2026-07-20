from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import Principal
from app.main import create_app
from app.rag.prompt_builder import PromptBuilder
from app.services.skill_service import SkillService


def _headers(token: str = "admin") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Request-ID": "skill-test"}


def test_default_skills_and_progressive_context(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "skills"
    monkeypatch.setattr(settings, "skills_root", str(root))

    service = SkillService()
    skills = service.list_skills()
    names = {skill.name for skill in skills}

    assert {"skill-creator", "document-analysis", "secure-rag"} <= names
    assert all(skill.is_system and skill.enabled for skill in skills)
    context = service.resolve_context(
        "Use $skill-creator to create a document analysis skill",
        Principal(external_user_id="admin", username="admin", roles={"admin"}),
    )
    assert "skill-creator" in context.triggered_names
    assert "secure-rag" in context.triggered_names
    assert "Active Skill Instructions" in context.prompt
    assert "Never include secrets" in context.prompt

    system_prompt, _ = PromptBuilder().build(
        masked_query="Create a reusable skill",
        retrieved_context="",
        skill_context=context.prompt,
    )
    assert "Configured Agent Skills" in system_prompt
    assert "skill-creator" in system_prompt


def test_skills_crud_files_and_raw_preview_api(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "skills_root", str(tmp_path / "skills"))
    client = TestClient(create_app())

    initial = client.get("/api/v1/skills", headers=_headers())
    assert initial.status_code == 200
    assert any(item["name"] == "skill-creator" for item in initial.json()["items"])

    created = client.post(
        "/api/v1/skills",
        headers=_headers(),
        json={
            "name": "materials-review",
            "description": "Review materials research. Use for evidence-based materials questions.",
            "instructions": "# Materials Review\n\nCite supplied evidence.",
            "enabled": True,
        },
    )
    assert created.status_code == 201
    detail = created.json()
    assert detail["name"] == "materials-review"
    assert {item["relative_path"] for item in detail["files"]} == {
        "SKILL.md",
        "agents/openai.yaml",
    }

    skill_file = next(item for item in detail["files"] if item["relative_path"] == "SKILL.md")
    raw = client.get(skill_file["raw_url"], headers=_headers())
    assert raw.status_code == 200
    assert "name: materials-review" in raw.text
    assert "# Materials Review" in raw.text
    assert raw.headers["content-disposition"].startswith("inline")

    updated = client.patch(
        "/api/v1/skills/materials-review",
        headers=_headers(),
        json={
            "description": "Review materials evidence. Use for research synthesis.",
            "instructions": "# Updated Workflow\n\nCompare claims and cite sources.",
            "enabled": False,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False
    assert updated.json()["instructions"].startswith("# Updated Workflow")

    uploaded = client.post(
        "/api/v1/skills/materials-review/files",
        headers=_headers(),
        data={"relative_path": "references/policy.md", "overwrite": "false"},
        files={"file": ("policy.md", b"# Review policy\n", "text/markdown")},
    )
    assert uploaded.status_code == 200
    uploaded_file = uploaded.json()["file"]
    assert uploaded_file["relative_path"] == "references/policy.md"
    assert client.get(uploaded_file["raw_url"], headers=_headers()).text == "# Review policy\n"

    traversal = client.post(
        "/api/v1/skills/materials-review/files",
        headers=_headers(),
        data={"relative_path": "../escape.txt", "overwrite": "false"},
        files={"file": ("escape.txt", b"no", "text/plain")},
    )
    assert traversal.status_code == 400
    assert not (tmp_path / "escape.txt").exists()

    deleted_file = client.delete(
        "/api/v1/skills/materials-review/files/references/policy.md",
        headers=_headers(),
    )
    assert deleted_file.status_code == 204

    forbidden = client.post(
        "/api/v1/skills",
        headers=_headers("reader|qa|internal|reader"),
        json={
            "name": "forbidden-skill",
            "description": "Should not be created.",
            "instructions": "# No",
        },
    )
    assert forbidden.status_code == 403

    protected = client.delete("/api/v1/skills/skill-creator", headers=_headers())
    assert protected.status_code == 400

    deleted = client.delete("/api/v1/skills/materials-review", headers=_headers())
    assert deleted.status_code == 204
    missing = client.get("/api/v1/skills/materials-review", headers=_headers())
    assert missing.status_code == 404


def test_skill_permissions_filter_api_files_and_llm_context(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "skills_root", str(tmp_path / "skills"))
    client = TestClient(create_app())
    created = client.post(
        "/api/v1/skills",
        headers=_headers(),
        json={
            "name": "restricted-review",
            "description": "Review restricted research evidence.",
            "instructions": "# Restricted Review\n\nUse the approved workflow.",
            "enabled": True,
        },
    )
    assert created.status_code == 201
    raw_url = created.json()["skill_file_url"]

    permission = client.post(
        "/api/v1/permissions/skills/restricted-review",
        headers=_headers(),
        json={"subject_type": "role", "subject_value": "analyst", "permission": "read"},
    )
    assert permission.status_code == 200
    permission_id = permission.json()["permission_id"]
    assert client.get(
        "/api/v1/skills/restricted-review",
        headers=_headers(),
    ).json()["permission_count"] == 1

    unauthorized_headers = _headers("bob|research|internal|reader")
    unauthorized_list = client.get("/api/v1/skills", headers=unauthorized_headers)
    assert unauthorized_list.status_code == 200
    assert "restricted-review" not in {
        item["name"] for item in unauthorized_list.json()["items"]
    }
    assert client.get(
        "/api/v1/skills/restricted-review",
        headers=unauthorized_headers,
    ).status_code == 403
    assert client.get(raw_url, headers=unauthorized_headers).status_code == 403

    authorized_headers = _headers("alice|research|internal|analyst")
    authorized_list = client.get("/api/v1/skills", headers=authorized_headers)
    assert "restricted-review" in {item["name"] for item in authorized_list.json()["items"]}
    assert client.get(raw_url, headers=authorized_headers).status_code == 200

    service = SkillService()
    unauthorized_context = service.resolve_context(
        "Use $restricted-review",
        Principal(external_user_id="bob", username="bob", roles={"reader"}),
    )
    assert "restricted-review" not in unauthorized_context.triggered_names
    assert "$restricted-review" not in unauthorized_context.prompt
    authorized_context = service.resolve_context(
        "Use $restricted-review",
        Principal(external_user_id="alice", username="alice", roles={"analyst"}),
    )
    assert "restricted-review" in authorized_context.triggered_names
    assert "# Restricted Review" in authorized_context.prompt

    assert client.get(
        "/api/v1/permissions/skills/restricted-review",
        headers=unauthorized_headers,
    ).status_code == 403
    invalid_level = client.post(
        "/api/v1/permissions/skills/restricted-review",
        headers=_headers(),
        json={"subject_type": "role", "subject_value": "writer", "permission": "write"},
    )
    assert invalid_level.status_code == 400

    deleted = client.delete(
        f"/api/v1/permissions/skills/restricted-review/{permission_id}",
        headers=_headers(),
    )
    assert deleted.status_code == 204
    open_list = client.get("/api/v1/skills", headers=unauthorized_headers)
    assert "restricted-review" in {item["name"] for item in open_list.json()["items"]}
