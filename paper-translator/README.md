# Paper Translator

追加機能: [訳語候補のGPT確認・AI読解メモ・段落IDでの質問](docs/ANNOTATIONS.md)。
[外部レビュー指摘・モデル指定の対応と検証記録](docs/EXTERNAL_REVIEW.md)。
閲覧にはpaper_ja_reading.mdを使用すると、段落IDをコピーしてCodexの
paper-explain Skillへ質問できる。原文に忠実なpaper_ja.mdは別に維持する。

arXiv論文を取得し、原文との対応を保持した日本語Markdownを作成する。
Pythonが取得・構造解析・分割・数式保護・検証・統合、Codexが翻訳、
Claude Codeが独立した対訳レビューを担当する。新しい翻訳APIキーは不要。

## セットアップ（Windows / PowerShell）

VS Codeでこの`paper-translator`フォルダーを開く。Python 3.11以上を使用する。

```powershell
python -m venv .venv
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m paper_translator --help
.\.venv\Scripts\python.exe -m pytest
```

今回の作業フォルダーにはセットアップ済みの`.venv`があるため、作り直す必要はない。
Unixでは実行ファイルを`.venv/bin/python`に読み替える。
ネットワーク制限下では取得・インストール・Claudeへの接続が失敗する場合があり、
成功した工程と失敗した工程を保存レポートで区別する。

## URLから始める

Codexで次のように依頼する。

```text
$paper-translate https://arxiv.org/abs/quant-ph/0005006v1 を日本語Markdownにしてください。
```

Codex用Skillは`.agents/skills/paper-translate/SKILL.md`、Claude Code用Skillは
`.claude/skills/paper-review/SKILL.md`。両者ともこのフォルダーから起動する。
現在のチャットが親フォルダーから始まってSkillが一覧にない場合は、Skillファイルを
明示して読ませるか、このフォルダーを開いて新しいセッションを開始する。

Pythonだけで準備する場合:

```powershell
.\.venv\Scripts\python.exe -m paper_translator acquire https://arxiv.org/abs/quant-ph/0005006v1
$paperWork = 'workspace\quant-ph_0005006v1'
.\.venv\Scripts\python.exe -m paper_translator status $paperWork
.\.venv\Scripts\python.exe -m paper_translator prepare $paperWork
```

abstract URL、PDF URL（末尾`.pdf`の有無に対応）、新旧の論文IDを受け付ける。
版指定がなければabstractページから版を確定して固定する。
TeX → HTML → PDFの順に試し、版付きURLとリダイレクト先を照合する。
TeX取得時も同じ版の公開PDFを保存し、独立テキスト照合に利用する。
すべてのソースが同じ版のURLに由来することと、その本文が完全に一致することは別であり、
本文の不一致は抽出検証で検出・報告する。完全な版一致を文字数だけで保証しない。

工程を個別に再実行する場合:

```powershell
.\.venv\Scripts\python.exe -m paper_translator fetch quant-ph/0005006v1
.\.venv\Scripts\python.exe -m paper_translator extract $paperWork
.\.venv\Scripts\python.exe -m paper_translator split $paperWork
.\.venv\Scripts\python.exe -m paper_translator validate-extraction $paperWork
```

main TeXが複数ある場合は`extract $paperWork --main main.tex`で指定。
原本ハッシュが変わった場合は停止し、上書き取得しない。
取得したTeXをコンパイルしたり、BibTeXやシェルコードを実行したりしない。

## Codexで翻訳する

抽出PASS後、Codexに`glossary.json`の専門用語候補を論文全体から補完させ、
`prepare`を再実行する。用語集を更新すると古いレビューは無効になる。
各`packets/*.json`には保護済み本文、数式等の対応表、前後文脈が入る。
Codexは`translate=true`の`protected_text`を日本語化し、`translate=false`は完全コピーする。
数式内の`\text{...}`を含む数式全体・書誌情報は原文のまま。
本文・見出し・脚注・図表キャプション・表内の自然言語は翻訳対象。

応答JSONを例えば`translation-response.json`へ保存する。
具体的な契約は[FORMATS.md](docs/FORMATS.md)を参照。

```json
{"paper_id":"パケットからコピー","packet_sha256":"パケットからコピー","chunk":"001-sec00.md","blocks":[{"id":"sec00-p001","text":"日本語訳"}]}
```

この例は形だけを示す。実際には対象チャンクの全IDを順序通りに含める。

```powershell
.\.venv\Scripts\python.exe -m paper_translator import-translation $paperWork translation-response.json
.\.venv\Scripts\python.exe -m paper_translator validate-translation $paperWork
```

全チャンクが揃う前は「残りの翻訳がない」としてFAILになる。これは部分保存の失敗とは異なる。
同じ英語と日本語の文字数を要求しない。日本語が含まれるだけで意味的に合格とはしない。

## Claude Codeで独立レビューする

既存のClaude Codeのログインを使用する。先にClaude Codeでログインを完了しておく。
Pythonから呼ぶ場合（PATHまたはVS Code拡張内の既存CLIを検出）:

```powershell
.\.venv\Scripts\python.exe -m paper_translator review-prepare $paperWork
.\.venv\Scripts\python.exe -m paper_translator review-run $paperWork
```

review-runは既定で **Opus 5.5（`claude-opus-5-5`）・推論レベル`xhigh`** を明示指定する。
このプロジェクトのレビュー呼び出しだけに適用し、グローバルなClaude Code設定は変更しない。
別の設定を使う場合は`--model sonnet --effort high`などで上書きする。
各依頼の指定値はworkspace内の`reviews/claude-request-<packet_id>.json`に記録する。
指定値の記録と実際にサーバーで使われたモデルの検証は別であり、生のClaude応答も従来通り保存する。
モデルIDと推論レベルは[Claude Code公式仕様](https://code.claude.com/docs/en/model-config)に従う。

review-prepareは`packets`配列を返す。既定では各依頼を40ブロック・JSON 60000文字以内にし、
隣接段落・用語集・節構成・過去の指摘もその文字数に含める。`--max-blocks 10 --max-chars 30000`
などで両コマンドの上限を調整できる。単一ブロックと文脈が収まらない場合は停止する。
文字数はAPIトークン数の保証ではない。review-runは順に処理し、中断後の通常の再実行は
現時点でPASSでないブロックだけを依頼する。`--all`は承認済みブロックも再レビューする。
明示的に1件だけ実行する場合は`--packet <path>`を指定する。

レビューの内容ハッシュに加え、依頼作成時の承認状態も照合する。新しい指摘が届いたら
新たにreview-prepareする。古いPASSを再取り込みしてもNEEDS_REVISION/INCONCLUSIVEを消せない。

CLIの場所を指定する場合は`review-run $paperWork --claude-exe 'C:\path\claude.exe'`。
この呼び出しは英語原文・日本語訳・用語集・文脈を既存認証でClaudeへ渡す。
ReadとSkillだけを許可し、原文や訳をClaudeに編集させない。結果・生応答・標準エラーを保存する。

VS CodeのClaude Codeから手動で行う場合:

```text
/paper-review C:/absolute/path/to/workspace/paper_id/reviews/packet-HASH.json
```

表示された結果をUTF-8の`claude-review.json`として保存し、次を実行する。

```powershell
.\.venv\Scripts\python.exe -m paper_translator import-review $paperWork claude-review.json
```

NEEDS_REVISIONの段落はCodexで翻訳応答を修正して再取り込みし、機械検証、
`review-prepare`、Claudeレビューを繰り返す。変更段落と、その段落を前後文脈として
参照した段落の承認を失効させる。新しいパケットはその範囲を含む。
全体へ影響する用語変更は用語集も更新し、全体を再レビューする。
INCONCLUSIVEをPASSとして扱わない。模擬レビューを実レビューとして使用しない。

## 統合と表示確認

```powershell
.\.venv\Scripts\python.exe -m paper_translator merge $paperWork
.\.venv\Scripts\python.exe -m paper_translator status $paperWork
```

`paper_ja.md`は抽出PASS・翻訳機械検証PASS・全ブロックの現行レビューPASSが必要。
統合後もID・内容・順序・画像参照を再検証する。
`status`の`official: VALID`を確認してから使用する。変更後の旧完成版は履歴として
残ることがあるが、`STALE_OR_INVALID`なら現在の正式版として扱わない。
調査用には次を使用できる。

```powershell
.\.venv\Scripts\python.exe -m paper_translator merge $paperWork --draft
```

出力は警告コメント付き`paper_ja.draft.md`であり、正式版とは別名。
VS CodeでMarkdownソースを開き、コマンドパレットから
`Markdown: プレビューを開く`（`Markdown: Open Preview`）を選ぶ。
拡張機能がショートカットを上書きしていなければ`Ctrl+Shift+V`でも開ける。
`MPE:`で始まるコマンドは別のプレビューなので標準の表示確認には使用しない。
数式は`$...$`/`$$...$$`、単純な表はMarkdown表、画像は相対パスで参照する。
参考文献/図/数式の参照はアンカーを保持する。脚注は標準プレビューでも読めるように
HTMLの上付きリンクと本文アンカーで表現する。ページをまたぐ文脈は保存IDから追跡できる。

## 対応範囲と検証の限界

- Phase 1: 標準的なLaTeX、input/include、既存bbl、節、本文、inline/display数式、
  PNG/JPEG/PDF図、単純なtabular、引用、脚注、Appendix、参考文献、基本的な再開。
- 未対応の独自マクロや環境、複雑な表、EPS/PS/SVG図は黙って捨てずにエラーとする。
  任意のTeXを完全に処理するエンジンではない。既存bblがないBibTeXも自動実行しない。
- HTML/PDFへの取得フォールバックと診断用テキスト抽出は実装しているが、
  式・図表を保持した本格的変換（Phase 2）は未実装。診断成果物から正式版は生成できない。
  Doclingを任意依存候補としたが、本環境でモデル取得・処理まで検証していない。
- Phase 3: 高度な部分再抽出・並列実行・翻訳の無人起動は未実装。
  翻訳はCodexのSkillで行い、PythonがLLMの代わりに機械翻訳したとは扱わない。
- 正規化文字数95%以上に加え、原本の独立した要素数・語句・段落・公開PDFとの照合を行う。
  これらは欠落の検出支援であり、任意論文の完全性や意味の正確さの数学的証明ではない。
  未翻訳段落の検出もヒューリスティックであり、Claudeの意味レビューを併用する。

再実行時に同一内容は書き換えず、変更済み生成物は`history/`に退避する。
原本は変更しない。[実装計画と採用資料](docs/PLAN.md)、[データ契約](docs/FORMATS.md)を参照。
検証結果と実論文の到達範囲は[VALIDATION.md](docs/VALIDATION.md)に記録する。

公式仕様の確認先:
[Codex Skills](https://developers.openai.com/codex/skills)、
[Claude Code Skills](https://code.claude.com/docs/en/skills)、
[pylatexenc](https://pylatexenc.readthedocs.io/en/v2.10/latexwalker/)、
[arXiv versions](https://info.arxiv.org/help/versions.html)、
[VS Code Markdown](https://code.visualstudio.com/docs/languages/markdown)。
