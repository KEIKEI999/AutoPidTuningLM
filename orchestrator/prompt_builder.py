from __future__ import annotations

from orchestrator.models import ConfigBundle, PIDGains, TrialRecord


RECENT_HISTORY_WINDOW = 10


class PromptBuilder:
    def build_system_prompt(self, bundle: ConfigBundle) -> str:
        if bundle.llm.prompt_language == "ja":
            return "\n".join(
                [
                    "あなたは PID チューニング候補を返す自動エージェントです。",
                    "system prompt は固定ルール、user prompt は各 trial の状況です。",
                    "固定ルールを最優先してください。",
                    "出力は JSON オブジェクト 1 個のみです。",
                    "Markdown や説明文を JSON の外に出してはいけません。",
                    "JSON には mode, next_candidate, expectation, explanation, rationale を必ず含めてください。",
                    "next_candidate は数値の Kp, Ki, Kd を持つ object にしてください。",
                    "rationale は observed_issue, parameter_actions, expected_tradeoff, risk を持つ object にしてください。",
                    "過去に使った Kp, Ki, Kd の組み合わせを再利用してはいけません。",
                    "制約違反や重複を正当化してはいけません。",
                    "chain-of-thought は出さず、短い explanation と構造化 rationale のみ返してください。",
                ]
            )
        return "\n".join(
            [
                "You are a PID tuning candidate generator.",
                "The system prompt contains fixed rules. The user prompt contains only current trial context.",
                "Follow the fixed rules with highest priority.",
                "If an Operator Intent section is present in the user prompt, treat it as the highest-priority run-specific tuning intent.",
                "Treat Operator Intent as a run contract, not as an optional suggestion.",
                "Apply Operator Intent unless it conflicts with hard safety rules, JSON schema, numeric limits, or duplicate-candidate prohibition.",
                "If Operator Intent conflicts with recent-history heuristics or the default conservative tendency, prefer Operator Intent.",
                "Do not silently weaken or dilute Operator Intent into a conservative move.",
                "If Operator Intent asks for aggressive exploration, larger gain moves, or intentional overshoot before later stabilization, honor that direction unless a hard constraint blocks it.",
                "When Operator Intent asks for a stronger move, do not return a tiny near-best adjustment unless limits or duplicate-candidate rules force it.",
                "Return exactly one JSON object and nothing else.",
                "Do not return markdown or prose outside JSON.",
                "The JSON must contain mode, next_candidate, expectation, explanation, rationale.",
                "next_candidate must be an object with numeric Kp, Ki, Kd.",
                "rationale must be an object with observed_issue, parameter_actions, expected_tradeoff, risk.",
                "Never repeat any previously used PID candidate.",
                "Do not justify violating limits or duplicate-candidate rules.",
                "When Operator Intent exists, explanation and rationale.expected_tradeoff must briefly reflect how you applied it.",
                "Do not include chain-of-thought. Return only a short explanation and a structured rationale.",
            ]
        )

    def build_candidate_prompt(
        self,
        bundle: ConfigBundle,
        history: list[TrialRecord],
        best_candidate: PIDGains,
        best_score: float,
        trial_index: int,
    ) -> str:
        if bundle.llm.prompt_language == "ja":
            return self._build_japanese_user_prompt(bundle, history, best_candidate, best_score, trial_index)
        return self._build_english_user_prompt(bundle, history, best_candidate, best_score, trial_index)

    def build_prompt_log_text(self, system_prompt: str, user_prompt: str) -> str:
        return "\n".join(
            [
                "[System Prompt]",
                system_prompt,
                "",
                "[User Prompt]",
                user_prompt,
            ]
        )

    def _build_recent_and_used_lines(
        self,
        history: list[TrialRecord],
    ) -> tuple[list[str], list[str]]:
        recent = history[-RECENT_HISTORY_WINDOW:]
        recent_lines: list[str] = []
        used_lines: list[str] = []
        seen_candidates: set[tuple[float, float, float]] = set()
        for index, item in enumerate(recent, start=1):
            metrics = item.metrics
            recent_lines.append(
                f"{index}) "
                f"Kp={item.candidate.kp:.4f} Ki={item.candidate.ki:.4f} Kd={item.candidate.kd:.4f} "
                f"rise={metrics.get('rise_time', 0):.3f} "
                f"settling={metrics.get('settling_time', 0):.3f} "
                f"overshoot={metrics.get('overshoot', 0):.3f} "
                f"sse={metrics.get('steady_state_error', 0):.6f} "
                f"oscillation={metrics.get('oscillation', False)} "
                f"divergence={metrics.get('divergence', False)} "
                f"saturation={metrics.get('saturation', False)} "
                f"score={metrics.get('score', 0):.6f}"
            )
        for item in history:
            key = (item.candidate.rounded().kp, item.candidate.rounded().ki, item.candidate.rounded().kd)
            if key in seen_candidates:
                continue
            seen_candidates.add(key)
            used_lines.append(
                f"- Kp={item.candidate.kp:.6f}, Ki={item.candidate.ki:.6f}, Kd={item.candidate.kd:.6f}"
            )
        return recent_lines or ["No previous trials."], used_lines or ["- No previous candidates."]

    def _build_english_user_prompt(
        self,
        bundle: ConfigBundle,
        history: list[TrialRecord],
        best_candidate: PIDGains,
        best_score: float,
        trial_index: int,
    ) -> str:
        recent_lines, used_lines = self._build_recent_and_used_lines(history)
        remaining_trials = max(bundle.trial.max_trials - trial_index + 1, 0)
        operator_lines = []
        if bundle.user_instruction:
            operator_lines = [
                "",
                "[Operator Intent]",
                bundle.user_instruction,
                "",
                "[Operator Intent Priority]",
                "- Treat this instruction as the highest-priority run-specific tuning direction.",
                "- Treat it as a run contract, not as a weak suggestion.",
                "- Follow it unless it conflicts with hard safety constraints, JSON schema, PID limits, or duplicate-candidate prohibition.",
                "- If it conflicts with recent-history heuristics, prefer Operator Intent.",
                "- Do not silently soften it into a conservative move.",
                "- If it asks for aggressive exploration or a larger parameter swing, make a meaningfully stronger move unless a hard constraint blocks it.",
            ]
        return "\n".join(
            [
                "Current trial context for PID tuning.",
                "Apply the fixed system rules. Use this prompt only as current context.",
                "",
                "[Targets]",
                f"- rise_time <= {bundle.target.rise_time_max}",
                f"- settling_time <= {bundle.target.settling_time_max}",
                f"- overshoot <= {bundle.target.overshoot_max}",
                f"- steady_state_error <= {bundle.target.steady_state_error_max}",
                f"- allow_oscillation = {bundle.target.allow_oscillation}",
                f"- allow_divergence = {bundle.target.allow_divergence}",
                f"- allow_saturation = {bundle.target.allow_saturation}",
                "",
                "[PID Limits]",
                f"- Kp: {bundle.limits.kp_min} ~ {bundle.limits.kp_max}",
                f"- Ki: {bundle.limits.ki_min} ~ {bundle.limits.ki_max}",
                f"- Kd: {bundle.limits.kd_min} ~ {bundle.limits.kd_max}",
                "",
                "[Current Best Candidate]",
                f"- Kp = {best_candidate.kp:.6f}",
                f"- Ki = {best_candidate.ki:.6f}",
                f"- Kd = {best_candidate.kd:.6f}",
                f"- score = {best_score:.6f}",
                *operator_lines,
                "",
                "[Used Candidates: Never Repeat]",
                *used_lines,
                "",
                "[Recent Trial History]",
                f"- showing last {min(len(history), RECENT_HISTORY_WINDOW)} of {len(history)} trials",
                *recent_lines,
                "",
                "[Required Output Format]",
                "- mode must be coarse or fine",
                "- next_candidate must contain numeric Kp, Ki, Kd",
                "- rationale.parameter_actions must contain Kp, Ki, Kd with increase, decrease, or keep",
                "- next_candidate must stay inside limits",
                "- next_candidate must not match any entry in [Used Candidates: Never Repeat]",
                "- explanation must be short and <= 100 characters",
                "- if [Operator Intent] exists, explanation must briefly state how the candidate follows it",
                "- if [Operator Intent] exists, rationale.expected_tradeoff must reflect that intent",
                "- if [Operator Intent] asks for aggressive exploration or larger gains, prefer a meaningfully stronger move over a tiny safe adjustment unless blocked by a hard constraint",
                f"- current_trial = {trial_index}",
                f"- max_trials = {bundle.trial.max_trials}",
                f"- remaining_trials = {remaining_trials}",
                "",
                "[Few-shot Example: Valid]",
                '{"mode":"fine","next_candidate":{"Kp":0.62,"Ki":0.08,"Kd":0.02},"expectation":"rise_too_slow","explanation":"Increase Kp and Ki slightly to follow the aggressive early exploration intent.","rationale":{"observed_issue":"rise_too_slow","parameter_actions":{"Kp":"increase","Ki":"increase","Kd":"keep"},"expected_tradeoff":"Faster rise with some overshoot risk, matching the aggressive exploration intent.","risk":"overshoot_or_saturation"}}',
                "",
                "[Few-shot Example: Invalid, do not do this]",
                '{"mode":"fine","next_candidate":"Kp=0.25 Ki=0.10 Kd=0.00","expectation":"bad_format","explanation":"next_candidate must not be a string","rationale":{"observed_issue":"bad_format","parameter_actions":{"Kp":"keep","Ki":"keep","Kd":"keep"},"expected_tradeoff":"invalid","risk":"invalid"}}',
            ]
        )

    def _build_japanese_user_prompt(
        self,
        bundle: ConfigBundle,
        history: list[TrialRecord],
        best_candidate: PIDGains,
        best_score: float,
        trial_index: int,
    ) -> str:
        recent_lines, used_lines = self._build_recent_and_used_lines(history)
        remaining_trials = max(bundle.trial.max_trials - trial_index + 1, 0)
        operator_lines = []
        if bundle.user_instruction:
            operator_lines = [
                "",
                "[運用者の追加方針]",
                bundle.user_instruction,
            ]
        return "\n".join(
            [
                "PID チューニングの現在 trial の状況です。",
                "固定ルールは system prompt にあります。この user prompt は現況だけです。",
                "",
                "[目標]",
                f"- rise_time <= {bundle.target.rise_time_max}",
                f"- settling_time <= {bundle.target.settling_time_max}",
                f"- overshoot <= {bundle.target.overshoot_max}",
                f"- steady_state_error <= {bundle.target.steady_state_error_max}",
                f"- allow_oscillation = {bundle.target.allow_oscillation}",
                f"- allow_divergence = {bundle.target.allow_divergence}",
                f"- allow_saturation = {bundle.target.allow_saturation}",
                "",
                "[PID 制約]",
                f"- Kp: {bundle.limits.kp_min} ~ {bundle.limits.kp_max}",
                f"- Ki: {bundle.limits.ki_min} ~ {bundle.limits.ki_max}",
                f"- Kd: {bundle.limits.kd_min} ~ {bundle.limits.kd_max}",
                "",
                "[現在の最良候補]",
                f"- Kp = {best_candidate.kp:.6f}",
                f"- Ki = {best_candidate.ki:.6f}",
                f"- Kd = {best_candidate.kd:.6f}",
                f"- score = {best_score:.6f}",
                *operator_lines,
                "",
                "[既出候補: 再利用禁止]",
                *used_lines,
                "",
                "[最近の trial 履歴]",
                f"- 直近 {min(len(history), RECENT_HISTORY_WINDOW)} 件 / 全 {len(history)} 件を表示",
                *recent_lines,
                "",
                "[出力条件]",
                "- mode は coarse または fine",
                "- next_candidate は数値の Kp, Ki, Kd を持つ object",
                "- rationale.parameter_actions は Kp, Ki, Kd を持ち、increase / decrease / keep のいずれか",
                "- next_candidate は PID 制約内に置く",
                "- next_candidate は既出候補と一致してはいけない",
                "- explanation は短く 100 文字以内",
                f"- current_trial = {trial_index}",
                f"- max_trials = {bundle.trial.max_trials}",
                f"- remaining_trials = {remaining_trials}",
                "",
                "[良い例]",
                '{"mode":"fine","next_candidate":{"Kp":0.62,"Ki":0.08,"Kd":0.02},"expectation":"rise_too_slow","explanation":"Kp と Ki を少し上げて立ち上がりを速める。","rationale":{"observed_issue":"rise_too_slow","parameter_actions":{"Kp":"increase","Ki":"increase","Kd":"keep"},"expected_tradeoff":"立ち上がりは速くなるがオーバーシュートのリスクが少し増える。","risk":"overshoot_or_saturation"}}',
                "",
                "[悪い例]",
                '{"mode":"fine","next_candidate":"Kp=0.25 Ki=0.10 Kd=0.00","expectation":"bad_format","explanation":"next_candidate を文字列にしてはいけない","rationale":{"observed_issue":"bad_format","parameter_actions":{"Kp":"keep","Ki":"keep","Kd":"keep"},"expected_tradeoff":"invalid","risk":"invalid"}}',
            ]
        )
