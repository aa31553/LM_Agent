import shutil
import zipfile
from pathlib import Path

import pytest

from app.services.office_file_preparation_service import (
    OfficeFilePreparationService,
    OfficePreparationError,
    PyWin32OfficeBackend,
)


def _openxml(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")


def test_prepared_office_copy_is_temporary_and_parser_safe(
    tmp_path: Path,
    office_preparation: OfficeFilePreparationService,
    office_backend,
) -> None:
    source = tmp_path / "report.xlsx"
    _openxml(source)

    with office_preparation.prepare_sync(source) as prepared:
        normalized = prepared.path
        temporary_root = prepared.temporary_root
        assert normalized != source
        assert zipfile.is_zipfile(normalized)
        assert prepared.source_path == source.resolve()
        assert office_backend.calls[0][2] == "xlsx"

    assert not temporary_root.exists()


def test_preparation_rejects_output_that_remains_encrypted(tmp_path: Path) -> None:
    class ProtectedBackend:
        def normalize(self, _source: Path, destination: Path, _file_type: str) -> None:
            destination.write_bytes(b"still-encrypted")

    source = tmp_path / "protected.xlsx"
    _openxml(source)

    with pytest.raises(OfficePreparationError, match="OFFICE_OUTPUT_STILL_PROTECTED"):
        OfficeFilePreparationService(ProtectedBackend()).prepare_sync(source)


def test_pywin32_backend_opens_read_only_disables_macros_and_saves_openxml(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.bin"
    _openxml(source)
    applications = {}

    class OpenedFile:
        def SaveAs2(self, **kwargs) -> None:
            shutil.copy2(source, kwargs["FileName"])

        def SaveAs(self, *args, **kwargs) -> None:
            destination = args[0] if args else kwargs["Filename"]
            shutil.copy2(source, destination)

        def Close(self, *args, **kwargs) -> None:
            return None

    class Collection:
        def __init__(self) -> None:
            self.open_kwargs = None

        def Open(self, **kwargs):
            self.open_kwargs = kwargs
            return OpenedFile()

    class Application:
        def __init__(self, prog_id: str) -> None:
            self.prog_id = prog_id
            self.Documents = Collection()
            self.Workbooks = Collection()
            self.Presentations = Collection()
            self.AutomationSecurity = None

        def Quit(self, *args, **kwargs) -> None:
            return None

    class Win32Client:
        @staticmethod
        def DispatchEx(prog_id: str):
            application = Application(prog_id)
            applications[prog_id] = application
            return application

    class PythonCom:
        COINIT_APARTMENTTHREADED = 2
        initialized = 0
        uninitialized = 0

        @classmethod
        def CoInitializeEx(cls, _mode: int) -> None:
            cls.initialized += 1

        @classmethod
        def CoUninitialize(cls) -> None:
            cls.uninitialized += 1

    def fake_import(name: str):
        return PythonCom if name == "pythoncom" else Win32Client

    monkeypatch.setattr(
        "app.services.office_file_preparation_service.platform.system",
        lambda: "Windows",
    )
    monkeypatch.setattr(
        "app.services.office_file_preparation_service.importlib.import_module",
        fake_import,
    )

    outputs = {
        "docx": tmp_path / "normalized.docx",
        "xlsx": tmp_path / "normalized.xlsx",
        "pptx": tmp_path / "normalized.pptx",
    }
    backend = PyWin32OfficeBackend()
    for file_type, destination in outputs.items():
        backend.normalize(source, destination, file_type)
        assert zipfile.is_zipfile(destination)

    assert applications["Word.Application"].Documents.open_kwargs["ReadOnly"] is True
    assert applications["Excel.Application"].Workbooks.open_kwargs["ReadOnly"] is True
    assert applications["Excel.Application"].Workbooks.open_kwargs["UpdateLinks"] == 0
    assert applications["PowerPoint.Application"].Presentations.open_kwargs["ReadOnly"] is True
    assert all(application.AutomationSecurity == 3 for application in applications.values())
    assert PythonCom.initialized == 3
    assert PythonCom.uninitialized == 3
