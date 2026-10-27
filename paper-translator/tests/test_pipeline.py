import io
import json
import tarfile
from collections import Counter

import pytest

from paper_translator.common import PipelineError, file_sha, load_blocks, parse_markdown, read_json, render_blocks, save, save_json, sha
from paper_translator.fetch import parse_id, same_version, unpack
from paper_translator.merge import merge, status
from paper_translator.protect import invariants, protect, restore
from paper_translator.review import import_review, prepare_review, review_state
from paper_translator.split import prepare, split
from paper_translator.translate import import_translation, translations
from paper_translator.validate import validate_extraction, validate_translation


def fake_translate(work):
    """Synthetic Japanese verifies protocol mechanics, never translation quality."""
    prepare(work)
    for packet_path in sorted((work / "packets").glob("*.json")):
        packet = read_json(packet_path)
        blocks = []
        for src in packet["blocks"]:
            if src["translate"]:
                # Preserve protected syntax/number tokens while replacing prose.
                import re
                tokens = re.findall(r"⟦PT_[^⟧]+⟧", src["protected_text"])
                text = "これは日本語の模擬訳です。 " + " ".join(tokens)
            else:
                text = src["text"]
            blocks.append({"id": src["id"], "text": text})
        response = work / (packet_path.stem + "-response.json")
        save_json(response, {"paper_id": packet["paper_id"], "packet_sha256": packet["packet_sha256"], "chunk": packet["chunk"], "blocks": blocks})
        import_translation(work, response)


def fake_review(work, decision="PASS"):
    packet_path = prepare_review(work)
    packet = read_json(packet_path)
    result = {"schema_version": 1, "reviewer": "Claude Code", "packet_id": packet["packet_id"], "blocks": []}
    for b in packet["blocks"]:
        result["blocks"].append({"id": b["id"], "source_sha256": b["source_sha256"], "target_sha256": b["target_sha256"], "glossary_sha256": b["glossary_sha256"], "decision": decision, "rationale": "Synthetic test result; not an actual AI review", "findings": [] if decision != "NEEDS_REVISION" else [{"type": "meaning", "severity": "major", "evidence": "test mismatch", "suggestion": "test correction"}]})
    path = work / "fake-review.json"
    save_json(path, result)
    import_review(work, path)
    return path


def test_extract_inventory_and_provenance(work):
    result = validate_extraction(work)
    assert result["status"] == "PASS", result
    counts = Counter(b["kind"] for b in load_blocks(work))
    for kind in ["title", "heading", "paragraph", "equation", "figure", "caption", "table", "reference", "footnote"]:
        assert counts[kind] > 0
    assert any(b["source"]["file"] == "method.tex" for b in load_blocks(work))
    assert any(b.get("appendix") for b in load_blocks(work))


def test_end_to_end_and_resume(work):
    fake_translate(work)
    assert validate_translation(work)["status"] == "PASS"
    with pytest.raises(PipelineError):
        merge(work)
    assert not (work / "paper_ja.md").exists()
    assert "DRAFT" in merge(work, draft=True).read_text(encoding="utf-8")
    fake_review(work)
    output = merge(work)
    before = output.stat().st_mtime_ns
    digest = file_sha(output)
    assert status(work)["official"] == "VALID"
    merge(work)
    assert output.stat().st_mtime_ns == before
    assert file_sha(output) == digest
    assert [b["id"] for b in parse_markdown(output.read_text(encoding="utf-8"))] == [b["id"] for b in load_blocks(work)]
    assert "](assets/" in output.read_text(encoding="utf-8")


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "order", "equation", "citation", "image"])
def test_extraction_rejects_chunk_damage(work, mutation):
    chunks = read_json(work / "chunks.json")
    path = work / "en" / chunks["chunks"][0]["file"]
    text = path.read_text(encoding="utf-8")
    blocks = parse_markdown(text)
    if mutation == "missing":
        text = render_blocks(blocks[1:])
    elif mutation == "duplicate":
        text += render_blocks(blocks[:1])
    elif mutation == "order":
        text = render_blocks(list(reversed(blocks)))
    elif mutation == "equation":
        text = text.replace("0.25", "0.75")
    elif mutation == "citation":
        text = text.replace("#ref-example", "#ref-wrong")
    else:
        text = text.replace("../assets/", "../missing/")
    save(path, text)
    assert validate_extraction(work)["status"] == "FAIL"


@pytest.mark.parametrize("decision", ["NEEDS_REVISION", "INCONCLUSIVE"])
def test_unresolved_review_blocks_official(work, decision):
    fake_translate(work)
    fake_review(work, decision)
    with pytest.raises(PipelineError):
        merge(work)
    assert not (work / "paper_ja.md").exists()


def test_translation_change_invalidates_approval_and_context(work):
    fake_translate(work)
    fake_review(work)
    merge(work)
    path = next((work / "ja_draft").glob("*.md"))
    content = path.read_text(encoding="utf-8")
    save(path, content.replace("これは日本語", "これは修正した日本語", 1))
    states = review_state(work)
    assert "STALE" in states.values()
    assert "STALE_CONTEXT" in states.values()
    assert status(work)["official"] == "STALE_OR_INVALID"
    with pytest.raises(PipelineError):
        merge(work)
    fake_review(work)
    merge(work)
    assert status(work)["official"] == "VALID"


def test_glossary_change_invalidates_all_approvals(work):
    fake_translate(work)
    fake_review(work)
    glossary = read_json(work / "glossary.json")
    glossary["terms"].append({"en": "state", "ja": "状態", "block_ids": []})
    save_json(work / "glossary.json", glossary)
    assert set(review_state(work).values()) == {"STALE"}


def test_stale_review_response_is_rejected(work):
    fake_translate(work)
    response = fake_review(work)
    path = next((work / "ja_draft").glob("*.md"))
    save(path, path.read_text(encoding="utf-8").replace("これは", "ここは", 1))
    with pytest.raises(PipelineError, match="Stale"):
        import_review(work, response)


@pytest.mark.parametrize("damage", ["missing", "duplicate", "altered"])
def test_placeholder_corruption(damage):
    text = r"This is $x=2$ and $$y=3$$ with \text{kept} [cite:key](#ref-key) at 0.25."
    protected, mapping = protect(text, "p001")
    token = next(iter(mapping))
    damaged = protected.replace(token, "") if damage == "missing" else protected + token if damage == "duplicate" else protected.replace(token, token.replace("PT_", "PX_"))
    with pytest.raises(PipelineError):
        restore(damaged, mapping)
    assert restore(protected, mapping) == text


def test_equation_text_and_inline_newlines_preserved(work):
    eq = next(b for b in load_blocks(work) if b["kind"] == "equation")
    assert r"\text{with probability}" in eq["text"]
    fake_translate(work)
    translated = next(b for b in translations(work) if b["id"] == eq["id"])
    assert translated["text"] == eq["text"]


@pytest.mark.parametrize("damage", ["untranslated", "equation", "numeric", "empty", "id"])
def test_translation_damage(work, damage):
    fake_translate(work)
    path = next((work / "ja_draft").glob("*.md"))
    dest = parse_markdown(path.read_text(encoding="utf-8"))
    source = load_blocks(work)
    index = next(i for i, b in enumerate(source) if b["kind"] == "paragraph")
    if damage == "untranslated":
        dest[index]["text"] = source[index]["text"]
    elif damage == "equation":
        eq_index = next(i for i,b in enumerate(source) if b["kind"] == "equation")
        dest[eq_index]["text"] = dest[eq_index]["text"].replace(r"\frac{1}{4}", r"\frac{1}{2}")
    elif damage == "numeric":
        dest[index]["text"] += " 99"
    elif damage == "empty":
        dest[index]["text"] = ""
    else:
        dest[index]["id"] = "wrong-id"
    save(path, render_blocks(dest))
    assert validate_translation(work)["status"] == "FAIL"


@pytest.mark.parametrize("value,expected", [("https://arxiv.org/abs/2401.01234v2", ("2401.01234", "v2")), ("https://arxiv.org/pdf/quant-ph/0005006v1.pdf", ("quant-ph/0005006", "v1")), ("2401.01234", ("2401.01234", ""))])
def test_arxiv_identifiers(value, expected):
    assert parse_id(value) == expected
    assert not same_version("https://arxiv.org/pdf/2401.01234v1", "2401.01234v2")


@pytest.mark.parametrize("name,link", [("../escape.tex", False), ("C:/escape.tex", False), ("safe.tex", True)])
def test_archive_security(tmp_path, name, link):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo(name)
        if link:
            info.type, info.linkname = tarfile.SYMTYPE, "outside"
            tar.addfile(info)
        else:
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(PipelineError):
        unpack(buffer.getvalue(), tmp_path / "unpacked")


def test_source_tamper_is_fatal(work):
    path = work / "source" / "tex" / "method.tex"
    save(path, path.read_text(encoding="utf-8") + "\nChanged.\n")
    assert validate_extraction(work)["status"] == "FAIL"


@pytest.mark.parametrize("length,ratio_failure", [(94, True), (95, False), (96, False)])
def test_95_percent_boundary_is_not_sufficient_for_pass(tmp_path, length, ratio_failure):
    from paper_translator.extract import extract
    work = tmp_path / "ratio-paper"
    source = work / "source" / "tex" / "main.tex"
    save(source, "\\documentclass{article}\n\\begin{document}\n" + "a" * 100 + "\n\\end{document}\n")
    save_json(work / "manifest.json", {"paper_id": "ratio-v1", "source_kind": "tex", "files": [{"path": "source/tex/main.tex", "sha256": file_sha(source)}]})
    extract(work)
    blocks = load_blocks(work)
    blocks[0]["text"] = "a" * length
    save(work / "blocks.jsonl", "\n".join(json.dumps(b) for b in blocks) + "\n")
    split(work)
    result = validate_extraction(work)
    assert result["normalized_character_ratio"] == pytest.approx(length / 100)
    assert any(i["code"] == "character_ratio" for i in result["issues"]) == ratio_failure
    assert result["status"] == "FAIL"  # Independent lexical/paragraph checks still detect alteration.


def test_changed_generated_image_is_detected(work):
    asset = next((work / "assets").iterdir())
    save(asset, b"not an image")
    assert any(i["code"] == "asset_changed" for i in validate_extraction(work)["issues"])


def test_source_change_invalidates_approvals(work):
    fake_translate(work)
    fake_review(work)
    blocks = load_blocks(work)
    blocks[0]["text"] += " changed"
    save(work / "blocks.jsonl", "\n".join(json.dumps(b) for b in blocks) + "\n")
    assert "STALE" in review_state(work).values()
    with pytest.raises(PipelineError):
        merge(work)


def test_date_localization_preserves_day_month_year():
    from paper_translator.protect import localize_dates
    src = "Published 13 March 2000."
    dst = "掲載日は2000年3月13日である。"
    assert localize_dates(src) == "Published 2000年3月13日."
    assert invariants(src) == invariants(dst)
    assert invariants(src) != invariants("2000年3月14日")
    assert invariants("$13 March 2000$") != invariants("$2000年3月13日$")
    assert localize_dates("$13 March 2000$") == "$13 March 2000$"


def test_unexpanded_custom_math_is_not_silently_accepted(tmp_path):
    from paper_translator.tex import extract_tex
    save(tmp_path / "main.tex", r"\documentclass{article}\newcommand{\mystate}{\Psi}\begin{document}An unknown macro $\mystate$ must retain its source.\end{document}")
    blocks, diagnostics = extract_tex(tmp_path)
    assert any(i["code"] == "custom_math_macro" and i["severity"] == "error" for i in diagnostics["issues"])
    assert r"$\mystate$" in blocks[0]["text"]


def test_failed_acquisition_does_not_become_success_on_retry(tmp_path, monkeypatch):
    import importlib
    module = importlib.import_module("paper_translator.fetch")
    def unavailable(url, session=None):
        if "/abs/" in url:
            return b'<html><meta name="citation_title" content="Test"><body>2401.01234v2</body></html>', url, "text/html"
        raise PipelineError("simulated unavailable source")
    monkeypatch.setattr(module, "download", unavailable)
    for _ in range(2):
        with pytest.raises(PipelineError, match="All source downloads failed"):
            module.fetch("2401.01234v2", tmp_path)
    manifest = read_json(tmp_path / "2401.01234v2" / "manifest.json")
    assert manifest["source_kind"] is None
    assert len(manifest["attempts"]) == 6


def test_source_priority_and_version_redirect_rejection(tmp_path, monkeypatch):
    import importlib
    module = importlib.import_module("paper_translator.fetch")
    visited = []
    def download(url, session=None):
        visited.append(url)
        if "/abs/" in url:
            return b"<html><body>2401.01234v2</body></html>", url, "text/html"
        if "/src/" in url:
            return b"wrong source", url.replace("v2", "v1"), "application/gzip"
        if "/html/" in url:
            return b"<article><h1>Title</h1><p>Content</p></article>", url, "text/html"
        return b"%PDF-1.7\nfixture", url, "application/pdf"
    monkeypatch.setattr(module, "download", download)
    work = module.fetch("2401.01234v2", tmp_path)
    result = read_json(work / "manifest.json")
    assert result["source_kind"] == "html"
    assert [u.split('/')[3] for u in visited] == ["abs", "src", "html", "pdf"]
    assert result["attempts"][0]["status"] == "failed"
    assert "changed version" in result["attempts"][0]["error"]


def test_markdown_math_normalizes_only_layout_and_retains_raw(tmp_path):
    from paper_translator.tex import extract_tex
    original = "$  H +\nV  $"
    save(tmp_path / "main.tex", "\\documentclass{article}\\begin{document}The state " + original + " is known.\\end{document}")
    blocks, diagnostics = extract_tex(tmp_path)
    assert not diagnostics["issues"]
    assert "$H + V$" in blocks[0]["text"]
    assert blocks[0]["inline_math"][0]["raw_tex"] == original
    assert blocks[0]["inline_math"][0]["id"].startswith(blocks[0]["id"])


def test_pinned_acquisition_resumes_without_network(tmp_path, monkeypatch):
    import importlib
    module = importlib.import_module("paper_translator.fetch")
    work = tmp_path / "2401.01234v2"
    save(work / "source" / "original-source.bin", b"fixed source")
    save_json(work / "manifest.json", {"paper_id": "2401.01234v2", "source_kind": "tex", "files": [{"path": "source/original-source.bin", "sha256": file_sha(work / "source" / "original-source.bin")}]})
    def offline(*args, **kwargs):
        raise AssertionError("Network must not be contacted for a cached pinned version")
    monkeypatch.setattr(module, "download", offline)
    assert module.fetch("2401.01234v2", tmp_path) == work
