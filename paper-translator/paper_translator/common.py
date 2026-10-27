from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


class PipelineError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(value: str | bytes):
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def file_sha(path):
    return sha(Path(path).read_bytes())


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def serialized(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def save(path, content: str | bytes):
    """Idempotent atomic replace; retain changed generated files for diagnosis."""
    path = Path(path)
    data = content.encode("utf-8") if isinstance(content, str) else content
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = path.read_bytes()
        if old == data:
            return
        hist = path.parent / "history" / (path.name + "." + sha(old)[:16])
        hist.parent.mkdir(parents=True, exist_ok=True)
        if not hist.exists():
            hist.write_bytes(old)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def save_json(path, value):
    save(path, serialized(value))


def load_blocks(work):
    return [json.loads(s) for s in (Path(work) / "blocks.jsonl").read_text(encoding="utf-8").splitlines() if s.strip()]


def safe_path(root, relative):
    text = str(relative).replace("\\", "/")
    if not text or text.startswith("/") or ":" in text or ".." in text.split("/"):
        raise PipelineError(f"Unsafe path: {relative}")
    root = Path(root).resolve()
    result = (root / text).resolve()
    if not result.is_relative_to(root):
        raise PipelineError(f"Path escapes root: {relative}")
    return result


ID_RE = re.compile(r"<!-- block:([A-Za-z0-9_-]+) -->\s*\n")


def render_blocks(blocks):
    return "\n\n".join(f"<!-- block:{b['id']} -->\n{b['text']}" for b in blocks) + "\n"


def parse_markdown(text):
    matches = list(ID_RE.finditer(text))
    if not matches or text[:matches[0].start()].strip():
        raise PipelineError("Markdown must start with a block ID comment")
    result = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        result.append({"id": match[1], "text": text[match.end():end].strip()})
    return result


def report(work, name, issues, **metrics):
    result = {"schema_version": 1, "checked_at": now(), "status": "FAIL" if any(i['severity'] == 'error' for i in issues) else "PASS", "issues": issues, **metrics}
    save_json(Path(work) / "reports" / f"{name}.json", result)
    lines = [f"# {name}", "", f"判定: {result['status']}", "", "```json", serialized(metrics).strip(), "```", ""]
    lines += [f"- {i['severity']}: {i['code']}: {i['message']}" for i in issues]
    save(Path(work) / "reports" / f"{name}.md", "\n".join(lines) + "\n")
    return result


def issue(code, message, severity="error"):
    return {"code": code, "message": message, "severity": severity}


def source_issues(work):
    work = Path(work)
    manifest = read_json(work / "manifest.json")
    issues = []
    for entry in manifest["files"]:
        path = safe_path(work, entry["path"])
        if not path.is_file() or file_sha(path) != entry["sha256"]:
            issues.append(issue("source_changed", entry["path"]))
    return issues
