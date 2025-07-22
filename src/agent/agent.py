import ast
import io
import os
import platform
import queue
import subprocess
import sys
import threading
import time
from typing import Dict, Generator, List

import docker
import grpc
import psutil
from podman import PodmanClient

import src.protos.agent_service_pb2 as pb
import src.protos.agent_service_pb2_grpc as grpc_pb
from src.agent.container_clients import (
    ContainerClient,
    DockerContainerClient,
    PodmanContainerClient,
)
from src.agent.utils import get_machine_agent_id
from src.logger.common import logger

try:
    import docker

    _docker_available = True
except ImportError:
    _docker_available = False


# ─── CONFIG ────────────────────────────────────────────────────────────────────
ORCHESTRATOR_GRPC = os.getenv("ORCHESTRATOR_GRPC", None)
if not ORCHESTRATOR_GRPC:
    logger.error(
        "ORCHESTRATOR_GRPC environment variable is not set. Please set it to the gRPC endpoint of the orchestrator."
    )
    sys.exit(1)

AGENT_TLS_CERTS = os.getenv("AGENT_TLS_CERTS", None)
if AGENT_TLS_CERTS:
    if not os.path.exists(AGENT_TLS_CERTS):
        logger.error(f"TLS certs path {AGENT_TLS_CERTS} does not exist.")
        sys.exit(1)


HOSTNAME = get_machine_agent_id()

PODMAN_SOCK = os.getenv("PODMAN_SOCK", "unix:///run/podman/podman.sock")

MAX_IMAGE_SIZE = 2000 * 1024 * 1024  # 2000MB
HEARTBEAT_INTERVAL = 5  # seconds
TIMEOUT_TIMER = 30 * 60  # 30 minutes per container


# ─── DOCKER CLIENT ────────────────────────────────────────────────────────────


# Try to get a Docker client, fallback to Podman if Docker is not available
def get_container_client() -> ContainerClient:
    if _docker_available:
        try:
            client = docker.from_env()
            # Test connection
            client.ping()
            logger.info("Using Docker as container backend.")
            return DockerContainerClient(client)
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

        # Check if podman socket is available
        sock_path = PODMAN_SOCK
        if sock_path.startswith("unix://"):
            sock_path = sock_path[len("unix://") :]
        if not os.path.exists(sock_path):
            logger.error(
                f"Podman socket not found at {PODMAN_SOCK} (checked path: {sock_path}). Please set PODMAN_SOCK environment variable. You can find the socket path by running podman desktop -> settings -> Resources -> Podman machine -> Podman endpoint."
            )
            sys.exit(1)

        client = PodmanClient(base_url=PODMAN_SOCK)
        client.ping()
        logger.info("Using Podman as container backend.")
        return PodmanContainerClient(client)
    except ImportError:
        logger.error("Neither docker nor podman Python packages are installed.")
    except Exception as e:
        logger.error(f"Podman not available: {e}.")

    logger.error("No container backend (docker or podman) available. Exiting.")
    sys.exit(1)


container_client = get_container_client()


# ─── STATE ─────────────────────────────────────────────────────────────────────
running_containers: List[str] = []
job_containers: Dict[str, str] = {}  # job_id -> container_id
lock = threading.Lock()


# ─── MESSAGE QUEUE ─────────────────────────────────────────────────────────────
message_queue: "queue.Queue[pb.AgentMessage]" = queue.Queue()


def message_generator() -> Generator[pb.AgentMessage, None, None]:
    """
    Generator that yields AgentMessage instances to the orchestrator.
    - Sends heartbeat messages at regular intervals (HEARTBEAT_INTERVAL seconds).
    - Forwards JobResult or LogResponse messages as soon as they are put into message_queue.
    """
    next_hb = time.time()  # First time heartbeat is sent immediately
    while True:
        try:
            # Prioritize sending operational messages to orchestrator as soon as they arrive,
            # so job results and log responses are not delayed by heartbeat timing.
            msg = message_queue.get(timeout=max(0, next_hb - time.time()))
            yield msg
            continue
        except queue.Empty:
            # If no operational message is available, fall back to sending heartbeat to maintain liveness and monitoring.
            pass

        # Heartbeat is sent to ensure orchestrator knows agent is alive and can monitor health,
        # even if no jobs or logs are being processed.
        if time.time() >= next_hb:
            with lock:
                # Lock is used to avoid race conditions when reading running_containers,
                # since other threads may be modifying it.
                containers = list(running_containers)
            # Resource stats are included so orchestrator can make scheduling and health decisions.
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
            # Update next_hb so heartbeats are sent at regular intervals, not too frequently.
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
        load_result = container_client.load_image(image_stream.read())
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
        cid = container_client.run_container(
            image_id, name=container_name, **run_params
        )
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
                status = container_client.get_container_status(cid)
                if status == "running":
                    container_client.stop_container(cid)
                    logger.warning(f"Job {job_id}: container {cid} timed out")
        except:
            pass

    threading.Thread(target=timeout_watch, daemon=True).start()

    # Wait for exit and capture logs
    code = container_client.wait_for_exit(cid)
    if code == 0:
        status = pb.JobResult.FINISHED
        detail = ""
        logger.info(f"Job {job_id}: finished successfully")
    else:
        logs = container_client.get_container_logs(cid)
        # Only include the last 2000 characters of logs
        logs_tail = logs[-2000:] if len(logs) > 2000 else logs
        status = pb.JobResult.FAILED
        detail = f"Exit {code}. Logs:\n{logs_tail}"
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
            content = container_client.get_container_logs(cid)
        except Exception as e:
            content = f"Error fetching logs: {e}"
    log_resp = pb.LogResponse(job_id=job_id, content=content)
    message_queue.put(pb.AgentMessage(log_response=log_resp))


# ─── STATIC INFO ────────────────────────────────────────────────────────────────
cpu_count = psutil.cpu_count(logical=True) or 1
mem_total = int(psutil.virtual_memory().total / (1024 * 1024)) or 0
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


def main():
    while True:
        try:
            metadata = [
                ("agent-id", HOSTNAME),
                ("cpu-count", str(cpu_count)),
                ("mem-total", str(mem_total)),
                ("os-name", os_name),
                ("arch", arch),
            ]

            channel = grpc.insecure_channel(
                ORCHESTRATOR_GRPC,  # type: ignore
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
                try:
                    time.sleep(3)
                except KeyboardInterrupt:
                    logger.info("Keyboard interrupt received, shutting down agent.")
                    return

            logger.warning(
                "gRPC connection lost. Will retry registration in 5 seconds."
            )
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
        time.sleep(5)


if __name__ == "__main__":
    main()
