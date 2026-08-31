import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes.leads import router as leads_router
from app.core.exceptions import GTMAgentOSError
from app.core.logging import configure_logging


configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="GTM AgentOS",
    version="0.2.0",
    description="Phase 1 qualification and Phase 2 LangGraph orchestration",
)
app.include_router(leads_router)


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
