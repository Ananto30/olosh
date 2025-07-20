import argparse
import json
import os
import subprocess
import sys

import requests


def save_docker_image(image_name, output_path):
    """Save a local Docker image as a tarball."""
    cmd = ["docker", "save", "-o", output_path, image_name]
    subprocess.check_call(cmd)


def push_dockerimage(orchestrator_url, image_tar_path, run_params=None):
    """Push a docker image tarball to the orchestrator."""
    files = {"docker_image": open(image_tar_path, "rb")}
    data = {"run_params": json.dumps(run_params or {})}
    resp = requests.post(
        f"{orchestrator_url.rstrip('/')}/run-dockerimage", files=files, data=data
    )
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(
        description="Push a local Docker image to the orchestrator."
    )
    parser.add_argument("image", help="Local Docker image name or ID")
    parser.add_argument(
        "--orchestrator", default="http://localhost:8000", help="Orchestrator HTTP URL"
    )
    parser.add_argument(
        "--run-params", default=None, help="JSON string of run parameters (optional)"
    )
    parser.add_argument(
        "--tmp", default="/tmp/dockerimage.tar", help="Temporary tarball path"
    )
    args = parser.parse_args()

    # Save docker image
    print(f"Saving Docker image '{args.image}' to {args.tmp} ...")
    save_docker_image(args.image, args.tmp)

    # Parse run params
    run_params = json.loads(args.run_params) if args.run_params else {}

    # Push to orchestrator
    print(f"Pushing image to orchestrator at {args.orchestrator} ...")
    result = push_dockerimage(args.orchestrator, args.tmp, run_params)
    print("Result:", result)

    # Clean up
    if os.path.exists(args.tmp):
        os.remove(args.tmp)


if __name__ == "__main__":
    main()
