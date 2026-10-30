"""
diagrams-v2 — Agentic Code-to-UML Generator

Entry point for the FastAPI application.
"""

import asyncio
import os

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, repo_upload, ir_generation, diagram_generation, projects, dependency_management, document_ingestion, operations
from app.utils.config import load_config, settings
from app.utils.logger import get_logger

logger = get_logger(__name__)
config = load_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize SQS queues and start worker if enabled."""
    if settings.sqs_enabled:
        logger.info("SQS mode enabled — initializing queues and starting worker...")
        from app.utils.sqs_initializer import initialize_queues
        queues_ok = initialize_queues()
        if queues_ok:
            logger.info(" SQS queues ready")
            # Pre-warm queue URL cache so proxy calls never block on resolution
            from app.utils.sqs_client import get_queue_url
            get_queue_url(settings.sqs_request_queue_name)
            get_queue_url(settings.sqs_response_queue_name)
            logger.info(" Queue URLs cached")
            # Start the SQS consumer loop as a background task
            from app.worker.diagram_worker import sqs_consumer_loop
            worker_task = asyncio.create_task(sqs_consumer_loop())
            logger.info(" SQS worker started")
        else:
            logger.warning("  SQS queue initialization failed — falling back to sync mode")
            settings.sqs_enabled = False
            worker_task = None
    else:
        logger.info("  Running in sync mode (APP_SQS_ENABLED=false)")
        worker_task = None

    yield  # Application runs here

    # Shutdown: cancel worker if running
    if worker_task and not worker_task.done():
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            logger.info("SQS worker stopped")


app = FastAPI(
    title="UML Diagram Generator v2",
    description=(
        "Agentic code-to-UML diagram generator. "
        "Uses Tree-sitter for IR extraction and Claude Agent SDK for diagram generation."
    ),
    version="2.0.0",
    openapi_version="3.0.2",
    docs_url="/ai/docs",
    openapi_url="/ai/openapi.json",
    lifespan=lifespan,
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
app.include_router(health.router, tags=["Health Check"])
app.include_router(repo_upload.router, tags=["Repository Upload"])
app.include_router(ir_generation.router, tags=["IR Generation"])
app.include_router(diagram_generation.router, tags=["Diagram Generation"])
app.include_router(projects.router, tags=["Projects"])
app.include_router(dependency_management.router, tags=["Dependency Management"])
app.include_router(document_ingestion.router, tags=["Document Ingestion"])
app.include_router(operations.router, tags=["Operations Proxy"])


if __name__ == "__main__":
    host = config["fastapi"]["host"]
    port = config["fastapi"]["port"]
    logger.info(f"Starting diagrams-v2 on {host}:{port}")
    uvicorn.run(
        app,
        host=host,
        port=port,
        timeout_keep_alive=int(os.getenv("APP_UVICORN_TIMEOUT", "300")),
    )
