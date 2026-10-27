# 実装計画と設計判断

2026-10-27。作業開始時のPaperJPは空。Python 3.12.10、Claude Code 2.1.310（既存claude.ai認証）を確認。

1. Phase 1: バージョン固定arXiv取得、原本とSHA-256保存、安全な展開。
2. pylatexenc 2.10のASTでTeXを解析。未知の実行命令は実行しない。複数ファイルの位置情報を保持。
3. JSONLブロックを正本とし、IDコメント付きMarkdown、翻訳パケット、用語集候補を生成。
4. 原本から別経路で要素・語句を数え、Markdownを検証。公開PDFも独立テキスト照合に使用。
5. Codexがパケットから翻訳。数式、参照、画像パス、数値を保護しPythonで復元・検証。
6. Claude Codeに原文・訳・用語集・文脈を渡し、ID別の判定JSONを取得。変更時は承認失効。
7. 全ブロックの有効なPASSと機械検証成功を条件に正式版を生成。ドラフトは別名で保存。
8. オフライン単体・統合テスト、実arXiv論文の取得から統合、VS Codeプレビュー確認。

依存関係: requests（HTTP）、pylatexenc（非実行TeX AST）、BeautifulSoup（版情報/HTML）、
PyMuPDF（独立PDFテキスト照合とPDF画像のPNG変換）、Pillow（画像検証）。
PDFの本格的な構造抽出はDoclingを任意依存にする。MinerUも比較対象だが、
Phase 1にモデルダウンロードやGPU環境を必須にしない。PDF由来の式・図表の完全性を
軽量テキスト抽出だけで合格扱いにしない。

Phase 2/3はPhase 1完了後に実装・検証できた部分だけ報告する。
再実行は入力/出力ハッシュが一致する工程を再利用し、変更時の旧出力はhistoryへ保存。
正式版の有効性もstatusで再計算する。原本は上書きしない。

調査した公式資料（2026-10-27）:
- https://developers.openai.com/codex/skills
- https://code.claude.com/docs/en/skills
- https://pylatexenc.readthedocs.io/en/v2.10/latexwalker/
- https://info.arxiv.org/help/api/user-manual.html
- https://info.arxiv.org/help/versions.html
- https://docling-project.github.io/docling/
- https://github.com/opendatalab/MinerU
- https://code.visualstudio.com/docs/languages/markdown
