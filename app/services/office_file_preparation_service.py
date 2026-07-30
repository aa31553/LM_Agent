from __future__ import annotations

import asyncio
import importlib
import logging
import platform
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Protocol, Self

from app.core.config import settings
from app.workers.process_runner import run_in_process

OFFICE_OPENXML_TYPES = {"docx", "xlsx", "pptx"}
logger = logging.getLogger(__name__)


class OfficePreparationError(RuntimeError):
    """Stable, user-facing failure raised before a parser reads an Office file."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class OfficeAutomationBackend(Protocol):
    def normalize(self, source: Path, destination: Path, file_type: str) -> None: ...


@dataclass
class PreparedOfficeFile:
    source_path: Path
    path: Path
    file_type: str
    temporary_root: Path

    def close(self) -> None:
        shutil.rmtree(self.temporary_root, ignore_errors=True)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


class PyWin32OfficeBackend:
    """Open Office through an authenticated desktop Office installation.

    The COM application is created and destroyed inside one thread.  Macros and
    external-link updates are disabled, the source is opened read-only, and only
    a temporary OpenXML copy is passed to downstream parsers.
    """

    _APPLICATIONS: ClassVar[dict[str, tuple[str, str]]] = {
        "docx": ("Word.Application", "Microsoft Word"),
        "xlsx": ("Excel.Application", "Microsoft Excel"),
        "pptx": ("PowerPoint.Application", "Microsoft PowerPoint"),
    }

    def normalize(self, source: Path, destination: Path, file_type: str) -> None:
        if platform.system() != "Windows":
            raise OfficePreparationError(
                "OFFICE_COM_UNAVAILABLE",
                "Office processing requires Windows, pywin32, and Microsoft Office.",
            )
        try:
            pythoncom = importlib.import_module("pythoncom")
            win32_client = importlib.import_module("win32com.client")
        except ImportError as exc:
            raise OfficePreparationError(
                "OFFICE_COM_UNAVAILABLE",
                "pywin32 is not installed in the Office worker environment.",
            ) from exc

        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        try:
            if file_type == "docx":
                self._normalize_word(win32_client, source, destination)
            elif file_type == "xlsx":
                self._normalize_excel(win32_client, source, destination)
            elif file_type == "pptx":
                self._normalize_powerpoint(win32_client, source, destination)
            else:
                raise OfficePreparationError(
                    "OFFICE_TYPE_UNSUPPORTED",
                    f"Unsupported Office file type: .{file_type}",
                )
        finally:
            pythoncom.CoUninitialize()

    def _dispatch(self, win32_client, file_type: str):
        prog_id, display_name = self._APPLICATIONS[file_type]
        try:
            return win32_client.DispatchEx(prog_id)
        except Exception as exc:
            raise OfficePreparationError(
                "OFFICE_APPLICATION_UNAVAILABLE",
                f"{display_name} could not be started by the worker account.",
            ) from exc

    def _normalize_word(self, win32_client, source: Path, destination: Path) -> None:
        app = self._dispatch(win32_client, "docx")
        document = None
        try:
            app.Visible = False
            app.DisplayAlerts = 0
            self._disable_macros(app)
            try:
                document = app.Documents.Open(
                    FileName=str(source),
                    ConfirmConversions=False,
                    ReadOnly=True,
                    AddToRecentFiles=False,
                    Revert=False,
                    Visible=False,
                    OpenAndRepair=True,
                    NoEncodingDialog=True,
                )
            except Exception as exc:
                raise self._open_failed("Microsoft Word", exc) from exc
            try:
                document.SaveAs2(
                    FileName=str(destination),
                    FileFormat=16,  # wdFormatDocumentDefault (.docx)
                    AddToRecentFiles=False,
                    ReadOnlyRecommended=False,
                )
            except Exception as exc:
                raise self._save_failed("Microsoft Word", exc) from exc
        finally:
            if document is not None:
                self._best_effort(
                    lambda: document.Close(SaveChanges=False),
                    "word_document_close_failed",
                )
            self._best_effort(
                lambda: app.Quit(SaveChanges=False),
                "word_application_quit_failed",
            )

    def _normalize_excel(self, win32_client, source: Path, destination: Path) -> None:
        app = self._dispatch(win32_client, "xlsx")
        workbook = None
        try:
            app.Visible = False
            app.DisplayAlerts = False
            app.EnableEvents = False
            app.AskToUpdateLinks = False
            self._disable_macros(app)
            self._best_effort(
                lambda: self._disable_excel_calculation(app),
                "excel_manual_calculation_unavailable",
            )
            try:
                workbook = app.Workbooks.Open(
                    Filename=str(source),
                    UpdateLinks=0,
                    ReadOnly=True,
                    Password="",
                    WriteResPassword="",
                    IgnoreReadOnlyRecommended=True,
                    Notify=False,
                    AddToMru=False,
                    Local=True,
                )
            except Exception as exc:
                raise self._open_failed("Microsoft Excel", exc) from exc
            try:
                workbook.SaveAs(
                    Filename=str(destination),
                    FileFormat=51,  # xlOpenXMLWorkbook (.xlsx)
                    Password="",
                    WriteResPassword="",
                    ReadOnlyRecommended=False,
                    CreateBackup=False,
                    AddToMru=False,
                    Local=True,
                )
            except Exception as exc:
                raise self._save_failed("Microsoft Excel", exc) from exc
        finally:
            if workbook is not None:
                self._best_effort(
                    lambda: workbook.Close(SaveChanges=False),
                    "excel_workbook_close_failed",
                )
            self._best_effort(app.Quit, "excel_application_quit_failed")

    def _normalize_powerpoint(self, win32_client, source: Path, destination: Path) -> None:
        app = self._dispatch(win32_client, "pptx")
        presentation = None
        try:
            app.DisplayAlerts = 1  # ppAlertsNone
            self._disable_macros(app)
            try:
                presentation = app.Presentations.Open(
                    FileName=str(source),
                    ReadOnly=True,
                    Untitled=False,
                    WithWindow=False,
                )
            except Exception as exc:
                raise self._open_failed("Microsoft PowerPoint", exc) from exc
            try:
                presentation.SaveAs(
                    str(destination),
                    24,  # ppSaveAsOpenXMLPresentation (.pptx)
                )
            except Exception as exc:
                raise self._save_failed("Microsoft PowerPoint", exc) from exc
        finally:
            if presentation is not None:
                self._best_effort(
                    presentation.Close,
                    "powerpoint_presentation_close_failed",
                )
            self._best_effort(app.Quit, "powerpoint_application_quit_failed")

    @staticmethod
    def _disable_macros(app) -> None:
        PyWin32OfficeBackend._best_effort(
            lambda: setattr(
                app,
                "AutomationSecurity",
                3,  # msoAutomationSecurityForceDisable
            ),
            "office_automation_security_unavailable",
        )

    @staticmethod
    def _disable_excel_calculation(app) -> None:
        app.Calculation = -4135  # xlCalculationManual
        app.CalculateBeforeSave = False

    @staticmethod
    def _best_effort(action, event: str) -> None:
        try:
            action()
        except Exception:
            logger.debug(event, exc_info=True)

    @staticmethod
    def _open_failed(application: str, _exc: Exception) -> OfficePreparationError:
        return OfficePreparationError(
            "OFFICE_OPEN_FAILED",
            f"{application} could not open the file. The worker account may lack "
            "enterprise decryption permission, the file may require an opening "
            "password, or the file may be damaged.",
        )

    @staticmethod
    def _save_failed(application: str, _exc: Exception) -> OfficePreparationError:
        return OfficePreparationError(
            "OFFICE_NORMALIZATION_FAILED",
            f"{application} opened the file but could not create a parser-safe "
            "temporary OpenXML copy. Verify the document protection policy and "
            "the worker account's export permission.",
        )


class OfficeFilePreparationService:
    """Create a short-lived, COM-normalized copy before any Office parser runs."""

    def __init__(self, backend: OfficeAutomationBackend | None = None) -> None:
        self.backend = backend or PyWin32OfficeBackend()
        self._uses_default_backend = backend is None

    async def prepare(
        self,
        file_path: str | Path,
        file_type: str | None = None,
    ) -> PreparedOfficeFile:
        return await asyncio.to_thread(
            self.prepare_bounded_sync,
            file_path,
            file_type,
        )

    def prepare_bounded_sync(
        self,
        file_path: str | Path,
        file_type: str | None = None,
    ) -> PreparedOfficeFile:
        if not self._uses_default_backend:
            return self.prepare_sync(file_path, file_type)

        source, detected_type = self._validated_source(file_path, file_type)
        temporary_root, destination = self._temporary_destination(detected_type)
        try:
            timeout_seconds = min(
                settings.office_com_timeout_seconds,
                max(1, settings.worker_job_timeout_seconds - 5),
            )
            run_in_process(
                normalize_office_file,
                timeout_seconds=timeout_seconds,
                kwargs={
                    "source_path": str(source),
                    "destination_path": str(destination),
                    "file_type": detected_type,
                },
            )
            return self._prepared_result(
                source,
                destination,
                detected_type,
                temporary_root,
            )
        except Exception:
            shutil.rmtree(temporary_root, ignore_errors=True)
            raise

    def prepare_sync(
        self,
        file_path: str | Path,
        file_type: str | None = None,
    ) -> PreparedOfficeFile:
        source, detected_type = self._validated_source(file_path, file_type)
        temporary_root, destination = self._temporary_destination(detected_type)
        try:
            self.backend.normalize(source, destination, detected_type)
            return self._prepared_result(
                source,
                destination,
                detected_type,
                temporary_root,
            )
        except Exception:
            shutil.rmtree(temporary_root, ignore_errors=True)
            raise

    def _validated_source(
        self,
        file_path: str | Path,
        file_type: str | None,
    ) -> tuple[Path, str]:
        source = Path(file_path).resolve()
        detected_type = (file_type or source.suffix.lstrip(".")).lower()
        if detected_type not in OFFICE_OPENXML_TYPES:
            raise OfficePreparationError(
                "OFFICE_TYPE_UNSUPPORTED",
                "Office processing accepts only .docx, .xlsx, and .pptx files.",
            )
        if not source.is_file():
            raise FileNotFoundError(f"Office document artifact does not exist: {source}")
        return source, detected_type

    @staticmethod
    def _temporary_destination(file_type: str) -> tuple[Path, Path]:
        temporary_root = Path(tempfile.mkdtemp(prefix="lm_agent_office_"))
        return temporary_root, temporary_root / f"normalized.{file_type}"

    @staticmethod
    def _prepared_result(
        source: Path,
        destination: Path,
        file_type: str,
        temporary_root: Path,
    ) -> PreparedOfficeFile:
        if not destination.is_file() or destination.stat().st_size == 0:
            raise OfficePreparationError(
                "OFFICE_NORMALIZATION_FAILED",
                "Microsoft Office did not create a readable temporary copy.",
            )
        if not zipfile.is_zipfile(destination):
            raise OfficePreparationError(
                "OFFICE_OUTPUT_STILL_PROTECTED",
                "The temporary copy is still encrypted or protected and cannot "
                "be passed to the OpenXML parser. Verify the worker account's "
                "Microsoft 365 rights and sensitivity-label policy.",
            )
        return PreparedOfficeFile(
            source_path=source,
            path=destination,
            file_type=file_type,
            temporary_root=temporary_root,
        )


def normalize_office_file(
    *,
    source_path: str,
    destination_path: str,
    file_type: str,
) -> None:
    """Pickle-safe Office COM entrypoint for the bounded document subprocess."""

    PyWin32OfficeBackend().normalize(
        Path(source_path),
        Path(destination_path),
        file_type,
    )
