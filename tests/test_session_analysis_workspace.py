import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.core.constants import RetrievalScope
from app.core.exceptions import APIError
from app.schemas.analysis import (
    AggregationSpec,
    AnalysisPlan,
    ChartSpec,
    FilterCondition,
    SortSpec,
)
from app.schemas.chat import ChatQueryRequest
from app.services.spreadsheet_analysis_service import SpreadsheetAnalysisService
from app.storage.local_storage import LocalStorage


def _workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Production"
    sheet.append(["Machine", "Yield", "Batch"])
    sheet.append(["A", 90, "B1"])
    sheet.append(["A", 100, "B2"])
    sheet.append(["B", 80, "B3"])
    sheet.append(["B", 100, "B4"])
    workbook.save(path)


def test_xlsx_inspection_and_deterministic_group_statistics(
    tmp_path: Path,
) -> None:
    path = tmp_path / "production.xlsx"
    _workbook(path)
    service = SpreadsheetAnalysisService()

    inspection = service.inspect(str(path), "xlsx")
    assert inspection[0]["name"] == "Production"
    assert inspection[0]["row_count"] == 4
    assert [column["name"] for column in inspection[0]["columns"]] == [
        "Machine",
        "Yield",
        "Batch",
    ]

    plan = AnalysisPlan(
        sheet="Production",
        group_by=["Machine"],
        aggregations=[
            AggregationSpec(function="count", alias="sample_count"),
            AggregationSpec(
                function="mean",
                column="Yield",
                alias="mean_yield",
            ),
            AggregationSpec(
                function="std",
                column="Yield",
                alias="std_yield",
            ),
            AggregationSpec(
                function="count_if",
                alias="below_95",
                condition=FilterCondition(
                    column="Yield",
                    operator="lt",
                    value=95,
                ),
            ),
        ],
        sort=[SortSpec(column="mean_yield", direction="asc")],
        charts=[
            ChartSpec(
                type="bar",
                x_field="Machine",
                y_field="mean_yield",
                title="各機台平均良率",
            )
        ],
    )
    result = service.execute(str(path), "xlsx", plan)
    rows = result["table"]["rows"]
    assert rows[0]["Machine"] == "B"
    assert rows[0]["mean_yield"] == 90.0
    assert rows[0]["below_95"] == 1
    assert rows[1]["Machine"] == "A"
    assert rows[1]["mean_yield"] == 95.0
    assert rows[1]["sample_count"] == 2
    assert rows[1]["std_yield"] == pytest.approx(7.0710678119)
    assert result["summary"]["processed_rows"] == 4
    assert result["charts"][0]["data"][0] == {
        "Machine": "B",
        "mean_yield": 90.0,
    }


def test_csv_cp950_inspection_and_column_extraction(tmp_path: Path) -> None:
    path = tmp_path / "traditional.csv"
    text = "機台,良率,備註\nA,98.5,正常\nB,91.0,檢查\n"
    path.write_bytes(text.encode("cp950"))
    service = SpreadsheetAnalysisService()

    inspection = service.inspect(str(path), "csv")
    assert inspection[0]["row_count"] is None
    assert inspection[0]["columns"][1]["inferred_type"] == "number"

    plan = AnalysisPlan(
        select=["機台", "良率"],
        filters=[
            FilterCondition(column="良率", operator="lt", value=95)
        ],
    )
    result = service.execute(str(path), "csv", plan)
    assert result["table"]["rows"] == [{"機台": "B", "良率": "91.0"}]


def test_invalid_plan_rejects_missing_and_non_numeric_columns(
    tmp_path: Path,
) -> None:
    path = tmp_path / "production.xlsx"
    _workbook(path)
    service = SpreadsheetAnalysisService()

    with pytest.raises(APIError) as missing:
        service.validate_plan(
            str(path),
            "xlsx",
            AnalysisPlan(select=["NotThere"]),
        )
    assert missing.value.details["missing_columns"] == ["NotThere"]

    with pytest.raises(APIError, match="not numeric"):
        service.validate_plan(
            str(path),
            "xlsx",
            AnalysisPlan(
                aggregations=[
                    AggregationSpec(
                        function="mean",
                        column="Machine",
                        alias="bad",
                    )
                ]
            ),
        )


def test_raw_extraction_sort_is_rejected_to_avoid_inexact_top_n(
    tmp_path: Path,
) -> None:
    path = tmp_path / "production.xlsx"
    _workbook(path)
    with pytest.raises(APIError, match="Sorting raw extracted rows"):
        SpreadsheetAnalysisService().validate_plan(
            str(path),
            "xlsx",
            AnalysisPlan(
                select=["Machine", "Yield"],
                sort=[SortSpec(column="Yield", direction="desc")],
            ),
        )


def test_chat_attachment_scope_requires_explicit_session_and_ids() -> None:
    with pytest.raises(ValueError, match="session_id"):
        ChatQueryRequest(
            query="test",
            attachment_ids=["00000000-0000-0000-0000-000000000001"],
        )
    with pytest.raises(ValueError, match="attachment_ids"):
        ChatQueryRequest(
            query="test",
            session_id="00000000-0000-0000-0000-000000000002",
            retrieval_scope=RetrievalScope.ATTACHMENTS_ONLY,
        )


class _Upload:
    def __init__(self, payload: bytes) -> None:
        self._buffer = BytesIO(payload)

    async def read(self, size: int) -> bytes:
        return self._buffer.read(size)


def test_streaming_upload_enforces_limit_and_cleans_partial_file(
    tmp_path: Path,
) -> None:
    storage = LocalStorage(root=str(tmp_path))
    path, size = asyncio.run(
        storage.save_upload(
            "analysis",
            "ok.csv",
            _Upload(b"a,b\n1,2\n"),
            max_bytes=100,
        )
    )
    assert size == 8
    assert Path(path).read_bytes() == b"a,b\n1,2\n"

    with pytest.raises(ValueError, match="exceeds"):
        asyncio.run(
            storage.save_upload(
                "analysis",
                "too-large.csv",
                _Upload(b"x" * 20),
                max_bytes=10,
            )
        )
    assert not (tmp_path / "analysis" / "too-large.csv").exists()
