import json
import tarfile


def extract_manifest_config(path: str) -> tuple[str | None, str | None, str | None]:
    """
    Extracts the OS and architecture from a Docker image manifest and config file.
    Returns a tuple of (os, architecture, error_message).
    If the manifest or config file is not found, or if they are invalid,
    returns appropriate error messages.
    """
    try:
        with tarfile.open(path, mode="r:*") as tar:
            manifest_file = tar.extractfile("manifest.json")
            if manifest_file is None:
                return (None, None, "manifest.json not found in Docker image")
            manifest = json.loads(manifest_file.read())
            if not manifest or not isinstance(manifest, list):
                return (None, None, "Invalid Docker image manifest")
            image_info = manifest[0]
            config_filename = image_info.get("Config")
            if not config_filename:
                return (
                    None,
                    None,
                    "Config filename not found in Docker image manifest",
                )
            config_file = tar.extractfile(config_filename)
            if config_file is None:
                return (
                    None,
                    None,
                    f"Config file {config_filename} not found in Docker image",
                )
            config_json = json.loads(config_file.read())
            manifest_os = config_json.get("os")
            manifest_arch = config_json.get("architecture")
            if not manifest_os or not manifest_arch:
                return (None, None, "Missing os or architecture in Docker image config")
            return (manifest_os, manifest_arch, None)
    except Exception as e:
        return (None, None, f"Failed to read Docker image manifest/config: {e}")
