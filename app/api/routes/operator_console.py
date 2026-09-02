from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app.core.config import Settings, get_settings
from app.core.operator_auth import (
    OPERATOR_SESSION_COOKIE,
    create_operator_session,
    valid_operator_key,
    valid_operator_session,
)


router = APIRouter(tags=["operator-console"])
_STATIC_ROOT = Path(__file__).resolve().parents[2] / "static"


@router.get("/operator/login", response_class=HTMLResponse)
def operator_login_page(request: Request) -> HTMLResponse:
    error = request.query_params.get("error") == "1"
    html = (_STATIC_ROOT / "operator-login.html").read_text(encoding="utf-8")
    return HTMLResponse(
        html.replace(
            "{{ERROR}}",
            "Invalid operator key." if error else "",
        ),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/operator/login")
async def operator_login(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> RedirectResponse:
    body = await request.body()
    if len(body) > 8192:
        return RedirectResponse("/operator/login?error=1", status_code=303)
    parsed = parse_qs(body.decode("utf-8", errors="replace"), max_num_fields=4)
    provided = parsed.get("operator_key", [None])[0]
    if not valid_operator_key(provided, settings):
        return RedirectResponse("/operator/login?error=1", status_code=303)
    response = RedirectResponse("/operator", status_code=303)
    response.set_cookie(
        OPERATOR_SESSION_COOKIE,
        create_operator_session(settings),
        max_age=settings.operator_session_max_age_seconds,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/",
    )
    return response


@router.post("/operator/logout")
def operator_logout() -> RedirectResponse:
    response = RedirectResponse("/operator/login", status_code=303)
    response.delete_cookie(OPERATOR_SESSION_COOKIE, path="/")
    return response


@router.get("/operator")
def operator_console(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
):
    if not valid_operator_session(
        request.cookies.get(OPERATOR_SESSION_COOKIE), settings
    ):
        return RedirectResponse("/operator/login", status_code=303)
    return FileResponse(
        _STATIC_ROOT / "operator-console.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )
