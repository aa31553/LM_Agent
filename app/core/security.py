from dataclasses import dataclass, field

from fastapi import Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.constants import ConfidentialLevel, ErrorCode
from app.core.exceptions import APIError

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    external_user_id: str
    username: str
    department: str | None = None
    roles: set[str] = field(default_factory=set)
    projects: set[str] = field(default_factory=set)
    clearance_level: ConfidentialLevel = ConfidentialLevel.INTERNAL
    is_active: bool = True


async def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Principal:
    if credentials is None or not credentials.credentials:
        raise APIError(
            ErrorCode.UNAUTHORIZED,
            "Missing bearer token.",
            status.HTTP_401_UNAUTHORIZED,
        )

    token = credentials.credentials
    if token == "admin":
        return Principal(
            external_user_id="admin",
            username="admin",
            department="admin",
            roles={"admin"},
            clearance_level=ConfidentialLevel.RESTRICTED,
        )

    return _principal_from_local_token(token)


def _principal_from_local_token(token: str) -> Principal:
    parts = token.split("|")
    external_user_id = parts[0].strip() or token
    department = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
    clearance = (
        _parse_clearance(parts[2])
        if len(parts) > 2 and parts[2].strip()
        else ConfidentialLevel.INTERNAL
    )
    roles = (
        {role.strip() for role in parts[3].split(",") if role.strip()}
        if len(parts) > 3 and parts[3].strip()
        else set()
    )
    projects = (
        {project.strip() for project in parts[4].split(",") if project.strip()}
        if len(parts) > 4 and parts[4].strip()
        else set()
    )
    return Principal(
        external_user_id=external_user_id,
        username=external_user_id,
        department=department,
        roles=roles,
        projects=projects,
        clearance_level=clearance,
    )


def _parse_clearance(value: str) -> ConfidentialLevel:
    try:
        return ConfidentialLevel(value.strip())
    except ValueError as exc:
        raise APIError(
            ErrorCode.UNAUTHORIZED,
            "Invalid clearance level in bearer token.",
            status.HTTP_401_UNAUTHORIZED,
        ) from exc
