import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.office_file_preparation_service import (
    OfficeFilePreparationService,
)


class CopyOfficeBackend:
    """Test double for a successful Office COM open-and-save operation."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path, str]] = []

    def normalize(self, source: Path, destination: Path, file_type: str) -> None:
        self.calls.append((source, destination, file_type))
        shutil.copy2(source, destination)


@pytest.fixture
def office_backend() -> CopyOfficeBackend:
    return CopyOfficeBackend()


@pytest.fixture
def office_preparation(office_backend: CopyOfficeBackend) -> OfficeFilePreparationService:
    return OfficeFilePreparationService(office_backend)
