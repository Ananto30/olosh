#!/usr/bin/env python3
import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel


# ─── MODELS ────────────────────────────────────────────────────────────────────
class AgentRegistration(BaseModel):
    hostname: str
    cpu: int
    mem: int
    labels: List[str]


class HeartbeatPayload(BaseModel):
    last_seen: float
    running_containers: List[str]


class RunDockerfileRequest(BaseModel):
    dockerfile: str
    build_args: Optional[Dict[str, str]] = {}
    run_params: Optional[Dict] = {}


class StatusUpdate(BaseModel):
    status: str  # "running" | "finished" | "failed"
    container_id: str
    detail: Optional[str] = ""


class JobInfo(BaseModel):
    job_id: str
    dockerfile: str
    build_args: Dict[str, str] = {}
    run_params: Dict = {}
    status: str = "pending"  # pending → running → finished/failed
    agent_id: Optional[str] = None
    container_id: Optional[str] = None
    detail: Optional[str] = ""
    created: float = time.time()
    updated: float = time.time()


class Agent(AgentRegistration):
    id: str
    last_seen: float
    running_containers: List[str] = []


# ─── IN‑MEMORY STATE ───────────────────────────────────────────────────────────
agents: Dict[str, Agent] = {}
jobs: Dict[str, JobInfo] = {}


# ─── BACKGROUND CLEANUP ─────────────────────────────────────────────────────────
async def cleanup_agents():
    """
    Remove agents that haven’t heart‑beated in >60s.
    """
    while True:
        await asyncio.sleep(30)
        now = time.time()
        stale = [aid for aid, ag in agents.items() if now - ag.last_seen > 60]
        for aid in stale:
            del agents[aid]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # start cleanup task
    asyncio.create_task(cleanup_agents())
    yield


# ─── FASTAPI APP ───────────────────────────────────────────────────────────────
app = FastAPI(lifespan=lifespan)


# ─── AGENT ENDPOINTS ───────────────────────────────────────────────────────────
@app.post("/agents/register")
def register_agent(reg: AgentRegistration):
    agent_id = str(uuid.uuid4())
    agents[agent_id] = Agent(
        id=agent_id,
        hostname=reg.hostname,
        cpu=reg.cpu,
        mem=reg.mem,
        labels=reg.labels,
        last_seen=time.time(),
    )
    return {"agent_id": agent_id}


@app.post("/agents/{agent_id}/heartbeat")
def heartbeat(agent_id: str, payload: HeartbeatPayload):
    agent = agents.get(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    # 1) update liveness + running containers
    agent.last_seen = payload.last_seen
    agent.running_containers = payload.running_containers

    # 2) reconcile any “running” jobs for this agent
    for job in jobs.values():
        if job.agent_id == agent_id and job.status == "running":
            if job.container_id not in payload.running_containers:
                job.status = "finished"
                job.updated = time.time()

    return {"status": "ok"}


@app.get("/agents")
def list_agents():
    """
    List all agents seen in the last minute, with their load.
    """
    now = time.time()
    alive = {}
    for aid, ag in agents.items():
        if now - ag.last_seen < 60:
            alive[aid] = {
                "hostname": ag.hostname,
                "cpu": ag.cpu,
                "mem": ag.mem,
                "labels": ag.labels,
                "running_containers": len(ag.running_containers),
            }
    return {"agents": alive}


# ─── JOB SUBMISSION & MONITORING ────────────────────────────────────────────────
@app.post("/run-dockerfile")
def api_run(req: RunDockerfileRequest):
    """
    Submit a Dockerfile job (API).
    """
    job_id = str(uuid.uuid4())
    jobs[job_id] = JobInfo(
        job_id=job_id,
        dockerfile=req.dockerfile,
        build_args=req.build_args or {},
        run_params=req.run_params or {},
    )
    return {"job_id": job_id}


@app.get("/agents/{agent_id}/jobs/next")
def get_next_job(agent_id: str):
    """
    Agent polls this to fetch its next pending job.
    """
    if agent_id not in agents:
        raise HTTPException(404, "Agent not found")

    # pick the oldest pending job
    pending = [j for j in jobs.values() if j.status == "pending"]
    if not pending:
        return PlainTextResponse(status_code=204)

    job = sorted(pending, key=lambda j: j.created)[0]
    job.status = "running"
    job.agent_id = agent_id
    job.updated = time.time()

    return {
        "job_id": job.job_id,
        "dockerfile": job.dockerfile,
        "build_args": job.build_args,
        "run_params": job.run_params,
    }


@app.post("/jobs/{job_id}/status")
def post_job_status(job_id: str, payload: StatusUpdate):
    """
    Agent reports back status changes here.
    """
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    job.status = payload.status
    job.container_id = payload.container_id
    job.detail = payload.detail
    job.updated = time.time()
    return {"status": "ok"}


@app.get("/jobs", response_class=PlainTextResponse)
def list_jobs():
    lines = []
    for j in jobs.values():
        line = f"{j.job_id}: {j.status}"
        if j.status == "failed" and j.detail:
            # just show first line of detail to keep things concise
            first_line = j.detail.splitlines()[0]
            line += f" ({first_line})"
        lines.append(line)
    return "\n".join(lines) if lines else "No jobs submitted."



# ─── SIMPLE HTML UI ────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(
        """
<html>
  <head><title>Orchestrator UI</title></head>
  <body>
    <h1>Submit a Dockerfile Job</h1>
    <form action="/ui/run-dockerfile" method="post">
      <textarea name="dockerfile" rows="20" cols="80"></textarea><br/>
      <button type="submit">Run</button>
    </form>
    <p><a href="/jobs">View all jobs</a></p>
  </body>
</html>
""",
        status_code=200,
    )


@app.post("/ui/run-dockerfile", response_class=HTMLResponse)
def ui_run_dockerfile(dockerfile: str = Form(...)):
    job_id = str(uuid.uuid4())
    jobs[job_id] = JobInfo(job_id=job_id, dockerfile=dockerfile)
    return HTMLResponse(
        content=f"""
<p>Job <strong>{job_id}</strong> submitted.</p>
<p><a href="/jobs">View all jobs</a></p>
""",
        status_code=200,
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
