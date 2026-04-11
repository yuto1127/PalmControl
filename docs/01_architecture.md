# アーキテクチャ概要

PalmControlは、リアルタイム性とGUI応答性の両立のために、処理を役割ごとに分割しています。

## 主要コンポーネント

- **GUI（PyQt6）**: 設定編集、プレビュー、ログ閲覧、マニュアル表示、常駐（トレイ）
- **VisionControlWorker（QThread）**: カメラ取得、検出、（有効時）制御の実行
- **HandDetector**: MediaPipe Tasks API による手の推定とジェスチャー解析（OS操作はしない）
- **MouseController**: 検出結果をOSのマウス操作へ変換
- **ConfigStore**: `settings.yaml` の読み書きとスナップショット提供
- **LoggingManager / JsonPrependLogger**: JSON Linesログ（研究記録）

## データの流れ（ざっくり）

1. Workerがカメラからフレームを取得する
2. `HandDetector` がフレームを解析し、`DetectionResult` を返す
3. `MouseController` が `DetectionResult` を入力として、必要ならマウス操作を発行する
4. GUIはWorkerからのシグナルでプレビューやステータスを更新する

## 設定とログ

調整パラメータは原則として `config/settings.yaml` に集約します。ログは `logging` セクションの設定に従い、JSON Linesとして保存されます。
