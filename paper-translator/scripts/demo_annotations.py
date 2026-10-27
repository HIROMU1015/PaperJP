"""Record a few Codex annotations on the existing paper without editing its translation."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paper_translator.annotation_common import document_pairs, initialize, paper_id
from paper_translator.common import file_sha, save_json
from paper_translator.context import explain_context
from paper_translator.reading import annotations_check, import_notes, reading_edition
from paper_translator.terminology import export_pending, register_candidates


def main():
    project = Path(__file__).resolve().parents[1]
    work = project / "workspace" / "quant-ph_0005006v1"
    faithful_before = file_sha(work / "paper_ja.md")
    approval_before = file_sha(work / "reviews" / "approvals.json")
    initialize(work)
    pairs = document_pairs(work)
    candidates = []
    for pair in pairs.values():
        if "uncopying" in pair["en"].casefold():
            ja = next((value for value in ["複製の取り消し", "複製取り消し"] if value in pair["ja"]), None)
            if ja:
                candidates.append({"en": "uncopying", "sense": "pb-definition", "ja": ja, "alternatives": ["複製解除"], "reason": "通常の不可逆な情報消去と区別し、PBの定義を指す訳語を論文全体で確認するため", "severity": "minor", "block_ids": [pair["block_id"]]})
        if "ancilla" in pair["en"].casefold() and "補助系" in pair["ja"]:
            candidates.append({"en": "ancilla", "sense": "auxiliary-subsystem", "ja": "補助系", "alternatives": ["補助システム"], "reason": "量子状態そのものと、その状態を持つ補助的な部分系を区別して読むため", "severity": "minor", "block_ids": [pair["block_id"]]})
    register_candidates(work, {"schema_version": 1, "paper_id": paper_id(work), "candidates": candidates})
    notes = []
    specifications = [
        ("sec00-p005", "definition-uncopying", "explanation", "explicit", "high", "Uncopying needs", "この段落では、uncopyingを、同じ状態を少なくとも二つ用意し、そのうちのコピーを所定の状態へ移す操作として紹介している。通常の不可逆な情報消去と区別して読む必要がある。"),
        ("sec00-p006", "question-construction", "derivation", "speculative", "medium", "even  though", "ここで述べられた変換の存在が、補助系の初期状態に関する前提の下でどこまで示されているかは、参照先と照合する余地がある。これは原文の結論を追認・訂正するものではなく、読解上の確認点である。"),
    ]
    for block_id, note_id, kind, claim_type, certainty, quote_start, content in specifications:
        pair = pairs[block_id]
        start = pair["en"].index(quote_start)
        end = pair["en"].index(".", start) + 1
        notes.append({"id": note_id, "block_id": block_id, "kind": kind, "claim_type": claim_type, "content": content, "certainty": certainty, "agent": "Codex", "source_sha256": pair["source_sha256"], "target_sha256": pair["target_sha256"], "evidence": [{"block_id": block_id, "quote": pair["en"][start:end], "source_sha256": pair["source_sha256"]}]})
    import_notes(work, {"schema_version": 1, "paper_id": paper_id(work), "notes": notes})
    output = reading_edition(work)
    packet = export_pending(work)
    context = explain_context(work, "sec00-p005", related=["sec00-p006"])
    save_json(project / "reports" / "explain-example.json", context)
    result = annotations_check(work)
    result.update(faithful_unchanged=file_sha(work / "paper_ja.md") == faithful_before, claude_approvals_unchanged=file_sha(work / "reviews" / "approvals.json") == approval_before, faithful_sha256=faithful_before, reading_output=str(output), pending_terms_packet=str(packet), gpt_semantic_review="not_run: pending candidates are intended for human handoff")
    save_json(project / "reports" / "annotations-demo.json", result)
    print(output)
    print(packet)
    print("Faithful body unchanged:", result["faithful_unchanged"])
    print("Claude approvals unchanged:", result["claude_approvals_unchanged"])


if __name__ == "__main__":
    main()
