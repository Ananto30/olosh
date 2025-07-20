import asyncio
import json
import logging
import os
import time
import uuid
from typing import Dict, List, Optional

import grpc
import grpc.aio
import protos.agent_service_pb2 as pb
import protos.agent_service_pb2_grpc as grpc_pb
from agent_service import AgentService
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field, PrivateAttr

# orchestrator.py
# ------------------
# Orchestrator with HTTP API (FastAPI) and gRPC server (grpc.aio).
# HTTP on port 8000; gRPC on port 50051.


# ─── LOGGER ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("orchestrator")


# ─── DATA MODELS ───────────────────────────────────────────────────────────────
class AgentRegistration(BaseModel):
    hostname: str
    cpu: int
    mem: int
    labels: List[str]


class Agent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    hostname: str
    cpu: int
    mem: int
    labels: List[str]
    last_seen: float = Field(default_factory=time.time)
    running_containers: List[str] = Field(default_factory=list)
    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    _queue: asyncio.Queue = PrivateAttr(default_factory=asyncio.Queue)

    @classmethod
    def from_registration(cls, reg: AgentRegistration):
        return cls(
            hostname=reg.hostname,
            cpu=reg.cpu,
            mem=reg.mem,
            labels=reg.labels,
        )


class JobInfo(BaseModel):
    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    dockerfile: str
    run_params: Dict
    docker_image: Optional[bytes] = None
    status: str = "pending"
    agent_id: Optional[str] = None
    container_id: Optional[str] = None
    detail: str = ""
    created: float = Field(default_factory=time.time)
    updated: float = Field(default_factory=time.time)
    logs_full: Optional[str] = None


# ─── IN‑MEMORY STATE ───────────────────────────────────────────────────────────
agents: Dict[str, Agent] = {}
jobs: Dict[str, JobInfo] = {}
# For synchronous log requests via HTTP
log_waiters: Dict[str, asyncio.Queue] = {}


# ─── gRPC SERVER STARTUP ───────────────────────────────────────────────────────
async def serve_grpc():
    server = grpc.aio.server(
        options=[
            ("grpc.max_send_message_length", 100 * 1024 * 1024),
            ("grpc.max_receive_message_length", 100 * 1024 * 1024),
        ]
    )
    grpc_pb.add_AgentServiceServicer_to_server(
        AgentService(agents, jobs, log_waiters), server
    )
    listen_addr = "[::]:50051"
    server.add_insecure_port(listen_addr)
    await server.start()
    logger.info(f"gRPC server listening on {listen_addr}")
    await server.wait_for_termination()


# ─── BACKGROUND TASKS ────────────────────────────────────────────────────────────
async def cleanup_agents():
    while True:
        await asyncio.sleep(30)
        now = time.time()
        stale = [aid for aid, ag in agents.items() if now - ag.last_seen > 60]
        for aid in stale:
            del agents[aid]
            logger.info(f"Removed stale agent {aid}")


# ─── FASTAPI HTTP APP ──────────────────────────────────────────────────────────
app = FastAPI(
    on_startup=[
        lambda: asyncio.create_task(serve_grpc()),
        lambda: asyncio.create_task(cleanup_agents()),
    ]
)


@app.post("/agents/register")
def http_register(reg: AgentRegistration):
    agent = Agent.from_registration(reg)
    agents[agent.id] = agent
    logger.info(f"HTTP: Registered agent {agent.id}")
    return {"agent_id": agent.id}


@app.get("/agents")
def http_list_agents():
    now = time.time()
    return {
        "agents": {
            aid: {
                "hostname": ag.hostname,
                "cpu": ag.cpu,
                "mem": ag.mem,
                "labels": ag.labels,
                "running_containers": len(ag.running_containers),
                "last_seen": ag.last_seen,
                "cpu_percent": getattr(ag, "cpu_percent", 0.0),
                "mem_percent": getattr(ag, "mem_percent", 0.0),
            }
            for aid, ag in agents.items()
            if now - ag.last_seen < 60
        }
    }


@app.post("/run-dockerimage")
async def http_run_dockerimage(
    docker_image: UploadFile = File(...), run_params: str = Form("{}")
):
    """
    Submit a Docker image tarball (as produced by `docker save`).
    Accepts multipart/form-data with fields:
      - docker_image: file upload (tarball)
      - run_params: JSON string (optional)
    """

    # Read docker image bytes
    docker_image_bytes = await docker_image.read()
    try:
        run_params_dict = json.loads(run_params)
    except Exception:
        run_params_dict = {}

    if not docker_image_bytes:
        raise HTTPException(400, "Missing or empty docker_image upload")

    # create job
    job = JobInfo(
        dockerfile="", run_params=run_params_dict, docker_image=docker_image_bytes
    )

    # pick best agent
    alive = {aid: ag for aid, ag in agents.items() if time.time() - ag.last_seen < 60}
    if not alive:
        raise HTTPException(503, "No available agents")
    best = min(alive.values(), key=lambda a: len(a.running_containers))

    job.agent_id = best.id
    jobs[job.job_id] = job

    # enqueue job assignment over gRPC
    assignment = pb.OrchestratorMessage(
        job_assignment=pb.JobAssignment(
            job_id=job.job_id,
            docker_image=docker_image_bytes,
            run_params={k: str(v) for k, v in job.run_params.items()},
        )
    )
    best._queue.put_nowait(assignment)

    logger.info(f"HTTP: Dispatched job {job.job_id} to agent {best.id}")
    return {"job_id": job.job_id, "agent_id": best.id}


@app.get("/jobs")
def http_list_jobs():
    """
    Return all jobs as JSON.
    """
    return {
        "jobs": [
            {
                "job_id": j.job_id,
                "agent_id": j.agent_id,
                "status": j.status,
                "container_id": j.container_id,
                "detail": j.detail,
                "created": j.created,
                "updated": j.updated,
            }
            for j in jobs.values()
        ]
    }


@app.get("/jobs/{job_id}/logs")
async def http_get_logs(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not job.agent_id:
        raise HTTPException(400, "Job not yet assigned")
    # ask agent for logs
    queue = asyncio.Queue()
    log_waiters[job_id] = queue
    # send request
    agent = agents.get(job.agent_id)
    if not agent:
        raise HTTPException(503, "Agent offline")
    agent._queue.put_nowait(
        pb.OrchestratorMessage(log_request=pb.LogRequest(job_id=job_id))
    )
    try:
        content = await asyncio.wait_for(queue.get(), timeout=10)
        return {"job_id": job_id, "logs": content}
    except asyncio.TimeoutError:
        raise HTTPException(504, "Timeout waiting for logs")
    finally:
        del log_waiters[job_id]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
