"""Archivos de la PWA que tienen que servirse desde la raíz (el service worker solo controla su carpeta)."""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from src.api.templating import STATIC_DIR

router = APIRouter(include_in_schema=False)


@router.get("/manifest.webmanifest")
def manifest() -> FileResponse:
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@router.get("/sw.js")
def service_worker() -> FileResponse:
    return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript")


@router.get("/favicon.ico")
def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "icon-192.png", media_type="image/png")
