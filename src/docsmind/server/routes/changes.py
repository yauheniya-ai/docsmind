"""Change Analyzer endpoints: stored comparisons, their tables, row edits, verification and the exports.

A comparison itself is started from chat (the Mind delegates it); these routes serve what it stored.
Only `POST /changes/{id}/verify` calls a model: it runs the Change Analyzer's verification on the
rows a person picked. A user's edits to a row are rules in code (`ChangeRow.checked`).
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from docsmind.agent.subagents.change_analysis.export import export_filename, info_rows, rows_to_xlsx
from docsmind.agent.subagents.change_analysis.report_export import markdown_to_docx, report_markdown
from docsmind.agent.subagents.change_analysis.rows import row_to_json
from docsmind.domain.changes import Binding, ChangeRow, Nature
from docsmind.domain.errors import AgentError, LLMConfigurationError, ToolInputError
from docsmind.server.routes.chat import get_agent

router = APIRouter(tags=["changes"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
# Editing any of these changes the claim a row makes, so earlier verification no longer applies.
CLAIM_FIELDS = ("current_clause", "previous_clause", "nature", "compliance_requirement", "binding")


class RowIn(BaseModel):
    current_clause: str = Field(default="", max_length=2_000)
    previous_clause: str = Field(default="", max_length=2_000)
    change_description: str = Field(default="", max_length=4_000)
    nature: Nature = Nature.MODIFIED
    category: str = Field(default="", max_length=200)
    compliance_requirement: str = Field(default="No change", max_length=2_000)
    binding: Binding = Binding.UNCHANGED
    applicability_scope: str = Field(default="", max_length=500)


class VerifyIn(BaseModel):
    row_ids: list[UUID] = Field(min_length=1, max_length=50)


class RowPatch(BaseModel):
    current_clause: str | None = Field(default=None, max_length=2_000)
    previous_clause: str | None = Field(default=None, max_length=2_000)
    change_description: str | None = Field(default=None, max_length=4_000)
    nature: Nature | None = None
    category: str | None = Field(default=None, max_length=200)
    compliance_requirement: str | None = Field(default=None, max_length=2_000)
    binding: Binding | None = None
    applicability_scope: str | None = Field(default=None, max_length=500)
    approved: bool | None = None


async def _analysis(request: Request, analysis_id: UUID):
    analysis = await get_agent(request).changes.get_analysis(analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Change analysis not found.")
    return analysis


async def _row(request: Request, analysis_id: UUID, row_id: UUID) -> ChangeRow:
    row = await get_agent(request).changes.get_row(row_id)
    if row is None or row.analysis_id != analysis_id:
        raise HTTPException(status_code=404, detail="Row not found.")
    return row


def _version_name(version: dict | None) -> str:
    return f"{version['title']} {version['label']}".strip() if version else ""


@router.get("/changes")
async def list_changes(request: Request, limit: int = 50) -> list[dict]:
    return await get_agent(request).changes.list_analyses(limit=min(max(limit, 1), 200))


@router.get("/changes/{analysis_id}")
async def get_change(request: Request, analysis_id: UUID) -> dict:
    agent = get_agent(request)
    analysis = await _analysis(request, analysis_id)
    rows = await agent.changes.list_rows(analysis_id)
    versions = {
        row["id"]: {"version_id": str(row["id"]), "title": row["title"], "label": row["label"]}
        for row in await agent.repo.list_catalog([analysis.current_version_id, analysis.previous_version_id])
    }
    return {
        "analysis": analysis.model_dump(mode="json"),
        "current": versions.get(analysis.current_version_id),
        "previous": versions.get(analysis.previous_version_id),
        "rows": [row_to_json(r) for r in rows],
        "report_markdown": report_markdown(
            analysis,
            _version_name(versions.get(analysis.current_version_id)),
            _version_name(versions.get(analysis.previous_version_id)),
        ),
    }


@router.delete("/changes/{analysis_id}")
async def delete_change(request: Request, analysis_id: UUID) -> dict:
    if not await get_agent(request).changes.delete_analysis(analysis_id):
        raise HTTPException(status_code=404, detail="Change analysis not found.")
    return {"deleted": True, "id": str(analysis_id)}


@router.post("/changes/{analysis_id}/rows", status_code=201)
async def add_row(request: Request, analysis_id: UUID, body: RowIn) -> dict:
    await _analysis(request, analysis_id)
    if not (body.current_clause.strip() or body.previous_clause.strip()):
        raise HTTPException(status_code=422, detail="A row needs a current or a previous clause.")
    row = ChangeRow(analysis_id=analysis_id, origin="user", **body.model_dump()).checked()
    return row_to_json(await get_agent(request).changes.add_row(row))


@router.patch("/changes/{analysis_id}/rows/{row_id}")
async def edit_row(request: Request, analysis_id: UUID, row_id: UUID, body: RowPatch) -> dict:
    current = await _row(request, analysis_id, row_id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        return row_to_json(current)

    merged = current.model_copy(update=changes).checked()  # the rules apply to a person's edit as well
    fields = {key: getattr(merged, key) for key in changes}
    fields.update(binding=merged.binding, compliance_requirement=merged.compliance_requirement)
    claim_changed = any(getattr(merged, k) != getattr(current, k) for k in CLAIM_FIELDS)
    if claim_changed and current.verified:
        fields.update(verified=False, proof=[], verification_note="Edited after verification: the proof no longer applies.")
    updated = await get_agent(request).changes.update_row(row_id, **fields)
    return row_to_json(updated)


@router.post("/changes/{analysis_id}/verify")
async def verify_rows(request: Request, analysis_id: UUID, body: VerifyIn) -> dict:
    """Run the Change Analyzer's verification on rows of the table: any row, not only the
    compliance-relevant ones. Waits for the result (a row takes about a minute)."""
    await _analysis(request, analysis_id)
    for row_id in body.row_ids:
        await _row(request, analysis_id, row_id)
    try:
        rows = await get_agent(request).verify_rows(analysis_id, body.row_ids)
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (AgentError, ToolInputError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"rows": [row_to_json(r) for r in rows]}


@router.delete("/changes/{analysis_id}/rows/{row_id}")
async def delete_row(request: Request, analysis_id: UUID, row_id: UUID) -> dict:
    await _row(request, analysis_id, row_id)
    await get_agent(request).changes.delete_row(row_id)
    return {"deleted": True, "id": str(row_id)}


@router.get("/changes/{analysis_id}/export.xlsx")
async def export_xlsx(
    request: Request,
    analysis_id: UUID,
    only: Literal["all", "actionable"] = "all",
    natures: Annotated[list[Nature] | None, Query()] = None,
) -> Response:
    agent = get_agent(request)
    analysis = await _analysis(request, analysis_id)
    all_rows = await agent.changes.list_rows(analysis_id)
    rows = all_rows
    if only == "actionable":
        rows = [r for r in rows if r.actionable]
    if natures:
        rows = [r for r in rows if r.nature in natures]
    titles = {
        row["id"]: row["title"]
        for row in await agent.repo.list_catalog([analysis.current_version_id, analysis.previous_version_id])
    }
    current = titles.get(analysis.current_version_id) or analysis.meta.get("current_title", "")
    previous = titles.get(analysis.previous_version_id) or analysis.meta.get("previous_title", "")
    info = info_rows(analysis, current, previous, all_rows)
    data = await asyncio.to_thread(rows_to_xlsx, rows, info)
    name = export_filename(analysis, current)
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/changes/{analysis_id}/report.{fmt}")
async def export_report(request: Request, analysis_id: UUID, fmt: Literal["docx", "md"]) -> Response:
    """The report alone (not the table): Word or Markdown, with a title and the two documents."""
    agent = get_agent(request)
    analysis = await _analysis(request, analysis_id)
    versions = {
        row["id"]: row for row in await agent.repo.list_catalog([analysis.current_version_id, analysis.previous_version_id])
    }
    current, previous = versions.get(analysis.current_version_id), versions.get(analysis.previous_version_id)
    document = report_markdown(analysis, _version_name(current), _version_name(previous))
    if document is None:
        raise HTTPException(status_code=404, detail="The report is not written yet.")
    title = (current or {}).get("title") or analysis.meta.get("current_title", "")
    name = export_filename(analysis, title, kind="change-report", ext=fmt)
    if fmt == "docx":
        data, media_type = await asyncio.to_thread(markdown_to_docx, document), DOCX
    else:
        data, media_type = document.encode("utf-8"), "text/markdown; charset=utf-8"
    return Response(data, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{name}"'})
