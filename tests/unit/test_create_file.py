"""`create_file`: txt, md, csv and xlsx written under the blob folder, with a download link."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from openpyxl import load_workbook

from docsmind.agent.catalog import Catalog
from docsmind.agent.sources import SourceRegistry
from docsmind.agent.tools import build_registry
from docsmind.agent.tools.base import ToolContext
from docsmind.config import DocsmindConfig
from docsmind.domain.agent import ToolCall


def _read_bytes(path: str) -> bytes:
    return Path(path).read_bytes()


@pytest.fixture
def cfg(tmp_path):
    return DocsmindConfig(_env_file=None, blob_root=str(tmp_path), create_file_max_cells=20)


async def _create(cfg, **args):
    ctx = ToolContext(repo=None, catalog=Catalog([]), sources=SourceRegistry(), config=cfg, emit=lambda e: None)
    return await build_registry().execute(ToolCall(id="1", name="create_file", arguments=args), ctx)


def _path(cfg, out):
    name = out.content.split("/blobs/exports/")[1].split(")")[0]
    return f"{cfg.blob_root}/exports/{name}", name


async def test_text_and_markdown_files(cfg):
    for fmt in ("txt", "md"):
        out = await _create(cfg, filename="Summary Notes", format=fmt, content="# Findings\n\nOne.")
        assert not out.is_error
        path, name = _path(cfg, out)
        assert name.startswith("summary-notes-") and name.endswith(f".{fmt}")
        assert _read_bytes(path).decode("utf-8") == "# Findings\n\nOne."


async def test_csv_has_a_bom_and_neutralizes_formulas(cfg):
    out = await _create(cfg, filename="t", format="csv", columns=["Clause", "Text"], rows=[["5.2", "=HYPERLINK(\"x\")"], ["5.3", None]])
    assert not out.is_error
    raw = _read_bytes(_path(cfg, out)[0])
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    assert rows[0] == ["Clause", "Text"] and rows[1][1].startswith("'=") and rows[2] == ["5.3", ""]


async def test_xlsx_with_columns_rows_and_several_sheets(cfg):
    out = await _create(cfg, filename="t", format="xlsx", columns=["A", "B"], rows=[["=1+1", 2]])
    sheet = load_workbook(_path(cfg, out)[0]).active
    assert [c.value for c in sheet[1]] == ["A", "B"] and sheet["A2"].value == "=1+1" and sheet["A2"].data_type == "s"

    out = await _create(cfg, filename="t", format="xlsx", sheets=[
        {"name": "One", "columns": ["x"], "rows": [[1]]}, {"name": "Two", "columns": ["y"], "rows": [[2]]},
    ])  # fmt: skip
    assert load_workbook(_path(cfg, out)[0]).sheetnames == ["One", "Two"]


@pytest.mark.parametrize(
    "args,message",
    [
        ({"format": "txt"}, "needs content"),
        ({"format": "txt", "content": "x", "rows": [["a"]]}, "only content"),
        ({"format": "csv", "content": "x"}, "not content"),
        ({"format": "csv", "columns": ["a"]}, "needs columns and rows"),
        ({"format": "csv", "columns": ["a", "b"], "rows": [["1"]]}, "Row 1 has 1 cells"),
        ({"format": "csv", "columns": ["a"], "rows": [[str(i)] for i in range(30)]}, "more than the limit"),
        ({"format": "csv", "sheets": [{"name": "s", "columns": ["a"], "rows": []}]}, "sheets are for xlsx"),
    ],
)
async def test_wrong_shapes_and_oversize_requests_say_what_to_pass(cfg, args, message):
    out = await _create(cfg, filename="t", **args)
    assert out.is_error and message in out.content


async def test_the_file_size_limit(cfg):
    small = DocsmindConfig(_env_file=None, blob_root=cfg.blob_root, create_file_max_bytes=10)
    out = await _create(small, filename="t", format="txt", content="x" * 100)
    assert out.is_error and "limit" in out.content
