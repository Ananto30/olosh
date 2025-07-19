#!/usr/bin/env python3
import io
import logging
import os
import tarfile
import threading
import time
import uuid
from typing import Dict, List, Optional

import docker
import requests
from pydantic import BaseModel

# ─── CONFIGURATION ─────────────────────────────────────────────────────────────

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:8000")
HOSTNAME = os.getenv("HOSTNAME", f"agent-{uuid.uuid4().hex[:8]}")
CPU = int(os.getenv("CPU", "2"))
MEM = int(os.getenv("MEM", "2048"))  # in MB
LABELS = os.getenv("LABELS", "").split(",") if os.getenv("LABELS") else []

HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "10"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "5"))
MAX_RUN_TIME = int(os.getenv("MAX_RUN_TIME", "1800"))  # 30 min

# ─── LOGGER ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("agent")

# ─── DOCKER CLIENT ────────────────────────────────────────────────────────────
docker_client = docker.from_env()

# ─── STATE ────────────────────────────────────────────────────────────────────
running_containers: List[str] = []
lock = threading.Lock()


# ─── SCHEMA FOR JOBS ───────────────────────────────────────────────────────────
class Job(BaseModel):
    job_id: str
    dockerfile: str
    build_args: Optional[Dict[str, str]] = {}
    run_params: Optional[Dict] = {}


# ─── REGISTRATION ──────────────────────────────────────────────────────────────
def register_agent() -> str:
    payload = {
        "hostname": HOSTNAME,
        "cpu": CPU,
        "mem": MEM,
        "labels": LABELS,
    }
    resp = requests.post(f"{ORCHESTRATOR_URL}/agents/register", json=payload)
    resp.raise_for_status()
    agent_id = resp.json()["agent_id"]
    logger.info("Registered as agent %s", agent_id)
    return agent_id


# ─── HEARTBEAT LOOP ────────────────────────────────────────────────────────────
def heartbeat_loop(agent_id: str):
    while True:
        with lock:
            containers = list(running_containers)
        payload = {
            "last_seen": time.time(),
            "running_containers": containers,
        }
        try:
            resp = requests.post(
                f"{ORCHESTRATOR_URL}/agents/{agent_id}/heartbeat",
                json=payload,
            )
            resp.raise_for_status()
            logger.debug("Heartbeat ok; %d running containers", len(containers))
        except Exception as e:
            logger.warning("Heartbeat failed: %s", e)
        time.sleep(HEARTBEAT_INTERVAL)


# ─── JOB POLLING LOOP ──────────────────────────────────────────────────────────
def poll_jobs_loop(agent_id: str):
    while True:
        try:
            resp = requests.get(f"{ORCHESTRATOR_URL}/agents/{agent_id}/jobs/next")
            if resp.status_code == 200:
                job = Job(**resp.json())
                logger.info("Picked up job %s", job.job_id)
                threading.Thread(target=run_job, args=(job,), daemon=True).start()
            elif resp.status_code != 204:
                logger.warning("Unexpected /jobs/next status: %s", resp.status_code)
        except Exception as e:
            logger.warning("Error polling for jobs: %s", e)
        time.sleep(POLL_INTERVAL)


# ─── BUILD & RUN ───────────────────────────────────────────────────────────────
def run_job(job: Job):
    """Builds the image, runs the container, enforces timeout, and reports back."""
    job_id = job.job_id

    # 1) Build the image
    tar_stream = io.BytesIO()
    with tarfile.open(fileobj=tar_stream, mode="w") as tar:
        df = job.dockerfile.encode("utf-8")
        info = tarfile.TarInfo("Dockerfile")
        info.size = len(df)
        tar.addfile(info, io.BytesIO(df))
    tar_stream.seek(0)

    tag = f"{HOSTNAME}-{int(time.time())}"
    try:
        image, _ = docker_client.images.build(
            fileobj=tar_stream,
            custom_context=True,
            tag=tag,
            buildargs=job.build_args,
        )
        logger.info("Job %s: build succeeded (image %s)", job_id, image.id)
    except Exception as e:
        logger.error("Job %s: build failed: %s", job_id, e)
        send_status(job_id, "failed", "", detail=str(e))
        return

    # 2) Run the container
    try:
        container = docker_client.containers.run(
            image.id, detach=True, **job.run_params
        )
    except Exception as e:
        logger.error("Job %s: container start failed: %s", job_id, e)
        send_status(job_id, "failed", "", detail=str(e))
        return

    cid = container.id
    with lock:
        running_containers.append(cid)
    logger.info("Job %s: container %s started", job_id, cid)

    # 3) Monitor for timeout
    def monitor():
        time.sleep(MAX_RUN_TIME)
        try:
            c = docker_client.containers.get(cid)
            if c.status == "running":
                c.stop()
                logger.warning("Job %s: container %s timed out", job_id, cid)
        except:
            pass

    threading.Thread(target=monitor, daemon=True).start()

    # 4) Wait for container to exit
    try:
        exit_status = container.wait()
        status = "finished" if exit_status["StatusCode"] == 0 else "failed"
    except Exception as e:
        logger.error("Job %s: error waiting: %s", job_id, e)
        status = "failed"

    # 5) Cleanup
    with lock:
        if cid in running_containers:
            running_containers.remove(cid)

    # 6) Report back
    send_status(job_id, status, cid)


# ─── REPORT STATUS ─────────────────────────────────────────────────────────────
def send_status(job_id: str, status: str, container_id: str, detail: str = ""):
    payload = {
        "status": status,
        "container_id": container_id,
        "detail": detail,
    }
    try:
        resp = requests.post(f"{ORCHESTRATOR_URL}/jobs/{job_id}/status", json=payload)
        resp.raise_for_status()
        logger.info("Job %s: reported status=%s", job_id, status)
    except Exception as e:
        logger.error("Job %s: failed to report status: %s", job_id, e)


# ─── ENTRYPOINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # 1) Register
    agent_id = register_agent()

    # 2) Start heartbeat
    threading.Thread(target=heartbeat_loop, args=(agent_id,), daemon=True).start()

    # 3) Start polling for jobs
    threading.Thread(target=poll_jobs_loop, args=(agent_id,), daemon=True).start()

    # 4) Keep main thread alive
    while True:
        time.sleep(60)
