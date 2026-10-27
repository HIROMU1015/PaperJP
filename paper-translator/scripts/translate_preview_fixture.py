"""Codex-authored translation of the original tiny test fixture (not a research paper)."""
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paper_translator.common import read_json, save_json
from paper_translator.split import prepare
from paper_translator.translate import import_translation

TEXT = {
    "sec00-title001": "# 小さな量子テスト",
    "sec00-h001": "## 要旨",
    "sec00-p001": "量子状態を解析し、すべての記述の科学的な意味を保持する。",
    "sec01-h001": "## はじめに",
    "sec01-p001": "この例では、確率はおよそ@0@となる可能性があるが、この限界は十分ではない。",
    "sec01-p002": "独立した二つ目の段落では、実験の仮定を一切変えずに、その実験を記述する。",
    "sec01-h002": "### 方法",
    "sec01-p003": "文献@0@の状態と、式@1@の数式を使用する。",
    "sec01-cap001": "確率@0@の量子状態の模式図。",
    "sec01-cap002": "入力状態とラベル。",
    "sec01-tab001": "| 状態 | ラベル |\n| --- | --- |\n| 基底状態 | ゼロ |\n| 励起状態 | イチ |",
    "sec02-h001": "## 証明",
    "sec02-p001": "この付録には、証明と説明のための脚注@0@@1@@2@@3@が含まれる。",
    "sec02-fn001": "@0@\n@1@. この条件は必要条件であり、十分条件ではない。 @2@",
    "sec02-h002": "## 参考文献",
}


def main():
    work = Path(__file__).resolve().parents[1] / "workspace" / "preview-fixture"
    prepare(work)
    save_json(work / "glossary.json", {"schema_version": 1, "status": "curated_by_codex", "terms": [{"en": "quantum state", "ja": "量子状態", "block_ids": ["sec00-p001"]}, {"en": "necessary", "ja": "必要", "block_ids": ["sec02-fn001"]}, {"en": "sufficient", "ja": "十分", "block_ids": ["sec02-fn001"]}]})
    prepare(work)
    for path in (work / "packets").glob("*.json"):
        packet = read_json(path)
        result = []
        for src in packet["blocks"]:
            tokens = list(src["placeholders"])
            text = re.sub(r"@(\d+)@", lambda m: tokens[int(m[1])], TEXT[src["id"]]) if src["translate"] else src["text"]
            result.append({"id": src["id"], "text": text})
        response = work / "codex-responses" / path.name
        save_json(response, {"chunk": packet["chunk"], "blocks": result})
        print(import_translation(work, response)["status"])


if __name__ == "__main__":
    main()
