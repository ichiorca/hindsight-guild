"""Experiments — running, decided, drift."""

from fastapi import APIRouter

from mongo import queries

router = APIRouter()


@router.get("/api/experiments/running")
def list_running(channel: str | None = None, icp_segment: str | None = None):
    return queries.running_experiments(channel=channel, icp_segment=icp_segment)


@router.get("/api/experiments/decided")
def list_decided(limit: int = 10):
    return queries.recent_decisions(limit=limit)


@router.get("/api/experiments/drift")
def list_drift():
    return queries.drift_investigations()
