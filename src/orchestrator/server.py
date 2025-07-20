# orchestrator.py
# ------------------
# Orchestrator with HTTP API (FastAPI) and gRPC server (grpc.aio).
# HTTP on port 8000; gRPC on port 50051.

import asyncio
import json
import logging
import os
import tarfile
import tempfile
import time
import uuid
from typing import Dict, List, Optional

import grpc
import grpc.aio
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, PrivateAttr

import src.protos.agent_service_pb2 as pb
import src.protos.agent_service_pb2_grpc as grpc_pb
from src.orchestrator.agent_service import AgentService

# ─── LOGGER ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("orchestrator")

# ─── CONFIG ──────────────────────────────────────────────────────────
ORCHESTRATOR_HTTP = os.getenv("ORCHESTRATOR_HTTP", "http://0.0.0.0:8000")
ORCHESTRATOR_GRPC = os.getenv("ORCHESTRATOR_GRPC", "0.0.0.0:50051")

MAX_IMAGE_SIZE = 2000 * 1024 * 1024  # 2000MB
STALE_AGENT_TIMEOUT = 15  # seconds


# ─── DATA MODELS ───────────────────────────────────────────────────────────────
class AgentPlatform(BaseModel):
    os: str
    arch: str


class AgentRegistration(BaseModel):
    id: str
    cpu: int
    mem: int
    labels: List[str]
    platform: AgentPlatform


class Agent(BaseModel):
    id: str
    cpu: int
    mem: int
    labels: List[str]
    last_seen: float = Field(default_factory=time.time)
    running_containers: List[str] = Field(default_factory=list)
    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    platform: AgentPlatform

    _queue: asyncio.Queue = PrivateAttr(default_factory=asyncio.Queue)

    @classmethod
    def from_registration(cls, reg: AgentRegistration):
        return cls(
            id=reg.id,
            cpu=reg.cpu,
            mem=reg.mem,
            labels=reg.labels,
            platform=reg.platform,
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
                "id": ag.id,
                "cpu": ag.cpu,
                "mem": ag.mem,
                "labels": ag.labels,
                "running_containers": len(ag.running_containers),
                "last_seen": ag.last_seen,
                "cpu_percent": getattr(ag, "cpu_percent", 0.0),
                "mem_percent": getattr(ag, "mem_percent", 0.0),
                "platform": {
                    "os": ag.platform.os,
                    "arch": ag.platform.arch,
                },
            }
            for aid, ag in agents.items()
            if now - ag.last_seen < STALE_AGENT_TIMEOUT
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

    # Stream upload to a temporary file to avoid memory spikes
    # Use /dev/shm (RAM disk) if available for much faster temp file writes
    shm_dir = (
        "/dev/shm"
        if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK)
        else None
    )
    if shm_dir:
        logger.info("Using /dev/shm for temp file upload")
    else:
        logger.info("Using default temp dir for upload")
    start_time = time.time()
    if shm_dir:
        tmp = tempfile.NamedTemporaryFile(delete=False, dir=shm_dir)
    else:
        tmp = tempfile.NamedTemporaryFile(delete=False)
    try:
        temp_path = tmp.name
        size = 0
        while True:
            chunk = await docker_image.read(32 * 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_IMAGE_SIZE:
                tmp.close()
                os.remove(temp_path)
                raise HTTPException(
                    413, f"Docker image too large (>{MAX_IMAGE_SIZE // (1024*1024)}MB)"
                )
            tmp.write(chunk)
        tmp.close()
    finally:
        pass
    elapsed = time.time() - start_time
    logger.info(
        f"Upload received: {size/(1024*1024):.2f} MB in {elapsed:.2f}s ({(size/1024/1024)/elapsed if elapsed>0 else 0:.2f} MB/s)"
    )

    try:
        run_params_dict = json.loads(run_params)
    except Exception:
        run_params_dict = {}

    if size == 0:
        os.remove(temp_path)
        raise HTTPException(400, "Missing or empty docker_image upload")

    alive = {
        aid: ag
        for aid, ag in agents.items()
        if time.time() - ag.last_seen < STALE_AGENT_TIMEOUT
    }
    if not alive:
        os.remove(temp_path)
        raise HTTPException(503, "No available agents")

    # Check manifest and config for os and arch
    try:
        with tarfile.open(temp_path, mode="r:*") as tar:
            manifest_file = tar.extractfile("manifest.json")
            if manifest_file is None:
                os.remove(temp_path)
                raise HTTPException(400, "manifest.json not found in Docker image")

            manifest = json.loads(manifest_file.read())
            if not manifest or not isinstance(manifest, list):
                os.remove(temp_path)
                raise HTTPException(400, "Invalid Docker image manifest")

            image_info = manifest[0]
            config_filename = image_info.get("Config")
            if not config_filename:
                os.remove(temp_path)
                raise HTTPException(
                    400, "Config filename not found in Docker image manifest"
                )

            config_file = tar.extractfile(config_filename)
            if config_file is None:
                os.remove(temp_path)
                raise HTTPException(
                    400, f"Config file {config_filename} not found in Docker image"
                )

            config_json = json.loads(config_file.read())
            manifest_os = config_json.get("os")
            manifest_arch = config_json.get("architecture")
            if not manifest_os or not manifest_arch:
                os.remove(temp_path)
                raise HTTPException(
                    400, "Missing os or architecture in Docker image config"
                )
    except Exception as e:
        os.remove(temp_path)
        raise HTTPException(400, f"Failed to read Docker image manifest/config: {e}")

    # If not agents with platform info, ask the user to build with --platform of available agents
    agents_with_matching_platform = [
        ag
        for ag in alive.values()
        if ag.platform.os == "darwin"
        or (ag.platform.os == manifest_os and ag.platform.arch == manifest_arch)
    ]
    if not agents_with_matching_platform:
        available = [
            f"{ag.id} (os={ag.platform.os}, arch={ag.platform.arch})"
            for ag in alive.values()
        ]
        os.remove(temp_path)
        raise HTTPException(
            400,
            f"Please build the image with --platform of one of the available agents: {', '.join(available)}",
        )

    # Read the file back into memory for gRPC (if needed)
    with open(temp_path, "rb") as f:
        docker_image_bytes = f.read()

    # Clean up temp file
    os.remove(temp_path)

    # Create job
    job = JobInfo(
        dockerfile="", run_params=run_params_dict, docker_image=docker_image_bytes
    )

    # Pick best agent based on running containers and less cpu/mem usage
    best = min(
        agents_with_matching_platform,
        key=lambda a: (len(a.running_containers), a.cpu_percent, a.mem_percent),
    )

    job.agent_id = best.id
    jobs[job.job_id] = job

    # Enqueue job assignment over gRPC
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
                "logs_url": f"/jobs/{j.job_id}/logs",
            }
            for j in jobs.values()
        ]
    }


@app.get("/jobs/{job_id}/logs", response_class=PlainTextResponse)
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
        return content
    except asyncio.TimeoutError:
        raise HTTPException(504, "Timeout waiting for logs")
    finally:
        del log_waiters[job_id]


@app.get("/jobs/{job_id}/status")
def http_get_job_status(job_id: str):
    """
    Get the status of a specific job.
    """
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {
        "job_id": job.job_id,
        "agent_id": job.agent_id,
        "status": job.status,
        "container_id": job.container_id,
        "detail": job.detail,
        "created": job.created,
        "updated": job.updated,
    }


if __name__ == "__main__":
    import uvicorn

    host = ORCHESTRATOR_HTTP.split("://")[-1].split(":")[0]
    port = int(ORCHESTRATOR_HTTP.split(":")[-1])
    logger.info(f"Starting HTTP server on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
