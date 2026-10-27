# 外部レビュー指摘への対応・検証（2026-10-27）

基本のmanifest / block_id / 翻訳・Claudeレビュー・統合の流れを維持し、7項目を修正・追加した。
後続の指定により、レビュー実行の既定モデル・推論レベルの明示指定も追加した。
ローカルのPython 3.12 / Windowsで**105テスト成功（既存68件＋外部レビュー対応32件＋モデル指定3件＋改行形式2件）**。
AI応答を使う回帰テストは模擬JSONによるプロトコル検証であり、実際の意味的承認ではない。

## 1〜3の修正前の再現

初期コミット`c840107af5c44dbd2ce8711f2abf909acb3863f1`のソースを隔離ディレクトリへコピーし、
現在の作業データとは別のオリジナルfixtureで実行した。4ケースとも期待した拒否・翻訳可能性の
アサーションに失敗し、追加の観測でも実際の状態を確認した。

| 指摘 | 修正前の実行結果 | 修正後 |
| --- | --- | --- |
| 1: 古いPASSの再取り込み | 後発のNEEDS_REVISION / INCONCLUSIVEを登録した後でも、古いPASSで全20ブロックがPASSへ戻り、正式統合がVALIDになった | 依頼時の承認状態と一致しない古い結果を拒否。未解決指摘を保持し正式統合も拒否 |
| 2: 用語集不一致 | パケット作成後にglossary.jsonを変更しても、取り込み結果がPASSになった | 保存前にglossary_sha256を現在のファイルと照合し拒否 |
| 3: リンク内の自然言語 | `[Experimental results](https://example.org/paper#results)`全体が1トークンとなり、翻訳対象に表示文言が残らなかった | URL・アンカー・区切りを保護し、表示文字列を翻訳可能にした |

修正前の[失敗テスト記録](../reports/external-review-reproduction.xml)と
[実際の状態の観測JSON](../reports/external-review-observed.json)を保存した。
これらは修正前の証拠であり、現行実装の失敗を表すものではない。

## 実装した変更

1. **レビューの順序整合性**：各ブロックの依頼に`base_review_sha256`と過去の判定・指摘を含める。
   現在の承認レコードが変わっていれば、応答全体を保存前に拒否する。指摘到着前に依頼した
   未取り込みのPASSでも上書きできない。現在の結果を再取り込みする場合は時刻も含め無変更。
   未解決指摘を解消するには、その指摘を含む新しい依頼でClaude Codeの再レビューを行う。
   旧承認のJSONは移行・再保存せず、保存された依頼のpaper_idで帰属を確認する。
2. **翻訳の鮮度**：用語集ハッシュの照合に加え、新規パケット・応答を`paper_id`と
   `packet_sha256`で結びつける。prepare後に旧応答だけを取り込む操作も拒否する。
   旧形式パケットでも用語集照合を行う。既存の正式訳や承認に書き換えは不要。
3. **リンクの自然言語**：インラインリンク・画像の表示文字列／altを翻訳可能にする。
   その中の数式・数値、移動先、表示URLは保持する。hrefだけのTeXを検証する際に見つかった
   独立照合側の引数解析も修正し、表示文言を比較する。記録済みの翻訳例は旧リンク単位から
   現在のトークンへ対応させる補助処理で維持する。
4. **レビュー分割**：review-prepareは複数の`packets`を返す。review-runはそれらを順番に処理する。
   既定は40ブロック・JSON 60000文字。用語集、節構成、隣接文脈、過去の指摘を含む上限で、
   `--max-blocks` / `--max-chars`で調整できる。単一ブロックと文脈が収まらない場合は停止する。
   文字数はモデルの正確なトークン数ではない。中断後は完了済みの承認を維持し、未承認・
   staleのブロックだけを再依頼する。明示指定した古い依頼もAI呼び出し前に拒否する。
5. **CI定義**：[python-tests.yml](../../.github/workflows/python-tests.yml)にpush / pull_request /
   workflow_dispatchを追加。Ubuntu・Windows × Python 3.11・3.12でpytestを実行する。
   依存インストール後のテストでは実ソケット接続・外部プロセス起動を禁止し、取得・Claudeの
   応答は明示的なモックに限定する。AI CLIの導入、認証情報、実AIリクエストは含めない。
   Actionの利用形式は公式の[checkout](https://github.com/actions/checkout)と
   [setup-python](https://github.com/actions/setup-python)を確認した。
6. **複数論文**：`papers --root workspace`でpaper_id、作業パス、工程、承認件数、正式版、
   訳語状態を一覧する。レポートも含め読み取りだけで、破損したmanifestは項目別エラーとし、
   他の論文を継続する。重複paper_idもエラー表示する。新旧レビュー、訳語、メモ、質問は
   各論文のmanifestと照合する。
7. **GPT受け渡し**：term-exportの出力を`term-share`で全内容プレビューし、`--write`時だけ
   `gpt-review/<paper_id>/requests/`へ依頼JSON1件をコピーする。原文・訳文の段落全文を含むため、
   公開可否はその表示で確認できる。同論文のresponsesから`term-share-import`で読み込み、
   共有依頼とworkspaceの依頼を照合してから従来のterm-importへ渡す。コピー操作はGitの
   コミット・プッシュやGPT送信を行わない。実論文の共有依頼は今回Git対象へ出力していない。

コマンド例・データ契約は[README](../README.md)、[FORMATS](FORMATS.md)、
[ANNOTATIONS](ANNOTATIONS.md)、[共有フォルダー](../../gpt-review/README.md)を参照。

後続の指定により、review-runの既定値を`--model claude-opus-5-5 --effort xhigh`とした。
両オプションで上書きでき、すべての分割依頼へ同じ値を渡す。指定値はworkspace内に保存し、
グローバル設定と既存の承認は変更しない。既定・上書きの受け渡しと不正effortの拒否を
模擬Claudeプロセスで検証した。実サーバーでのOpus 5.5 / xhigh実行確認とは区別する。

## 実行した検証

- 全105件の[pytest結果](../reports/external-review-regression.xml)。既存68件のアサーションは維持し、
  模擬翻訳の応答ヘルパーだけ新規パケットの識別情報へ対応した。
- 古いPASS、遅れて到着するPASS、混在する古いブロックを含む応答全体の原子的拒否、
  新しい依頼による解消、同じ現在の結果の再取り込みの無変更。
- glossary変更の直後とprepareし直した後の両方で、古い翻訳応答を拒否。
- リンク表示、画像alt、アンカー、括弧を含む移動先、リンク内数式、表示URL、
  リンクだけの未翻訳検出、記録済みプレビュー訳の新旧トークン互換性。
- 複数のサイズ上限で分割、全IDの順序・網羅性・前後文脈、単一ブロックの上限超過、
  模擬CLI障害後の再開、変更段落と隣接段落だけの再レビュー。
- 同じ原文・同じblock_idを持つ異なる2論文で、片方の操作が他方の全ファイルを変更しないこと。
  誤った論文のレビュー、旧承認、訳語、読解コメント、GPT結果の取り込みを拒否すること。
- papers / review-prepare / term-share / term-share-importのCLI実行。共有プレビュー時の無書き込み、
  JSONだけの出力、古い文脈・改変依頼・誤配置結果・余分なデータの拒否。
- 変更した2つのSkillのquick_validate成功、CIのYAML解析成功、git diff --check成功。

## 既存Phase 1成果物の保全

変更前の[ハッシュ記録](../reports/external-review-baseline.json)と変更後の
[比較結果](../reports/external-review-preservation.json)を照合した。

| 作業データ | ブロック承認 | 正式版 | 本文・承認等の実バイト |
| --- | --- | --- | --- |
| quant-ph_0005006v1（実論文） | 19 PASSを維持 | VALIDを維持 | 全7ファイルのSHA-256一致 |
| preview-fixture（オリジナル試験用） | 20 PASSを維持 | VALIDを維持 | 全6ファイルのSHA-256一致 |

対象はpaper_ja.md、finalization.json、reviews/approvals.json、blocks.jsonl、glossary.json、
全ja_draftチャンク。承認日時を含むファイル自体が一致し、不必要な再承認・翻訳の変更はない。

## GitHub Actionsと未実施の検証

初回の[GitHub Actions実走](https://github.com/HIROMU1015/PaperJP/actions/runs/40447725913)では
UbuntuのPython 3.11 / 3.12が成功し、Windows両構成はCRLFの中間TeX照合で失敗した。
read_textの自動改行変換を避けて保存内容を文字通り比較する修正と、LF / CRLFの回帰テストを追加した。
原本ハッシュや原文位置は維持し、改行を理由に検証基準を緩めていない。

長大な実論文を用いた分割Claude Codeの実通信、GPTの実確認は今回未実施。
新しいMarkdown表示のUI検証も行っていない。
分割・中断再開・共有往復はローカルで実行したが、AI部分は模擬応答による検証である。
過去のPhase 1におけるClaude Codeの実承認を、今回の模擬応答で置き換えていない。
