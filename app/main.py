import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes.leads import router as leads_router
from app.api.routes.knowledge import router as knowledge_router
from app.core.exceptions import GTMAgentOSError
from app.core.logging import configure_logging


configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="GTM AgentOS",
    version="0.4.0",
    description=(
        "Lead qualification, LangGraph orchestration, grounded GTM RAG, and "
        "controlled MCP tools"
    ),
)
app.include_router(leads_router)
app.include_router(knowledge_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


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
