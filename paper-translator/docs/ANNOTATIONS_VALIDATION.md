# 追加機能の検証結果

2026-10-27（日本時間）。既存Phase 1を維持した拡張として実装した。

## 実行結果

| 対象 | 実施した検証 | 結果 |
| --- | --- | --- |
| 単体・統合テスト | 既存40件＋追加28件 | 68 passed、11.84秒 |
| 訳語候補 | 意味ごとの集約、出現箇所の重複抑制、英日文脈・ハッシュ保存 | 成功 |
| GPT結果の取り込み | approve/correct/defer、古い結果の拒否、全出現箇所ごとの修正理由と全文指定 | 模擬結果で成功 |
| 修正後の承認 | 機械検証、変更段落と前後文脈のClaude承認失効、再承認前の正式統合拒否 | テストで成功 |
| 重大疑義 | major/criticalの未解決・保留で正式版を停止、minorの候補登録だけでは停止しない | 成功 |
| 読解メモ | 3分類、根拠引用の照合、古いメモ・未知IDの検出、本文からの分離 | 成功 |
| 閲覧版 | 表示ID・アンカー・ラベル付きメモ、本文の完全な復元照合、更新検出 | 成功 |
| 段落指定質問 | 原文・訳・前後・別節の参照・数式・図表の取得、インライン数式IDからの取得 | 成功 |
| 質問記録 | 明示保存、冪等性、文脈更新・パケット改変の検出 | 成功 |
| JSON Schema | 10種類のDraft 2020-12スキーマと入力/保存データの実検査 | 成功 |
| Skill | paper-explain / paper-translate / paper-reviewの検査 | 成功 |
| インストール | jsonschemaを含むeditable installとCLI呼び出し | 成功 |

[JUnit結果](../reports/pytest-annotations.xml)を参照。ローカルで実行したテストであり、GitHub CIではない。
テストのGPT/Claude応答は状態管理の検証用で、実際の意味レビューとは区別する。
Windowsサンドボックスの一時フォルダー権限により、テストは承認された制限外実行で行った。

## 実論文で確認した内容

対象は既存のworkspace/quant-ph_0005006v1。

- uncopying（5箇所）とancilla（1箇所）を、意味を区別した2候補として記録。
- 候補はいずれもminor・pending。GPTへの手渡し用JSONを出力した。
- 原文に明記された定義の説明1件と、AIによる読解上の疑問1件を別JSONへ保存した。
- paper_ja_reading.mdを生成し、標準Markdownプレビューで両方のラベル・段落ID・数式表示を確認した。
- 冒頭のリンクをクリックし、sec00-p005の段落へ移動することを実画面で確認。
- プレビューのIDを選択し、選択文字列が「〔sec00-p005〕」であることを確認。
- paper-explainの文脈取得を実論文で実行し、対訳・前後段落・追加指定箇所を取得した。
- 元のpaper_ja.mdは、前回のPhase 1の検証記録とSHA-256が一致。
  既存のClaude承認ファイルも変更せず、正式版は19ブロックすべて有効のまま。

実行記録: [annotations-demo.json](../reports/annotations-demo.json)、
[explain-example.json](../reports/explain-example.json)。
画面証拠:
[アンカー移動と説明メモ](../reports/vscode-reading-anchor.jpg)、
[段落IDの選択](../reports/vscode-reading-id-selection.jpg)、
[推測・疑問メモの区別](../reports/vscode-reading-speculation.jpg)。

## 未実施・制約

- 実際のGPTによる訳語確認は未実施。候補JSONを人間が渡して結果を取り込む運用であり、自動送信しない。
- 実論文の翻訳本文は変更していないため、この追加作業でClaudeの意味レビューを再実行する必要は発生していない。
  用語修正後の承認失効・再承認必須化は模擬応答を使った統合テストで検証した。
- paper-explainはCodex用Skillと読み取り専用の文脈取得CLI。
  説明回答はCodexが生成し、Pythonが自動的に科学的正しさを保証するものではない。
- メモ分類の入力形式・根拠引用の存在は機械検証するが、補足の科学的妥当性を機械判定したとは扱わない。
- 関連箇所は文書内リンク・近傍・明示指定から取得する。必要な関連節を漏れなく自動推定する機能ではない。
- クリックしてCodexへ質問を自動送信するUIは実装していない。IDをコピーして質問する方式。

利用方法とスキーマは[ANNOTATIONS.md](ANNOTATIONS.md)を参照。
