import asyncio
import time

import grpc
import grpc.aio
from fastapi import FastAPI

import src.protos.agent_service_pb2_grpc as grpc_pb
from orchestrator.agent__grpc_service import AgentService
from src.logger.common import LOG_FORMAT, CustomFormatter, logger
from src.orchestrator.agent__handler import agents
from src.orchestrator.agent__handler import router as agent_router
from src.orchestrator.config import (
    MAX_IMAGE_SIZE,
    ORCHESTRATOR_GRPC,
    ORCHESTRATOR_HTTP,
    STALE_AGENT_TIMEOUT,
)
from src.orchestrator.job__handler import jobs, log_waiters
from src.orchestrator.job__handler import router as job_router


# ─── gRPC SERVER STARTUP ───────────────────────────────────────────────────────
async def serve_grpc():
    server = grpc.aio.server(
        options=[
            ("grpc.max_send_message_length", MAX_IMAGE_SIZE),
            ("grpc.max_receive_message_length", MAX_IMAGE_SIZE),
        ]
    )
    grpc_pb.add_AgentServiceServicer_to_server(
        AgentService(agents, jobs, log_waiters), server
    )
    listen_addr = f"[::]:{ORCHESTRATOR_GRPC.split(':')[-1]}"
    server.add_insecure_port(listen_addr)
    await server.start()
    logger.info(f"gRPC server listening on {listen_addr}")
    await server.wait_for_termination()


# ─── BACKGROUND TASKS ────────────────────────────────────────────────────────────
async def cleanup_agents():
    while True:
        await asyncio.sleep(5)
        now = time.time()
        stale = [
            aid
            for aid, ag in agents.items()
            if now - ag.last_seen > STALE_AGENT_TIMEOUT
        ]
        for aid in stale:
            del agents[aid]
            logger.info(f"Removed stale agent {aid}")


# Scheduler to mark jobs as failed if their agent is missing
async def job_agent_checker():
    while True:
        await asyncio.sleep(5)
        for job in jobs.values():
            if job.agent_id and job.status == "pending" and job.agent_id not in agents:
                job.status = "failed"
                job.detail = "agent died"
                job.updated = time.time()


# ─── FASTAPI HTTP APP ──────────────────────────────────────────────────────────

app = FastAPI(
    on_startup=[
        lambda: asyncio.create_task(serve_grpc()),
        lambda: asyncio.create_task(cleanup_agents()),
        lambda: asyncio.create_task(job_agent_checker()),
    ]
)

app.include_router(agent_router)
app.include_router(job_router)


if __name__ == "__main__":
    import uvicorn

    host = ORCHESTRATOR_HTTP.split("://")[-1].split(":")[0]
    port = int(ORCHESTRATOR_HTTP.split(":")[-1])
    logger.info(f"Starting HTTP server on {host}:{port}")

    # Create a custom log_config for uvicorn to use our formatter and handler

    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "custom": {
                "()": CustomFormatter,
                "fmt": LOG_FORMAT,
            },
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "custom",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.error": {
                "handlers": ["default"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["default"],
                "level": "INFO",
                "propagate": False,
            },
            "orchestrator": {
                "handlers": ["default"],
                "level": "INFO",
                "propagate": False,
            },
        },
        "root": {"handlers": ["default"], "level": "INFO"},
    }

    uvicorn.run(app, host=host, port=port, log_level="info", log_config=log_config)
