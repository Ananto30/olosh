from typing import Dict, List, Optional, Protocol

import docker
from podman import PodmanClient
from pydantic import BaseModel


class Image(BaseModel):
    id: Optional[str] = None
    tags: List[str] = []


class ContainerClient(Protocol):
    def load_image(self, data: bytes) -> List[Image]:
        """Load a Docker image from bytes."""
        ...

    def run_container(
        self,
        image_id: str,
        name: str,
        **run_params,
    ) -> Optional[str]:
        """Run a container with the given image and parameters."""
        ...

    def get_container_logs(self, container_id: str) -> str:
        """Get logs for a specific container."""
        ...

    def get_container_status(self, container_id: str) -> str:
        """Get the status of a specific container."""
        ...

    def stop_container(self, container_id: str) -> None:
        """Stop a running container."""
        ...

    def remove_container(self, container_id: str) -> None:
        """Remove a stopped container."""
        ...

    def get_running_containers(self) -> List[str]:
        """Get a list of currently running container IDs."""
        ...

    def wait_for_exit(self, container_id: str) -> int:
        """Wait for a container to exit and return its exit info."""
        ...


class DockerContainerClient(ContainerClient):
    def __init__(self, client: docker.DockerClient):
        self.client = client

    def load_image(self, data: bytes) -> List[Image]:
        """Load a Docker image from bytes."""
        loaded_images = self.client.images.load(data)
        return [Image(id=image.id, tags=image.tags) for image in loaded_images]

    def run_container(
        self,
        image_id: str,
        name: str,
        **run_params,
    ) -> Optional[str]:
        """Run a container with the given image and parameters."""
        container = self.client.containers.run(
            image_id, detach=True, name=name, **run_params
        )
        return container.id

    def get_container_logs(self, container_id: str) -> str:
        """Get logs for a specific container."""
        return (
            self.client.containers.get(container_id)
            .logs()
            .decode("utf-8", errors="replace")
        )

    def get_container_status(self, container_id: str) -> str:
        """Get the status of a specific container."""
        return self.client.containers.get(container_id).status

    def stop_container(self, container_id: str) -> None:
        """Stop a running container."""
        self.client.containers.get(container_id).stop()

    def remove_container(self, container_id: str) -> None:
        """Remove a stopped container."""
        self.client.containers.get(container_id).remove()

    def get_running_containers(self) -> List[str]:
        """Get a list of currently running container IDs."""
        return [c.id for c in self.client.containers.list()]

    def wait_for_exit(self, container_id: str) -> int:
        """Wait for a container to exit and return its exit info."""
        exit_info = self.client.containers.get(container_id).wait()
        return exit_info.get("StatusCode", 1)


class PodmanContainerClient(ContainerClient):
    def __init__(self, client: PodmanClient):
        self.client = client

    def load_image(self, data: bytes) -> List[Image]:
        """Load a Docker image from bytes."""
        loaded_images = self.client.images.load(data)
        return [Image(id=image.id, tags=image.tags) for image in loaded_images]

    def run_container(
        self,
        image_id: str,
        name: str,
        **run_params,
    ) -> Optional[str]:
        """Run a container with the given image and parameters."""
        container = self.client.containers.run(
            image_id, detach=True, name=name, **run_params
        )
        # Podman may return a container object or an iterator of IDs
        if hasattr(container, "id"):
            return container.id
        elif isinstance(container, (list, tuple)):
            # If it's a list, return the first element
            return container[0].id
        elif hasattr(container, "__iter__"):
            # If it's an iterator, get the id of the first value
            first = next(iter(container))
            return getattr(first, "id", first)
        else:
            raise RuntimeError("Unknown container return type from Podman run")

    def get_container_logs(self, container_id: str) -> str:
        """Get logs for a specific container."""
        logs_output = self.client.containers.get(container_id).logs()
        if isinstance(logs_output, bytes):
            return logs_output.decode("utf-8", errors="replace")
        else:
            return b"".join(logs_output).decode("utf-8", errors="replace")

    def get_container_status(self, container_id: str) -> str:
        """Get the status of a specific container."""
        return self.client.containers.get(container_id).status

    def stop_container(self, container_id: str) -> None:
        """Stop a running container."""
        self.client.containers.get(container_id).stop()

    def remove_container(self, container_id: str) -> None:
        """Remove a stopped container."""
        self.client.containers.get(container_id).remove()

    def get_running_containers(self) -> List[str]:
        """Get a list of currently running container IDs."""
        return [c.id for c in self.client.containers.list()]

    def wait_for_exit(self, container_id: str) -> int:
        """Wait for a container to exit and return its exit info."""
        return self.client.containers.get(container_id).wait()
