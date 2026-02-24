"""Human simulator for MATTER evaluation.

``HumanSimulator`` accepts any *source* (``QuestionItem``, ``TaskSpec``,
paper ``Path``, or free-text ``str``) and returns a unified
``SimulatedTask`` containing the prompt, expected values, and data-file
references the runner needs.

Prompt verbosity is controlled by the ``difficulty`` setting:

- **1 (minimal)**: bare task, mat_master decides everything.
- **2 (guided)**: method framework but no specific parameters.
- **3 (detailed)**: explicit hints (structure source, scan range, etc.).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evomaster.utils.llm import LLMConfig, create_llm
from evomaster.utils.types import Dialog, SystemMessage, UserMessage

from .schemas import (
    DataFileRef,
    ExpectedResult,
    LLMRuntimeConfig,
    QuestionItem,
    ReferenceAnswer,
    ScoringCheckItem,
    SimulatedTask,
    TaskSpec,
    TouchpointBands,
)


# ---------------------------------------------------------------------------
# Prompt templates -- calc_type -> difficulty level -> template string
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATES: dict[str, dict[int, str]] = {
    "eos": {
        1: "计算 {formula} 的状态方程。输出 {expected_keys}。",
        2: (
            "对 {formula} ({space_group}) 做 EOS 拟合，在平衡体积附近做体积扫描，"
            "拟合 Birch-Murnaghan EOS，输出 {expected_keys}。"
        ),
        3: (
            "对 {formula} ({space_group}, MP ID: {mp_id}) 做 EOS 拟合：从数据库获取结构，"
            "做 7 点体积扫描（0.94–1.06 倍），拟合 B-M EOS，输出 {expected_keys}。"
        ),
    },
    "elastic": {
        1: "计算 {formula} 的弹性常数。输出 {expected_keys}。",
        2: (
            "对 {formula} ({space_group}) 做弹性常数计算，使用有限应变法，"
            "输出 {expected_keys}。"
        ),
        3: (
            "对 {formula} ({space_group}, MP ID: {mp_id}) 做弹性常数计算：从数据库获取结构，"
            "先优化晶格，再施加 ±0.01 应变计算应力张量，拟合弹性常数，"
            "输出 {expected_keys}。"
        ),
    },
    "surface": {
        1: "计算 {formula} 的表面能。输出 {expected_keys}。",
        2: (
            "对 {formula} ({space_group}) 计算表面能：先优化体相获取单原子能量，"
            "再切出 slab 模型加真空层，优化后计算表面能，输出 {expected_keys}。"
        ),
        3: (
            "对 {formula} ({space_group}, MP ID: {mp_id}) 计算表面能：从数据库获取结构，"
            "优化体相，切出至少 5 层 slab + 15 A 真空，优化 slab，"
            "按 γ = (E_slab - N·E_bulk)/(2A) 计算，输出 {expected_keys}。"
        ),
    },
    "defect": {
        1: "计算 {formula} 的空位形成能。输出 {expected_keys}。",
        2: (
            "对 {formula} ({space_group}) 计算空位形成能：构建超胞，移除一个原子，"
            "优化后计算形成能，输出 {expected_keys}。"
        ),
        3: (
            "对 {formula} ({space_group}, MP ID: {mp_id}) 计算空位形成能：从数据库获取结构，"
            "构建 3×3×3 超胞，移除一个原子，优化离子位置，"
            "按 E_f = E_defect - (N-1)/N · E_perfect 计算，输出 {expected_keys}。"
        ),
    },
    "band": {
        1: "计算 {formula} 的能带结构和带隙。输出 {expected_keys}。",
        2: (
            "对 {formula} ({space_group}) 做能带结构计算：先做 SCF，"
            "再沿高对称路径做 NSCF 计算，输出 {expected_keys}。"
        ),
        3: (
            "对 {formula} ({space_group}, MP ID: {mp_id}) 做能带结构计算：从数据库获取结构，"
            "先优化 + SCF，再沿 Brillouin 区高对称路径做非自洽计算，"
            "输出 {expected_keys}。"
        ),
    },
    "dos": {
        1: "计算 {formula} 的态密度。输出 {expected_keys}。",
        2: (
            "对 {formula} ({space_group}) 做态密度计算：先做 SCF，"
            "再用加密 k 网格做 NSCF，输出 {expected_keys}。"
        ),
        3: (
            "对 {formula} ({space_group}, MP ID: {mp_id}) 做态密度计算：从数据库获取结构，"
            "先优化，加密 k 网格后做 NSCF 计算态密度，输出 {expected_keys}。"
        ),
    },
    "phonon": {
        1: "计算 {formula} 的声子色散。输出 {expected_keys}。",
        2: (
            "对 {formula} ({space_group}) 做声子色散计算：构建超胞，"
            "使用有限位移法计算力常数矩阵，输出 {expected_keys}。"
        ),
        3: (
            "对 {formula} ({space_group}, MP ID: {mp_id}) 做声子色散计算：从数据库获取结构，"
            "构建 2×2×2 超胞，施加 0.01 A 位移计算力常数，"
            "用 Phonopy 做后处理，输出 {expected_keys}。"
        ),
    },
    "md": {
        1: "对 {formula} 做分子动力学模拟。输出 {expected_keys}。",
        2: (
            "对 {formula} ({space_group}) 做 MD 模拟：构建超胞，"
            "在合适温度下做 NVT 系综模拟，输出 {expected_keys}。"
        ),
        3: (
            "对 {formula} ({space_group}, MP ID: {mp_id}) 做 MD 模拟：从数据库获取结构，"
            "构建超胞，选择合适的势函数，在目标温度下做 NVT 模拟至少 10 ps，"
            "输出 {expected_keys}。"
        ),
    },
    "neb": {
        1: "计算 {formula} 中的迁移能垒。输出 {expected_keys}。",
        2: (
            "对 {formula} ({space_group}) 做 NEB 计算：构建初态和终态，"
            "插值过渡态图像，优化路径后输出 {expected_keys}。"
        ),
        3: (
            "对 {formula} ({space_group}, MP ID: {mp_id}) 做 NEB 计算：从数据库获取结构，"
            "构建超胞，设定初末态，插入 5 个图像，优化 MEP，"
            "输出 {expected_keys}。"
        ),
    },
    "adsorption": {
        1: "计算 {formula} 表面的吸附能。输出 {expected_keys}。",
        2: (
            "对 {formula} ({space_group}) 表面做吸附能计算：优化 slab 和分子，"
            "放置吸附物在表面上优化，按 E_ads = E_total - E_slab - E_mol 计算，"
            "输出 {expected_keys}。"
        ),
        3: (
            "对 {formula} ({space_group}, MP ID: {mp_id}) 表面做吸附能计算：从数据库获取结构，"
            "切出表面 slab，优化体相/slab/分子，在多个吸附位放置吸附物，"
            "优化后取最稳定构型，输出 {expected_keys}。"
        ),
    },
    "formation_energy": {
        1: "计算 {formula} 的形成能。输出 {expected_keys}。",
        2: (
            "对 {formula} ({space_group}) 计算形成能：优化化合物和所有参考元素单质结构，"
            "按定义计算形成能，输出 {expected_keys}。"
        ),
        3: (
            "对 {formula} ({space_group}, MP ID: {mp_id}) 计算形成能：从数据库获取结构，"
            "优化化合物，对每个组分元素优化其稳定单质相，"
            "按 ΔH_f = E_compound - Σ n_i·μ_i 计算，输出 {expected_keys}。"
        ),
    },
    "magnetic": {
        1: "计算 {formula} 的磁性。输出 {expected_keys}。",
        2: (
            "对 {formula} ({space_group}) 做自旋极化计算：打开自旋极化，"
            "设定合理初始磁矩，优化后输出 {expected_keys}。"
        ),
        3: (
            "对 {formula} ({space_group}, MP ID: {mp_id}) 做自旋极化计算：从数据库获取结构，"
            "打开自旋极化，对磁性原子设定初始磁矩（如 Fe ~4 μ_B），"
            "优化结构后输出 {expected_keys}。"
        ),
    },
}


_REWRITE_SYSTEM_PROMPT = """\
You are a human scientist asking a question to an AI research assistant.
Rewrite the seed prompt to look like natural human wording while preserving:
1) physics goal
2) observable or deliverable
3) required accuracy/constraints
4) method class constraints

Output STRICT JSON:
{"prompt": "<rewritten prompt>"}
Do not add any extra text.
"""


# ---------------------------------------------------------------------------
# Rubric used when converting TaskSpec -> QuestionItem
# ---------------------------------------------------------------------------

_LIT_RUBRIC_ID = "R_LIT_001"


# ---------------------------------------------------------------------------
# HumanSimulator
# ---------------------------------------------------------------------------


class HumanSimulator:
    """Simulates a human researcher who reads, decides, and instructs.

    Accepts any *source* via :meth:`formulate` and returns a
    :class:`SimulatedTask` that bundles prompt, expected values, and
    data-file references.
    """

    def __init__(
        self,
        llm_cfg: LLMRuntimeConfig | None = None,
        difficulty: int = 1,
        papers_dir: Path | None = None,
        *,
        use_seed_prompt: bool = True,
    ):
        self._llm = None
        if llm_cfg is not None:
            cfg = LLMConfig(
                provider=llm_cfg.provider,
                model=llm_cfg.model,
                api_key=llm_cfg.api_key,
                base_url=llm_cfg.base_url,
                temperature=llm_cfg.temperature,
                max_tokens=llm_cfg.max_tokens,
                timeout=llm_cfg.timeout,
            )
            self._llm = create_llm(cfg, output_config={"show_in_console": False, "log_to_file": False})
        self._difficulty = max(1, min(difficulty, 3))
        self._papers_dir = papers_dir
        self._use_seed_prompt = use_seed_prompt

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def formulate(
        self,
        source: QuestionItem | TaskSpec | Path | str,
        *,
        hint: str = "",
    ) -> SimulatedTask:
        """Simulate a human formulating a research task.

        Parameters
        ----------
        source:
            - ``QuestionItem``  -- existing evaluation question (backward compat).
            - ``TaskSpec``      -- lightweight literature task specification.
            - ``Path``          -- paper PDF; planner pre-check will parse it.
            - ``str``           -- free-text prompt passthrough.
        hint:
            Optional focus hint for paper-based sources (e.g. ``"Si EOS"``).
        """
        if isinstance(source, QuestionItem):
            return self._from_question(source)
        if isinstance(source, TaskSpec):
            return self._from_spec(source)
        if isinstance(source, Path):
            return self._from_paper(source, hint)
        return self._from_text(str(source))

    def render_prompt(self, question: QuestionItem) -> str:
        """Drop-in replacement for ``SingleTurnSimulator.render_prompt``."""
        return self.formulate(question).prompt

    # ------------------------------------------------------------------
    # Backward-compat alias
    # ------------------------------------------------------------------

    def _from_question(self, q: QuestionItem) -> SimulatedTask:
        prompt = q.human_prompt_seed
        if not self._use_seed_prompt and self._llm is not None:
            prompt = self._rewrite_prompt(q) or prompt
        expected = [
            ExpectedResult(
                key=r.key,
                value=float(r.value) if isinstance(r.value, (int, float)) else 0.0,
                tolerance=r.tolerance or 0.0,
                unit=r.unit,
            )
            for r in q.reference_answers
            if isinstance(r.value, (int, float))
        ]
        return SimulatedTask(
            prompt=prompt,
            expected=expected,
            data_files=list(q.data_files),
            question=q,
        )

    # ------------------------------------------------------------------
    # TaskSpec -> SimulatedTask
    # ------------------------------------------------------------------

    def _from_spec(self, spec: TaskSpec) -> SimulatedTask:
        prompt = self._generate_prompt(spec)
        data_files = self._discover_data_files(spec)
        return SimulatedTask(
            prompt=prompt,
            expected=list(spec.expected),
            data_files=data_files,
            spec=spec,
        )

    def _generate_prompt(self, spec: TaskSpec) -> str:
        level = min(max(spec.difficulty, 1), 3)
        templates = _PROMPT_TEMPLATES.get(spec.calc_type, {})
        template = templates.get(level)
        if template:
            return template.format(**spec.template_vars())
        keys = ", ".join(e.key for e in spec.expected)
        return f"计算 {spec.formula} 的 {spec.calc_type}。输出: {keys}。"

    def _discover_data_files(self, spec: TaskSpec) -> list[DataFileRef]:
        files: list[DataFileRef] = []
        if self._papers_dir and spec.paper_id:
            matches = sorted(self._papers_dir.glob(f"{spec.paper_id}_*.pdf"))
            if matches:
                files.append(
                    DataFileRef(
                        key="paper",
                        path=str(matches[0]),
                        description=f"Paper {spec.paper_id}",
                    )
                )
        if spec.cif_path:
            files.append(
                DataFileRef(
                    key="structure",
                    path=spec.cif_path,
                    description=f"Structure file for {spec.formula}",
                )
            )
        return files

    # ------------------------------------------------------------------
    # Paper PDF -> SimulatedTask (planner reads the paper autonomously)
    # ------------------------------------------------------------------

    def _from_paper(self, pdf_path: Path, hint: str) -> SimulatedTask:
        prompt = f"请阅读工作目录中的论文 {pdf_path.name}，"
        if hint:
            prompt += f"复现其中关于 {hint} 的核心计算结果。"
        else:
            prompt += "识别并复现其中 1-2 个最核心的可计算结果。"
        prompt += "\n输出每个结果的数值和单位。"
        data_files = [DataFileRef(key="paper", path=str(pdf_path))]
        return SimulatedTask(prompt=prompt, expected=[], data_files=data_files)

    # ------------------------------------------------------------------
    # Free text passthrough
    # ------------------------------------------------------------------

    def _from_text(self, text: str) -> SimulatedTask:
        return SimulatedTask(prompt=text, expected=[])

    # ------------------------------------------------------------------
    # LLM-based prompt rewriting (inherited from SingleTurnSimulator)
    # ------------------------------------------------------------------

    def _rewrite_prompt(self, question: QuestionItem) -> str:
        if self._llm is None:
            return question.human_prompt_seed
        dialog = Dialog(
            messages=[
                SystemMessage(content=_REWRITE_SYSTEM_PROMPT),
                UserMessage(
                    content=(
                        f"Question intent:\n{question.intent}\n\n"
                        f"Seed prompt:\n{question.human_prompt_seed}\n\n"
                        "Return JSON only."
                    )
                ),
            ],
            tools=[],
        )
        try:
            reply = self._llm.query(dialog)
            payload = _parse_json_payload(reply.content or "")
            prompt = str(payload.get("prompt", "")).strip()
            return prompt or question.human_prompt_seed
        except Exception:
            return question.human_prompt_seed

    # ------------------------------------------------------------------
    # TaskSpec -> QuestionItem conversion (for MATTER pipeline compat)
    # ------------------------------------------------------------------

    def spec_to_question(self, spec: TaskSpec) -> QuestionItem:
        """Build a full ``QuestionItem`` from a ``TaskSpec`` so it can be
        fed directly into the existing MATTER runner/evaluator pipeline.
        """
        prompt = self._generate_prompt(spec)
        data_files = self._discover_data_files(spec)

        ref_answers: list[ReferenceAnswer] = [
            ReferenceAnswer(key=e.key, value=e.value, tolerance=e.tolerance, unit=e.unit)
            for e in spec.expected
        ]

        num = max(len(spec.expected), 1)
        checklist: list[ScoringCheckItem] = [
            ScoringCheckItem(
                id=e.key,
                criterion=f"{e.key} is within tolerance.",
                weight=round(1.0 / num, 3),
                verify="numerical_range",
            )
            for e in spec.expected
        ]

        tags = [f"calc_{spec.calc_type}", f"paper_{spec.paper_id}"]
        tags.extend(spec.tags)

        return QuestionItem(
            id=f"LIT_{spec.id}",
            level="L3",
            rubric_id=_LIT_RUBRIC_ID,
            intent=(
                f"Reproduce {spec.calc_type} calculation for {spec.formula} "
                f"from paper {spec.paper_id}."
            ),
            human_prompt_seed=prompt,
            tags=tags,
            mode_scope=["planner"],
            data_files=data_files,
            reference_answers=ref_answers,
            scoring_checklist=checklist,
            touchpoints=TouchpointBands(
                full=[f"All {spec.calc_type} target values within tolerance."],
                partial=[f"{spec.calc_type.capitalize()} workflow completed but values outside tolerance."],
                fail=["Calculation failed or produced physically inconsistent results."],
            ),
        )


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

SingleTurnSimulator = HumanSimulator


class MultiTurnSimulator:
    """Reserved extension point for multi-turn simulation."""

    def __init__(self) -> None:
        self.enabled = False

    def run(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("MultiTurnSimulator is reserved for future iterations.")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _parse_json_payload(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return json.loads(stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return json.loads(stripped[start : end + 1])
    raise ValueError("No JSON object found in simulator output")
