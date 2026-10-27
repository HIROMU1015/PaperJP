import json
import re

import pytest

from paper_translator.annotation_common import digest, document_pairs, initialize, paper_id
from paper_translator.annotation_schema import SCHEMAS, schema, validate_data
from paper_translator.common import PipelineError, file_sha, load_blocks, read_json, save, save_json
from paper_translator.context import explain_context, question_prepare, question_states, search_context
from paper_translator.merge import merge, status
from paper_translator.reading import annotations_check, import_notes, note_states, reading_edition
from paper_translator.review import review_state
from paper_translator.terminology import apply_correction, export_pending, import_result, register_candidates, store, terminology_status
from paper_translator.translate import import_translation, replace_translation_blocks
from paper_translator.validate import validate_translation
from test_pipeline import fake_review, fake_translate


@pytest.fixture
def translated(work):
    fake_translate(work)
    fake_review(work)
    merge(work)
    return work


def term_payload(work, severity="minor", sense="quantum-state", block_ids=None):
    pairs = document_pairs(work)
    ids = block_ids or [key for key, p in pairs.items() if p["kind"] == "paragraph" and "state" in p["en"]]
    return {"schema_version": 1, "paper_id": paper_id(work), "candidates": [{"en": "state", "sense": sense, "ja": "これは日本語", "alternatives": ["状態"], "reason": "Synthetic terminology protocol test", "severity": severity, "block_ids": ids}]}


def gpt_result(work, decision="approve"):
    """Synthetic GPT response: protocol tests only, not a real semantic review."""
    packet = read_json(export_pending(work))
    results = []
    for c in packet["candidates"]:
        entry = {"candidate_id": c["id"], "candidate_sha256": c["candidate_sha256"], "decision": decision, "rationale": "Synthetic GPT review for protocol testing"}
        if decision == "correct":
            entry["suggestions"] = {o["block_id"]: "修正した日本語" for o in c["occurrences"]}
        results.append(entry)
    return {"schema_version": 1, "paper_id": paper_id(work), "reviewer": "GPT", "packet_id": packet["packet_id"], "results": results}


def note_payload(work, claim_type="explicit"):
    pair = next(p for p in document_pairs(work).values() if p["kind"] == "paragraph")
    return {"schema_version": 1, "paper_id": paper_id(work), "notes": [{"block_id": pair["block_id"], "kind": "explanation", "claim_type": claim_type, "content": "原文を読むための補足です。", "certainty": "high", "agent": "Codex", "source_sha256": pair["source_sha256"], "target_sha256": pair["target_sha256"], "evidence": [{"block_id": pair["block_id"], "quote": pair["en"], "source_sha256": pair["source_sha256"]}]}]}


def test_same_term_same_sense_is_deduplicated_and_occurrences_grouped(translated):
    payload = term_payload(translated)
    register_candidates(translated, payload)
    before = file_sha(translated / "terminology_candidates.json")
    register_candidates(translated, payload)
    assert file_sha(translated / "terminology_candidates.json") == before
    data = store(translated)["candidates"]
    assert len(data) == 1 and len(data[0]["occurrences"]) >= 2
    assert all(o["source_context"] and o["target_context"] and o["source_location"] for o in data[0]["occurrences"])


def test_same_word_different_senses_remain_separate(translated):
    register_candidates(translated, term_payload(translated))
    register_candidates(translated, term_payload(translated, sense="classical-condition"))
    assert len(store(translated)["candidates"]) == 2


@pytest.mark.parametrize("severity,blocked", [("minor", False), ("major", True), ("critical", True)])
def test_only_serious_unresolved_terms_block_official(translated, severity, blocked):
    register_candidates(translated, term_payload(translated, severity))
    assert (status(translated)["official"] == "STALE_OR_INVALID") == blocked
    if blocked:
        with pytest.raises(PipelineError):
            merge(translated)
        assert merge(translated, draft=True).name == "paper_ja.draft.md"
    else:
        merge(translated)
    assert validate_translation(translated)["status"] == "PASS"


@pytest.mark.parametrize("decision,expected", [("approve", "approved"), ("correct", "revision_requested"), ("defer", "deferred")])
def test_gpt_decisions_and_pending_export(translated, decision, expected):
    register_candidates(translated, term_payload(translated))
    import_result(translated, gpt_result(translated, decision))
    assert {v["state"] for v in terminology_status(translated).values()} == {expected}
    assert bool(read_json(export_pending(translated))["candidates"]) == (decision != "approve")


def test_stale_gpt_response_is_rejected(translated):
    register_candidates(translated, term_payload(translated))
    result = gpt_result(translated)
    key = store(translated)["candidates"][0]["occurrences"][0]["block_id"]
    pair = document_pairs(translated)[key]
    replace_translation_blocks(translated, [{"block_id": key, "text": pair["ja"] + " 補足した訳。"}])
    with pytest.raises(PipelineError, match="Stale"):
        import_result(translated, result)
    assert {v["state"] for v in terminology_status(translated).values()} == {"stale"}


def correction_payload(work):
    candidate = store(work)["candidates"][0]
    pairs = document_pairs(work)
    return {"schema_version": 1, "paper_id": paper_id(work), "candidate_id": candidate["id"], "occurrences": [{"block_id": o["block_id"], "text": pairs[o["block_id"]]["ja"].replace("これは日本語", "これは修正した日本語"), "rationale": "This occurrence was individually checked in the synthetic test", "source_sha256": pairs[o["block_id"]]["source_sha256"], "target_sha256": pairs[o["block_id"]]["target_sha256"]} for o in candidate["occurrences"]]}


def test_per_occurrence_correction_invalidates_claude_and_requires_rereview(translated):
    register_candidates(translated, term_payload(translated, "major"))
    import_result(translated, gpt_result(translated, "correct"))
    result = apply_correction(translated, correction_payload(translated))
    assert "STALE" in result["review"].values()
    assert "STALE_CONTEXT" in result["review"].values()
    assert validate_translation(translated)["status"] == "PASS"
    with pytest.raises(PipelineError):
        merge(translated)
    fake_review(translated)
    merge(translated)
    assert status(translated)["official"] == "VALID"
    assert {v["state"] for v in terminology_status(translated).values()} == {"approved"}


def test_bad_term_correction_does_not_mutate_translation(translated):
    register_candidates(translated, term_payload(translated))
    import_result(translated, gpt_result(translated, "correct"))
    changes = correction_payload(translated)
    changes["occurrences"][0]["text"] += " 999"
    paths = list((translated / "ja_draft").glob("*.md"))
    before = [file_sha(p) for p in paths]
    with pytest.raises(PipelineError, match="machine validation"):
        apply_correction(translated, changes)
    assert [file_sha(p) for p in paths] == before


def test_translation_response_can_register_terms(translated):
    response_path = next((translated / "translations").glob("*.json"))
    response = read_json(response_path)
    payload = term_payload(translated)
    ids = {b["id"] for b in response["blocks"]}
    payload["candidates"][0]["block_ids"] = [k for k in payload["candidates"][0]["block_ids"] if k in ids]
    response["terminology_candidates"] = payload["candidates"]
    path = translated / "translation-with-terms.json"
    save_json(path, response)
    import_translation(translated, path)
    assert len(store(translated)["candidates"]) == 1


@pytest.mark.parametrize("claim_type", ["explicit", "derived", "speculative"])
def test_notes_are_labelled_and_never_change_faithful_content(translated, claim_type):
    before = file_sha(translated / "paper_ja.md")
    import_notes(translated, note_payload(translated, claim_type))
    path = reading_edition(translated)
    text = path.read_text(encoding="utf-8")
    assert "AI読解メモ｜原文外" in text
    assert "〔sec00-p001〕" in text and '<a id="block-sec00-p001"></a>' in text
    assert "[〔sec00-p001〕](#block-sec00-p001)" in text
    assert file_sha(translated / "paper_ja.md") == before
    assert validate_translation(translated)["status"] == "PASS"
    assert annotations_check(translated)["reading_edition"] == "current"


def test_stale_notes_and_questions_are_flagged_without_affecting_coverage(translated):
    payload = note_payload(translated)
    block_id = payload["notes"][0]["block_id"]
    import_notes(translated, payload)
    question_prepare(translated, block_id, "この段落の意味は？")
    pair = document_pairs(translated)[block_id]
    replace_translation_blocks(translated, [{"block_id": block_id, "text": pair["ja"] + " 訳を改訂。"}])
    assert set(note_states(translated).values()) == {"stale"}
    assert set(question_states(translated).values()) == {"stale"}
    assert validate_translation(translated)["status"] == "PASS"
    fake_review(translated)
    merge(translated)
    assert "要再確認" in (translated / "paper_ja_reading.md").read_text(encoding="utf-8")


def test_unknown_note_id_bad_evidence_and_schema_are_rejected(translated):
    for mutation in ["id", "evidence", "classification"]:
        payload = note_payload(translated)
        if mutation == "id":
            payload["notes"][0]["block_id"] = "missing"
        elif mutation == "evidence":
            payload["notes"][0]["evidence"][0]["quote"] = "This text is not in the source."
        else:
            payload["notes"][0]["claim_type"] = "paper_claim"
        with pytest.raises(PipelineError):
            import_notes(translated, payload)


def test_note_markup_cannot_inject_faithful_ids(translated):
    payload = note_payload(translated)
    payload["notes"][0]["content"] = "<!-- block:fake -->\n# 偽の本文見出し"
    import_notes(translated, payload)
    text = reading_edition(translated).read_text(encoding="utf-8")
    assert "<!-- block:fake -->" not in text
    assert "> # 偽の本文見出し" in text


def test_note_import_is_idempotent(translated):
    payload = note_payload(translated)
    import_notes(translated, payload)
    before = file_sha(translated / "reading_notes.json")
    import_notes(translated, payload)
    assert file_sha(translated / "reading_notes.json") == before


def test_explain_collects_cross_section_references_and_inline_math_without_writes(translated):
    before = {str(p): file_sha(p) for p in translated.rglob("*") if p.is_file()}
    context = explain_context(translated, "sec01-p003", radius=0)
    assert context["target"]["en"] and context["target"]["ja"]
    assert any(p["kind"] == "equation" for p in context["related"])
    assert any(p["kind"] == "figure" for p in context["related"])
    assert any(p["block_id"] == "sec02-bib001" for p in context["related"])
    assert any(p["block_id"] == "sec01-p001" for p in search_context(translated, "probability"))
    assert {str(p): file_sha(p) for p in translated.rglob("*") if p.is_file()} == before
    pair = next(p for p in document_pairs(translated).values() if p["inline_math"])
    assert explain_context(translated, pair["inline_math"][0]["id"])["block_id"] == pair["block_id"]


def test_question_storage_is_separate_and_idempotent(translated):
    output = question_prepare(translated, "sec01-p003", "この式はなぜ成立する？", related=["sec02-p001"])
    digest_before = file_sha(output)
    assert question_prepare(translated, "sec01-p003", "この式はなぜ成立する？", related=["sec02-p001"]) == output
    assert file_sha(output) == digest_before
    assert len(read_json(translated / "questions.json")["questions"]) == 1
    assert read_json(translated / "reading_notes.json")["notes"] == []
    assert store(translated)["candidates"] == []


def test_reading_requires_current_official_but_draft_can_be_generated(translated):
    pair = next(p for p in document_pairs(translated).values() if p["kind"] == "paragraph")
    replace_translation_blocks(translated, [{"block_id": pair["block_id"], "text": pair["ja"] + " 改訂。"}])
    with pytest.raises(PipelineError, match="currently valid"):
        reading_edition(translated)
    merge(translated, draft=True)
    assert "DRAFT" in reading_edition(translated, draft=True).read_text(encoding="utf-8")


def test_new_schemas_are_valid_and_wrong_paper_is_rejected(translated):
    from jsonschema import Draft202012Validator
    for name in SCHEMAS:
        Draft202012Validator.check_schema(schema(name))
    payload = term_payload(translated)
    payload["paper_id"] = "another-paper-v2"
    with pytest.raises(PipelineError, match="another paper"):
        register_candidates(translated, payload)


def test_modified_question_packet_is_detected(translated):
    path = question_prepare(translated, "sec01-p003", "質問テスト")
    packet = read_json(path)
    packet["target"]["ja"] += " ファイルだけを変更。"
    save_json(path, packet)
    assert set(question_states(translated).values()) == {"modified"}


def test_reading_is_reproducible_and_note_change_marks_old_edition_stale(translated):
    output = reading_edition(translated)
    digest_before = file_sha(output)
    mtime_before = output.stat().st_mtime_ns
    reading_edition(translated)
    assert file_sha(output) == digest_before
    assert output.stat().st_mtime_ns == mtime_before
    import_notes(translated, note_payload(translated))
    assert annotations_check(translated)["reading_edition"] == "stale"
    assert status(translated)["official"] == "VALID"


def test_term_plan_cannot_be_applied_without_per_occurrence_reason(translated):
    register_candidates(translated, term_payload(translated))
    import_result(translated, gpt_result(translated, "correct"))
    payload = correction_payload(translated)
    payload["occurrences"][0]["rationale"] = ""
    with pytest.raises(PipelineError):
        apply_correction(translated, payload)


def test_bad_translation_term_input_does_not_save_a_chunk(translated):
    path = next((translated / "translations").glob("*.json"))
    response = read_json(path)
    before = {p.name: file_sha(p) for p in (translated / "ja_draft").glob("*.md")}
    response["blocks"][0]["text"] += " 改訂。"
    term = term_payload(translated)["candidates"][0]
    term["block_ids"] = ["not-in-this-chunk"]
    response["terminology_candidates"] = [term]
    incoming = translated / "invalid-term-translation.json"
    save_json(incoming, response)
    with pytest.raises(PipelineError):
        import_translation(translated, incoming)
    assert {p.name: file_sha(p) for p in (translated / "ja_draft").glob("*.md")} == before


def test_reading_body_round_trips_to_exact_faithful_blocks(translated):
    from paper_translator.common import parse_markdown
    import_notes(translated, note_payload(translated, "speculative"))
    output = reading_edition(translated).read_text(encoding="utf-8")
    recovered = [{"id": m[1], "text": m[2].strip()} for m in re.finditer(r"<!-- block:([A-Za-z0-9_-]+) -->\n([\s\S]*?)\n<!-- /block:\1 -->", output)]
    faithful = parse_markdown((translated / "paper_ja.md").read_text(encoding="utf-8"))
    assert recovered == faithful
