"""Change Analyzer endpoints: stored comparisons, their tables, row edits and the Excel export.

A comparison itself is started from chat (the Mind delegates it); these routes serve what it stored.
Nothing here calls a model: a user's edits to a row are rules in code (`ChangeRow.checked`).
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from docsmind.agent.subagents.change_analysis.export import rows_to_xlsx
from docsmind.agent.subagents.change_analysis.rows import row_to_json
from docsmind.domain.changes import Binding, ChangeRow, Nature
from docsmind.server.routes.chat import get_agent

router = APIRouter(tags=["changes"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
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
    await _analysis(request, analysis_id)
    rows = await get_agent(request).changes.list_rows(analysis_id)
    if only == "actionable":
        rows = [r for r in rows if r.actionable]
    if natures:
        rows = [r for r in rows if r.nature in natures]
    data = await asyncio.to_thread(rows_to_xlsx, rows)
    name = f"change-table-{str(analysis_id)[:8]}.xlsx"
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"'})
