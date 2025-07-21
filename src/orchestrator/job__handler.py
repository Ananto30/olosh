import asyncio
import concurrent.futures
import json
import os
import tempfile
import time
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

import src.protos.agent_service_pb2 as pb
from src.logger.common import logger
from src.orchestrator.agent__handler import agents, select_least_busy_agent
from src.orchestrator.config import MAX_IMAGE_SIZE, STALE_AGENT_TIMEOUT
from src.orchestrator.image_utils import extract_manifest_config

router = APIRouter()


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
jobs: Dict[str, JobInfo] = {}
log_waiters: Dict[str, asyncio.Queue] = {}


@router.post("/run-dockerimage")
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

    # Check manifest and config for os and arch using multiprocessing and decoupled logic
    loop = asyncio.get_event_loop()
    with concurrent.futures.ProcessPoolExecutor() as pool:
        manifest_os, manifest_arch, manifest_err = await loop.run_in_executor(
            pool, extract_manifest_config, temp_path
        )
    if manifest_err:
        os.remove(temp_path)
        raise HTTPException(400, manifest_err)

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
    best = select_least_busy_agent(agents_with_matching_platform)
    if not best:
        raise HTTPException(503, "No suitable agents available")

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


@router.get("/jobs")
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


@router.get("/jobs/{job_id}/logs", response_class=PlainTextResponse)
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


@router.get("/jobs/{job_id}/status")
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
