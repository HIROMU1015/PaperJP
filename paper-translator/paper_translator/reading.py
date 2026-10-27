"""Separate AI notes and a labelled reading edition; never rewrite paper_ja.md."""
import html
import re
from pathlib import Path

from .annotation_common import checked_payload, digest, document_pairs, load_store, matching_hashes, paper_id, require_pair, save_store
from .common import PipelineError, file_sha, issue, now, parse_markdown, read_json, report, save, save_json

LABELS = {"explicit": "原文に明記された内容の説明", "derived": "原文から論理的に導いた補足", "speculative": "AIの推測・仮説・疑問"}
KINDS = {"logic": "論理関係", "assumption": "仮定", "derivation": "導出", "notation": "記号・用語", "relation": "他の箇所との関係", "question": "疑問", "explanation": "説明"}
CERTAINTIES = {"high": "高", "medium": "中", "low": "低"}


def source_label(location):
    label = location.get("file", "原文")
    if location.get("line"):
        label += f":{location['line']}"
    if location.get("page"):
        label += f"（{location['page']}ページ）"
    if location.get("element_id"):
        label += f" #{location['element_id']}"
    return label


def notes_store(work):
    return load_store(work, "reading_notes.json", "reading-notes", "notes")


def import_notes(work, payload):
    checked_payload(work, "notes-input", payload)
    pairs, data = document_pairs(work), notes_store(work)
    staged_ids = set()
    for item in payload["notes"]:
        pair = require_pair(pairs, item["block_id"])
        if not matching_hashes(item, pair):
            raise PipelineError(f"Stale note context: {item['block_id']}")
        evidence = []
        for cited in item["evidence"]:
            src = require_pair(pairs, cited["block_id"])
            if cited["source_sha256"] != src["source_sha256"] or cited["quote"] not in src["en"]:
                raise PipelineError(f"Evidence is not an exact quote from the current English block: {cited['block_id']}")
            evidence.append({**cited, "source_location": src["source_location"]})
        note_id = item.get("id") or "note-" + digest({k: v for k, v in item.items() if k != "id"})[:20]
        if note_id in staged_ids:
            raise PipelineError("Duplicate note ID in input")
        staged_ids.add(note_id)
        old = next((n for n in data["notes"] if n["id"] == note_id), None)
        if old and old["block_id"] != item["block_id"]:
            raise PipelineError("An existing note ID cannot be moved to another block")
        value = {**item, "id": note_id, "evidence": evidence, "created_at": old["created_at"] if old else now()}
        if old:
            old.update(value)
        else:
            data["notes"].append(value)
    save_store(work, "reading_notes.json", "reading-notes", data)
    return note_states(work)


def note_states(work):
    pairs = document_pairs(work)
    result = {}
    for note in notes_store(work)["notes"]:
        block_id = note["block_id"]
        if block_id not in pairs or any(e["block_id"] not in pairs for e in note["evidence"]):
            result[note["id"]] = "orphan"
        elif not matching_hashes(note, pairs[block_id]) or any(e["source_sha256"] != pairs[e["block_id"]]["source_sha256"] or e["quote"] not in pairs[e["block_id"]]["en"] for e in note["evidence"]):
            result[note["id"]] = "stale"
        else:
            result[note["id"]] = "current"
    return result


def reading_edition(work, draft=False):
    work = Path(work)
    from .merge import status
    if not draft and status(work)["official"] != "VALID":
        raise PipelineError("Reading edition requires a currently valid paper_ja.md; use --draft for diagnosis")
    source_file = work / ("paper_ja.draft.md" if draft else "paper_ja.md")
    text = source_file.read_text(encoding="utf-8")
    faithful = parse_markdown(text[text.index("<!-- block:"):])
    pairs = document_pairs(work)
    if [b["id"] for b in faithful] != list(pairs) or any(b["text"] != pairs[b["id"]]["ja"].replace("](../assets/", "](assets/") for b in faithful):
        raise PipelineError("Faithful output differs from current translation; re-merge first")
    notes, states = notes_store(work)["notes"], note_states(work)
    if "orphan" in states.values():
        raise PipelineError("Orphan reading note IDs; inspect annotations-check before generating the reading edition")
    original_hash = file_sha(source_file)
    lines = ["<!-- 閲覧版：本文とAI読解メモを分離。引用にはpaper_ja.mdの本文を使用してください。 -->", ""]
    if draft:
        lines += ["> **DRAFT｜未検証・未承認を含む閲覧版**", ""]
    lines += ["**段落へ移動**：" + " · ".join(f"[〔{b['id']}〕](#block-{b['id']})" for b in faithful), ""]
    for block in faithful:
        block_id = block["id"]
        lines += [f'<a id="block-{block_id}"></a>', f"**〔{block_id}〕**", "", f"<!-- block:{block_id} -->", block["text"], f"<!-- /block:{block_id} -->", ""]
        for note in notes:
            if note["block_id"] != block_id:
                continue
            stale = "｜要再確認：原文または訳が更新" if states[note["id"]] == "stale" else ""
            header = f"AI読解メモ｜原文外｜{LABELS[note['claim_type']]}{stale}"
            lines += [f"> **{header}**", ">", f"> 種類：{KINDS[note['kind']]}／確実性：{CERTAINTIES[note['certainty']]}／作成：{note['agent']}", ">"]
            lines += ["> " + html.escape(line, quote=False) for line in note["content"].splitlines()]
            for evidence in note["evidence"]:
                quote = " ".join(evidence["quote"].splitlines())
                lines += [">", f"> 根拠：[〔{evidence['block_id']}〕](#block-{evidence['block_id']}) {html.escape(source_label(evidence['source_location']), quote=False)}", f"> 原文：{html.escape(quote, quote=False)}"]
            lines += [""]
    output = "\n".join(lines).replace("](../assets/", "](assets/") + "\n"
    expected = [b["id"] for b in faithful]
    if re.findall(r"<!-- block:([A-Za-z0-9_-]+) -->", output) != expected:
        raise PipelineError("Reading edition changed or duplicated block IDs")
    recovered = [{"id": m[1], "text": m[2].strip()} for m in re.finditer(r"<!-- block:([A-Za-z0-9_-]+) -->\n([\s\S]*?)\n<!-- /block:\1 -->", output)]
    if recovered != faithful:
        raise PipelineError("Reading edition failed faithful-body round-trip verification")
    name = "paper_ja_reading.draft.md" if draft else "paper_ja_reading.md"
    save(work / name, output)
    if file_sha(source_file) != original_hash:
        raise PipelineError("Faithful output changed while generating reading edition")
    marker = {"schema_version": 1, "paper_id": paper_id(work), "draft": draft, "faithful_sha256": original_hash, "notes_sha256": digest(notes), "output_sha256": file_sha(work / name), "block_ids": expected}
    checked_payload(work, "reading-edition", marker)
    save_json(work / "reading-edition.json", marker)
    return work / name


def annotations_check(work):
    from .terminology import terminology_status, terminology_issues
    from .context import question_states
    problems = terminology_issues(work)
    terms = terminology_status(work)
    for key, value in terms.items():
        if value["severity"] == "minor" and value["state"] == "stale":
            problems.append(issue("terminology_stale", key, "warning"))
    notes = note_states(work)
    questions = question_states(work)
    for kind, states in (("reading_note", notes), ("question", questions)):
        for key, state in states.items():
            if state != "current":
                problems.append(issue(f"{kind}_{state}", key, "warning"))
    marker = Path(work) / "reading-edition.json"
    edition_state = "not_generated"
    if marker.exists():
        recorded = read_json(marker)
        checked_payload(work, "reading-edition", recorded)
        name = "paper_ja_reading.draft.md" if recorded["draft"] else "paper_ja_reading.md"
        faithful_name = "paper_ja.draft.md" if recorded["draft"] else "paper_ja.md"
        output_path, faithful_path = Path(work) / name, Path(work) / faithful_name
        current = output_path.is_file() and faithful_path.is_file() and recorded["faithful_sha256"] == file_sha(faithful_path) and recorded["notes_sha256"] == digest(notes_store(work)["notes"]) and recorded["output_sha256"] == file_sha(output_path)
        if not recorded["draft"]:
            from .merge import status
            current = current and status(work)["official"] == "VALID"
        edition_state = "current" if current else "stale"
        if not current:
            problems.append(issue("reading_edition_stale", "Regenerate reading edition after document/note changes", "warning"))
    return report(work, "annotations", problems, terminology=terms, reading_notes=notes, questions=questions, reading_edition=edition_state)
