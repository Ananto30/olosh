import argparse
import json
import os
import subprocess
import sys

import requests
from tqdm import tqdm

# For true streaming multipart upload with progress
try:
    from requests_toolbelt.multipart.encoder import (
        MultipartEncoder,
        MultipartEncoderMonitor,
    )
except ImportError:
    print(
        "\033[91mError: 'requests_toolbelt' is required for upload progress. Install with 'pip install requests-toolbelt'.\033[0m",
        file=sys.stderr,
    )
    sys.exit(1)


def save_docker_image(image_name, output_path):
    """Save a local Docker image as a tarball using docker or podman."""
    tool = get_container_tool()
    cmd = [tool, "save", "-o", output_path, image_name]
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as e:
        print(
            f"\033[91mError: Docker image '{image_name}' not found or could not be saved.\033[0m",
            file=sys.stderr,
        )
        print(
            f"Details: {e}\nMake sure the image exists locally. Use '{tool} images' to list available images.",
            file=sys.stderr,
        )
        sys.exit(1)


def get_container_tool():
    """Return 'docker' if available, else 'podman', else print colored error and exit."""
    import shutil

    for tool in ("docker", "podman"):
        if shutil.which(tool):
            return tool
    # Colored error (red)
    print(
        "\033[91mError: Neither 'docker' nor 'podman' is available in PATH. Please install one of them.\033[0m",
        file=sys.stderr,
    )
    sys.exit(1)


def push_dockerimage(orchestrator_url, image_tar_path, run_params=None):
    """Push a docker image tarball to the orchestrator, showing a true network upload progress bar."""

    file_size = os.path.getsize(image_tar_path)
    fields = {
        "docker_image": (
            os.path.basename(image_tar_path),
            open(image_tar_path, "rb"),
            "application/x-tar",
        ),
        "run_params": json.dumps(run_params or {}),
    }
    encoder = MultipartEncoder(fields=fields)

    with tqdm(
        total=encoder.len, unit="B", unit_scale=True, desc="Uploading", ncols=80
    ) as t:

        def monitor_callback(monitor):
            t.update(monitor.bytes_read - t.n)

        monitor = MultipartEncoderMonitor(encoder, monitor_callback)
        headers = {"Content-Type": monitor.content_type}
        resp = requests.post(
            f"{orchestrator_url.rstrip('/')}/run-dockerimage",
            data=monitor,
            headers=headers,
        )

    try:
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"Error pushing docker image: {e} | {resp.text}", file=sys.stderr)
        sys.exit(1)


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
