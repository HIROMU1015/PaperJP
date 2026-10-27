from __future__ import annotations

import gzip
import io
import re
import tarfile
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .common import PipelineError, file_sha, now, read_json, safe_path, save_json, sha, source_issues

ARXIV_ID = re.compile(r"(?P<base>(?:\d{4}\.\d{4,5}|[a-zA-Z-]+(?:\.[A-Z]{2})?/\d{7}))(?P<version>v[1-9]\d*)?")
MAX_BYTES = 80 * 1024 * 1024


def parse_id(value):
    value = value.strip().removeprefix("arXiv:")
    if "://" in value:
        url = urlparse(value)
        if url.scheme != "https" or url.hostname not in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
            raise PipelineError("Only HTTPS arXiv URLs are supported in Phase 1")
        value = re.sub(r"^/(abs|pdf|html|src|e-print)/", "", url.path).removesuffix(".pdf")
    match = ARXIV_ID.fullmatch(value)
    if not match:
        raise PipelineError(f"Invalid arXiv identifier: {value}")
    return match["base"], match["version"] or ""


def download(url, session=None):
    client = session or requests.Session()
    failure = None
    for attempt in range(3):
        try:
            with client.get(url, timeout=(15, 90), stream=True, headers={"User-Agent": "PaperTranslator/0.1 (local academic reading)"}) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise PipelineError(f"Download exceeds {MAX_BYTES} bytes")
                return bytes(data), response.url, response.headers.get("Content-Type", "")
        except requests.RequestException as exc:
            failure = exc
            if getattr(exc.response, "status_code", None) in {400, 403, 404}:
                break
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    raise PipelineError(f"Download failed: {url}: {failure}")


def unpack(data, destination):
    """Never execute TeX; reject traversal, links, devices and archive bombs."""
    destination = Path(destination)
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except tarfile.ReadError:
        if data[:2] == b"\x1f\x8b":
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
                data = compressed.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise PipelineError("Expanded source too large")
        if b"\\document" not in data:
            raise PipelineError("Source is neither a supported TeX archive nor TeX file")
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "main.tex").write_bytes(data)
        return
    with archive:
        members = archive.getmembers()
        if len(members) > 10000 or sum(m.size for m in members) > MAX_BYTES:
            raise PipelineError("Expanded source too large")
        checked = []
        seen = set()
        for member in members:
            target = safe_path(destination, member.name)
            key = str(target).casefold()
            if not (member.isfile() or member.isdir()) or member.issym() or member.islnk():
                raise PipelineError(f"Unsupported archive entry: {member.name}")
            if key in seen:
                raise PipelineError(f"Duplicate archive path: {member.name}")
            seen.add(key)
            checked.append((member, target))
        for member, target in checked:
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as src:
                    target.write_bytes(src.read())


def same_version(url, pinned):
    try:
        base, version = parse_id(url)
        return base + version == pinned
    except PipelineError:
        return False


def fetch(value, root="workspace", session=None):
    base, requested = parse_id(value)
    cached_work = Path(root) / (base + requested).replace("/", "_")
    cached_manifest = cached_work / "manifest.json"
    if requested and cached_manifest.exists():
        cached = read_json(cached_manifest)
        if cached.get("paper_id") != base + requested or source_issues(cached_work):
            raise PipelineError("Cached fixed-version source identity/hash mismatch")
        if cached.get("source_kind"):
            return cached_work
    try:
        metadata, metadata_url, _ = download(f"https://arxiv.org/abs/{base}{requested}", session)
    except PipelineError as exc:
        save_json(Path(root) / "fetch-failures" / f"{base.replace('/', '_')}{requested}.json", {"at": now(), "input": value, "status": "failed", "error": str(exc)})
        raise
    soup = BeautifulSoup(metadata, "html.parser")
    # The displayed version identifier is authoritative, never guess 'v1'.
    matches = re.findall(re.escape(base) + r"(v[1-9]\d*)", soup.get_text(" ", strip=True))
    if requested and requested not in matches:
        raise PipelineError("Requested version not confirmed by arXiv abstract page")
    version = requested or (max(matches, key=lambda v: int(v[1:])) if matches else "")
    if not version:
        raise PipelineError("Cannot establish an explicit arXiv version")
    pinned = base + version
    work = Path(root) / pinned.replace("/", "_")
    previous = None
    if (work / "manifest.json").exists():
        if source_issues(work):
            raise PipelineError("Immutable source files have changed")
        previous = read_json(work / "manifest.json")
        if previous.get("source_kind"):
            return work
        # Resume a failed acquisition without rewriting its original metadata.
        metadata = (work / "source" / "metadata.html").read_bytes()
        metadata_url = next(f["url"] for f in previous["files"] if f["path"] == "source/metadata.html")
        soup = BeautifulSoup(metadata, "html.parser")
    source = work / "source"
    source.mkdir(parents=True, exist_ok=True)
    manifest = previous or {"schema_version": 1, "paper_id": pinned, "input": value, "version": version, "retrieved_at": now(), "files": [], "attempts": [], "source_kind": None}

    def store(name, data, url):
        path = source / name
        if path.exists() and path.read_bytes() != data:
            raise PipelineError(f"Refusing to replace immutable source: {path}")
        path.write_bytes(data)
        relative = path.relative_to(work).as_posix()
        if not any(f["path"] == relative for f in manifest["files"]):
            manifest["files"].append({"path": relative, "url": url, "sha256": sha(data), "bytes": len(data), "retrieved_at": now()})

    store("metadata.html", metadata, metadata_url)
    title = soup.find("meta", attrs={"name": "citation_title"})
    manifest["title"] = title.get("content", "") if title else pinned
    for kind, route, name in [("tex", "src", "original-source.bin"), ("html", "html", "original.html"), ("pdf", "pdf", "paper.pdf")]:
        # A PDF is retained even for TeX to independently cross-check extraction.
        if manifest["source_kind"] and kind != "pdf":
            continue
        url = f"https://arxiv.org/{route}/{pinned}"
        try:
            data, final_url, content_type = download(url, session)
            if not same_version(final_url, pinned):
                raise PipelineError(f"Source redirect changed version: {final_url}")
            # Keep every received original, including unsupported source formats.
            store(name, data, final_url)
            if kind == "tex":
                unpack(data, source / "tex")
                if not list((source / "tex").rglob("*.tex")):
                    raise PipelineError("Archive contains no .tex files")
            elif kind == "pdf" and not data.startswith(b"%PDF-"):
                raise PipelineError("Response is not a PDF")
            elif kind == "html":
                html = BeautifulSoup(data, "html.parser")
                if not html.select_one("article"):
                    raise PipelineError("No structured article in HTML")
            manifest["source_kind"] = manifest["source_kind"] or kind
            manifest["attempts"].append({"kind": kind, "url": url, "status": "success", "content_type": content_type})
        except (PipelineError, OSError, tarfile.TarError) as exc:
            manifest["attempts"].append({"kind": kind, "url": url, "status": "failed", "error": str(exc)})
    for path in sorted((source / "tex").rglob("*")) if (source / "tex").exists() else []:
        if path.is_file():
            manifest["files"].append({"path": path.relative_to(work).as_posix(), "sha256": file_sha(path), "bytes": path.stat().st_size, "derived_from": "source/original-source.bin"})
    save_json(work / "manifest.json", manifest)
    if not manifest["source_kind"]:
        raise PipelineError(f"All source downloads failed; see {work / 'manifest.json'}")
    return work
