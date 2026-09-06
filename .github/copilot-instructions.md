# GitHub Copilot project instructions

このリポジトリで機能追加、不具合修正、仕様反映、直接実装を依頼された場合は、最初に適用範囲の`AGENTS.md`、`AGENTS.override.md`、READMEを確認し、プロジェクト固有規則に従ってください。

ユーザーが「Direct Implementationを使用」「直接実装」「仕様本文として実装」などと指定した場合は、`docs/implementation/copilot-direct-implementation.md`の手順を適用してください。チャットへ仕様書が貼り付けられていなければ、`docs/implementation/current.md`全体を今回の個別仕様として使用してください。

仕様書を別の実装指示書へ変換して回答するだけで終了せず、停止条件に該当しない限り、関連箇所の調査、最小差分の実装、静的確認、テスト、起動・ログ確認、エラー修正、差分レビュー、完了報告まで進めてください。
