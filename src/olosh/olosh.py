#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from typing import Optional

import httpx
import typer

from push_dockerimage import push_dockerimage, save_docker_image

app = typer.Typer()


@app.command()
def orchestrator(port: int = typer.Argument(..., help="Port to run orchestrator on")):
    """Run orchestrator server"""
    env = os.environ.copy()
    env["ORCHESTRATOR_HTTP"] = f"http://0.0.0.0:{port}"
    orchestrator_dir = os.path.join(os.path.dirname(__file__), "..", "orchestrator")
    subprocess.run([sys.executable, "-m", "server"], env=env, cwd=orchestrator_dir)


@app.command()
def agent(
    orchestrator: str = typer.Option(..., help="Orchestrator address"),
    tls: Optional[str] = typer.Option(None, help="TLS certs path"),
):
    """Run agent"""
    env = os.environ.copy()
    env["ORCHESTRATOR_HTTP"] = orchestrator
    if tls:
        env["AGENT_TLS_CERTS"] = tls
    agent_dir = os.path.join(os.path.dirname(__file__), "..", "agent")
    subprocess.run([sys.executable, "-m", "agent"], env=env, cwd=agent_dir)


job_app = typer.Typer()
app.add_typer(job_app, name="job")


@job_app.command("push")
def job_push(
    orchestrator: str = typer.Option(..., help="Orchestrator address"),
    local_docker_image: str = typer.Argument(..., help="Local docker image name or ID"),
):
    """Push docker image to orchestrator"""
    tmp_tar = f"/tmp/olosh_{os.getpid()}.tar"
    print(f"Saving Docker image '{local_docker_image}' to {tmp_tar} ...")
    save_docker_image(local_docker_image, tmp_tar)
    print(f"Pushing image to orchestrator at {orchestrator} ...")
    result = push_dockerimage(orchestrator, tmp_tar)
    print("Result:")
    print(json.dumps(result, indent=2))
    if os.path.exists(tmp_tar):
        os.remove(tmp_tar)


@job_app.command("log")
def job_log(
    orchestrator: str = typer.Option(..., help="Orchestrator address"),
    job_id: str = typer.Argument(..., help="Job ID"),
):
    """Get job logs"""
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
    orchestrator: str = typer.Option(..., help="Orchestrator address"),
    job_id: str = typer.Argument(..., help="Job ID"),
):
    """Get job status"""
    url = f"{orchestrator.rstrip('/')}/jobs/{job_id}/status"
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
        typer.secho(f"Error fetching status: {e}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)


@app.command()
def jobs(orchestrator: str = typer.Option(..., help="Orchestrator address")):
    """List jobs"""
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
def agents(orchestrator: str = typer.Option(..., help="Orchestrator address")):
    """List agents"""
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
