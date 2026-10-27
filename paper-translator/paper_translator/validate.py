from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path

import pymupdf as fitz
from pylatexenc.latex2text import LatexNodes2Text

from .common import PipelineError, file_sha, issue, load_blocks, parse_markdown, read_json, report, source_issues
from .protect import PROTECTED, invariants, natural_text


def normalized(text):
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(re.findall(r"[a-z0-9\u3040-\u30ff\u3400-\u9fff]+", text))


def words(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    # Join discretionary line-end hyphenation from PDF, ignore math/single letters.
    text = re.sub(r"([a-z])-\s*\n\s*([a-z])", r"\1\2", text)
    return Counter(re.findall(r"[a-z]{3,}", text))


def coverage(expected, actual):
    return sum((expected & actual).values()) / max(1, sum(expected.values()))


def md_prose(text):
    text = re.sub(r"<!--[^>]*-->|<[^>]*>", " ", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", lambda m: " " if m[1].startswith(("cite:", "ref:")) else m[1], text)
    text = re.sub(r"\[\^[^\]]+\](?::)?", " ", text)
    text = re.sub(r"\$\$[\s\S]*?\$\$|(?<!\\)\$[^$]*\$", " ", text)
    return text


def source_prose(text):
    # Remove control arguments which are identifiers/paths, not printed prose.
    # latex2text expands maketitle with a synthetic current date if none was given.
    # That date is not original paper text and must never enter the comparison.
    text = re.sub(r"\\maketitle\b", " ", text)
    text = re.sub(r"\\(?:label|ref|eqref|autoref|cref|Cref|cite\w*|includegraphics)\*?\s*(?:\[[^]]*\]\s*)*\{[^}]*\}", " ", text)
    text = re.sub(r"\\begin\{tabular\*?\}\s*\{[^}]*\}|\\end\{tabular\*?\}", " ", text)
    text = text.replace("&", " ")
    return LatexNodes2Text(math_mode="remove").latex_to_text(text)


def independent_inventory(source):
    """Separate lexical scan of original expanded TeX, independent of generated blocks."""
    from .tex import MATH_ENVS
    body = source.split(r"\begin{document}", 1)[-1].split(r"\end{document}", 1)[0]
    return {
        "headings": len(re.findall(r"\\(?:section|subsection|subsubsection|paragraph|chapter)\*?\s*(?:\[[^]]*\])?\{", body)),
        "equations": sum(len(re.findall(r"\\begin\{" + re.escape(env) + r"\}", body)) for env in MATH_ENVS) + len(re.findall(r"\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]", body)),
        "figures": len(re.findall(r"\\includegraphics\*?\s*(?:\[[^]]*\])?\{", body)),
        "tables": len(re.findall(r"\\begin\{(?:tabular|tabular\*|tabularx)\}", body)),
        "references": len(re.findall(r"\\bibitem(?:\[[^]]*\])?\{", body)) or len(re.findall(r"(?m)^\([1-9]\d*\)\s+[A-Z]", body)),
        "footnotes": len(re.findall(r"\\footnote(?:\[[^]]*\])?\{", body)),
        "captions": len(re.findall(r"\\caption(?:\[[^]]*\])?\{", body)),
        "citations": Counter(key.strip() for group in re.findall(r"\\cite\w*\*?(?:\[[^]]*\])*\{([^}]+)\}", body) for key in group.split(",")),
        "appendix": bool(re.search(r"\\appendix\b", body)),
    }


def inline_math(text):
    text = re.sub(r"\$\$[\s\S]*?\$\$", "", text)
    return Counter("$" + match[1:-1].strip().replace("\n", " ") + "$" for match in re.findall(r"(?<![\\$])\$(?!\$)(?:\\.|[^$])*?(?<!\\)\$", text))


def check_links(work, blocks):
    problems = []
    anchors = set(re.findall(r'<a id="([^"]+)"', "\n".join(b["text"] for b in blocks)))
    for block in blocks:
        for image in re.findall(r"!\[[^]]*\]\(([^)]+)\)", block["text"]):
            # Block-level image paths are relative to en/ and ja_draft/.
            path = (Path(work) / "en" / image).resolve()
            if not path.is_relative_to(Path(work).resolve()) or not path.is_file():
                problems.append(issue("broken_image", f"{block['id']}: {image}"))
        for ref in re.findall(r"\]\(#([^)]+)\)", block["text"]):
            if ref not in anchors:
                problems.append(issue("broken_reference", f"{block['id']}: {ref}"))
    return problems


def validate_extraction(work):
    work = Path(work)
    blocks = load_blocks(work)
    problems = source_issues(work)
    diagnostics = read_json(work / "reports" / "extraction-diagnostics.json")
    problems.extend(diagnostics["issues"])
    ids = [b["id"] for b in blocks]
    if len(set(ids)) != len(ids):
        problems.append(issue("duplicate_ids", "Duplicate structured block IDs"))
    chunks_path = work / "chunks.json"
    rendered = []
    if chunks_path.exists():
        chunks = read_json(chunks_path)
        if chunks["blocks_sha256"] != file_sha(work / "blocks.jsonl"):
            problems.append(issue("stale_chunks", "Run split again after extraction changes"))
        for chunk in chunks["chunks"]:
            path = work / "en" / chunk["file"]
            if not path.exists():
                problems.append(issue("missing_chunk", chunk["file"]))
                continue
            if file_sha(path) != chunk["sha256"]:
                problems.append(issue("changed_chunk", chunk["file"]))
            try:
                rendered.extend(parse_markdown(path.read_text(encoding="utf-8-sig")))
            except PipelineError as exc:
                problems.append(issue("invalid_chunk", str(exc)))
        if [b["id"] for b in rendered] != ids:
            problems.append(issue("block_order_or_coverage", "Chunk ID sequence differs from source blocks"))
        if [b["text"] for b in rendered] != [b["text"] for b in blocks]:
            problems.append(issue("chunk_content", "Chunk contents differ from structured blocks"))
    else:
        problems.append(issue("not_split", "Run split before the extraction gate"))
    problems.extend(check_links(work, blocks))
    for block in blocks:
        asset = block.get("asset")
        if asset:
            path = work / asset["path"]
            if not path.is_file() or file_sha(path) != asset["sha256"]:
                problems.append(issue("asset_changed", block["id"]))
    counts = Counter(b["kind"] for b in blocks)
    metrics = {"block_count": len(blocks), "counts": dict(counts), "ratio_threshold": 0.95}
    source_path = work / "extracted-source.tex"
    if source_path.exists():
        # Re-read and expand immutable source, not a cached generated baseline.
        from .tex import expanded_source
        main = diagnostics["included_files"][0]
        source, _, _ = expanded_source(work / "source" / "tex", main)
        if source != source_path.read_text(encoding="utf-8"):
            problems.append(issue("stale_expansion", "Expanded TeX differs from immutable inputs"))
        inventory = independent_inventory(source)
        mapping = {"headings": "heading", "equations": "equation", "figures": "figure", "tables": "table", "references": "reference", "footnotes": "footnote", "captions": "caption"}
        for key, kind in mapping.items():
            observed = sum(b["kind"] == kind and not b.get("synthetic") for b in blocks)
            if inventory[key] != observed:
                problems.append(issue("inventory_mismatch", f"{key}: source={inventory[key]}, extracted={observed}"))
        observed_cites = Counter(re.findall(r"\]\(#ref-([^\)]+)\)", "\n".join(b["text"] for b in blocks)))
        if inventory["citations"] != observed_cites:
            problems.append(issue("citation_coverage", "Citations differ from independent source scan"))
        body = source.split(r"\begin{document}", 1)[-1].split(r"\end{document}", 1)[0]
        rendered_text = "\n".join(b["text"] for b in blocks)
        # Independent lexical scan also catches the loss of a short inline equation.
        if inline_math(body) != inline_math(rendered_text):
            problems.append(issue("inline_math_coverage", "Inline equations differ from immutable TeX"))
        if inventory["appendix"] and not any(b.get("appendix") for b in blocks):
            problems.append(issue("missing_appendix", "Appendix missing"))
        # Separate plain-text conversion, with math removed on both paths.
        baseline_source = re.sub(r"\\(?:baselineskip|parindent|parskip)\s*=\s*[-+.\d]+\s*(?:pt|cm|mm|in|em|ex)", "", source)
        baseline = source_prose(baseline_source)
        actual = md_prose("\n".join(b["text"] for b in blocks))
        ratio = len(normalized(actual)) / max(1, len(normalized(baseline)))
        lexical = coverage(words(baseline), words(actual))
        metrics.update(normalized_character_ratio=ratio, source_word_coverage=lexical, inventory={k: dict(v) if isinstance(v, Counter) else v for k, v in inventory.items()})
        if ratio < 0.95:
            problems.append(issue("character_ratio", f"{ratio:.5f} < 0.95"))
        if lexical < 0.95:
            problems.append(issue("source_text_coverage", f"Independent source words: {lexical:.5f} < 0.95"))
        missing_paragraphs = []
        for index, paragraph in enumerate(re.split(r"\n\s*\n", body), 1):
            paragraph_words = words(source_prose(paragraph))
            if sum(paragraph_words.values()) >= 8 and coverage(paragraph_words, words(actual)) < 0.95:
                missing_paragraphs.append(index)
        metrics["source_paragraphs_with_missing_words"] = missing_paragraphs
        if missing_paragraphs:
            problems.append(issue("source_paragraph_loss", f"Original paragraph spans need inspection: {missing_paragraphs}"))
        if ratio > 1.20:
            problems.append(issue("possible_duplication", f"Character ratio is unexpectedly high: {ratio:.5f}"))
    pdf_path = work / "source" / "paper.pdf"
    if pdf_path.exists():
        with fitz.open(pdf_path) as doc:
            pdf_text = "\n".join(p.get_text() for p in doc)
            page_count = len(doc)
        # Independent direction: can the Markdown account for words printed in the PDF?
        lexical = coverage(words(pdf_text), words("\n".join(b["text"] for b in blocks)))
        metrics.update(pdf_word_coverage=lexical, pdf_pages=page_count)
        if lexical < 0.95:
            problems.append(issue("pdf_crosscheck", f"Independent PDF word coverage {lexical:.5f} < 0.95; inspect source/PDF mismatch or extraction loss"))
    else:
        problems.append(issue("pdf_not_available", "No independent published PDF cross-check", "warning"))
    return report(work, "extraction", problems, **metrics)


def validate_translation(work, translated=None, write_report=True):
    work = Path(work)
    from .translate import translations
    source = load_blocks(work)
    translated = translations(work) if translated is None else translated
    problems = []
    if [b["id"] for b in source] != [b["id"] for b in translated]:
        problems.append(issue("translation_ids", "Missing, duplicate, extra or reordered translation IDs"))
    lookup = {b["id"]: b for b in source}
    for block in translated:
        src = lookup.get(block["id"])
        if not src:
            continue
        if not block["text"]:
            problems.append(issue("empty_translation", block["id"]))
        if "⟦PT_" in block["text"]:
            problems.append(issue("unrestored_placeholder", block["id"]))
        if src["translate"]:
            if invariants(src["text"]) != invariants(block["text"]):
                problems.append(issue("protected_content_changed", block["id"]))
            prose = natural_text(src["text"])
            target = natural_text(block["text"])
            if re.search(r"[A-Za-z]{3,}", prose) and not re.search(r"[\u3040-\u30ff\u3400-\u9fff]", target):
                problems.append(issue("untranslated", block["id"]))
            # Long identical English spans flag partial untranslated sentences.
            for sentence in re.split(r"[.!?]\s+", prose):
                if len(re.findall(r"[A-Za-z]{3,}", sentence)) >= 12 and sentence.strip() in target:
                    problems.append(issue("untranslated_span", block["id"]))
        elif src["text"] != block["text"]:
            problems.append(issue("immutable_block_changed", block["id"]))
    problems.extend(check_links(work, translated))
    if not write_report:
        return {"status": "FAIL" if any(p["severity"] == "error" for p in problems) else "PASS", "issues": problems}
    return report(work, "translation", problems, expected_blocks=len(source), translated_blocks=len(translated), semantic_quality="Requires independent Claude Code review; machine checks do not establish semantic accuracy")
