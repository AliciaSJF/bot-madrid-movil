"""Marcar y desmarcar polideportivos favoritos del perfil (solo en esta app, no en el portal)."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

from src.api.deps import CurrentProfile, HouseConn, redirect
from src.db import centers as centers_repo

router = APIRouter(prefix="/favoritos")


@router.post("/{center_id:int}")
def toggle(
    request: Request,
    conn: HouseConn,
    profile: CurrentProfile,
    center_id: int,
    favorite: Annotated[bool, Form()],
    next: Annotated[str, Form()] = "/reservar",
) -> Response:
    if centers_repo.get_center(conn, center_id) is not None:
        centers_repo.set_favorite(conn, profile.id, center_id, favorite)
        conn.commit()
    # La página lo pide con fetch() para no recargar; sin JS vuelve a la lista
    if request.headers.get("x-requested-with") == "fetch":
        return Response(status_code=204)
    return redirect(next if next.startswith("/reservar") else "/reservar")
