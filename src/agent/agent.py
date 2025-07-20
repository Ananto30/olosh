import io
import logging
import os
import platform
import queue
import subprocess
import sys
import threading
import time
import uuid
from typing import Dict, List, Protocol

import docker
import grpc
import psutil
import requests

import protos.agent_service_pb2 as pb
import protos.agent_service_pb2_grpc as grpc_pb

try:
    import docker

    _docker_available = True
except ImportError:
    _docker_available = False


# ─── CONFIG ────────────────────────────────────────────────────────────────────
ORCHESTRATOR_HTTP = os.getenv("ORCHESTRATOR_HTTP", "http://localhost:8000")
ip = ORCHESTRATOR_HTTP.split("://")[-1].split(":")[0]
ORCHESTRATOR_GRPC = os.getenv("ORCHESTRATOR_GRPC", f"{ip}:50051")
HOSTNAME = os.getenv("HOSTNAME", f"agent-{uuid.uuid4().hex[:8]}")

MAX_IMAGE_SIZE = 2000 * 1024 * 1024  # 2000MB
HEARTBEAT_INTERVAL = 10  # seconds
TIMEOUT_TIMER = 30 * 60  # 30 minutes per container


# ─── LOGGER ───────────────────────────────────────────────────────────────────
class CustomFormatter(logging.Formatter):
    def format(self, record):
        record.hostname = HOSTNAME
        record.threadName = threading.current_thread().name
        # Pad levelname to 7 chars for alignment (e.g., 'WARNING ')
        record.levelname = f"{record.levelname:<7}"
        return super().format(record)


LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(hostname)s] [%(threadName)s] %(message)s"
handler = logging.StreamHandler()
handler.setFormatter(CustomFormatter(LOG_FORMAT))
logger = logging.getLogger("grpc_agent")
logger.setLevel(logging.INFO)
logger.handlers.clear()
logger.addHandler(handler)


# ─── DOCKER CLIENT ────────────────────────────────────────────────────────────


# Try to get a Docker client, fallback to Podman if Docker is not available
def get_container_client():
    if _docker_available:
        try:
            client = docker.from_env()
            # Test connection
            client.ping()
            logger.info("Using Docker as container backend.")
            return client
        except Exception as e:
            logger.warning(f"Docker not available: {e}")
    # Try Podman
    try:
        # Check if podman is installed
        subprocess.run(
            ["podman", "info"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import podman
        from podman import PodmanClient

        client = PodmanClient(base_url="unix:///run/podman/podman.sock")
        logger.info("Using Podman as container backend.")
        return client
    except ImportError:
        logger.error("Neither docker nor podman Python packages are installed.")
    except Exception as e:
        logger.error(f"Podman not available: {e}")
    logger.error("No container backend (docker or podman) available. Exiting.")
    sys.exit(1)


docker_client = get_container_client()


# ─── STATE ─────────────────────────────────────────────────────────────────────
running_containers: List[str] = []
job_containers: Dict[str, str] = {}  # job_id -> container_id
lock = threading.Lock()


# ─── MESSAGE QUEUE ─────────────────────────────────────────────────────────────
message_queue: "queue.Queue[pb.AgentMessage]" = queue.Queue()


def message_generator():
    """
    Yields AgentMessage instances:
    - Heartbeat every HEARTBEAT_INTERVAL seconds
    - JobResult or LogResponse as put into message_queue
    """
    next_hb = time.time()
    while True:
        try:
            msg = message_queue.get(timeout=max(0, next_hb - time.time()))
            yield msg
            continue
        except queue.Empty:
            pass

        # Time for heartbeat
        if time.time() >= next_hb:
            with lock:
                containers = list(running_containers)
            # Get actual CPU and memory usage
            cpu_percent = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            mem_percent = mem.percent
            hb = pb.Heartbeat(
                timestamp=int(time.time()),
                running_containers=containers,
                cpu_percent=cpu_percent,
                mem_percent=mem_percent,
            )
            yield pb.AgentMessage(heartbeat=hb)
            next_hb = time.time() + HEARTBEAT_INTERVAL


def handle_responses(stream):
    """
    Consume OrchestratorMessage from stream:
      - If job_assignment: start a new thread to run the job
      - If log_request: collect logs and queue a LogResponse
    """
    for msg in stream:
        if msg.HasField("job_assignment"):
            job = msg.job_assignment
            logger.info(f"Received job {job.job_id}")
            threading.Thread(target=run_job, args=(job,), daemon=True).start()
        elif msg.HasField("log_request"):
            req = msg.log_request
            threading.Thread(target=send_logs, args=(req.job_id,), daemon=True).start()


def run_job(job: pb.JobAssignment):
    job_id = job.job_id
    # Load docker image from bytes
    try:
        image_stream = io.BytesIO(job.docker_image)
        load_result = docker_client.images.load(image_stream.read())
        # load() returns a list of images, use the first one
        image = load_result[0]
        logger.info(f"Job {job_id}: docker image loaded: {image.id}")
    except Exception as e:
        logger.error(f"Job {job_id}: failed to load docker image: {e}")
        result = pb.JobResult(
            job_id=job_id,
            status=pb.JobResult.FAILED,
            container_id="",
            detail=f"Failed to load docker image: {e}",
        )
        message_queue.put(pb.AgentMessage(job_result=result))
        return

    # Run container with a name for easier management
    container_name = f"olosh-job-{job_id}"
    try:
        import ast

        allowed_keys = {
            "environment",
            "command",
            "entrypoint",
            "working_dir",
            "user",
            "ports",
            "volumes",
        }
        raw_params = dict(job.run_params)
        run_params = {}
        for k in allowed_keys:
            if k in raw_params:
                if k in {"ports", "volumes"}:
                    v = raw_params[k]
                    if isinstance(v, str):
                        try:
                            v = ast.literal_eval(v)
                        except Exception:
                            continue
                    if isinstance(v, dict):
                        run_params[k] = v
                else:
                    run_params[k] = raw_params[k]
        # Ensure image_id is a string, fallback to tag if needed
        image_id = getattr(image, "id", None)
        if not image_id or image_id is None:
            tags = getattr(image, "tags", [])
            if tags:
                image_id = tags[0]
            else:
                raise RuntimeError("No valid image id or tag found for loaded image")
        container = docker_client.containers.run(
            image_id, detach=True, name=container_name, **run_params
        )
        cid = container.id
        if not cid or cid is None:
            raise RuntimeError("Container id is None after creation")
        with lock:
            running_containers.append(cid)
            job_containers[job_id] = cid
        logger.info(f"Job {job_id}: container {cid} started with name {container_name}")
        running_msg = pb.JobResult(
            job_id=job_id,
            status=pb.JobResult.RUNNING,
            container_id=cid,
            detail="",
        )
        message_queue.put(pb.AgentMessage(job_result=running_msg))
    except Exception as e:
        logger.error(f"Job {job_id}: run failed: {e}")
        result = pb.JobResult(
            job_id=job_id,
            status=pb.JobResult.FAILED,
            container_id="",
            detail=str(e),
        )
        message_queue.put(pb.AgentMessage(job_result=result))
        return

    # Monitor timeout
    def timeout_watch():
        time.sleep(TIMEOUT_TIMER)
        try:
            if cid:
                c = docker_client.containers.get(cid)
                if c.status == "running":
                    c.stop()
                    logger.warning(f"Job {job_id}: container {cid} timed out")
        except:
            pass

    threading.Thread(target=timeout_watch, daemon=True).start()

    # Wait for exit and capture logs
    exit_info = container.wait()
    code = exit_info.get("StatusCode", 1)
    if code == 0:
        status = pb.JobResult.FINISHED
        detail = ""
        logger.info(f"Job {job_id}: finished successfully")
    else:
        logs = container.logs(tail=50).decode("utf-8", errors="replace")
        status = pb.JobResult.FAILED
        detail = f"Exit {code}. Logs:\n{logs}"
        logger.error(f"Job {job_id}: failed with code {code}")

    with lock:
        if cid in running_containers:
            running_containers.remove(cid)

    result = pb.JobResult(
        job_id=job_id,
        status=status,
        container_id=cid,
        detail=detail,
    )
    message_queue.put(pb.AgentMessage(job_result=result))


def send_logs(job_id: str):
    cid = job_containers.get(job_id, "")
    content = ""
    if cid:
        try:
            content = (
                docker_client.containers.get(cid)
                .logs()
                .decode("utf-8", errors="replace")
            )
        except Exception as e:
            content = f"Error fetching logs: {e}"
    log_resp = pb.LogResponse(job_id=job_id, content=content)
    message_queue.put(pb.AgentMessage(log_response=log_resp))


def register_agent():
    """Register via HTTP to get agent_id, including platform info"""
    cpu_count = psutil.cpu_count(logical=True)
    mem_total = int(psutil.virtual_memory().total / (1024 * 1024))
    os_name = platform.system().lower()  # e.g., 'linux', 'darwin', 'windows'
    raw_arch = platform.machine().lower()  # e.g., 'x86_64', 'aarch64', 'arm64'
    # Map to Docker-style arch names
    arch_map = {
        "x86_64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "armv7l": "arm/v7",
        "armv6l": "arm/v6",
    }
    arch = arch_map.get(raw_arch, raw_arch)
    payload = {
        "hostname": HOSTNAME,
        "cpu": cpu_count,
        "mem": mem_total,
        "labels": [],
        "platform": {"os": os_name, "arch": arch},
    }
    logger.info(
        f"Registering agent with platform: os={os_name}, arch={arch} (raw: {raw_arch})"
    )
    resp = requests.post(f"{ORCHESTRATOR_HTTP}/agents/register", json=payload)
    resp.raise_for_status()
    agent_id = resp.json()["agent_id"]
    logger.info(f"Registered agent_id={agent_id}")
    return agent_id


def main():
    while True:
        try:
            agent_id = register_agent()
            metadata = [("agent-id", agent_id)]

            channel = grpc.insecure_channel(
                ORCHESTRATOR_GRPC,
                options=[
                    ("grpc.max_send_message_length", MAX_IMAGE_SIZE),
                    ("grpc.max_receive_message_length", MAX_IMAGE_SIZE),
                ],
            )
            stub = grpc_pb.AgentServiceStub(channel)
            stream = stub.Communicate(message_generator(), metadata=metadata)

            t = threading.Thread(target=handle_responses, args=(stream,), daemon=True)
            t.start()

            # Wait for the response thread to finish (i.e., connection lost)
            while t.is_alive():
                time.sleep(1)
            logger.warning(
                "gRPC connection lost. Will retry registration in 5 seconds."
            )
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
        time.sleep(5)


if __name__ == "__main__":
    main()
