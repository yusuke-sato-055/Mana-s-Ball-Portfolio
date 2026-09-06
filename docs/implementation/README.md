# 直接実装用の仕様書運用

ブラウザ版GPTが作成した個別仕様書を、Codexの`direct-implementation` Skillへ渡すためのフォルダです。仕様書をCodex用の指示書へ変換し直す必要はありません。Skillが仕様書全体を従来の「13．今回の仕様本文」として扱い、プロジェクト調査、直接実装、静的確認、テスト、ゲーム起動、ログ確認、エラー修正、最終レビュー、完了報告まで進めます。

## 通常使用

1. `docs/implementation/current.md`へ、GPTが作成した仕様書全体をそのまま貼り付けます。
2. Codexへ次の1行を送ります。

```text
$direct-implementation
```

これは次の明示的な指定と同じ扱いです。

```text
$direct-implementation を使用し、docs/implementation/current.mdを今回の仕様本文として直接実装してください。
```

## チャットへ直接貼り付ける場合

次の実行文の後ろへ、GPTが作成した仕様書全体をそのまま貼り付けます。

```text
$direct-implementation を使用し、以下を今回の仕様本文として現在のプロジェクトへ直接実装してください。

ここにGPTが作成した仕様書を貼り付ける。
```

チャットへ貼り付けた仕様書は、指定ファイルや`current.md`より優先されます。入力元の優先順位は、現在の依頼の個別指示、直接貼り付けた仕様書、明示した仕様ファイル、`docs/implementation/current.md`の順です。

## `current.md`の扱い

`current.md`には今回の個別仕様だけを保存します。従来の共通依頼文は記載せず、ファイル内容全体を「13．今回の仕様本文」として扱います。ファイルが存在しない、空である、または記入例だけの場合は実装を開始せず、有効な仕様がないことを報告します。

共通の調査・実装・確認手順は`.agents/skills/direct-implementation/SKILL.md`、プロジェクト固有規則は適用範囲の`AGENTS.md`、今回の個別仕様は`current.md`という役割分担です。

## GitHub Copilotでも使用する場合

GitHub Copilot向けの導入ファイル、依頼文、共通手順は`docs/implementation/copilot-direct-implementation.md`にまとめています。Copilotでは`$direct-implementation`というCodex Skill呼び出しの代わりに、次の依頼文を使用します。

```text
Direct Implementationを使用し、docs/implementation/current.md全体を今回の仕様本文として、このリポジトリへ直接実装してください。
```

新規プロジェクトの作成元となるベースフォルダへ`.agents`、`.github`、`docs/implementation`を含めておけば、そこから作成したプロジェクトでも同じ運用を開始できます。
