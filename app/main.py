import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes.leads import router as leads_router
from app.api.routes.knowledge import router as knowledge_router
from app.api.routes.actions import router as actions_router
from app.api.routes.integrations import router as integrations_router
from app.api.routes.admin import router as admin_router
from app.api.routes.operator_console import router as operator_console_router
from app.api.dependencies import get_observability_service
from app.core.exceptions import GTMAgentOSError
from app.core.logging import configure_logging
from app.services.observability_service import ObservabilityService


configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="GTM AgentOS",
    version="0.6.0",
    description=(
        "Lead qualification, LangGraph orchestration, grounded GTM RAG, and "
        "controlled MCP tools, and approval-gated external actions"
    ),
)
app.include_router(leads_router)
app.include_router(knowledge_router)
app.include_router(actions_router)
app.include_router(integrations_router)
app.include_router(admin_router)
app.include_router(operator_console_router)
app.mount(
    "/operator-assets",
    StaticFiles(directory=Path(__file__).resolve().parent / "static"),
    name="operator-assets",
)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", tags=["health"])
def ready(
    service: ObservabilityService = Depends(get_observability_service),
) -> dict[str, str]:
    return service.readiness()


@app.exception_handler(GTMAgentOSError)
async def handle_application_error(
    request: Request, exc: GTMAgentOSError
) -> JSONResponse:
    logger.warning(
        "request_failed",
        extra={"path": request.url.path, "error_code": exc.code},
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.public_message,
            }
        },
    )
