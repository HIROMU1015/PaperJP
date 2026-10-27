# 訳語候補・読解メモ・段落指定質問

既存のblock_id・manifest・英日対訳を使う追加機能。翻訳本文と三種類の付帯情報を別々に管理する。

| 保存先 | 内容 | 正式翻訳への影響 |
| --- | --- | --- |
| terminology_candidates.json | 訳語候補、意味、出現箇所、GPT確認状態 | 未解決のmajor/criticalは正式版を停止 |
| reading_notes.json | 原文明記の説明・論理的補足・AIの推測を区別したメモ | 網羅性検証や翻訳承認には混ぜない |
| questions.json / questions/*.json | 明示的に保存した質問と取得時点の対訳文脈 | 本文を変更しない。更新後はstale表示 |
| paper_ja.md | 原文に忠実な正式翻訳 | 従来の検証・Claude承認が必要 |
| paper_ja_reading.md | 同じ本文、表示ID、移動リンク、AI読解メモ | 正式本文が有効な場合だけ生成 |

以降はpaper-translatorフォルダーで実行する。WindowsのPythonは .venv/Scripts/python.exe。
既存の作業例を使う場合、PowerShellで次を設定する。

    $paperWork = 'workspace\quant-ph_0005006v1'
    .\.venv\Scripts\python.exe -m paper_translator annotations-init $paperWork

prepareでも未作成の空のJSONを初期化する。既存データは上書き初期化しない。

## 訳語候補を記録する

Codexの翻訳応答に、任意のterminology_candidates配列を追加できる。
各要素は下記candidatesと同じ形式で、block_idsはそのチャンク内だけを指定する。
英語・日本語の文脈と内容ハッシュは、プレースホルダー復元後にPythonが記録する。
既存訳に追加する場合は、次のようなterms-input.jsonを用意する。

    {
      "schema_version": 1,
      "paper_id": "quant-ph/0005006v1",
      "candidates": [{
        "en": "uncopying",
        "sense": "pb-definition",
        "ja": "複製の取り消し",
        "alternatives": ["複製解除"],
        "reason": "通常の情報消去と区別し、本文全体で用語を統一するため",
        "severity": "minor",
        "block_ids": ["sec00-p005"]
      }]
    }

    .\.venv\Scripts\python.exe -m paper_translator term-add $paperWork terms-input.json
    .\.venv\Scripts\python.exe -m paper_translator term-export $paperWork

enとsenseをUnicode正規化・大小文字/空白を正規化して同一候補へ集約する。
同じ英単語でも意味が違えばsenseを分ける。各出現箇所はblock_idで重複を抑制する。
同じ意味でも日本語表現が違う場合は、その出現箇所のjaで別々に登録できる。
登録対象の英語/日本語が現在の対訳に含まれない場合は拒否する。

候補はid、en、sense、ja（代表訳）、alternatives、reasons、severity、status、
occurrences、reviewを保持する。occurrencesにはblock_id、ja、英日全文脈、
原文位置、source_sha256、target_sha256が入る。
科学的意味を左右する疑義はmajor/criticalにする。単なる検討候補や用語統一はminorにできる。

## 人間がGPTへ渡して確認する

term-exportが表示するterminology/terms-HASH.jsonを人間がGPTへ渡す。
現在の文脈を含め、pending・deferred・revision_requested・staleだけを出力する。
このコマンドからGPTへの自動送信や新規API契約は行わない。

GPTには次の形式で結果を返すよう依頼する。

    {
      "schema_version": 1,
      "paper_id": "quant-ph/0005006v1",
      "reviewer": "GPT",
      "packet_id": "エクスポートからコピー",
      "results": [{
        "candidate_id": "候補idをコピー",
        "candidate_sha256": "候補ハッシュをコピー",
        "decision": "approve",
        "rationale": "文脈と科学的意味に基づく判断理由"
      }]
    }

decisionはapprove（承認）/correct（修正）/defer（保留）。
correctの場合はsuggestionsを追加し、全出現箇所をblock_idから推奨訳への辞書で指定する。
例: "suggestions": {"sec00-p005": "複製の解除", "sec00-p006": "複製の解除"}。
resultsの一部だけを取り込むこともでき、未回答候補は未確認のまま残る。
空の形式例やテストの模擬結果を実際のGPT確認として使わない。

    .\.venv\Scripts\python.exe -m paper_translator term-import $paperWork gpt-result.json

パケット/候補/現在の対訳ハッシュが一致しなければ、古い結果として拒否する。
承認・保留は訳を変更しない。修正結果も修正予定として保存し、まだ本文には適用しない。
保存したstatusは操作時点の状態で、現在の対訳との整合性はannotations-check/statusで再計算する。
手動取り込みは内容の対応を検証する仕組みで、GPTの署名認証ではない。

## Codexが各出現箇所を修正する

Codexはexplainで各出現箇所を読み、意味を確認して段落全文と修正理由を用意する。
全文に対する一括文字列置換は使わない。

    {
      "schema_version": 1,
      "paper_id": "quant-ph/0005006v1",
      "candidate_id": "対象候補id",
      "occurrences": [{
        "block_id": "sec00-p005",
        "text": "数式・参照を保持した修正後の段落全文",
        "rationale": "この出現箇所の意味を確認した理由",
        "source_sha256": "explainが示す現在の原文ハッシュ",
        "target_sha256": "修正前の訳のハッシュ"
      }]
    }

occurrencesにはその候補の全出現箇所を一度ずつ含める。

    .\.venv\Scripts\python.exe -m paper_translator term-apply $paperWork correction.json
    .\.venv\Scripts\python.exe -m paper_translator validate-translation $paperWork
    .\.venv\Scripts\python.exe -m paper_translator review-prepare $paperWork
    .\.venv\Scripts\python.exe -m paper_translator review-run $paperWork
    .\.venv\Scripts\python.exe -m paper_translator merge $paperWork

適用前に全段落の機械検証を行い、失敗した修正はファイルへ書かない。
訳の変更で既存Claude承認と、その前後文脈に依存する承認が失効する。
用語集の翻訳方針も変える必要があればCodexがglossary.jsonを更新し、prepareを再実行して
失効した全段落をレビューする。GPTの用語確認はClaudeの翻訳レビューを代替しない。
重大な未解決候補や保留・古い確認が残れば、正式版の生成は禁止される。

## 読解メモを別管理する

CodexまたはClaude Codeに、重要な数箇所だけを対象にnotes-inputのJSONを作らせる。
対象段落のhashと引用はexplainの結果から取得する。形式例:

    {
      "schema_version": 1,
      "paper_id": "対象paper_id",
      "notes": [{
        "block_id": "sec00-p005",
        "kind": "explanation",
        "claim_type": "explicit",
        "content": "原文の定義を読むためのメモ",
        "certainty": "high",
        "agent": "Codex",
        "source_sha256": "現在の原文ハッシュ",
        "target_sha256": "現在の訳のハッシュ",
        "evidence": [{
          "block_id": "sec00-p005",
          "quote": "英語原文からそのまま引用した文字列",
          "source_sha256": "その根拠段落の原文ハッシュ"
        }]
      }]
    }

- claim_type: explicit（原文に明記）、derived（原文から論理的に導いた補足）、
  speculative（AIの推測・仮説・疑問）。推測を原文の主張として記載しない。
- kind: logic / assumption / derivation / notation / relation / question / explanation。
- certainty: high / medium / low。agent: Codex / Claude Code。
- evidenceの引用は指定した現在の原文に実在しなければ拒否する。位置はPythonが補完。
- 同じ入力の再取り込みは冪等。メモを改訂するときは既存idを指定する。
  保存数の固定上限は設けないが、Skillは必要な重要箇所に限定するよう指示する。

    .\.venv\Scripts\python.exe -m paper_translator notes-import $paperWork notes-input.json
    .\.venv\Scripts\python.exe -m paper_translator reading $paperWork

閲覧版では必ず「AI読解メモ｜原文外」と分類・確実性・作成者・根拠を表示する。
変更前のメモはstaleとして「要再確認」と表示する。存在しないIDへのメモは閲覧版生成を止める。
メモ追加・変更だけではpaper_ja.mdのバイト列や翻訳網羅性、Claude承認は変えない。
正式本文が無効なときは、merge --draft後にreading --draftで別名の診断版を作れる。
メモが壊れていて閲覧版の生成が失敗しても、忠実本文の検証結果とは分けてレポートする。

## 段落IDでCodexへ質問する

paper_ja_reading.mdを標準Markdownプレビューで開く。
各段落の〔sec00-p005〕等をコピーして、Codexへ次のように質問する。

    $paper-explain sec00-p005のuncopyingの定義を説明して

CLIで取得内容を確認することもできる。

    .\.venv\Scripts\python.exe -m paper_translator explain $paperWork sec00-p005
    .\.venv\Scripts\python.exe -m paper_translator context-search $paperWork "ancilla"
    .\.venv\Scripts\python.exe -m paper_translator explain $paperWork sec00-p005 --related sec00-p006 --radius 2

原文・訳・前後段落・近くの式/図表・リンク先の参考文献/数式・章立てを返す。
別節も--relatedで指定可能。インライン数式IDを指定すると所属段落と原式を返す。
必要な関連箇所をすべて自動推測するものではなく、Skillが検索/参照を追加する。
explain/context-searchは読み取り専用で、本文にも質問履歴にも書き込まない。
質問を保存したい場合だけ、次を使う。

    .\.venv\Scripts\python.exe -m paper_translator question-prepare $paperWork sec00-p005 "この定義の意味は？"

質問と文脈パケットを専用保存先へ記録する。回答はチャットで行い、本文へ自動挿入しない。

## スキーマ・整合性検証

    .\.venv\Scripts\python.exe -m paper_translator annotations-check $paperWork
    .\.venv\Scripts\python.exe -m paper_translator schema-export schemas

schemas/にDraft 2020-12のJSON Schemaを出力する。入力・保存データ・GPT用パケット、
質問文脈・閲覧版のマニフェストを区別したスキーマがある。
定義元はpaper_translator/annotation_schema.py。jsonschemaで実データを検査する。
原文・訳・根拠・用語確認・質問パケットの不一致を状態として報告する。
メモや質問のstaleは警告であり、忠実な翻訳の不合格と混同しない。
