"""Health probe endpoint."""
from fastapi import APIRouter

from services.web_api.main import PROJECT_ID, REGION

router = APIRouter()


@router.get("/api/health")
def health():
    return {"ok": True, "project": PROJECT_ID, "region": REGION}
