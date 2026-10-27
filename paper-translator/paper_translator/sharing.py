"""Explicit, inspectable sharing of terminology packets only; never copy a workspace."""
import re
from pathlib import Path

from .annotation_common import checked_payload, digest, paper_id
from .common import PipelineError, read_json, safe_path, save_json
from .terminology import import_result


def paper_directory(work, root):
    identifier = paper_id(work)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", identifier):
        raise PipelineError("Paper ID cannot be used in a shared directory")
    return safe_path(root, identifier.replace("/", "_"))


def share_terms(work, packet_path, root="../gpt-review", write=False):
    """Default is a complete preview. --write copies exactly one validated packet."""
    work, packet_path = Path(work), Path(packet_path).resolve()
    if not packet_path.is_relative_to((work / "terminology").resolve()):
        raise PipelineError("Share only a term-export packet inside this workspace's terminology directory")
    packet = checked_payload(work, "terminology-packet", read_json(packet_path))
    identifier = packet["packet_id"]
    content = {k: v for k, v in packet.items() if k != "packet_id"}
    if identifier != "terms-" + digest(content)[:20] or packet_path.name != identifier + ".json":
        raise PipelineError("Term export filename/content hash mismatch")
    directory = paper_directory(work, root)
    destination = safe_path(directory, f"requests/{identifier}.json")
    # A normalized directory name must never combine two different paper IDs.
    for existing in (directory / "requests").glob("*.json"):
        if read_json(existing).get("paper_id") != packet["paper_id"]:
            raise PipelineError("Shared directory already belongs to another paper")
    if destination.exists() and read_json(destination) != packet:
        raise PipelineError("Shared request is immutable; export a new packet")
    if write:
        save_json(destination, packet)
        safe_path(directory, "responses").mkdir(parents=True, exist_ok=True)
    return {"mode": "exported" if write else "preview", "destination": str(destination), "files_to_share": [str(destination)], "packet": packet}


def import_shared_terms(work, response_path, root="../gpt-review"):
    directory = paper_directory(work, root)
    response_path = Path(response_path).resolve()
    if response_path.parent != safe_path(directory, "responses") or response_path.suffix != ".json":
        raise PipelineError("Select a JSON result from this paper's gpt-review responses directory")
    payload = checked_payload(work, "terminology-result", read_json(response_path))
    request = safe_path(directory, f"requests/{payload['packet_id']}.json")
    shared = checked_payload(work, "terminology-packet", read_json(request))
    local = read_json(Path(work) / "terminology" / f"{payload['packet_id']}.json")
    if shared != local:
        raise PipelineError("Shared request differs from the workspace term-export packet")
    return import_result(work, payload)
