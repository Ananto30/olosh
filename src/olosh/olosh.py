#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from typing import Optional

import httpx
import typer

from push_dockerimage import push_dockerimage, save_docker_image


class OloshTyper(typer.Typer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def get_command(self, *args, **kwargs):
        cmd = super().get_command(*args, **kwargs)
        # Show help if no subcommand is provided
        cmd.callback = typer.main.get_command_callback(cmd)
        return cmd


app = OloshTyper(
    help="Olosh: Orchestrator and Agent CLI. Use --help on any command for details.",
    add_completion=True,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.command()
def orchestrator(
    port: int = typer.Argument(
        ..., help="[required] Port to run orchestrator HTTP server on"
    ),
    grpc_port: int = typer.Option(
        50051, help="[optional] gRPC port for orchestrator (default: 50051)"
    ),
):
    """
    Run the orchestrator server.

    Example:
        olosh orchestrator 8080 --grpc-port 50051
    """
    env = os.environ.copy()
    env["ORCHESTRATOR_HTTP"] = f"http://0.0.0.0:{port}"
    env["ORCHESTRATOR_GRPC"] = f"0.0.0.0:{grpc_port}"
    subprocess.run([sys.executable, "-m", "src.orchestrator.server"], env=env)


@app.command()
def agent(
    orchestrator: str = typer.Option(
        ...,
        "--orchestrator",
        "-o",
        help="[required] Orchestrator HTTP address, e.g. http://localhost:8080",
    ),
    tls: Optional[str] = typer.Option(
        None, "--tls", help="[optional] Path to TLS certs for agent (default: None)"
    ),
):
    """
    Run agent process.

    Example:
        olosh agent --orchestrator http://localhost:8080
    """
    env = os.environ.copy()
    env["ORCHESTRATOR_HTTP"] = orchestrator
    if tls:
        env["AGENT_TLS_CERTS"] = tls
    # agent_dir = os.path.join(os.path.dirname(__file__), "..", "agent")
    # subprocess.run([sys.executable, "-m", "agent"], env=env, cwd=agent_dir)
    subprocess.run([sys.executable, "-m", "src.agent.agent"], env=env)


job_app = typer.Typer(
    help="Job management commands: push, log, status.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(job_app, name="job", help="Job management commands.")


@job_app.command("push")
def job_push(
    orchestrator: str = typer.Option(
        ...,
        "--orchestrator",
        "-o",
        help="[required] Orchestrator HTTP address, e.g. http://localhost:8080",
    ),
    local_docker_image: str = typer.Argument(
        ..., help="[required] Local docker image name or ID to push"
    ),
):
    """
    Push a local Docker image to the orchestrator.

    Example:
        olosh job push --orchestrator http://localhost:8080 myimage:latest
    """
    tmp_tar = f"/tmp/olosh_{os.getpid()}.tar"
    print(f"Saving Docker image '{local_docker_image}' to {tmp_tar} ...")

    save_docker_image(local_docker_image, tmp_tar)
    print(f"Pushing image to orchestrator at {orchestrator} ...")

    result = push_dockerimage(orchestrator, tmp_tar)
    print("Result:")
    print(json.dumps(result, indent=2))

    print()
    print("To see the job status, run:")
    print(f"  olosh job status --orchestrator {orchestrator} {result['job_id']}")

    if os.path.exists(tmp_tar):
        os.remove(tmp_tar)


@job_app.command("log")
def job_log(
    orchestrator: str = typer.Option(
        ...,
        "--orchestrator",
        "-o",
        help="[required] Orchestrator HTTP address, e.g. http://localhost:8080",
    ),
    job_id: str = typer.Argument(..., help="[required] Job ID to fetch logs for"),
):
    """
    Get logs for a job.

    Example:
        olosh job log --orchestrator http://localhost:8080 <job_id>
    """
    url = f"{orchestrator.rstrip('/')}/jobs/{job_id}/logs"
    try:
        with httpx.Client(timeout=None) as client:
            resp = client.get(url)
            resp.raise_for_status()
            print(resp.text)
    except Exception as e:
        typer.secho(f"Error fetching logs: {e}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)


@job_app.command("status")
def job_status(
    orchestrator: str = typer.Option(
        ...,
        "--orchestrator",
        "-o",
        help="[required] Orchestrator HTTP address, e.g. http://localhost:8080",
    ),
    job_id: str = typer.Argument(..., help="[required] Job ID to fetch status for"),
):
    """
    Get status for a job.

    Example:
        olosh job status --orchestrator http://localhost:8080 <job_id>
    """
    url = f"{orchestrator.rstrip('/')}/jobs/{job_id}/status"
    try:
        with httpx.Client(timeout=None) as client:
            resp = client.get(url)
            resp.raise_for_status()
            try:
                data = resp.json()
                print(json.dumps(data, indent=2))

                print()
                print("To see the job logs, run:")
                print(f"  olosh job log --orchestrator {orchestrator} {job_id}")
            except Exception:
                print(resp.text)
    except Exception as e:
        typer.secho(f"Error fetching status: {e}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)


@app.command()
def jobs(
    orchestrator: str = typer.Option(
        ...,
        "--orchestrator",
        "-o",
        help="[required] Orchestrator HTTP address, e.g. http://localhost:8080",
    ),
):
    """
    List all jobs.

    Example:
        olosh jobs --orchestrator http://localhost:8080
    """
    url = f"{orchestrator.rstrip('/')}/jobs"
    try:
        with httpx.Client(timeout=None) as client:
            resp = client.get(url)
            resp.raise_for_status()
            try:
                data = resp.json()
                print(json.dumps(data, indent=2))
            except Exception:
                print(resp.text)
    except Exception as e:
        typer.secho(f"Error listing jobs: {e}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)


@app.command()
def agents(
    orchestrator: str = typer.Option(
        ...,
        "--orchestrator",
        "-o",
        help="[required] Orchestrator HTTP address, e.g. http://localhost:8080",
    ),
):
    """
    List all agents.

    Example:
        olosh agents --orchestrator http://localhost:8080
    """
    url = f"{orchestrator.rstrip('/')}/agents"
    try:
        with httpx.Client(timeout=None) as client:
            resp = client.get(url)
            resp.raise_for_status()
            try:
                data = resp.json()
                print(json.dumps(data, indent=2))
            except Exception:
                print(resp.text)
    except Exception as e:
        typer.secho(f"Error listing agents: {e}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
