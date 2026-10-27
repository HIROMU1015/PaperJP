# PaperJP

英語の学術論文を、原文との対応・数式・引用を保持した日本語Markdownへ変換するための
Codex / Claude Code用SkillとPython処理基盤です。

- Codex: 論文取得、構造抽出、分割、翻訳、修正、統合
- Claude Code: 原文と日本語訳の独立レビュー
- Python: ハッシュ管理、数式保護、機械検証、承認状態、ファイル操作

実装は [paper-translator/](paper-translator/) にあります。
VS Codeではこのサブフォルダーを開くと、プロジェクト内のSkillを利用できます。

## 開始手順

PowerShell、Python 3.11以上:

```powershell
cd paper-translator
python -m venv .venv
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m pytest
```

Codexへの依頼例:

```text
$paper-translate https://arxiv.org/abs/quant-ph/0005006v1 を日本語Markdownにしてください。
```

取得・翻訳・Claude Codeレビュー・統合の詳細は
[操作手順](paper-translator/README.md)を参照してください。

## 読解支援

訳語候補をGPTへ確認するためのJSON出力と結果取り込み、本文とは別のAI読解メモ、
段落ID付き閲覧版、段落指定質問用の paper-explain Skill を提供します。

```text
$paper-explain sec00-p005のuncopyingの定義を説明して
```

[読解支援の操作手順とデータ形式](paper-translator/docs/ANNOTATIONS.md)。

`papers --root workspace`で複数論文の状態を一覧できます。GPTの訳語レビュー用に、
term-exportの出力をプレビューしてから`gpt-review/<paper_id>/requests/`へ明示的にコピーし、
responsesの結果を取り込めます。[共有手順](gpt-review/README.md)を参照してください。

## 検証と対応範囲

TeXベースのPhase 1について、実arXiv論文で取得から翻訳・独立レビュー・正式統合まで確認しています。
追加機能を含むローカルテストは103件です。GitHub ActionsのPythonテスト定義も含みます。
Claude Codeレビューの既定値はOpus 5.5・推論レベルxhighです。
HTML/PDFは診断用抽出までで、式・図表を保持した本格的な変換と高度な並列化は未実装です。

- [Phase 1の検証記録](paper-translator/docs/VALIDATION.md)
- [読解支援の検証記録](paper-translator/docs/ANNOTATIONS_VALIDATION.md)
- [保存データの契約](paper-translator/docs/FORMATS.md)
- [外部レビュー指摘の再現・修正と検証範囲](paper-translator/docs/EXTERNAL_REVIEW.md)

仮想環境、取得した論文原本・翻訳などの workspace/、一時データ、デバッグログはGit管理の対象外です。
テスト用のオリジナル論文データと再現用スクリプトは含まれています。
