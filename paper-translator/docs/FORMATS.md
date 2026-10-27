# データ契約（schema_version: 1）

全JSON/MarkdownはUTF-8、SHA-256はUTF-8文字列またはファイルの実バイトに対して計算。
ブロックの`text`は前後空白を除いた文字列。改行はLF。

- `manifest.json`: 固定したpaper_id/version、取得時刻、取得試行、URL、原本ごとのpath/sha256/bytes。
  `source/`は不変。展開したTeXもアーカイブに由来するハッシュ付き原本として管理。
- `blocks.jsonl`: 1行1ブロック。`id,kind,section,text,translate,source`が必須。
  `source`はTeXのfile/line/offset、PDFではpage/bbox。数式/表には`raw_tex`を追加。
  インライン数式は所属ブロックの`inline_math`配列に固有ID・原文raw_tex・表示用markdown・位置を保存。
  Markdownで区切りが壊れないよう、インライン式の端の空白を除き、式内の改行を空白にする。
  演算子・コマンド・数値・`\text{...}`の自然言語は変えず、原本とraw_texで変換前へ追跡できる。
  IDは同じ抽出結果を分割・翻訳・レビュー・統合しても不変。原文の構造自体が変わると
  IDが変わる場合があるため、ID一致だけで旧翻訳や承認を再利用しない。
- `chunks.json`: blocksファイルのハッシュ、論文順のchunk一覧とブロックID列、英語Markdownハッシュ。
  分割は段落途中では行わない。トークン数は文字数/4の概算で、構造保持を優先する。
- `en/*.md`, `ja_draft/*.md`, `ja_reviewed/*.md`: 各ブロックは`<!-- block:ID -->`で始まる。
  `ja_reviewed`は正式統合時の承認済みスナップショット。
- `glossary.json`: `status`, `terms: [{en,ja,block_ids}]`。最初は候補、Codexが全体を読み補完。
  辞書の変更はレビュー失効の対象。
- `packets/*.json`: 英語/保護済みtext/プレースホルダー対応表/原文ハッシュと前後文脈。
  `translate=false`は原文の完全コピー、`true`は`protected_text`を翻訳する。
  英語の明示的な日付（13 March 2000等）は取り込み時に2000年3月13日へ決定的に表記変換し、
  数値検証では年月日の組を照合する。数式・引用等の保護対象内部の日付は変換しない。
- 翻訳応答: `{ "chunk": "001-sec00.md", "blocks": [{"id":"...","text":"..."}] }`。
  全IDを順序通りに含める。非翻訳ブロックも含む。Pythonが復元しMarkdownを保存する。
- `reviews/packet-HASH.json`: 原文、訳、用語集、文脈と各ハッシュ。パケットは内容アドレスで保存。
- レビュー応答: `reviewer: "Claude Code"`, `packet_id`, `blocks`。各項目は
  `id,source_sha256,target_sha256,glossary_sha256,decision,rationale,findings`。
  decisionはPASS/NEEDS_REVISION/INCONCLUSIVE。findingsはtype/severity/evidence/suggestion。
- `reviews/approvals.json`: ID別の判定とハッシュ、参照文脈のハッシュ、取得元packet ID。
  ハッシュ照合は内容の対応を保証するが、手動JSONの書き手を暗号学的に認証しない。
  実レビューの根拠はClaudeの保存応答と実行ログ。テストの模擬応答と区別する。
- `reports/*.json`, `*.md`: PASS/FAIL、数値指標、error/warningを両形式で保存。
  警告だけで完全性を証明したとは扱わない。PDF/HTML診断モードは重大エラーで正式版を遮断。
- `paper_ja.draft.md`: 調査用。未承認である旨のコメント付き。`paper_ja.md`は全ゲート通過時のみ。
- `finalization.json`: 統合出力・blocks・全訳・用語集のハッシュ。変更後は`status`が
  STALE_OR_INVALIDを返す。古い正式版は履歴資料として残るため、配布前にstatusを確認する。

再実行: 取得済み版の原本は再利用、同一出力は書き換えない。変更済み生成物は同じ場所の
`history/`へハッシュ名で退避してから原子的に置換。状態は実ファイルから再計算する。
未実行はnot_run/UNREVIEWED、失敗はFAIL、旧承認はSTALEと区別する。
