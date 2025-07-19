import asyncio
import logging
import time

import grpc
import protos.agent_service_pb2 as pb
import protos.agent_service_pb2_grpc as grpc_pb

logger = logging.getLogger("orchestrator")


class AgentService(grpc_pb.AgentServiceServicer):
    def __init__(self, agents, jobs, log_waiters):
        self.agents = agents
        self.jobs = jobs
        self.log_waiters = log_waiters

    async def Communicate(self, request_iterator, context):
        # Identify agent via metadata
        md = dict(context.invocation_metadata())
        agent_id = md.get("agent-id")
        if not agent_id or agent_id not in self.agents:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Unknown agent")
        agent = self.agents[agent_id]
        logger.info(f"gRPC: Agent {agent_id} connected")

        try:
            # Process incoming and outgoing concurrently
            async for msg in request_iterator:
                # Handle heartbeat
                if msg.HasField("heartbeat"):
                    hb = msg.heartbeat
                    agent.last_seen = hb.timestamp
                    agent.running_containers = list(hb.running_containers)
                    agent.cpu_percent = getattr(hb, "cpu_percent", 0.0)
                    agent.mem_percent = getattr(hb, "mem_percent", 0.0)
                    # reconcile jobs
                    for job in self.jobs.values():
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
                # Handle log response
                elif msg.HasField("log_response"):
                    lr = msg.log_response
                    job = self.jobs.get(lr.job_id)
                    if job:
                        job.logs_full = lr.content
                        job.updated = time.time()
                        # deliver to waiter if exists
                        queue = self.log_waiters.get(lr.job_id)
                        if queue:
                            await queue.put(lr.content)
                # After processing incoming, send any queued messages
                while not agent._queue.empty():
                    out = await agent._queue.get()
                    await context.write(out)
        except asyncio.CancelledError:
            logger.info(f"gRPC: Agent {agent_id} disconnected")
        finally:
            logger.info(f"gRPC: Cleanup agent {agent_id}")
