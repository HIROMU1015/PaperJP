"""External-review regressions. AI results here are protocol fixtures, not approvals."""
import json
import shutil
from types import SimpleNamespace

import pytest

from paper_translator.common import PipelineError, file_sha, load_blocks, read_json, save_json
from paper_translator.merge import merge, status
from paper_translator.protect import invariants, protect, restore
from paper_translator.review import approvals, import_review, prepare_review, prepare_reviews, review_state, run_review
from paper_translator.split import prepare
from paper_translator.translate import import_translation, replace_translation_blocks
from test_pipeline import fake_review, fake_translate


@pytest.mark.parametrize("decision", ["NEEDS_REVISION", "INCONCLUSIVE"])
def test_old_pass_cannot_clear_new_unresolved_review(work, decision):
    fake_translate(work)
    old_pass = work / "old-pass.json"
    save_json(old_pass, read_json(fake_review(work)))
    # Force a new review even though the previous result was PASS.
    packet = read_json(prepare_review(work, all_blocks=True))
    later = read_json(old_pass)
    later["packet_id"] = packet["packet_id"]
    for block in later["blocks"]:
        block["decision"] = decision
        block["findings"] = [] if decision == "INCONCLUSIVE" else [{"type": "meaning", "severity": "major", "evidence": "test mismatch", "suggestion": "test correction"}]
    later_path = work / "later-review.json"
    save_json(later_path, later)
    import_review(work, later_path)
    assert set(review_state(work).values()) == {decision}
    before = file_sha(work / "reviews" / "approvals.json")
    with pytest.raises(PipelineError, match="superseded|replay|revision"):
        import_review(work, old_pass)
    assert file_sha(work / "reviews" / "approvals.json") == before
    assert set(review_state(work).values()) == {decision}
    with pytest.raises(PipelineError):
        merge(work)


def test_import_translation_rejects_changed_glossary_before_writes(work):
    fake_translate(work)
    response = next((work / "translations").glob("*.json"))
    before = {p.name: file_sha(p) for p in (work / "ja_draft").glob("*.md")}
    glossary = read_json(work / "glossary.json")
    glossary["terms"].append({"en": "state", "ja": "状態", "block_ids": []})
    save_json(work / "glossary.json", glossary)
    with pytest.raises(PipelineError, match="glossary"):
        import_translation(work, response)
    assert {p.name: file_sha(p) for p in (work / "ja_draft").glob("*.md")} == before


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["LF", "CRLF"])
def test_original_tex_line_endings_are_not_reported_as_stale(work, newline):
    from paper_translator.extract import extract
    from paper_translator.split import split
    from paper_translator.validate import validate_extraction
    for path in (work / "source/tex").glob("*.tex"):
        text = path.read_text(encoding="utf-8")
        path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    manifest = read_json(work / "manifest.json")
    for entry in manifest["files"]:
        entry["sha256"] = file_sha(work / entry["path"])
    save_json(work / "manifest.json", manifest)
    extract(work)
    split(work)
    from paper_translator.tex import expanded_source
    expanded, _, _ = expanded_source(work / "source/tex")
    assert (work / "extracted-source.tex").read_bytes().decode("utf-8") == expanded
    result = validate_extraction(work)
    assert result["status"] == "PASS", result


def test_markdown_link_label_is_translatable_but_destination_is_immutable():
    source = "See [Experimental results](https://example.org/paper#results)."
    protected, mapping = protect(source, "sec01-p001")
    assert "Experimental results" in protected
    translated = restore(protected.replace("See", "参照").replace("Experimental results", "実験結果"), mapping)
    assert "[実験結果](https://example.org/paper#results)" in translated
    assert invariants(translated) == invariants(source)
    assert invariants(translated.replace("#results", "#wrong")) != invariants(source)


def result_for(packet, decision="PASS"):
    return {"schema_version": 1, "reviewer": "Claude Code", "packet_id": packet["packet_id"], "blocks": [
        {**{k: b[k] for k in ("id", "source_sha256", "target_sha256", "glossary_sha256")}, "decision": decision,
         "rationale": "Synthetic protocol result, not an actual Claude review",
         "findings": [{"type": "meaning", "severity": "major", "evidence": "synthetic evidence", "suggestion": "synthetic fix"}] if decision == "NEEDS_REVISION" else []}
        for b in packet["blocks"]]}


def import_packet_result(work, path, decision="PASS"):
    packet = read_json(path)
    response = work / f"test-result-{packet['packet_id']}-{decision}.json"
    save_json(response, result_for(packet, decision))
    import_review(work, response)
    return response


def test_late_inflight_pass_is_rejected_and_fresh_review_can_resolve(work):
    fake_translate(work)
    packet = prepare_review(work)
    late_pass = work / "inflight-pass.json"
    save_json(late_pass, result_for(read_json(packet)))
    import_packet_result(work, packet, "NEEDS_REVISION")
    with pytest.raises(PipelineError, match="superseded"):
        import_review(work, late_pass)
    fresh = prepare_review(work)
    assert fresh != packet
    assert all(b["previous_review"]["findings"] for b in read_json(fresh)["blocks"])
    accepted = import_packet_result(work, fresh)
    before = file_sha(work / "reviews/approvals.json")
    import_review(work, accepted)
    assert file_sha(work / "reviews/approvals.json") == before
    assert set(review_state(work).values()) == {"PASS"}


def test_mixed_superseded_response_is_atomic_and_not_sent_to_ai(work):
    fake_translate(work)
    older = prepare_review(work)
    last_packet = prepare_reviews(work, max_blocks=1)[-1]
    import_packet_result(work, last_packet, "INCONCLUSIVE")
    before = file_sha(work / "reviews/approvals.json")
    response = work / "old-all-pass.json"
    save_json(response, result_for(read_json(older)))
    with pytest.raises(PipelineError, match="superseded"):
        import_review(work, response)
    assert file_sha(work / "reviews/approvals.json") == before
    with pytest.raises(PipelineError, match="superseded before AI"):
        run_review(work, older)
    assert file_sha(work / "reviews/approvals.json") == before


def test_stale_translation_response_remains_rejected_after_prepare(work):
    fake_translate(work)
    old = next((work / "translations").glob("*.json"))
    glossary = read_json(work / "glossary.json")
    glossary["status"] = "updated"
    save_json(work / "glossary.json", glossary)
    prepare(work)
    with pytest.raises(PipelineError, match="old packet/glossary"):
        import_translation(work, old)


@pytest.mark.parametrize("source", [
    '[Experimental results](#label-results)',
    '![Experimental results](../assets/figure.png)',
    '[Experimental results](https://example.org/a_(b)#results "original title")',
    '[Experimental results $x=2$](https://example.org/results)',
    '[Experimental results [extended]](https://example.org/results)',
])
def test_link_labels_preserve_math_assets_and_balanced_destinations(source):
    protected, mapping = protect(source, "p001")
    translated = restore(protected.replace("Experimental results", "実験結果").replace("extended", "拡張"), mapping)
    assert "実験結果" in translated
    assert invariants(translated) == invariants(source)
    assert restore(protected, mapping) == source


def test_visible_url_and_autolink_are_immutable():
    source = '[https://example.org/paper](https://example.org/paper) <https://example.org/other>'
    protected, mapping = protect(source, "p001")
    assert "https://" not in protected
    assert restore(protected, mapping) == source


def test_link_only_english_is_detected_and_japanese_link_passes(tmp_path):
    from paper_translator.extract import extract
    from paper_translator.split import split
    work = tmp_path / "link-paper"
    source = work / "source/tex/main.tex"
    source.parent.mkdir(parents=True)
    source.write_text(r"\documentclass{article}\begin{document}\href{https://example.org/results}{Experimental results}\end{document}", encoding="utf-8")
    save_json(work / "manifest.json", {"paper_id": "link-v1", "source_kind": "tex", "files": [{"path": "source/tex/main.tex", "sha256": file_sha(source)}]})
    extract(work)
    split(work)
    prepare(work)
    packet = read_json(next((work / "packets").glob("*.json")))
    response = {k: packet[k] for k in ("paper_id", "packet_sha256", "chunk")}
    response["blocks"] = [{"id": b["id"], "text": b["protected_text"]} for b in packet["blocks"]]
    path = work / "response.json"
    save_json(path, response)
    assert import_translation(work, path)["status"] == "FAIL"
    response["blocks"][0]["text"] = response["blocks"][0]["text"].replace("Experimental results", "実験結果")
    save_json(path, response)
    assert import_translation(work, path)["status"] == "PASS"


def test_recorded_preview_translations_work_with_current_packets(work):
    from scripts.recorded_response import recorded_text
    from scripts.translate_preview_fixture import TEXT
    legacy = {"text": "See [1](#ref) with $x$.", "placeholders": {"⟦PT_old_0⟧": "[1](#ref)", "⟦PT_old_1⟧": "$x$"}}
    assert restore(recorded_text(legacy, "参照@0@：@1@"), legacy["placeholders"]) == "参照[1](#ref)：$x$"
    prepare(work)
    for path in (work / "packets").glob("*.json"):
        packet = read_json(path)
        response = {k: packet[k] for k in ("paper_id", "packet_sha256", "chunk")}
        response["blocks"] = [{"id": b["id"], "text": recorded_text(b, TEXT[b["id"]]) if b["translate"] else b["text"]} for b in packet["blocks"]]
        incoming = work / "recorded-response.json"
        save_json(incoming, response)
        result = import_translation(work, incoming)
    assert result["status"] == "PASS", result


@pytest.mark.parametrize("max_blocks,max_chars", [(1, 10000), (3, 9000), (40, 8000)])
def test_review_batches_are_bounded_ordered_and_keep_neighbor_context(work, max_blocks, max_chars):
    fake_translate(work)
    paths = prepare_reviews(work, max_blocks=max_blocks, max_chars=max_chars)
    source = load_blocks(work)
    assert len(paths) > 1
    blocks = []
    for path in paths:
        packet = read_json(path)
        assert len(packet["blocks"]) <= max_blocks
        assert len(path.read_text(encoding="utf-8")) <= max_chars
        blocks.extend(packet["blocks"])
    assert [b["id"] for b in blocks] == [b["id"] for b in source]
    for i, block in enumerate(blocks):
        assert [b["id"] for b in block["context"]] == [b["id"] for b in source[max(0, i-1):i] + source[i+1:i+2]]
    for path in paths:
        import_packet_result(work, path)
    assert set(review_state(work).values()) == {"PASS"}
    assert status(work)["official"] == "not_generated"
    merge(work)
    assert status(work)["official"] == "VALID"


def test_review_budget_rejects_oversize_single_block_before_ai(work, monkeypatch):
    fake_translate(work)
    before = set((work / "reviews").glob("packet-*.json"))
    with pytest.raises(PipelineError, match="exceeds"):
        run_review(work, max_chars=100)
    assert set((work / "reviews").glob("packet-*.json")) == before


def mock_claude(monkeypatch, fail_at=None, expected_model=None, expected_effort=None):
    import paper_translator.review as module
    calls = []
    def run(args, input, **kwargs):
        from pathlib import Path
        if expected_model is not None:
            assert args[args.index("--model") + 1] == expected_model
        if expected_effort is not None:
            assert args[args.index("--effort") + 1] == expected_effort
        path = Path(input.decode("utf-8").splitlines()[0].removeprefix("/paper-review "))
        packet = read_json(path)
        calls.append([b["id"] for b in packet["blocks"]])
        if len(calls) == fail_at:
            return SimpleNamespace(returncode=1, stdout=b'{}', stderr=b'simulated interruption')
        return SimpleNamespace(returncode=0, stdout=json.dumps({"result": result_for(packet)}).encode("utf-8"), stderr=b'')
    monkeypatch.setattr(module, "find_claude", lambda explicit: "mocked-claude")
    monkeypatch.setattr(module.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("options,model,effort", [([], "claude-opus-5-5", "xhigh"), (["--model", "sonnet", "--effort", "high"], "sonnet", "high")])
def test_review_cli_passes_model_and_effort_to_every_batch(work, monkeypatch, capsys, options, model, effort):
    from paper_translator.cli import main
    fake_translate(work)
    calls = mock_claude(monkeypatch, expected_model=model, expected_effort=effort)
    assert main(["review-run", str(work), "--max-blocks", "3", *options]) == 0
    capsys.readouterr()
    assert len(calls) > 1
    requests = list((work / "reviews").glob("claude-request-*.json"))
    assert len(requests) == len(calls)
    assert all(read_json(path)["requested_model"] == model and read_json(path)["requested_effort"] == effort for path in requests)
    assert set(review_state(work).values()) == {"PASS"}


def test_invalid_effort_is_rejected_before_running_claude(work, capsys):
    from paper_translator.cli import main
    before = tree_hashes(work)
    with pytest.raises(SystemExit) as exc:
        main(["review-run", str(work), "--effort", "invalid"])
    assert exc.value.code == 2
    assert tree_hashes(work) == before


def test_review_run_resumes_after_partial_batch_failure(work, monkeypatch):
    fake_translate(work)
    calls = mock_claude(monkeypatch, fail_at=2)
    with pytest.raises(PipelineError, match="Claude Code failed"):
        run_review(work, max_blocks=3)
    completed = calls[0]
    before = {k: approvals(work)["blocks"][k] for k in completed}
    retry_calls = mock_claude(monkeypatch)
    run_review(work, max_blocks=3)
    assert not set(completed).intersection(k for batch in retry_calls for k in batch)
    assert {k: approvals(work)["blocks"][k] for k in completed} == before
    assert set(review_state(work).values()) == {"PASS"}


def test_changes_at_batch_boundary_rereview_only_affected_blocks(work):
    from paper_translator.annotation_common import document_pairs
    fake_translate(work)
    for path in prepare_reviews(work, max_blocks=2):
        import_packet_result(work, path)
    before = approvals(work)["blocks"]
    pair = next(p for p in document_pairs(work).values() if p["kind"] == "paragraph")
    replace_translation_blocks(work, [{"block_id": pair["block_id"], "text": pair["ja"] + " 改訂。"}])
    states = review_state(work)
    assert states[pair["block_id"]] == "STALE"
    assert "STALE_CONTEXT" in states.values()
    paths = prepare_reviews(work, max_blocks=2)
    requested = [b["id"] for p in paths for b in read_json(p)["blocks"]]
    assert set(requested) == {key for key, value in states.items() if value != "PASS"}
    for path in paths:
        import_packet_result(work, path)
    assert all(approvals(work)["blocks"][k] == v for k, v in before.items() if k not in requested)


@pytest.fixture
def two_papers(work):
    fake_translate(work)
    other = work.parent / "other-paper"
    shutil.copytree(work, other)
    manifest = read_json(other / "manifest.json")
    manifest["paper_id"] = "another-paper-v2"
    save_json(other / "manifest.json", manifest)
    for name in ["terminology_candidates.json", "reading_notes.json", "questions.json"]:
        data = read_json(other / name)
        data["paper_id"] = manifest["paper_id"]
        save_json(other / name, data)
    fake_translate(other)
    return work, other


def tree_hashes(work):
    return {p.relative_to(work).as_posix(): file_sha(p) for p in work.rglob("*") if p.is_file()}


def test_multiple_papers_have_isolated_reviews_terms_notes_and_questions(two_papers):
    from paper_translator.context import question_prepare
    from paper_translator.reading import import_notes
    from paper_translator.terminology import export_pending, import_result, register_candidates, store
    from test_annotations import gpt_result, note_payload, term_payload
    a, b = two_papers
    untouched = tree_hashes(b)
    response = fake_review(a)
    register_candidates(a, term_payload(a))
    import_notes(a, note_payload(a))
    question_prepare(a, "sec01-p003", "Aだけの質問")
    result = gpt_result(a)
    import_result(a, result)
    merge(a)
    assert tree_hashes(b) == untouched
    assert approvals(b)["blocks"] == {}
    assert store(b)["candidates"] == []
    # IDs and text intentionally coincide. A copied review packet must still fail.
    packet_id = read_json(response)["packet_id"]
    save_json(b / "reviews" / f"packet-{packet_id}.json", read_json(a / "reviews" / f"packet-{packet_id}.json"))
    with pytest.raises(PipelineError, match="another paper"):
        import_review(b, response)
    for function, payload in [(register_candidates, term_payload(a)), (import_notes, note_payload(a)), (import_result, result)]:
        with pytest.raises(PipelineError, match="another paper"):
            function(b, payload)
    register_candidates(b, term_payload(b))
    assert read_json(export_pending(a))["paper_id"] != read_json(export_pending(b))["paper_id"]
    fake_review(b)
    merge(b)
    assert "> **AI読解メモ｜原文外" not in (b / "paper_ja_reading.md").read_text(encoding="utf-8")
    assert read_json(b / "reading_notes.json")["notes"] == []
    assert read_json(b / "questions.json")["questions"] == []


def test_paper_list_is_read_only_and_tolerates_a_broken_workspace(two_papers):
    from paper_translator.library import list_papers
    a, b = two_papers
    fake_review(a)
    merge(a)
    before = tree_hashes(a.parent)
    result = list_papers(a.parent)
    assert result["count"] == 2
    by_id = {p["paper_id"]: p for p in result["papers"]}
    assert by_id["fixture-v1"]["official"] == "VALID"
    assert by_id["another-paper-v2"]["review"] == {"UNREVIEWED": len(load_blocks(b))}
    assert tree_hashes(a.parent) == before
    save_json(a.parent / "broken" / "manifest.json", {})
    result = list_papers(a.parent)
    assert result["count"] == 3
    assert any(p.get("status") == "ERROR" for p in result["papers"])


def test_paper_list_flags_duplicate_ids_and_handles_empty_root(two_papers, tmp_path):
    from paper_translator.library import list_papers
    assert list_papers(tmp_path / "missing")["papers"] == []
    a, b = two_papers
    manifest = read_json(b / "manifest.json")
    manifest["paper_id"] = read_json(a / "manifest.json")["paper_id"]
    save_json(b / "manifest.json", manifest)
    assert all(p.get("status") == "ERROR" for p in list_papers(a.parent)["papers"])


def test_translation_response_from_another_paper_is_rejected(two_papers):
    a, b = two_papers
    before = tree_hashes(b)
    with pytest.raises(PipelineError, match="another paper"):
        import_translation(b, next((a / "translations").glob("*.json")))
    assert tree_hashes(b) == before


def test_legacy_approvals_preserve_validity_and_check_paper_provenance(two_papers):
    a, b = two_papers
    fake_review(a)
    legacy = approvals(a)
    del legacy["paper_id"]
    for record in legacy["blocks"].values():
        del record["response_sha256"]
    save_json(a / "reviews/approvals.json", legacy)
    before = file_sha(a / "reviews/approvals.json")
    assert set(review_state(a).values()) == {"PASS"}
    assert file_sha(a / "reviews/approvals.json") == before
    save_json(b / "reviews/approvals.json", legacy)
    for path in (a / "reviews").glob("packet-*.json"):
        save_json(b / "reviews" / path.name, read_json(path))
    with pytest.raises(PipelineError, match="another paper"):
        review_state(b)


def test_shared_terms_preview_export_and_response_import(two_papers, tmp_path):
    from paper_translator.sharing import import_shared_terms, share_terms
    from paper_translator.terminology import export_pending, register_candidates, terminology_status
    from test_annotations import gpt_result, term_payload
    a, b = two_papers
    register_candidates(a, term_payload(a))
    packet = export_pending(a)
    root = tmp_path / "gpt-review"
    preview = share_terms(a, packet, root)
    assert preview["mode"] == "preview" and not root.exists()
    assert preview["packet"]["candidates"][0]["occurrences"][0]["source_context"]
    before = tree_hashes(b)
    exported = share_terms(a, packet, root, write=True)
    assert len(list(root.rglob("*.json"))) == 1
    assert not list(root.rglob("*.tex")) and not list(root.rglob("*.md"))
    from pathlib import Path
    response = Path(exported["destination"]).parent.parent / "responses" / "gpt-result.json"
    save_json(response, gpt_result(a))
    import_shared_terms(a, response, root)
    assert {v["state"] for v in terminology_status(a).values()} == {"approved"}
    assert tree_hashes(b) == before
    with pytest.raises(PipelineError, match="this paper"):
        import_shared_terms(b, response, root)


@pytest.mark.parametrize("damage", ["stale_context", "shared_request", "wrong_paper"])
def test_shared_terms_reject_stale_modified_or_misfiled_results(two_papers, tmp_path, damage):
    from pathlib import Path
    from paper_translator.annotation_common import document_pairs
    from paper_translator.sharing import import_shared_terms, share_terms
    from paper_translator.terminology import export_pending, register_candidates
    from test_annotations import gpt_result, term_payload
    a, b = two_papers
    register_candidates(a, term_payload(a))
    root = tmp_path / "gpt-review"
    shared = share_terms(a, export_pending(a), root, write=True)
    request = Path(shared["destination"])
    response = request.parent.parent / "responses/result.json"
    payload = gpt_result(a)
    if damage == "stale_context":
        pair = next(p for p in document_pairs(a).values() if p["kind"] == "paragraph" and "state" in p["en"])
        replace_translation_blocks(a, [{"block_id": pair["block_id"], "text": pair["ja"] + " 改訂。"}])
    elif damage == "shared_request":
        data = read_json(request)
        data["instructions"] += " modified"
        save_json(request, data)
    else:
        payload["paper_id"] = "another-paper-v2"
    save_json(response, payload)
    before = file_sha(a / "terminology_candidates.json")
    with pytest.raises(PipelineError):
        import_shared_terms(a, response, root)
    assert file_sha(a / "terminology_candidates.json") == before


def test_share_rejects_extra_data_or_packets_outside_workspace(work, tmp_path):
    from paper_translator.sharing import share_terms
    from paper_translator.terminology import export_pending
    fake_translate(work)
    path = export_pending(work)
    outside = tmp_path / path.name
    save_json(outside, read_json(path))
    with pytest.raises(PipelineError, match="inside this workspace"):
        share_terms(work, outside, tmp_path / "shared", write=True)
    packet = read_json(path)
    packet["internal_log"] = "must not be shared"
    save_json(path, packet)
    with pytest.raises(PipelineError, match="Additional properties"):
        share_terms(work, path, tmp_path / "shared", write=True)
    assert not (tmp_path / "shared").exists()


def test_new_cli_commands(two_papers, tmp_path, capsys):
    from paper_translator.cli import main
    from paper_translator.terminology import export_pending, register_candidates
    from test_annotations import term_payload
    a, _ = two_papers
    assert main(["papers", "--root", str(a.parent)]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 2
    assert main(["review-prepare", str(a), "--max-blocks", "2"]) == 0
    assert len(json.loads(capsys.readouterr().out)["packets"]) > 1
    register_candidates(a, term_payload(a))
    packet = export_pending(a)
    root = tmp_path / "gpt-review"
    assert main(["term-share", str(a), str(packet), "--root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "preview"
    assert not root.exists()
    assert main(["term-share", str(a), str(packet), "--root", str(root), "--write"]) == 0
    from pathlib import Path
    request = Path(json.loads(capsys.readouterr().out)["destination"])
    from test_annotations import gpt_result
    response = request.parent.parent / "responses/result.json"
    save_json(response, gpt_result(a))
    assert main(["term-share-import", str(a), str(response), "--root", str(root)]) == 0
    assert {v["state"] for v in json.loads(capsys.readouterr().out).values()} == {"approved"}
