"""On-demand embeddings and image captions.

Nothing here runs by itself: the UI reads `/enrichment/status` to learn what is
missing, asks `/enrichment/{kind}/estimate` for an up-front cost, and only
starts work via `/enrichment/{kind}/run` once the user confirms.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from docsmind.enrichment.service import EnrichmentService
from docsmind.server.jobs import JobConflictError, JobManager

router = APIRouter(tags=["enrichment"])

Kind = Literal["embeddings", "captions"]


class Scope(BaseModel):
    version_ids: list[UUID] | None = None  # None = every document


def get_service(request: Request) -> EnrichmentService:
    service = getattr(request.app.state, "enrichment", None)
    if service is None:
        from docsmind.adapters.storage.postgres.repository import PostgresRepository

        config = request.app.state.config
        service = EnrichmentService(config, PostgresRepository(config))
        request.app.state.enrichment = service
    return service


def get_jobs(request: Request) -> JobManager:
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        jobs = JobManager()
        request.app.state.jobs = jobs
    return jobs


@router.get("/enrichment/status")
async def enrichment_status(request: Request, version_id: UUID | None = None) -> dict:
    service = get_service(request)
    jobs = get_jobs(request)
    status = await service.status([version_id] if version_id else None)
    for kind in ("embeddings", "captions"):
        job = jobs.latest(kind)
        status[kind]["job"] = job.to_dict() if job else None
    return status


@router.post("/enrichment/{kind}/estimate")
async def enrichment_estimate(request: Request, kind: Kind, scope: Scope | None = None) -> dict:
    service = get_service(request)
    version_ids = scope.version_ids if scope else None
    estimate = await service.estimate(kind, version_ids)
    estimate["configured"] = (reason := service.readiness(kind)) is None
    estimate["reason"] = reason
    return estimate


@router.post("/enrichment/{kind}/run")
async def enrichment_run(request: Request, kind: Kind, scope: Scope | None = None) -> dict:
    service = get_service(request)
    reason = service.readiness(kind)
    if reason:
        raise HTTPException(status_code=400, detail=reason)
    try:
        job = get_jobs(request).start(kind, service, scope.version_ids if scope else None)
    except JobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.to_dict()


@router.get("/enrichment/jobs/{job_id}")
async def enrichment_job(request: Request, job_id: str) -> dict:
    job = get_jobs(request).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found (the server may have restarted).")
    return job.to_dict()


@router.post("/enrichment/jobs/{job_id}/cancel")
async def enrichment_cancel(request: Request, job_id: str) -> dict:
    job = get_jobs(request).cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job.to_dict()
