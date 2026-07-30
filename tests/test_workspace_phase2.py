import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.constants import ConfidentialLevel, PermissionLevel, PermissionSubjectType
from app.core.exceptions import APIError
from app.core.security import Principal
from app.db.base import Base
from app.models.analysis import AnalysisFile, AnalysisJob
from app.models.audit import AuditEvent
from app.models.chat import ChatSession
from app.models.user import User
from app.models.workspace import Workspace, WorkspacePermission
from app.schemas.analysis import AnalysisJobCreate, AnalysisPlan
from app.services.analysis_job_service import AnalysisJobService
from app.services.audit_service import AuditService
from app.services.session_service import SessionService
from app.services.workspace_service import WorkspaceService
from app.storage.workspace_storage import WorkspaceStorage


class _Upload:
    def __init__(self, payload: bytes) -> None:
        self._buffer = BytesIO(payload)

    async def read(self, size: int) -> bytes:
        return self._buffer.read(size)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    for table in (
        User.__table__,
        Workspace.__table__,
        WorkspacePermission.__table__,
        ChatSession.__table__,
        AnalysisFile.__table__,
        AnalysisJob.__table__,
        AuditEvent.__table__,
    ):
        table.create(engine)
    return engine


def _workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Machine", "Yield"])
    sheet.append(["A", 98])
    workbook.save(path)


def test_workspace_file_is_reusable_from_another_linked_session(
    tmp_path: Path,
) -> None:
    engine = _engine()
    path = tmp_path / "production.xlsx"
    _workbook(path)
    principal = Principal(
        external_user_id="owner",
        username="owner",
        clearance_level=ConfidentialLevel.INTERNAL,
    )

    with Session(engine) as db:
        user = AuditService(db).ensure_user(principal)
        workspace = WorkspaceService(db).get_or_create_personal(principal)
        first = ChatSession(user_id=user.id, workspace_id=workspace.id)
        second = ChatSession(user_id=user.id, workspace_id=workspace.id)
        db.add_all([first, second])
        db.flush()
        source = AnalysisFile(
            workspace_id=workspace.id,
            session_id=first.id,
            created_by=user.id,
            filename=path.name,
            original_filename=path.name,
            file_type="xlsx",
            file_path=str(path),
            size_bytes=path.stat().st_size,
            confidential_level=ConfidentialLevel.INTERNAL.value,
            status="ready",
        )
        db.add(source)
        db.commit()

        job = AnalysisJobService(db).create(
            AnalysisJobCreate(
                file_id=source.id,
                session_id=second.id,
                plan=AnalysisPlan(select=["Machine"]),
            ),
            principal,
        )

        assert job.workspace_id == workspace.id
        assert job.session_id == second.id
        items, total, resolved_workspace_id = AnalysisJobService(db).list_jobs(
            principal,
            session_id=second.id,
        )
        assert resolved_workspace_id == workspace.id
        assert total == 1
        assert items[0].file_id == source.id


def test_workspace_permissions_control_reuse(
    tmp_path: Path,
) -> None:
    engine = _engine()
    path = tmp_path / "production.xlsx"
    _workbook(path)
    owner = Principal(
        external_user_id="owner",
        username="owner",
        clearance_level=ConfidentialLevel.INTERNAL,
    )
    analyst = Principal(
        external_user_id="analyst",
        username="analyst",
        department="quality",
        projects={"yield-project"},
        clearance_level=ConfidentialLevel.INTERNAL,
    )

    with Session(engine) as db:
        owner_user = AuditService(db).ensure_user(owner)
        workspace = WorkspaceService(db).get_or_create_personal(owner)
        owner_session = ChatSession(
            user_id=owner_user.id,
            workspace_id=workspace.id,
        )
        db.add(owner_session)
        db.flush()
        source = AnalysisFile(
            workspace_id=workspace.id,
            session_id=owner_session.id,
            created_by=owner_user.id,
            filename=path.name,
            original_filename=path.name,
            file_type="xlsx",
            file_path=str(path),
            size_bytes=path.stat().st_size,
            confidential_level=ConfidentialLevel.INTERNAL.value,
            status="ready",
        )
        db.add(source)
        db.commit()

        workspace_service = WorkspaceService(db)
        workspace_service.create_or_update_permission(
            workspace.id,
            subject_type=PermissionSubjectType.DEPARTMENT,
            subject_value="quality",
            permission=PermissionLevel.READ,
            principal=owner,
        )
        assert workspace_service.permission_level(analyst, workspace) == PermissionLevel.READ
        analyst_user = AuditService(db).ensure_user(analyst)
        analyst_session = ChatSession(user_id=analyst_user.id)
        db.add(analyst_session)
        db.commit()
        workspace_service.link_session(
            workspace.id,
            analyst_session.id,
            analyst,
        )
        assert analyst_session.workspace_id == workspace.id
        _items, total, _workspace_id = AnalysisJobService(db).list_jobs(
            analyst,
            session_id=analyst_session.id,
        )
        assert total == 0
        with pytest.raises(APIError, match="write permission"):
            AnalysisJobService(db).create(
                AnalysisJobCreate(
                    file_id=source.id,
                    plan=AnalysisPlan(select=["Machine"]),
                ),
                analyst,
            )

        workspace_service.create_or_update_permission(
            workspace.id,
            subject_type=PermissionSubjectType.PROJECT,
            subject_value="yield-project",
            permission=PermissionLevel.WRITE,
            principal=owner,
        )
        assert workspace_service.permission_level(analyst, workspace) == PermissionLevel.WRITE
        shared_job = AnalysisJobService(db).create(
            AnalysisJobCreate(
                file_id=source.id,
                session_id=analyst_session.id,
                plan=AnalysisPlan(select=["Machine"]),
            ),
            analyst,
        )
        assert shared_job.workspace_id == workspace.id
        assert shared_job.session_id == analyst_session.id


def test_workspace_storage_separates_original_profile_result_and_artifact(
    tmp_path: Path,
) -> None:
    from uuid import uuid4

    workspace_id = uuid4()
    file_id = uuid4()
    job_id = uuid4()
    artifact_id = uuid4()
    storage = WorkspaceStorage(root=str(tmp_path))

    original_path, size = asyncio.run(
        storage.save_original_upload(
            workspace_id,
            file_id,
            "production.xlsx",
            _Upload(b"xlsx"),
            max_bytes=100,
        )
    )
    profile_path = storage.save_profile(
        workspace_id,
        file_id,
        {"sheets": []},
    )
    result_path = storage.save_result(
        workspace_id,
        job_id,
        {"table": {"rows": []}},
    )
    artifact_path = storage.artifact_path(
        workspace_id,
        artifact_id,
        "export.csv",
    )

    assert size == 4
    assert f"files/{file_id}/original" in original_path
    assert f"files/{file_id}/profile/profile.json" in profile_path
    assert f"jobs/{job_id}/results/result.json" in result_path
    assert f"artifacts/{artifact_id}/export.csv" in str(artifact_path)
    assert storage.load_json(profile_path) == {"sheets": []}


def test_deleting_chat_session_keeps_workspace_analysis(
    tmp_path: Path,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    path = tmp_path / "persistent.xlsx"
    _workbook(path)
    principal = Principal(
        external_user_id="owner",
        username="owner",
        clearance_level=ConfidentialLevel.INTERNAL,
    )

    with Session(engine) as db:
        user = AuditService(db).ensure_user(principal)
        workspace = WorkspaceService(db).get_or_create_personal(principal)
        chat_session = ChatSession(
            user_id=user.id,
            workspace_id=workspace.id,
        )
        db.add(chat_session)
        db.flush()
        source = AnalysisFile(
            workspace_id=workspace.id,
            session_id=chat_session.id,
            created_by=user.id,
            filename=path.name,
            original_filename=path.name,
            file_type="xlsx",
            file_path=str(path),
            size_bytes=path.stat().st_size,
            confidential_level=ConfidentialLevel.INTERNAL.value,
            status="ready",
        )
        db.add(source)
        db.flush()
        job = AnalysisJob(
            workspace_id=workspace.id,
            session_id=chat_session.id,
            file_id=source.id,
            created_by=user.id,
            status="completed",
            progress=100,
            request_json={"select": ["Machine"]},
            result_json={"table": {"rows": []}},
        )
        db.add(job)
        db.commit()
        session_id = chat_session.id
        source_id = source.id
        job_id = job.id

        response = SessionService(db).delete(session_id, principal)

        assert response.deleted_analysis_files == 0
        assert db.get(ChatSession, session_id) is None
        assert db.get(AnalysisFile, source_id).session_id is None
        assert db.get(AnalysisJob, job_id).session_id is None
        assert path.exists()
