# Olosh

Olosh is a lightweight container orchestration tool designed to make use of idle machines on your local network. The name comes from the Bangla word **"অলস" (olosh)** meaning "idle". You can run CI/CD workloads on idle laptops or PCs by connecting them as Olosh agents.

## Features

* **Orchestrator** – central server that coordinates agents and jobs.
* **Agents** – lightweight processes that register with the orchestrator and execute container jobs.
* **CLI** – single `olosh` command for running the orchestrator, agents and interacting with jobs.

## Why Olosh?

Many organizations have machines that remain idle for large portions of the day. Olosh lets you harness that unused compute power for tasks such as building and testing containers. By distributing work to otherwise idle CPUs, you can speed up pipelines without investing in additional hardware.

## Installation

**Active development is ongoing, so please use the latest code from the repository.**

* Olosh requires Python 3.12 or later and [uv](https://github.com/astral-sh/uv) package manager. If you don't have `uv` follow this [installation guide](https://github.com/astral-sh/uv?tab=readme-ov-file#installation).

- Clone the repository and install olosh in editable mode:

  ```bash
  git clone https://github.com/Ananto30/olosh
  cd olosh
  uv venv
  source .venv/bin/activate
  uv sync
  pip install -e .
  ```

This installs the `olosh` command and its dependencies.

## Quick start

1. **Start the orchestrator**

   ```bash
   olosh orchestrator 9000 --grpc-port 50051
   ```

2. **Start an agent on another machine**

   ```bash
   olosh agent --orchestrator http://<orchestrator-host>:9000
   ```

3. **Push a Docker image to run as a job**

   ```bash
   olosh job push --orchestrator http://<orchestrator-host>:9000 my-image:latest
   ```

4. **Check job status and logs**

   ```bash
   olosh job status --orchestrator http://<orchestrator-host>:9000 <job_id>
   olosh job log --orchestrator http://<orchestrator-host>:9000 <job_id>
   ```

5. **List jobs and agents**

   ```bash
   olosh jobs --orchestrator http://<orchestrator-host>:9000
   olosh agents --orchestrator http://<orchestrator-host>:9000
   ```

## CLI reference

Run `olosh --help` to see all options. Important commands are:

| Command | Description |
| ------- | ----------- |
| `olosh orchestrator <port> [--grpc-port PORT]` | Start the orchestrator server. |
| `olosh agent --orchestrator URL [--tls PATH]` | Run an agent that connects to the orchestrator. |
| `olosh job push --orchestrator URL IMAGE` | Upload a local Docker image as a job. |
| `olosh job log --orchestrator URL JOB_ID` | Retrieve logs for a job. |
| `olosh job status --orchestrator URL JOB_ID` | Check status of a job. |
| `olosh jobs --orchestrator URL` | List all jobs. |
| `olosh agents --orchestrator URL` | List all registered agents. |

## Some design decisions

* gRPC bidirect streaming is used for orchestrator-agent communication to avoid firewall issues for agents. So any agent can connect to the orchestrator without needing to open specific ports on the orchestrator machine.
* For now only one orchestrator is supported because the agents and jobs are not persisted or shared across orchestrators. This may change in the future to allow multiple orchestrators for redundancy or scaling.

## License

MIT
