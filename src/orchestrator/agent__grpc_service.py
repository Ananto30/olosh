import asyncio
import logging
import time
from typing import TYPE_CHECKING, AsyncIterable, Dict

import grpc

import src.protos.agent_service_pb2 as pb
import src.protos.agent_service_pb2_grpc as grpc_pb

if TYPE_CHECKING:
    from src.orchestrator.agent__handler import Agent
    from src.orchestrator.job__handler import JobInfo

logger = logging.getLogger("orchestrator")


class AgentService(grpc_pb.AgentServiceServicer):
    def __init__(
        self,
        agents: Dict[str, "Agent"],
        jobs: Dict[str, "JobInfo"],
        log_waiters: Dict[str, asyncio.Queue],
    ):
        self.agents = agents
        self.jobs = jobs
        self.log_waiters = log_waiters

    async def Communicate(
        self,
        request_iterator: AsyncIterable[pb.AgentMessage],
        context: grpc.aio.ServicerContext,
    ) -> None:
        # Identify agent via metadata
        md = dict(context.invocation_metadata() or {})
        agent_id = md.get("agent-id")
        if not agent_id or agent_id not in self.agents:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Unknown agent")

        agent = self.agents[agent_id]
        logger.info(f"gRPC: Agent {agent_id} connected")

        try:
            async for msg in request_iterator:
                if msg.HasField("heartbeat"):
                    await self.handle_heartbeat(agent, agent_id, msg.heartbeat)
                elif msg.HasField("job_result"):
                    await self.handle_job_result(agent_id, msg.job_result)
                elif msg.HasField("log_response"):
                    await self.handle_log_response(msg.log_response)

                # Messages are queued from fastapi server
                # We send them here after listening to the stream
                await self.send_queued_messages(agent, context)
        except asyncio.CancelledError:
            logger.info(f"gRPC: Agent {agent_id} disconnected")
        finally:
            logger.info(f"gRPC: Cleanup agent {agent_id}")
            self.agents.pop(agent_id, None)

    async def handle_heartbeat(
        self, agent: "Agent", agent_id: str, hb: pb.Heartbeat
    ) -> None:
        agent.last_seen = hb.timestamp
        agent.running_containers = list(hb.running_containers)
        cpu_percent = getattr(hb, "cpu_percent", 0.0)
        mem_percent = getattr(hb, "mem_percent", 0.0)
        agent.update_heartbeat(cpu_percent, mem_percent)
        # reconcile jobs
        for job in self.jobs.values():
            if job.agent_id == agent_id and job.status == "running":
                if job.container_id not in agent.running_containers:
                    job.status = "finished"
                    job.updated = time.time()
                    logger.info(f"Job {job.job_id} marked finished (container gone)")

    async def handle_job_result(self, agent_id: str, jr: pb.JobResult) -> None:
        job = self.jobs.get(jr.job_id)
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
            if job.job_id in self.log_waiters and job.logs_full is not None:
                pass
            logger.info(f"Job {job.job_id} status updated to {job.status}")

    async def handle_log_response(self, lr: pb.LogResponse) -> None:
        job = self.jobs.get(lr.job_id)
        if job:
            job.logs_full = lr.content
            job.updated = time.time()
            # deliver to waiter if exists
            queue = self.log_waiters.get(lr.job_id)
            if queue:
                await queue.put(lr.content)

    async def send_queued_messages(
        self, agent: "Agent", context: grpc.aio.ServicerContext
    ) -> None:
        while not agent._queue.empty():
            out = await agent._queue.get()
            await context.write(out)
