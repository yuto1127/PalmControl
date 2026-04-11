# 安全に使う・困ったとき

## まず守る運用

- 初回確認は **OS操作OFF** で行い、プレビューとステータスが期待通りかを見ます
- 誤操作が起きたらすぐ **OS操作OFF** に戻します

## macOSで起きやすいこと

- **権限不足**: クリック/ドラッグが不安定、または一部アプリだけ反応しない
- **対象アプリの仕様**: セキュリティの高いアプリは合成イベントを制限することがあります

## 切り分けのコツ

- **移動はできるがクリック/ドラッグが弱い**: 接触閾値（`control.click_threshold`）や予兆閾値（`control.cursor_anchoring.pre_contact_threshold`）、長押し（`drag_hold_ms`）を調整
- **ドラッグが途中で切れる**: `drag_contact_grace_ms` を増やす、`drag_contact_release_frames` を増やす
- **タップ列が意図とズレる**: `tap_interval_ms` を増やす（タップの「まとまり」を広げる）
