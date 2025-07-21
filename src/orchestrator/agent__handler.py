import asyncio
import time
from typing import Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field, PrivateAttr

from src.logger.common import logger
from src.orchestrator.config import STALE_AGENT_TIMEOUT

router = APIRouter()


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
    busy: bool = False
    busy_since: Optional[float] = None
    heartbeat_history: List[Dict] = Field(
        default_factory=list
    )  # [{timestamp, cpu_percent, mem_percent}]
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

    def update_heartbeat(self, cpu_percent: float, mem_percent: float):
        now = time.time()
        self.last_seen = now
        self.cpu_percent = cpu_percent
        self.mem_percent = mem_percent
        self.heartbeat_history.append(
            {
                "timestamp": now,
                "cpu_percent": cpu_percent,
                "mem_percent": mem_percent,
            }
        )
        if len(self.heartbeat_history) > 100:
            self.heartbeat_history = self.heartbeat_history[-100:]
        # Mark busy if above 70% cpu and mem
        if cpu_percent > 70 and mem_percent > 70:
            if not self.busy:
                self.busy = True
                self.busy_since = now
        else:
            # If busy, check if below 70% for 5 minutes
            if self.busy:
                # Only clear busy if last 5 minutes are below 70%
                cutoff = now - 300
                recent = [h for h in self.heartbeat_history if h["timestamp"] >= cutoff]
                if recent and all(
                    h["cpu_percent"] < 70 and h["mem_percent"] < 70 for h in recent
                ):
                    self.busy = False
                    self.busy_since = None


# ─── IN‑MEMORY STATE ───────────────────────────────────────────────────────────
agents: Dict[str, Agent] = {}


def select_least_busy_agent(agents: List[Agent]) -> Optional[Agent]:
    # Only consider agents not marked busy
    candidates = [ag for ag in agents if not ag.busy]
    if not candidates:
        return None

    # Use average of last 5 heartbeats for cpu/mem
    def avg_usage(agent):
        recent = agent.heartbeat_history[-5:] if agent.heartbeat_history else []
        if not recent:
            return (agent.cpu_percent, agent.mem_percent)
        avg_cpu = sum(h["cpu_percent"] for h in recent) / len(recent)
        avg_mem = sum(h["mem_percent"] for h in recent) / len(recent)
        return (avg_cpu, avg_mem)

    # Sort by running containers, then avg cpu, then avg mem
    return min(
        candidates,
        key=lambda a: (len(a.running_containers), *avg_usage(a)),
    )


@router.post("/agents/register")
def http_register(reg: AgentRegistration):
    agent = Agent.from_registration(reg)
    agents[agent.id] = agent
    logger.info(f"HTTP: Registered agent {agent.id}")
    return {"agent_id": agent.id}


@router.get("/agents")
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
