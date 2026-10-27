"""Read-only inventory of independent, version-pinned paper workspaces."""
from collections import Counter
from pathlib import Path

from .common import PipelineError, read_json
from .merge import status


def list_papers(root="workspace"):
    root = Path(root)
    papers = []
    for manifest in sorted(root.glob("*/manifest.json")):
        work = manifest.parent
        entry = {"work": str(work)}
        try:
            entry["paper_id"] = read_json(manifest)["paper_id"]
            entry.update(status(work, write_reports=False))
        except (PipelineError, OSError, ValueError, KeyError) as exc:
            entry.update(status="ERROR", error=str(exc))
        papers.append(entry)
    counts = Counter(p.get("paper_id") for p in papers if p.get("paper_id"))
    for paper in papers:
        if counts[paper.get("paper_id")] > 1:
            paper.update(status="ERROR", error="Duplicate paper_id in separate workspace directories")
    return {"root": str(root), "count": len(papers), "papers": papers}
