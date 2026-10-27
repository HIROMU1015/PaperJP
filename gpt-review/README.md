# GPT訳語レビューの受け渡し

ここには明示的に共有した訳語レビューJSONだけを置きます。論文本体・作業用workspace・内部ログはコピーしません。

```text
gpt-review/<paper_id（/は_に変換）>/requests/terms-HASH.json
gpt-review/<paper_id（/は_に変換）>/responses/<任意の名前>.json
```

`paper-translator/`で`term-export`を実行してから、`term-share <work> <packet.json>`で共有予定の全内容を表示します。候補には出現箇所の英語原文・日本語訳の段落全文が含まれます。公開してよい内容か確認した後、同じコマンドに`--write`を付けるとrequestsへ1ファイルだけコピーします。コピーだけではコミット・プッシュ・GPT送信は行いません。

GPTの返答を該当論文のresponsesへ保存し、`term-share-import <work> <response.json>`で既存の`term-import`処理に渡します。論文ID、共有依頼とworkspaceの依頼の一致、候補と現在の原文・訳文のハッシュを検証します。修正提案は本文へ自動反映しません。

詳しいコマンド・結果JSONの形式は[ANNOTATIONS.md](../paper-translator/docs/ANNOTATIONS.md)を参照してください。
