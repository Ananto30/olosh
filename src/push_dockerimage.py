import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys

import httpx
from tqdm import tqdm


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
    """Push a docker image tarball to the orchestrator, showing a true network upload progress bar (using httpx)."""
    file_size = os.path.getsize(image_tar_path)

    # Detect if file is gzipped by extension
    if image_tar_path.endswith(".gz"):
        content_type = "application/gzip"
    else:
        content_type = "application/x-tar"

    with open(image_tar_path, "rb") as f:
        # httpx requires a tuple (filename, fileobj, content_type)
        files = {
            "docker_image": (os.path.basename(image_tar_path), f, content_type),
            "run_params": (None, json.dumps(run_params or {}), "application/json"),
        }

        # httpx does not natively support progress bars, so we wrap the file object
        class ProgressFile:
            def __init__(self, fileobj, total, tqdm_bar):
                self.fileobj = fileobj
                self.total = total
                self.tqdm_bar = tqdm_bar
                self.read_bytes = 0

            def read(self, size=-1):
                chunk = self.fileobj.read(size)
                if chunk:
                    self.tqdm_bar.update(len(chunk))
                    self.read_bytes += len(chunk)
                return chunk

            def __getattr__(self, name):
                return getattr(self.fileobj, name)

        with tqdm(
            total=file_size, unit="B", unit_scale=True, desc="Uploading", ncols=80
        ) as t:
            pf = ProgressFile(f, file_size, t)
            files["docker_image"] = (
                os.path.basename(image_tar_path),
                pf,
                content_type,
            )
            with httpx.Client(timeout=None) as client:
                resp = client.post(
                    f"{orchestrator_url.rstrip('/')}/run-dockerimage",
                    files=files,
                )
    try:
        resp.raise_for_status()
        return resp.json()
    except (httpx.RequestError, httpx.HTTPStatusError) as e:
        print(
            f"Error pushing docker image: {e} | {getattr(resp, 'text', '')}",
            file=sys.stderr,
        )
        sys.exit(1)


def process_docker_image_upload(
    image_name, orchestrator_url, run_params=None, tmp_path="/tmp/dockerimage.tar"
):
    """
    Process a Docker image upload:
    1. Save the image to a tarball.
    2. Check size and gzip if larger than 500MB.
    3. Push to orchestrator with run parameters.
    """
    # Save docker image
    print(f"Saving Docker image '{image_name}' to {tmp_path} ...")
    save_docker_image(image_name, tmp_path)

    # Check size and gzip if > 500MB
    tar_size = os.path.getsize(tmp_path)
    upload_path = tmp_path
    if tar_size > 500 * 1024 * 1024:
        gz_path = tmp_path + ".gz"
        print(
            f"Tarball is larger than 500MB ({tar_size/1024/1024:.2f}MB). Compressing with gzip..."
        )
        with open(tmp_path, "rb") as f_in, gzip.GzipFile(gz_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        upload_path = gz_path
    else:
        print(f"Tarball size is {tar_size/1024/1024:.2f}MB. No compression needed.")

    # Parse run params
    run_params = json.loads(run_params) if run_params else {}

    # Push to orchestrator
    print(f"Pushing image to orchestrator at {orchestrator_url} ...")
    result = push_dockerimage(orchestrator_url, upload_path, run_params)
    print("Result:")
    print(json.dumps(result, indent=2))

    # Clean up
    for path in [tmp_path, tmp_path + ".gz"]:
        if os.path.exists(path):
            os.remove(path)

    return result


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

    if not args.image:
        parser.error("Local Docker image name or ID is required.")
    if not args.orchestrator:
        parser.error("Orchestrator HTTP URL is required.")
    if not args.run_params:
        args.run_params = "{}"

    try:
        process_docker_image_upload(
            args.image, args.orchestrator, args.run_params, args.tmp
        )
    except Exception as e:
        print(f"Error processing Docker image upload: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
