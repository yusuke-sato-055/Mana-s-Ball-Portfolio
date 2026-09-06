# クエスト選択・敵・戦闘パターン 移植用 Codex 実装仕様書

## 0. 目的と適用範囲

本書は、`編成画面_移植用Codex実装仕様書.md` の前段となるクエスト選択画面と、クエストを増やすための敵マスター・敵グループ・戦闘パターンを別ゲームへ移植する仕様である。

Codexへ実装させる際は、原則として次の2ファイルを同時に渡すこと。

- `docs/クエスト選択_敵戦闘パターン_移植用Codex実装仕様書.md`
- `docs/編成画面_移植用Codex実装仕様書.md`

クエストや敵を作成・編集する管理画面、および戦闘ログ・エラーログの管理も実装する場合は、`docs/クエスト敵ログ管理_移植用Codex実装仕様書.md` も参照すること。

必須範囲:

1. ホームからクエスト選択画面を開く。
2. 有効なクエストを一覧表示する。
3. 選択したクエストの詳細、敵、人数、勝利条件を表示する。
4. 決定時に `quest_id` を編成画面へ渡す。
5. 敵を個体マスターとグループに分けて定義できる。
6. 同じ敵でも組み合わせ、AI、人数、初期保持者、ルールを変えて別の戦闘を作れる。

---

## 1. Mana's Ball の参照先

| 目的 | 参照先 |
|---|---|
| クエスト一覧のロード | `manaball/ui.py` の `open_quest_select()` |
| クエスト選択画面の描画 | `manaball/ui.py` の `draw_quest_select()` |
| クエスト選択入力 | `manaball/ui.py` の `handle_pointer()` とクエスト用ボタン処理 |
| クエスト・試合条件ロード | `manaball/battle_setup.py` |
| 敵マスターと敵グループ生成 | `manaball/data.py` |
| クエストデータ | `data/csv/quests.csv` |
| 試合条件 | `data/csv/match_rules.csv` |
| 敵マスター | `data/csv/enemies.csv` |
| 敵グループ | `data/csv/enemy_groups.csv` |
| AIプロフィール | `data/csv/ai_profiles.csv` |
| スキル | `data/csv/skills.csv` |
| 画面テスト | `tests/test_ui.py` |
| データ統合・ルールテスト | `tests/test_battle_setup.py` |

現行画面は1440×900を基準とし、左にクエスト一覧、右に詳細、下に決定・戻るを置く。

---

## 2. 全体フロー

```text
ホーム
  └─ クエスト
       └─ クエスト選択
            ├─ 一覧選択 → 右側詳細を更新
            ├─ 決定 → 選択quest_idを渡して編成画面
            └─ 戻る/Esc → ホーム

編成画面
  ├─ 試合開始 → quest_idに紐づく戦闘を生成
  └─ 戻る/Esc → 同じquest_idを選択したクエスト選択
```

画面間でクエスト、フィールド、敵グループ等の可変オブジェクトを渡さない。正として渡す値は `quest_id` とし、遷移先がリポジトリまたはデータサービスから関連データを取得する。

---

## 3. クエストデータモデル

最低限、以下を持つ。

```text
QuestDefinition
  quest_id: string
  name: string
  description: string
  field_id: string
  match_rule_id: string
  enemy_group_id: string
  clear_condition_id: string
  failure_condition_id: string?
  recommended_level: int?
  recommended_cost: int?
  bgm_override_path: string?
  rule_type: enum
  required_hold_turns: int
  initial_object_holder: string?
  display_order: int?       移植先では追加を推奨
  enabled: bool
```

Mana's Ball の現在の `rule_type`:

| 値 | 名称 | 概要 | 初期保持チーム |
|---:|---|---|---|
| 1 | 浄化運搬 | ボールを敵ゴールへ運び、必要得点を先取する標準戦 | 通常は味方 |
| 2 | マナ奪還 | 敵が持つボールを奪い返して得点する | 敵 |
| 3 | 儀式維持 | 味方がボールを指定ターン連続保持すると勝利 | 味方 |

移植先ゲームにボールがない場合は、オブジェクトを「旗」「宝石」「拠点」「護衛対象」等へ置き換えてよい。重要なのは、同じ敵データを勝利条件と初期状態の違いで再利用できること。

`initial_object_holder` の推奨形式:

- `ally:1`: 味方出場枠1。
- `enemy:1`: 敵出場枠1。
- 固有の戦闘用ID。
- 空欄: ルール既定値。

未対応の `rule_type` は他ルールとして推測せず、クエストIDと値をログへ出してロードまたは開始を失敗させる。

---

## 4. クエスト選択画面

### 4.1 初期化

```text
openQuestSelect(requestedQuestId?):
  load all quest definitions
  keep only enabled quests
  for each quest:
      load and validate field
      load and validate match rule
      load and validate enemy group
      create QuestSetupSummary
  sort by display_order, then source order
  selectedQuestId = requested ID if valid, otherwise first valid quest
  clamp scroll
  transition to QUEST_SELECT
```

一つのクエストが壊れている場合の扱いは、プロジェクト方針に合わせて次のどちらかに統一する。

- 推奨: 壊れたクエストだけを無効表示または除外し、他クエストは表示する。ログへ原因を残す。
- 厳格モード: 一件でも関連データが壊れていれば画面全体をエラー表示にする。

プレイヤー向け製品では前者、開発用データ検証では後者または起動時一括検証を推奨する。

### 4.2 左側一覧

各行へ表示する情報:

- クエスト名。
- 味方出場人数 対 敵出場人数。
- 味方・敵のパーティー上限。
- フィールドサイズまたは難易度。
- 説明の先頭部分。
- 選択中の強調表示。
- ロックを実装する場合は鍵アイコンと解放条件。

Mana's Ball は5件を同時表示し、ホイール、タップ、上下ボタンでスクロールする。

```text
maximumScroll = max(0, enabledQuestCount - visibleRowCount)
scroll = clamp(scroll + delta, 0, maximumScroll)
```

入力:

- 行クリック/タップ: 選択IDだけ変更し、画面遷移しない。
- ダブルクリック開始は実装しない。誤操作防止のため「決定」を使う。
- ホイール/スワイプ: 一覧を移動。
- 上下ボタン: 1件ずつ移動。

### 4.3 右側詳細

表示項目:

- クエスト名、ID、説明。
- ルール名称と勝利条件の文章。
- 味方対敵の出場人数。
- 味方・敵のベンチ最大数。
- フィールド名、フィールドサイズ。
- 敵グループ名。
- 味方・敵パーティー上限。
- 勝利必要得点。
- 最大ターン数。0は「制限なし」。
- 交代機能、負傷機能。
- 推奨レベル、推奨コスト。未設定は `-`。
- 敵の代表アイコン、構成または役割タグを追加してもよい。

勝利条件は数値だけでなく文章化する。

例:

- `敵ゴールへ運び、2点先取すると勝利`
- `敵が持つマナボールを奪還して2点先取すると勝利`
- `マナボールを3ターン連続で保持すると勝利`

### 4.4 フッター

- 「決定」: 有効なクエスト選択中だけ有効。
- 「戻る」: ホームへ戻る。
- エラーメッセージまたはロック理由。

決定時:

```text
confirmQuest():
  if selected quest is missing, disabled, locked, or invalid:
      show error and remain
  openPartySetup(quest_id = selectedQuestId)
```

### 4.5 空・エラー状態

- 有効クエスト0件: `有効なクエストがありません。クエストデータを確認してください`。
- 関連データ不足: 対象クエスト名と不足IDをログへ記録。
- 選択なし: 右側へ `左の一覧からクエストを選択してください`。
- 画像欠損: テキスト表示を維持し、選択や開始を禁止しない。

---

## 5. 敵データの設計

### 5.1 敵マスター

敵マスターは「敵一体の基礎能力と標準スキル」を定義し、HP残量や位置を持たない。

```text
EnemyMaster
  enemy_id: string
  enemy_type_id: string
  name: string
  image_id: string?
  max_hp: int
  max_resource: int
  power: int
  magic: int
  speed: int
  technique: int
  stamina: int
  skill_ids: ordered list<string>
  default_ai_profile_id: string
  default_ai_level: int
  enabled: bool
  notes: string?
```

一つの敵マスターを同じグループへ複数配置可能にする。試合開始時は各スロットを別個体として生成し、IDを `enemy:{group_id}:{slot}` のようにする。HP、リソース、位置、状態、クールタイム、使用回数を共有してはならない。

### 5.2 敵グループ

```text
EnemyGroup
  enemy_group_id: string
  name: string
  ordered_enemy_ids: list<string>       最大パーティー人数まで
  enemy_scale: float
  group_ai_profile_id: string?
  group_ai_level: int?
  slot_ai_profile_ids: list<string?>
  slot_ai_levels: list<int?>
  display_order: int
  enabled: bool
  ui_selectable: bool
  description: string
```

AIの決定優先順位は一つに固定し、テストする。推奨:

1. スロット別AI。
2. グループ共通AI。
3. 敵マスター標準AI。
4. `standard`。

Mana's Ball には役割マッピングもあるため、現行コードを直接拡張する場合は既存の優先順位に従うこと。

### 5.3 データ検証

- 敵ID、名前、能力値、AIレベルを検証する。
- 無効な敵を含むグループは開始不可とするか、グループ全体を選択候補から除く。
- グループ人数が敵出場人数未満なら開始不可。
- グループ人数が敵パーティー上限を超える場合も開始不可。
- 出場人数を超えた末尾個体は控えにする。
- 存在しないスキルは黙って削除せず、データエラーとしてログへ出す。

---

## 6. 追加敵サンプル6体

以下は Mana's Ball の既存スキルと既存AIプロフィールだけで作れる案である。数値は現在の訓練敵（能力3～9程度）を基準とした初期案で、バランステスト後に調整する。

| ID | 名前 | 役割 | HP/MP | 力/魔/速/技/体 | 主なスキル | AI |
|---|---|---|---|---|---|---|
| iron_guard | 鉄壁の守護兵 | ゴール前防衛・護衛 | 38/5 | 7/3/3/5/10 | シールドガード、パスカット、自己回復 | defense |
| mana_hunter | マナハンター | 保持者追跡・奪取 | 27/7 | 6/4/9/9/5 | スティール、影渡り、ボールカット攻撃 | steal |
| swift_runner | 疾風の運び手 | 高速運搬・得点 | 25/7 | 5/5/10/8/5 | 影渡り、クイックパス、高速の保持 | score |
| arcane_passer | 魔導司令兵 | 後方支援・長距離連携 | 26/10 | 4/10/5/9/5 | 属性魔法、クイックパス、MP回復 | pass |
| siege_breaker | 破城重兵 | 前線突破・押し出し | 36/5 | 10/3/4/5/8 | 突破、押し込み攻撃、パワーアップ | attack |
| ritual_keeper | 儀式の司祭 | 回復・保持支援 | 30/11 | 4/10/4/7/8 | ヒール、HP回復、MP回復、不屈の旗印 | healer |

### 6.1 Mana's Ball互換のCSV追記例

実際に現行プロジェクトへ追加する場合の候補行。ヘッダーは既存 `data/csv/enemies.csv` を使用する。

```csv
iron_guard,physical_stamina,鉄壁の守護兵,,38,5,7,3,3,5,10,shield_guard,pass_cut,single_stamina_recover,normal_physical_attack,ball_hold_iron_self,,,defense,6,true,ゴール前防衛と護衛
mana_hunter,physical_technique,マナハンター,,27,7,6,4,9,9,5,steal,shadow_step,single_technique_ball_cut,normal_physical_attack,ball_hold_precise_self,,,steal,7,true,保持者追跡と奪取
swift_runner,speed_technique,疾風の運び手,,25,7,5,5,10,8,5,shadow_step,quick_pass,normal_magic_attack,ball_hold_fast_self,,,,score,7,true,高速運搬と得点
arcane_passer,magic_technique,魔導司令兵,,26,10,4,10,5,9,5,elemental_bolt,quick_pass,recover_mp,normal_magic_attack,ball_hold_magic_banner,,,pass,6,true,後方支援とパス連携
siege_breaker,physical_power,破城重兵,,36,5,10,3,4,5,8,breakthrough,push_strike,power_up,normal_physical_attack,ball_hold_power_banner,,,attack,7,true,押し出しと前線突破
ritual_keeper,magic_stamina,儀式の司祭,,30,11,4,10,4,7,8,heal,heal_hp,recover_mp,normal_magic_attack,ball_hold_stamina_banner,,,healer,7,true,回復と保持支援
```

注意:

- `enemy_type_id` を別マスターで厳密に管理している移植先では、先に対応タイプを追加する。
- スキルIDは移植先で実在するものへ置換する。
- 画像は `image_id` または敵IDに紐づけ、欠損時は文字フォールバックを使う。
- 上記行は提案値であり、本書作成時点では現行CSVへは追加していない。

---

## 7. 追加戦闘パターン6件

「敵の種類」だけでなく、役割構成、初期保持者、勝利条件、出場人数を変えて体験を分ける。

### 7.1 城門防衛隊

- 形式: 3対3、パーティー上限5。
- ルール: 浄化運搬。
- 敵: 鉄壁の守護兵、破城重兵、儀式の司祭、控えに鉄壁の守護兵と魔導司令兵。
- AI: 守備、攻撃、回復。
- 狙い: 防御と回復で固い相手を崩し、ゴールまで運ぶ。
- 推奨難度: 序盤後半。

### 7.2 盗賊団の奇襲

- 形式: 2対3、味方上限4・敵上限4。
- ルール: マナ奪還。
- 初期保持者: `enemy:1` の疾風の運び手。
- 敵: 疾風の運び手、マナハンター×2、控えに魔導司令兵。
- AI: 得点、奪取、奪取、パス。
- 狙い: 数的不利で逃げる保持者を止め、奪い返す。
- 推奨難度: 中盤。

### 7.3 魔導連携陣

- 形式: 3対3、パーティー上限5。
- ルール: 浄化運搬。
- 敵: 魔導司令兵×2、疾風の運び手、控えに儀式の司祭と鉄壁の守護兵。
- AI: パス、パス、得点、回復、守備。
- 狙い: パス主体でボールを回す敵の経路を読み、カットする。
- 推奨難度: 中盤。

### 7.4 破城突撃隊

- 形式: 4対4、パーティー上限6。
- ルール: 浄化運搬。
- 敵: 破城重兵×2、マナハンター、疾風の運び手、控えに守護兵と司祭。
- AI: 攻撃、攻撃、奪取、得点、守備、回復。
- 狙い: 押し出しで陣形を崩す前衛と高速得点役の連携。
- 推奨難度: 中盤後半。

### 7.5 封印の儀式

- 形式: 3対3、パーティー上限5。
- ルール: 儀式維持。
- 必要保持: 5ターン。
- 初期保持者: `ally:1`。
- 敵: マナハンター×2、破城重兵、控えに魔導司令兵と疾風の運び手。
- AI: 奪取、奪取、攻撃、パス、得点。
- 狙い: 得点ではなく、追跡・奪取に耐えて保持者を守る。
- 推奨難度: 高難度。

### 7.6 総力決戦

- 形式: 5対5、パーティー上限7。
- ルール: マナ奪還または浄化運搬。
- 敵: 追加敵6種から5体を出場、残り2枠に役割を重複配置。
- AI: 全役割を混成。
- 交代・負傷: 有効。
- 狙い: 控えと交代を含む全システムの総合戦。
- 推奨難度: 終盤。

### 7.7 敵グループCSVの構成例

列順は移植先のデータ定義を正とする。Mana's Ball では `enemy_1_id` ～ `enemy_7_id` と、グループ/スロット別AI列を持つ。

```text
castle_guard:
  [iron_guard, siege_breaker, ritual_keeper, iron_guard, arcane_passer]

thief_ambush:
  [swift_runner, mana_hunter, mana_hunter, arcane_passer]

arcane_network:
  [arcane_passer, arcane_passer, swift_runner, ritual_keeper, iron_guard]

siege_assault:
  [siege_breaker, siege_breaker, mana_hunter, swift_runner, iron_guard, ritual_keeper]

ritual_breakers:
  [mana_hunter, mana_hunter, siege_breaker, arcane_passer, swift_runner]

all_star_enemy:
  [iron_guard, mana_hunter, swift_runner, arcane_passer, siege_breaker, ritual_keeper, mana_hunter]
```

同じマスターを複数含む場合でも、試合中は各スロットを独立個体として生成する。

---

## 8. 戦闘パターンを増やす設計

戦闘パターンをコードの条件分岐として増やさず、原則として次の組み合わせでデータ化する。

```text
戦闘パターン =
  フィールド
  + 試合ルール（人数・上限・得点・交代等）
  + 敵グループ（敵種・順序・AI）
  + ルール種別
  + 初期保持者/初期状態
  + クエスト説明・推奨値
```

同じ敵グループを別フィールドや別ルールで再利用してよい。ただしプレイヤーから見て同一内容の水増しにならないよう、最低でも次のうち2項目以上を変える。

- 勝利条件。
- 初期保持チーム。
- 出場人数または数的有利不利。
- 敵の役割構成。
- 敵AI。
- 控えと交代の有無。
- フィールド形状、障害物、ゴール位置。
- ターン制限。

AIプロフィールと能力値の両方を極端に強くすると対処不能になりやすい。新パターンでは最初に役割構成とAIで特色を作り、数値倍率は最後に調整する。

---

## 9. ロック・進行管理（任意拡張）

現在の Mana's Ball は有効クエストをすべて表示する。別ゲームで進行を持たせる場合は以下を追加する。

```text
QuestProgress
  quest_id
  unlocked
  cleared
  best_rank
  best_turns
  first_clear_reward_received
```

規則:

- `enabled` は開発・配信データ上の有効性、`unlocked` はプレイヤー個別の解放状態として分離する。
- 未解放クエストを非表示にするかロック表示するかは全画面で統一する。
- ロック表示する場合、詳細欄には解放条件を出し、「決定」を無効にする。
- セーブ値だけを信用せず、前提クエストIDが存在するか検証する。
- 初回報酬と通常報酬を分離する。

---

## 10. 受け入れテスト

### 10.1 クエスト選択

1. ホームの「クエスト」で選択画面へ遷移する。
2. 有効クエストだけが規定順で並ぶ。
3. 最初の有効クエストが初期選択される。
4. 指定済みの有効IDがあれば、そのクエストを選択状態で復帰する。
5. 行選択で右側詳細が更新される。
6. 一覧スクロールが上端・下端を超えない。
7. マウス、タップ、ホイール、上下ボタンが重複発火しない。
8. 選択なしまたは無効クエストでは「決定」が無効。
9. 決定時に選択した `quest_id` だけを編成画面へ渡す。
10. 戻る/Escでホームへ戻る。

### 10.2 データ検証

1. 存在しないフィールド、試合ルール、敵グループを参照するクエストを検出する。
2. 未対応ルール種別をエラーにする。
3. 儀式維持の必要ターン数が0以下ならエラーまたは明示した既定値になる。
4. 無効な敵、スキル、AIを参照するグループを検出する。
5. 敵数が出場人数未満またはパーティー上限超過なら開始不可。
6. 同じ敵マスターを複数配置しても戦闘中状態が独立する。

### 10.3 追加敵・戦闘パターン

1. 追加敵6体をロードできる。
2. 各敵の全スキルIDとAIプロフィールIDが存在する。
3. 追加グループの順序が戦闘生成後も維持される。
4. 各パターンの出場人数と控え人数が仕様通り。
5. マナ奪還では指定した敵が初期保持する。
6. 儀式維持では指定ターン保持で勝利する。
7. 6パターンを固定シードで最低1試合ずつ最後まで実行し、例外・停止・無限ループがない。
8. 複数シードの自動戦闘結果を記録し、一方の勝率が極端なら能力、AI、人数を調整する。

---

## 11. Codexへの実装指示文

```text
このリポジトリへ、以下の仕様書を順に読み、クエスト選択から編成、戦闘開始までを実装してください。

1. docs/クエスト選択_敵戦闘パターン_移植用Codex実装仕様書.md
2. docs/編成画面_移植用Codex実装仕様書.md

実装前にリポジトリ全体を調査し、既存の画面遷移、データモデル、敵生成、AI、勝利条件、UIフレームワーク、テスト方式へ対応付けてください。Python/Pygame固有のクラス名やCSV形式を無理に持ち込まず、既存設計へ適合させてください。

必須実装:
- ホーム→クエスト選択→編成→戦闘の遷移。
- 有効クエスト一覧、詳細、スクロール、決定、戻る。
- quest_idを基準とする関連データロードと検証。
- 読み取り専用敵マスター、敵グループ、戦闘用敵個体の分離。
- 仕様書の追加敵6体と戦闘パターン6件。ただし移植先に存在しないスキルは、同じ役割を満たす既存能力へ置換し、対応関係を報告する。
- 関連する自動テスト。

進行ロックは既存ゲームに進行・セーブ機能がある場合のみ実装してください。新しい戦闘ルールのプログラム追加は今回必須ではありません。既存ルールの組み合わせで6パターンを作成してください。

作業後は静的確認、自動テスト、可能なら全追加クエストの起動確認または固定シードのヘッドレス完走確認を行い、変更ファイル、データ追加内容、検証結果、バランス上の暫定値、残課題を報告してください。
```

---

## 12. 実装上の注意

- クエスト選択画面で戦闘用インスタンスを生成しない。
- 一覧に出すための検証済みサマリーと、戦闘中の可変状態を共有しない。
- `enabled` とプレイヤーの `unlocked` を混同しない。
- 敵グループの配列順を失わない。順番は出場/控えと初期配置に影響する。
- 同じ敵マスターの複数配置を禁止しない。ただし戦闘用状態は独立させる。
- AIのフォールバック優先順位を画面表示と戦闘生成で一致させる。
- 新敵はまず既存スキルで構成し、必要性が確認できるまで専用スキルを増やしすぎない。
- 新パターンは能力倍率だけで差を作らず、役割、人数、初期状態、勝利条件を組み合わせる。
- 提案した能力値とAIレベルは暫定値であり、自動戦闘と手動プレイで調整する。
