# orchestrator.py
# ------------------
# Orchestrator with HTTP API (FastAPI) and gRPC server (grpc.aio).
# HTTP on port 8000; gRPC on port 50051.

import asyncio
import logging
import os
import time
import uuid
from typing import Dict, List, Optional

import grpc
import grpc.aio
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from protos import agent_service_pb2 as pb
from protos import agent_service_pb2_grpc as grpc_pb

# ─── LOGGER ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("orchestrator")


# ─── DATA MODELS ───────────────────────────────────────────────────────────────
class AgentRegistration(BaseModel):
    hostname: str
    cpu: int
    mem: int
    labels: List[str]


class HeartbeatPayload(BaseModel):
    timestamp: int
    running_containers: List[str]


class RunDockerfileRequest(BaseModel):
    dockerfile: str
    build_args: Optional[Dict[str, str]] = {}
    run_params: Optional[Dict] = {}


class JobResultPayload(BaseModel):
    job_id: str
    status: int
    container_id: str
    detail: str


class LogRequestPayload(BaseModel):
    job_id: str


# Internal representations
class Agent:
    def __init__(self, reg: AgentRegistration):
        self.id = str(uuid.uuid4())
        self.hostname = reg.hostname
        self.cpu = reg.cpu
        self.mem = reg.mem
        self.labels = reg.labels
        self.last_seen = time.time()
        self.running_containers: List[str] = []
        self.queue: asyncio.Queue = asyncio.Queue()


class JobInfo:
    def __init__(self, dockerfile: str, build_args: Dict[str, str], run_params: Dict):
        self.job_id = str(uuid.uuid4())
        self.dockerfile = dockerfile
        self.build_args = build_args
        self.run_params = run_params
        self.status = "pending"
        self.agent_id: Optional[str] = None
        self.container_id: Optional[str] = None
        self.detail: str = ""
        self.created = time.time()
        self.updated = self.created
        self.logs_full: Optional[str] = None


# ─── IN‑MEMORY STATE ───────────────────────────────────────────────────────────
agents: Dict[str, Agent] = {}
jobs: Dict[str, JobInfo] = {}
# For synchronous log requests via HTTP
log_waiters: Dict[str, asyncio.Queue] = {}


# ─── gRPC AGENT SERVICE ────────────────────────────────────────────────────────
class AgentService(grpc_pb.AgentServiceServicer):
    async def Communicate(self, request_iterator, context):
        # Identify agent via metadata
        md = dict(context.invocation_metadata())
        agent_id = md.get("agent-id")
        if not agent_id or agent_id not in agents:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Unknown agent")
        agent = agents[agent_id]
        logger.info(f"gRPC: Agent {agent_id} connected")

        try:
            # Process incoming and outgoing concurrently
            async for msg in request_iterator:
                # Handle heartbeat
                if msg.HasField("heartbeat"):
                    hb = msg.heartbeat
                    agent.last_seen = hb.timestamp
                    agent.running_containers = list(hb.running_containers)
                    # reconcile jobs
                    for job in jobs.values():
                        if job.agent_id == agent_id and job.status == "running":
                            if job.container_id not in agent.running_containers:
                                job.status = "finished"
                                job.updated = time.time()
                                logger.info(
                                    f"Job {job.job_id} marked finished (container gone)"
                                )
                # Handle job result
                elif msg.HasField("job_result"):
                    jr = msg.job_result
                    job = jobs.get(jr.job_id)
                    if job:
                        # map status enum
                        if jr.status == pb.JobResult.RUNNING:
                            job.status = "running"
                        elif jr.status == pb.JobResult.FINISHED:
                            job.status = "finished"
                        else:
                            job.status = "failed"
                        job.container_id = jr.container_id
                        job.detail = jr.detail
                        job.updated = time.time()
                        # satisfy any log waiter for finished job
                        if job.job_id in log_waiters and job.logs_full is not None:
                            # skip; full logs handled separately
                            pass
                        logger.info(f"Job {job.job_id} status updated to {job.status}")
                # Handle log response
                elif msg.HasField("log_response"):
                    lr = msg.log_response
                    job = jobs.get(lr.job_id)
                    if job:
                        job.logs_full = lr.content
                        job.updated = time.time()
                        # deliver to waiter if exists
                        queue = log_waiters.get(lr.job_id)
                        if queue:
                            await queue.put(lr.content)
                # After processing incoming, send any queued messages
                # Drain agent.queue
                while not agent.queue.empty():
                    out = await agent.queue.get()
                    await context.write(out)
        except asyncio.CancelledError:
            logger.info(f"gRPC: Agent {agent_id} disconnected")
        finally:
            logger.info(f"gRPC: Cleanup agent {agent_id}")
            # Optionally cleanup queues


# ─── gRPC SERVER STARTUP ───────────────────────────────────────────────────────
async def serve_grpc():
    server = grpc.aio.server()
    grpc_pb.add_AgentServiceServicer_to_server(AgentService(), server)
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
    agent = Agent(reg)
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
            }
            for aid, ag in agents.items()
            if now - ag.last_seen < 60
        }
    }


@app.post("/run-dockerfile")
async def http_run(request: Request):
    """
    Submit a Dockerfile job.
    Accepts either JSON body:
      { "dockerfile": "...", "build_args": {...}, "run_params": {...} }
    or HTML form data with a 'dockerfile' field.
    """
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        data = await request.json()
        dockerfile = data.get("dockerfile")
        build_args = data.get("build_args", {}) or {}
        run_params = data.get("run_params", {}) or {}
    else:
        form = await request.form()
        dockerfile = form.get("dockerfile")
        build_args = {}
        run_params = {}

    if not dockerfile:
        raise HTTPException(400, "Missing 'dockerfile'")

    # create job
    job = JobInfo(dockerfile, build_args, run_params)

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
            dockerfile=job.dockerfile,
            build_args=job.build_args,
            run_params={k: str(v) for k, v in job.run_params.items()},
        )
    )
    best.queue.put_nowait(assignment)

    logger.info(f"HTTP: Dispatched job {job.job_id} to agent {best.id}")
    return {"job_id": job.job_id, "agent_id": best.id}


@app.get("/jobs")
def http_list_jobs():
    if not jobs:
        return PlainTextResponse("No jobs submitted.")
    lines = []
    for j in jobs.values():
        line = f"{j.job_id}: agent={j.agent_id}, status={j.status}"
        if j.status in ("finished", "failed"):
            line += f", container={j.container_id}"
            if j.status == "failed":
                line += f", detail={j.detail.splitlines()[0]}"
        lines.append(line)
    return PlainTextResponse("\n".join(lines))


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
    agent.queue.put_nowait(
        pb.OrchestratorMessage(log_request=pb.LogRequest(job_id=job_id))
    )
    try:
        content = await asyncio.wait_for(queue.get(), timeout=10)
        return {"job_id": job_id, "logs": content}
    except asyncio.TimeoutError:
        raise HTTPException(504, "Timeout waiting for logs")
    finally:
        del log_waiters[job_id]


@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(
        """
<html><body>
<h1>Submit Dockerfile</h1>
<form action='/run-dockerfile' method='post'>
<textarea name='dockerfile' rows='20' cols='80'></textarea><br/>
<input type='submit' value='Run'>
</form>
</body></html>"""
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
