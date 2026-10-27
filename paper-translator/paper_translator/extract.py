from __future__ import annotations

import re
from pathlib import Path

import pymupdf as fitz
from bs4 import BeautifulSoup
from PIL import Image

from .common import PipelineError, file_sha, issue, read_json, render_blocks, safe_path, save, save_json, serialized, source_issues
from .tex import extract_tex


def materialize_assets(work, blocks):
    work = Path(work)
    problems = []
    root = work / "source" / "tex"
    for block in blocks:
        if block["kind"] != "figure":
            continue
        name = block.get("asset_source", "")
        choices = []
        parents = [root / Path(block["source"]["file"]).parent, root]
        for parent in parents:
            for suffix in ([""] if Path(name).suffix else ["", ".png", ".jpg", ".jpeg", ".pdf", ".eps", ".svg"]):
                candidate = safe_path(parent, name + suffix)
                if candidate.is_file():
                    choices.append(candidate)
        if not choices:
            problems.append(issue("missing_image", name))
            continue
        src = choices[0]
        dest = work / "assets" / (file_sha(src)[:16] + ".png")
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            if src.suffix.lower() == ".pdf":
                with fitz.open(src) as doc:
                    data = doc[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes("png")
                save(dest, data)
            elif src.suffix.lower() in {".eps", ".ps", ".svg"}:
                problems.append(issue("unsupported_image", f"{src.name}: EPS/PS/SVG needs a separately verified renderer; source was retained"))
                continue
            else:
                with Image.open(src) as img:
                    img.load()
                dest = dest.with_suffix(src.suffix.lower())
                save(dest, src.read_bytes())
            block["text"] = f"![figure](../assets/{dest.name})"
            block["asset"] = {"path": dest.relative_to(work).as_posix(), "sha256": file_sha(dest), "source": src.relative_to(root).as_posix()}
        except (OSError, ValueError) as exc:
            problems.append(issue("image_conversion_failed", f"{name}: {exc}"))
    return problems


def extract(work, main=None):
    work = Path(work)
    errors = source_issues(work)
    if errors:
        raise PipelineError(str(errors))
    manifest = read_json(work / "manifest.json")
    if manifest["source_kind"] == "tex":
        blocks, diagnostics = extract_tex(work / "source" / "tex", main)
        diagnostics["issues"].extend(materialize_assets(work, blocks))
        save(work / "extracted-source.tex", diagnostics.pop("expanded_tex"))
    elif manifest["source_kind"] == "html":
        blocks, diagnostics = extract_html(work)
    else:
        # Honest diagnostic fallback: text alone cannot establish mathematical completeness.
        with fitz.open(work / "source" / "paper.pdf") as doc:
            blocks = []
            for page in doc:
                for entry in page.get_text("blocks", sort=True):
                    if entry[4].strip():
                        blocks.append({"id": f"page{page.number + 1:03d}-p{len(blocks) + 1:04d}", "kind": "paragraph", "section": page.number, "text": entry[4].strip(), "translate": True, "source": {"file": "paper.pdf", "page": page.number + 1, "bbox": list(entry[:4])}})
        diagnostics = {"issues": [issue("pdf_diagnostic_only", "PDF text extracted, but equation/table/figure completeness is not established. Phase 2 structured extraction is not implemented.")], "included_files": ["paper.pdf"]}
    if not blocks:
        raise PipelineError("No content extracted")
    save(work / "blocks.jsonl", "".join(serialized(b).replace("\n", "") + "\n" for b in blocks))
    save_json(work / "reports" / "extraction-diagnostics.json", diagnostics)
    save(work / "paper_en.md", render_blocks(blocks))
    return blocks


def extract_html(work):
    soup = BeautifulSoup((Path(work) / "source" / "original.html").read_bytes(), "html.parser")
    article = soup.select_one("article")
    blocks = []
    for element in article.select("h1,h2,h3,h4,p,figure,table"):
        if any(p.name in {"figure", "table", "p"} for p in element.parents if p != article):
            continue
        kind = "heading" if element.name.startswith("h") else "paragraph"
        value = element.get_text(" ", strip=True)
        if kind == "heading":
            value = "#" * int(element.name[1]) + " " + value
        blocks.append({"id": f"html-{len(blocks) + 1:05d}", "kind": kind, "section": 0, "text": value, "translate": True, "source": {"file": "original.html", "element_id": element.get("id")}})
    return blocks, {"issues": [issue("html_diagnostic_only", "Structured HTML was acquired with a versioned URL, but mathematical/asset preservation is not yet implemented; final publication is blocked.")], "included_files": ["original.html"]}
