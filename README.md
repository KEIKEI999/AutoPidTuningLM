# PID Auto-Tuning MVP

`spec.md` を唯一の仕様ソースとして実装した、PID 自動調整の初版 MVP です。
ローカルで設定検証、仮想 plant 評価、`pid_params.h` 更新、trial 保存、CLI 実行、テスト実行まで完結できます。

現時点の既定構成は以下です。

- 候補生成の既定実行: trial 1 は `initial_pid` をそのまま評価し、trial 2 以降は `local_ovms` を優先し、失敗時は fallback
- ローカル LLM の既定候補: `OpenVINO/Qwen3-8B-int4-ov`
- ローカル LLM の代替候補: `OpenVINO/Qwen3-14B-int4-ov`
- Plan B: OpenAI API の `Responses API` 経由

# 概要

以下の記事にて概要説明をしています。

https://www.simulationroom999.com/blog/local-llm-openai-api-pid-auto-tuning-comparison/

## 仕様の正本

- 仕様の正本は `spec.md`
- README は実装状態、起動手順、前提、制約を補足するための文書
- 仕様差分や運用上の注意は README に明記する

## ライセンス

- このリポジトリの自作コードと自作ドキュメントは `Apache-2.0` です。
- 詳細はルートの `LICENSE` を参照してください。
- 外部依存物、外部 SDK、外部モデル、配布バイナリはこのライセンスの対象外であり、それぞれ元のライセンスや利用条件に従います。

## 実装済み範囲

- `target_response.yaml` / `plant_cases.yaml` / `limits.yaml` の読込と検証
- `--dry-run` による設定検証のみの実行
- `pid_params.h` の安全更新
- plant simulator
  - 一次遅れ
  - 二次遅れ
  - 無駄時間
  - ノイズ
  - `tanh` 非線形
- 仮想 CAN / codec
- evaluator
  - rise time
  - settling time
  - overshoot
  - steady-state error
  - IAE / ISE / ITAE
  - control variation
  - oscillation
  - divergence
  - saturation
- orchestrator
- VS2017 `msbuild` 連携
- `Vector XL` 実アダプタ接続
- Python plant simulator と C controller の `Vector XL` 往復確認
- runtime からの `controller.exe` 切替
- trial 成果物保存
- 単体テスト / 結合テスト

## 前提環境

- Windows
- Python 3.10 以上
- Visual Studio 2017 / `MSBuild.exe`
- `Vector XL Driver Library`

### Python パッケージ

必須:

- `matplotlib`
- `PyYAML`

インストール:

```powershell
python -m pip install -r requirements.txt
```

Plan B で OpenAI API を使う場合の追加候補:

- `openai`

```powershell
python -m pip install openai
```

## ディレクトリ構成

- `spec.md`: 仕様の正本
- `orchestrator/`: CLI、設定、候補生成、runtime、evaluator
- `plant/`: plant simulator、CAN I/O、往復確認 CLI
- `controller/`: C controller、CAN codec、CAN I/F、VS2017 project
- `configs/`: サンプル設定
- `tests/`: 単体テスト / 結合テスト
- `tests/fixtures/`: 固定フィクスチャ
- `docs/`: 補助ドキュメント
- `tools/ovms/`: ローカル配置した OVMS バイナリ
  - 公開リポジトリには含めない想定
  - 利用時は各自で別途セットアップ

## 実行手順

### 役割分担

- ユーザが事前に行うこと
  - ローカル LLM を起動する
  - 必要なら `Vector XL Driver Library`、Visual Studio 2017、`OPENAI_API_KEY` などの外部前提を用意する
- システムが自動で行うこと
  - 設定読込と検証
  - PID 候補生成
  - `pid_params.h` 更新
  - controller のビルド
  - controller の起動
  - plant simulator の起動
  - trial 実行
  - 評価
  - ranking と成果物保存

### Step By Step

1. ローカル LLM を起動する

ローカル OVMS を使う場合は、ユーザ側で先に起動しておきます。

2. 必要なら初回の追加方針を用意する

必要な場合だけ、初回の追加方針を 1 回だけ与えられます。
与え方は 2 通りです。

- `--user-instruction "..."` で直接渡す
- `--user-instruction-file <path>` で UTF-8 テキストを渡す

注意:

- `--user-instruction` は run 全体の `Operator Intent` として扱います
- `Operator Intent` は hard safety 制約、PID 範囲制約、重複禁止、JSON スキーマより下ですが、それ以外の既定ヒューリスティクスより優先されます
- `Operator Intent` は単なる参考意見ではなく、run 内で優先的に従う方針として扱います
- `Operator Intent` が aggressive な探索や大きめの係数変更を求める場合、hard rule に反しない限り、小さな無難調整へ勝手に弱めない方針です
- 既定構成では trial 1 だけ `initial_pid` を固定評価し、trial 2 以降で LLM 候補生成に入ります
- そのため `--max-trials 1` だと、追加方針の差は出ません

3. 設定ファイルを確認する

- `configs/target_response.yaml`
- `configs/plant_cases.yaml`
- `configs/limits.yaml`

4. `dry-run` で設定だけ検証する

```powershell
python orchestrator/main.py --config configs/target_response.yaml --dry-run
```

5. 探索を開始する

```powershell
python orchestrator/main.py --config configs/target_response.yaml --case first_order_nominal --max-trials 4 --output-dir results/manual_run
```

初回だけ追加方針を与える場合:

```powershell
python orchestrator/main.py --config configs/target_response.yaml --case first_order_nominal --max-trials 4 --user-instruction "Prefer monotonic response. Avoid oscillatory candidates even if rise time becomes slightly slower." --output-dir results/manual_run
```

ファイルで渡す場合:

```powershell
python orchestrator/main.py --config configs/target_response.yaml --user-instruction-file configs/operator_instruction.txt --output-dir results/manual_run
```

`Vector XL` 実経路で実行する場合:

```powershell
python orchestrator/main.py --config configs/target_response.yaml --build-mode msbuild --can-adapter vector_xl --case first_order_nominal --max-trials 4 --output-dir results/vector_xl_run
```

ここは重要です。

- `--can-adapter vector_xl` だけでは不十分です
- `--build-mode msbuild` と `--can-adapter vector_xl` の両方を指定したときだけ、`Vector XL` 実経路へ切り替わります
- 切替後の `trial_xxxx.json` では `runtime.runtime_backend = "c_controller_vector_xl"` を確認できます
- `--build-mode mock` のままでは `virtual_stub` 実行で、BUSMASTER からは見えません

6. システムの自動処理を待つ

この間は orchestrator が内部で以下を順に実行します。

- 各 trial 用の内部 prompt を生成
- LLM へ候補問い合わせ
- `pid_params.h` 更新
- controller build
- controller launch
- plant launch
- 閉ループ試験
- 評価と保存

標準出力には進捗が 1 行ずつ出ます。主に次を確認できます。

- `PREPARE_TRIAL`: trial 番号、case、seed
- `CANDIDATE`: 現在の `Kp` / `Ki` / `Kd`、候補 source、mode
- `BUILD`: build 成否、終了コード、処理時間
- `RUN_TRIAL`: 実行 mode
- `EVALUATE`: score、`overall_pass`、dominant issue
- `SAVE_RESULT`: best 更新、acceptable candidate 発見有無

表示例:

```text
[progress][run][START] total_trials=4 output_dir=C:\Path\To\AutoTuningLM\results\manual_run cases=first_order_nominal
[progress][trial 1/4][PREPARE_TRIAL] case=first_order_nominal seed=101
[progress][trial 1/4][CANDIDATE] case=first_order_nominal source=bootstrap mode=coarse Kp=0.250000 Ki=0.050000 Kd=0.000000
[progress][trial 1/4][BUILD] status=success exit_code=0 duration_ms=28
[progress][trial 1/4][RUN_TRIAL] case=first_order_nominal build_mode=mock
[progress][trial 1/4][EVALUATE] score=0.156973 overall_pass=True dominant_issue=none
[progress][trial 1/4][SAVE_RESULT] status=completed score=0.156973 overall_pass=True best_trial=1 best_score=0.156973 acceptable_found=True best_acceptable_trial=1
[progress][run][FINISHED] trials=4 best_trial=1 best_score=0.156973 acceptable_found=True best_acceptable_trial=1
```

7. 結果を確認する

- `trial_xxxx.json`
- `trial_xxxx/metrics.json`
- `trial_xxxx/waveform.csv`
- `waveform_overlay.png`
- `ranking.json`

8. `ranking.json` を読む

- `best_trial_index`: ranking 先頭
- `best_acceptable_trial_index`: 全制約を満たした候補の先頭
- `acceptable_candidate_found=false`: fully compliant な候補は未発見
- `trial_xxxx.json` の `candidate_source.rationale`: LLM が見ていた問題、各係数の操作意図、想定トレードオフ

補足:

- ユーザが各 trial ごとに自然言語 prompt を手入力する運用ではありません
- ただし、初回だけ `--user-instruction` または `--user-instruction-file` で追加方針を渡せます
- 各 trial の prompt は orchestrator が内部生成し、`trial_xxxx/logs/llm_prompt.txt` に保存します
- `--user-instruction` は trial 1 の `initial_pid` 評価そのものは変更しません
- `係数を大きめに振って` のような指示が効きにくい場合は、hard rule、重複禁止、`temperature=0.0` の組み合わせで保守的に見えることがあります

## CLI

単一エントリは `orchestrator/main.py` です。

```powershell
python orchestrator/main.py --config configs/target_response.yaml
```

主な引数:

- `--config`
- `--dry-run`
- `--case`
- `--max-trials`
- `--user-instruction`
- `--user-instruction-file`
- `--output-dir`
- `--build-mode {mock,msbuild}`
- `--can-adapter {stub,vector_xl}`
- `--vector-channel-index`
- `--vector-bitrate`
- `--vector-rx-timeout-ms`
- `--vector-startup-wait-ms`
- `--vector-exchange-timeout-ms`
- `--vector-resend-interval-ms`

実行例:

```powershell
python orchestrator/main.py --config configs/target_response.yaml --case first_order_nominal --max-trials 4 --output-dir results/manual_run
```

`dry-run`:

```powershell
python orchestrator/main.py --config configs/target_response.yaml --dry-run
```

## 設定ファイル

サンプル設定は `configs/` 配下にあります。

- `target_response.yaml`
- `plant_cases.yaml`
- `limits.yaml`

`limits.yaml` の `llm` セクションで、ローカル OVMS と Plan B の OpenAI API を切り替えられます。
現時点では `local_ovms` の実接続を確認済みで、成果物には prompt と response も保存されます。

例:

```json
{
  "llm": {
    "provider": "local_ovms",
    "model": "OpenVINO/Qwen3-8B-int4-ov",
    "endpoint": "http://127.0.0.1:8000/v3/chat/completions",
    "api_env": null,
    "json_schema_name": "pid_candidate_response",
    "prompt_language": "en",
    "use_conversation_state": false
  }
}
```

推奨:

- `local_ovms` は `use_conversation_state: false` を推奨
- 理由は、`true` だと system 側で履歴を積んで毎 trial 再送するため、trial 後半ほど応答が遅くなりやすいから
- `openai_responses` は会話状態を API 側で持てるため、`use_conversation_state: true` の相性がよい

利用可能な `provider`:

- `rule_based_stub`
- `local_ovms`
- `openai_responses`

利用可能な `prompt_language`:

- `en`
- `ja`

運用方針:

- ユーザ向け説明やドキュメントは日本語でよい
- LLM に渡す system / user prompt は `prompt_language: en` を既定にする
- 現時点の `Qwen3-8B + OVMS` では、英語 prompt の方が JSON 準拠と重複回避が安定した

## ローカル LLM

### 現在の方針

MVP の既定候補は `OpenVINO/Qwen3-8B-int4-ov` です。
この環境で実際に確認した結果は次のとおりです。

- `OpenVINO/Qwen3-8B-int4-ov`: Intel GPU で起動成功、推論成功
- `OpenVINO/Qwen3-14B-int4-ov`: Intel GPU で起動成功、推論成功
- `Qwen3-Coder-30B-A3B-Instruct-int4-ov`: Intel GPU で USM メモリ不足により起動失敗

そのため、README とサンプル設定は 8B を既定候補に寄せています。
また、現時点では `prompt_language: en` を既定にしています。実測では英語 prompt の方が JSON 準拠と重複回避が安定しました。

### 公開リポジトリでの扱い

- `tools/ovms/` はローカル検証用の配置先であり、GitHub 公開物には含めない想定です。
- 公開版を利用する場合は、OVMS 本体、必要な Python 同梱物、モデル本体を各自で別途セットアップしてください。
- この README の OVMS 起動例は、ローカルに OVMS とモデルがセットアップ済みであることを前提にしています。

### OVMS の入手先

- OVMS 概要
  - https://docs.openvino.ai/2025/openvino-workflow/model-server/ovms_what_is_openvino_model_server.html
- OVMS Releases
  - https://github.com/openvinotoolkit/model_server/releases
- OVMS Docker Hub
  - https://hub.docker.com/r/openvino/model_server/tags/
- OVMS LLM QuickStart
  - https://docs.openvino.ai/2026/model-server/ovms_docs_llm_quickstart.html

### OVMS セットアップ

1. OVMS を取得する
2. `Qwen3-8B-int4-ov` を ASCII パスへ配置する
3. OVMS を起動する
4. `chat/completions` に到達できることを確認する

Windows での一例:

```powershell
$env:OVMS_DIR='C:\Path\To\ovms'
$env:PYTHONHOME='C:\Path\To\ovms\python'
$env:PATH="$env:OVMS_DIR;$env:PYTHONHOME;$env:PYTHONHOME\Scripts;$env:PATH"
```

この環境で確認済みの GPU 起動例:

```powershell
ovms --model_repository_path C:\ovms_models `
     --source_model OpenVINO/Qwen3-8B-int4-ov `
     --task text_generation `
     --target_device GPU `
     --tool_parser hermes3 `
     --reasoning_parser qwen3 `
     --rest_port 8000 `
     --cache_dir .ovcache `
     --model_name Qwen3-8B
```

補足:

- `C:\ovms_models` は、この環境で実際に使った ASCII パスです
- 上のコマンドでは `--source_model OpenVINO/Qwen3-8B-int4-ov` を取得しつつ、API 上の公開名は `Qwen3-8B` になります
- 初回はモデル取得とキャッシュ生成を含むため、起動完了まで少し時間がかかります

疎通確認:

```powershell
curl http://127.0.0.1:8000/v1/config
curl http://127.0.0.1:8000/v3/models
```

`/v3/models` では `Qwen3-8B` が見えることを確認してください。

最小推論確認例:

```powershell
$body = @{
  model = 'Qwen3-8B'
  messages = @(
    @{ role = 'system'; content = 'You are a concise assistant. Reply with exactly OK.' },
    @{ role = 'user'; content = 'Return exactly OK.' }
  )
  stream = $false
  temperature = 0.0
  max_completion_tokens = 8
} | ConvertTo-Json -Depth 6

Invoke-RestMethod -Uri 'http://127.0.0.1:8000/v3/chat/completions' -Method Post -ContentType 'application/json' -Body $body
```

### 実測メモ

この環境(Intel(R) Core(TM) Ultra 7 165U (1.70 GHz) RAM 32GB)での `Qwen3-8B-int4-ov` の一例:

- REST 起動まで: 約 `2.91s`
- `/v3/models` 登録まで: 約 `58.31s`
- 最小推論応答まで: 約 `2.15s`

`Qwen3-14B-int4-ov` も動作しましたが、8B の方が起動と運用の負担が軽いです。

### ローカル LLM の内部 prompt 例

以下は orchestrator が trial ごとに内部生成して LLM に渡す具体例です。
ユーザがそのまま手入力する prompt ではありません。
出力は JSON のみを要求します。

システム指示例:

```text
You are a PID tuning candidate generator.
The system prompt contains fixed rules. The user prompt contains only current trial context.
If an Operator Intent section is present, treat it as the highest-priority run-specific tuning intent.
Treat Operator Intent as a run contract, not as an optional suggestion.
Apply Operator Intent unless it conflicts with hard safety rules, JSON schema, numeric limits, or duplicate-candidate prohibition.
Do not silently weaken or dilute Operator Intent into a conservative move.
If Operator Intent asks for aggressive exploration, larger gain moves, or intentional overshoot before later stabilization, honor that direction unless a hard constraint blocks it.
Return exactly one JSON object and nothing else.
Never repeat any previously used PID candidate.
```

内部 user prompt 例:

```text
Current trial context for PID tuning.

[Targets]
- rise_time <= 1.0
- settling_time <= 2.5
- overshoot <= 5.0
- steady_state_error <= 0.01
- allow_oscillation = False
- allow_divergence = False
- allow_saturation = False

[PID Limits]
- Kp: 0.01 ~ 10.0
- Ki: 0.00 ~ 5.0
- Kd: 0.00 ~ 2.0

[Current Best Candidate]
- Kp = 0.25
- Ki = 0.05
- Kd = 0.00
- score = 7.69

[Operator Intent]
Start with an intentionally aggressive candidate. Allow initial overshoot, then gradually stabilize in later trials. Do not stay conservative in early trials.

[Operator Intent Priority]
- Treat this instruction as the highest-priority run-specific tuning direction.
- Treat it as a run contract, not as a weak suggestion.
- Follow it unless it conflicts with hard safety constraints, JSON schema, PID limits, or duplicate-candidate prohibition.
- If it conflicts with recent-history heuristics, prefer Operator Intent.
- Do not silently soften it into a conservative move.
- If it asks for aggressive exploration or a larger parameter swing, make a meaningfully stronger move unless a hard constraint blocks it.

[Recent Trial History]
1) Kp=0.25 Ki=0.05 Kd=0.00 rise=2.43 settling=5.00 overshoot=0.0 score=7.69
2) Kp=0.50 Ki=0.05 Kd=0.00 rise=1.78 settling=4.70 overshoot=0.0 score=7.01

[Required Output Format]
- mode must be coarse or fine
- next_candidate must contain numeric Kp, Ki, Kd
- explanation must briefly state how the candidate follows Operator Intent
- rationale.expected_tradeoff must reflect Operator Intent when it exists

Return exactly this JSON shape:
{
  "mode": "coarse or fine",
  "next_candidate": { "Kp": number, "Ki": number, "Kd": number },
  "expectation": "short text",
  "explanation": "short text <= 100 chars",
  "rationale": {
    "observed_issue": "short text",
    "parameter_actions": { "Kp": "increase|decrease|keep", "Ki": "increase|decrease|keep", "Kd": "increase|decrease|keep" },
    "expected_tradeoff": "short text",
    "risk": "short text"
  }
}
```

期待 JSON 例:

```json
{
  "mode": "fine",
  "next_candidate": {
    "Kp": 0.62,
    "Ki": 0.08,
    "Kd": 0.02
  },
  "expectation": "rise_too_slow",
  "explanation": "応答が遅いためKpとKiを少し上げて立ち上がり短縮を狙う"
}
```

注意:

- `Qwen3` 系は `<think>` を含む出力を返すことがある
- 実運用では prompt 制約強化か、受信側で JSON 抽出が必要
- このリポジトリでは `try_extract_json()` で JSON 抽出を行う

### Prompt の役割

このシステムでは、prompt には 2 種類あります。

- 初回指示: ユーザが最初に与える目標、対象 case、試行条件
- 内部 prompt: 各 trial ごとに orchestrator が自動生成して LLM に渡す prompt

CLI 実行では、通常ユーザは初回指示だけを設定ファイルと引数で与えます。
README に載せる長い英語 prompt 例は、trial 途中で orchestrator が内部生成する prompt の例です。

### ユーザが最初に与える条件の説明例

ユーザが最初に与える情報は、実質的には `target_response.yaml`、`plant_cases.yaml`、`limits.yaml` と CLI 引数です。
必要なら初回だけ `--user-instruction` または `--user-instruction-file` で追加方針を与えられます。
以下はその内容を自然言語で説明した例であり、実際に毎回入力する prompt ではありません。

自然言語で表すと次のような内容です。

初回の日本語指示イメージ:

```text
first_order_nominal を対象に PID 自動調整を実行する。
目標は rise time 1.0 秒以内、settling time 2.5 秒以内、overshoot 5% 以下、
steady-state error 0.01 以下とする。
試行回数上限は 4 回。
既出候補の再利用は禁止。
```

初回の英語指示イメージ:

```text
Run PID auto-tuning for the first_order_nominal case.
Target rise time is <= 1.0 s, settling time <= 2.5 s, overshoot <= 5%,
and steady-state error <= 0.01.
Use at most 4 trials.
Do not reuse previously tested PID candidates.
```

### 内部英語 prompt 例

現在の既定は英語 prompt です。
実測では、日本語 prompt より英語 prompt の方が `Qwen3-8B` で安定して有効候補を返しました。

以下は、trial 4 時点で orchestrator が内部生成して LLM に渡す prompt の例です。

英語 system prompt 例:

```text
You are a PID tuning candidate generator.
The system prompt contains fixed rules. The user prompt contains only current trial context.
If an Operator Intent section is present, treat it as the highest-priority run-specific tuning intent.
Apply Operator Intent unless it conflicts with hard safety rules, JSON schema, numeric limits, or duplicate-candidate prohibition.
If Operator Intent conflicts with recent-history heuristics or the default conservative tendency, prefer Operator Intent.
Return exactly one JSON object and no markdown.
Do not include chain-of-thought.
The JSON must contain mode, next_candidate, expectation, explanation, rationale.
Never repeat any previously used PID candidate.
If a proposed Kp, Ki, Kd triple already appeared in the prompt, choose a different triple.
```

内部の英語 user prompt 例:

```text
You are selecting the next PID candidate for an automated tuning loop.
Return exactly one JSON object with keys: mode, next_candidate, expectation, explanation.
Do not return markdown. Do not return prose outside JSON.
Hard rule: never repeat any PID candidate that already appeared in previous trials.
Hard rule: if a candidate matches any previously used Kp, Ki, Kd triple, it is invalid.
Hard rule: next_candidate must be an object with numeric Kp, Ki, Kd. Never encode it as a string.

[Targets]
- rise_time <= 1.0
- settling_time <= 2.5
- overshoot <= 5.0
- steady_state_error <= 0.01
- allow_oscillation = False
- allow_divergence = False
- allow_saturation = False

[PID Limits]
- Kp: 0.01 ~ 10.0
- Ki: 0.0 ~ 5.0
- Kd: 0.0 ~ 2.0

[Current Best Candidate]
- Kp = 0.250000
- Ki = 0.100000
- Kd = 0.000000
- score = 6.560475

[Operator Intent]
Start with an intentionally aggressive candidate. Allow initial overshoot, then gradually stabilize in later trials. Do not stay conservative in early trials.

[Operator Intent Priority]
- Treat this instruction as the highest-priority run-specific tuning direction.
- Follow it unless it conflicts with hard safety constraints, JSON schema, PID limits, or duplicate-candidate prohibition.
- If it conflicts with recent-history heuristics, prefer Operator Intent.

[Used Candidates: Never Repeat]
- Kp=0.250000, Ki=0.050000, Kd=0.000000
- Kp=0.500000, Ki=0.050000, Kd=0.000000
- Kp=0.250000, Ki=0.100000, Kd=0.000000

[Recent Trial History]
1) Kp=0.2500 Ki=0.0500 Kd=0.0000 rise=5.000 settling=5.000 overshoot=0.000 sse=0.671474 oscillation=False divergence=False saturation=False score=7.691042
2) Kp=0.5000 Ki=0.0500 Kd=0.0000 rise=5.000 settling=5.000 overshoot=0.000 sse=0.572411 oscillation=False divergence=False saturation=False score=6.689592
3) Kp=0.2500 Ki=0.1000 Kd=0.0000 rise=5.000 settling=5.000 overshoot=0.000 sse=0.559003 oscillation=False divergence=False saturation=False score=6.560475

[Required Output Format]
- mode must be coarse or fine
- next_candidate must contain numeric Kp, Ki, Kd
- next_candidate must stay inside limits
- next_candidate must not match any entry in [Used Candidates: Never Repeat]
- explanation must be short and <= 100 characters
- if [Operator Intent] exists, explanation must briefly state how the candidate follows it
- if [Operator Intent] exists, rationale.expected_tradeoff must reflect that intent
- if [Operator Intent] asks for aggressive exploration or larger gains, prefer a meaningfully stronger move over a tiny safe adjustment unless blocked by a hard constraint
- current_trial = 4

[Few-shot Example: Valid]
{"mode":"fine","next_candidate":{"Kp":0.62,"Ki":0.08,"Kd":0.02},"expectation":"rise_too_slow","explanation":"Increase Kp and Ki slightly to follow the aggressive early exploration intent.","rationale":{"observed_issue":"rise_too_slow","parameter_actions":{"Kp":"increase","Ki":"increase","Kd":"keep"},"expected_tradeoff":"Faster rise with some overshoot risk, matching the aggressive exploration intent.","risk":"overshoot_or_saturation"}}

[Few-shot Example: Invalid, do not do this]
{"mode":"fine","next_candidate":"Kp=0.25 Ki=0.10 Kd=0.00","expectation":"bad_format","explanation":"next_candidate must not be a string","rationale":{"observed_issue":"bad_format","parameter_actions":{"Kp":"keep","Ki":"keep","Kd":"keep"},"expected_tradeoff":"invalid","risk":"invalid"}}
```

比較メモ:

- 英語 prompt: trial 4 で `local_ovms` 候補がそのまま採用され、score 改善を確認
- 日本語 prompt: 同条件では重複候補を返しやすく、fallback に落ちた
- そのため既定値は `prompt_language: en`

## Plan B: OpenAI API

ローカル OVMS が使えない場合の代替として、OpenAI API の `Responses API` を想定しています。

既定の想定値:

- `provider`: `openai_responses`
- `model`: `gpt-5.4`
- `endpoint`: `https://api.openai.com/v1/responses`
- `api_env`: `OPENAI_API_KEY`

補足:

- 公開されている OpenAI の公式 docs では、利用可能モデル名は更新されることがあります
- `gpt-5.4` が利用できない環境では、組織で利用可能な最新モデル名に置き換えてください
- 2026-04-09 時点で `OPENAI_API_KEY` を使った `Responses API` の疎通確認を実施し、`gpt-5.4` で最小 JSON 応答と orchestrator 4 trial 実行を確認済みです

OpenAI 公式 docs:

- API introduction
  - https://platform.openai.com/docs/introduction
- Responses API guide
  - https://platform.openai.com/docs/guides/text
- Streaming responses
  - https://platform.openai.com/docs/guides/streaming-responses

環境変数例:

```powershell
$env:OPENAI_API_KEY='sk-...'
```

現在の PowerShell セッションだけで使う場合は上記で十分です。
Windows に永続設定する場合の例:

```powershell
[System.Environment]::SetEnvironmentVariable('OPENAI_API_KEY', 'sk-...', 'User')
```

設定後は新しい PowerShell を開いて反映させてください。

現在の設定状態を確認する例:

```powershell
if ($env:OPENAI_API_KEY) { 'OPENAI_API_KEY is set' } else { 'OPENAI_API_KEY is not set' }
```

値そのものを確認したい場合は、全文を表示せず一部だけ確認することを推奨します。

```powershell
if ($env:OPENAI_API_KEY) {
  $k = $env:OPENAI_API_KEY
  '{0}...{1}' -f $k.Substring(0,4), $k.Substring($k.Length-4)
}
```

設定例:

```json
{
  "llm": {
    "provider": "openai_responses",
    "model": "gpt-5.4",
    "endpoint": "https://api.openai.com/v1/responses",
    "api_env": "OPENAI_API_KEY",
    "json_schema_name": "pid_candidate_response"
  }
}
```

Python での最小例:

```python
from openai import OpenAI

client = OpenAI()
response = client.responses.create(
    model="gpt-5.4",
    input=[
        {
            "role": "system",
            "content": "You are a PID tuning candidate generator. Output JSON only.",
        },
        {
            "role": "user",
            "content": "Return one next PID candidate as JSON.",
        },
    ],
)

print(response.output_text)
```

## `Vector XL`

環境変数例:

PowerShell:

```powershell
$env:VECTOR_XL_SDK_DIR='C:\Path\To\Vector\XL Driver Library'
$env:PATH="$env:VECTOR_XL_SDK_DIR\bin;$env:PATH"
```

この環境で確認した実パス例:

```powershell
$env:VECTOR_XL_SDK_DIR='C:\Users\Public\Documents\Vector\XL Driver Library 20.30.14'
$env:PATH='C:\Users\Public\Documents\Vector\XL Driver Library 20.30.14\bin;' + $env:PATH
```

`cmd.exe`:

```cmd
set VECTOR_XL_SDK_DIR=C:\Users\Public\Documents\Vector\XL Driver Library 20.30.14
set PATH=C:\Users\Public\Documents\Vector\XL Driver Library 20.30.14\bin;%PATH%
```

設定確認:

PowerShell:

```powershell
echo $env:VECTOR_XL_SDK_DIR
Get-ChildItem "$env:VECTOR_XL_SDK_DIR\bin\vxlapi*.dll"
```

`cmd.exe`:

```cmd
echo %VECTOR_XL_SDK_DIR%
where vxlapi.dll
```

注意:

- `--build-mode msbuild --can-adapter vector_xl` を使うと、`VECTOR_XL_SDK_DIR` が未設定のままでは build が止まります
- 先に上の環境変数を設定してから orchestrator を実行してください

`vector_xl` での C controller build:

```powershell
& 'C:\Path\To\MSBuild.exe' controller\vs2017\controller.sln /t:Build /p:Configuration=Release /p:Platform=Win32 /p:CanAdapter=vector_xl /m /nologo
```

orchestrator 実行:

```powershell
python orchestrator/main.py --config configs/target_response.yaml --build-mode msbuild --can-adapter vector_xl
```

明示的に case と出力先を付ける例:

```powershell
python orchestrator/main.py --config configs/target_response.yaml --build-mode msbuild --can-adapter vector_xl --case first_order_nominal --max-trials 4 --output-dir results/vector_xl_run
```

runtime パラメータ override 例:

```powershell
python orchestrator/main.py --config configs/target_response.yaml --build-mode msbuild --can-adapter vector_xl --vector-channel-index 1 --vector-bitrate 500000 --vector-rx-timeout-ms 150 --vector-startup-wait-ms 100 --vector-exchange-timeout-ms 1200 --vector-resend-interval-ms 50
```

### `Vector XL` 実行時に何がつながるか

- `controller.exe` は `Vector XL` 経由で CAN 送受信します
- Python 側 plant は orchestrator プロセス内で動作し、measurement と control_output の計算を担当します
- つまり orchestrator の `vector_xl` 実行では、Python plant は使われていますが、独立した `Vector XL` ノードとして常駐するわけではありません
- BUSMASTER から見えるのは、主に orchestrator が `controller.exe` とやり取りする CAN フレームです

独立した Python plant ノードを `Vector XL` 上で確認したい場合は、以下の確認 CLI を使います。

## 往復確認 CLI

Python host と Python plant simulator:

```powershell
python -m plant.vector_xl_roundtrip --target configs/target_response.yaml --case first_order_nominal --output-dir .tmp_tests/vector_xl_roundtrip
```

C controller と Python plant simulator:

```powershell
python -m plant.controller_vector_xl_roundtrip --target configs/target_response.yaml --case first_order_nominal --output-dir .tmp_tests/controller_vector_xl_roundtrip
```

## テスト

```powershell
python -m unittest discover -s tests -v
```

主な対象:

- config validation
- `pid_params.h` update
- real `msbuild`
- can codec
- virtual can
- evaluator
- heartbeat timeout
- orchestrator state transitions
- dry-run
- 3 から 5 trial の短い探索
- same seed 再現性
- plant roundtrip
- runtime backend 切替

## 成果物

`--output-dir` 配下に少なくとも以下を保存します。

- `trial_0001.json`
- `trial_0001/metrics.json`
- `trial_0001/waveform.csv`
- `waveform_overlay.png`
- `trial_0001/pid_params.h`
- `trial_0001/pid_params.diff`
- `trial_0001/logs/build_stdout.log`
- `trial_0001/logs/build_stderr.log`
- `trial_0001/logs/llm_prompt.txt`
- `trial_0001/logs/llm_response.json`
- `ranking.json`

`vector_xl` 実行時は追加で controller 実行ログを保存します。

- `trial_0001/controller_stdout.log`
- `trial_0001/controller_stderr.log`

## グラフの見方

探索完了後、`--output-dir` 直下に `waveform_overlay.png` を自動生成します。
この PNG には 2 つの段があります。

- 上段: `setpoint` と各 trial の `measurement`
- 下段: 各 trial の `control_output`

見方の目安:

- `measurement` が早く `setpoint` に近づくほど rise time は短い
- `setpoint` 近傍で早く収束するほど settling time は短い
- `measurement` が `setpoint` を大きく超えるほど overshoot が大きい
- 下段の線が激しく上下するほど control variation が大きい
- 線が上限付近に張り付きやすい trial は saturation を疑う

trial 1 から trial n を重ね描きするので、ベース候補と改善候補の差を 1 枚で比較できます。
`ranking.json` の先頭は、制約達成を優先したうえでの現在の先頭 trial です。
また、`ranking.json` には次も入ります。

- `candidate`
- `overall_pass`
- `failure_count`
- `failed_constraints`
- `acceptable_candidate_found`
- `best_acceptable_trial_index`
- `best_acceptable_score`

見方:

- `best_trial_index` は ranking 先頭の trial
- `best_acceptable_trial_index` は全制約を満たした trial の先頭
- `acceptable_candidate_found=false` の場合は、探索は完了していても fully compliant な候補は未発見
- その場合の `best_trial_index` は、あくまで未達候補の中で相対的に良かったもの
- `trial_xxxx.json` の `candidate_source.rationale.parameter_actions` では `Kp` / `Ki` / `Kd` を `increase` / `decrease` / `keep` で確認できる

## 既知の制約

- `local_ovms` は実接続済みで、trial 1 の `initial_pid` 評価後は trial 2 以降で使う
- `openai_responses` は実接続確認済みだが、API キーと外部ネットワークが必要
- `Qwen3` 系は `<think>` や文字列化された `next_candidate` を返すことがあり、補正と fallback で吸収している
- `--user-instruction` は hard rule より優先されず、trial 1 の `initial_pid` 固定評価も直接は変えない
- `--user-instruction` は `Operator Intent` として扱い、hard rule には勝ちませんが、既定ヒューリスティクスや recent history より優先されるよう prompt を構成している
- `Operator Intent` は単なる末尾メモではなく、system prompt と user prompt の両方で「run contract」として繰り返し強調している
- 飽和判定は brief な初期クリップと sustained saturation を分けており、短い立ち上がり飽和だけでは即失格にしない
- 実機 plant との接続確認は未完

## 未対応事項 / 将来拡張

- `openai_responses` の自動テスト化
- 実機 plant 接続
- 実機 `Vector XL` 異常系の系統検証
- `pyproject.toml` ベースの Python パッケージ管理

## 仕様との差分メモ

- `spec.md` のローカル LLM 前提は、実機検証結果に合わせて `OpenVINO/Qwen3-8B-int4-ov` を第一候補へ更新
- `Qwen3-Coder-30B-A3B-Instruct-int4-ov` は保持するが、現環境では Intel GPU メモリ条件で起動不可
- Plan B として OpenAI API を明示し、設定上は `gpt-5.4` を既定値として扱う
- `prompt_language: en` を既定値にし、英語 prompt 例を README に追加

## Prompt Role Policy

- system prompt is the fixed rule set for the whole tuning run.
- user prompt is the per-trial dynamic context.
- The recent-history window is 10 trials.
- 	rial_xxxx/logs/llm_prompt.txt stores both [System Prompt] and [User Prompt].

### Provider Behavior

- openai_responses
  - 既定では `use_conversation_state: true` です。
  - `use_conversation_state: true` のとき、tuning run 開始時に OpenAI Conversations API へ fixed system prompt を 1 回積みます。
  - その後の各 trial では Responses API に `conversation` を指定し、per-trial user prompt だけを送ります。
  - `use_conversation_state: false` のときは従来どおり Responses API の `instructions` に system prompt を毎回送ります。
- local_ovms
  - 実運用では `use_conversation_state: false` を推奨します。
  - `use_conversation_state: true` のとき、システム側で run 単位の conversation 相当を保持します。
  - trial 1 で固定 system prompt を session に入れ、以後は `system + prior user/assistant + current user` を `messages` として OVMS に送ります。
  - `use_conversation_state: false` のときは従来どおり `system + current user` だけを毎回送ります。
  - `true` は role 分離と run 内経緯保持には有効ですが、trial が進むほど再送メッセージが増え、応答遅延が目立ちやすくなります。

### Current Limitation

- `openai_responses` は run をまたいで conversation を保持しません。新しい tuning run では新しい conversation を作ります。
- `local_ovms` の `use_conversation_state: true` は server-side session ではなく、system 側で会話履歴を保持して毎回再送する実装です。
- そのため role separation と run 内の経緯保持はできますが、OpenAI Conversations API のような API 側 durable conversation ではありません。
- `local_ovms` で trial 後半ほど応答が遅くなる場合は、まず `use_conversation_state: false` を試してください。
- `trial_xxxx.json` の `candidate_source.llm_context` と `trial_xxxx/logs/llm_response.json` に、conversation 利用有無と `conversation_id` を残します。OpenAI では `conversation_id`、OVMS では system-side conversation mode が確認できます。

## Future Prompt Session Work

- OpenAI 側は今後、必要に応じて conversation item の明示管理や compaction を追加できます。
- The main goal is to preserve the role split while reducing prompt reconstruction burden, not to aggressively optimize for token compression first.
