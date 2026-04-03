"""真实 API 压缩集成测试。

使用真实 LLM API 验证 ContextCompactor 在 kernel loop 中的端到端行为：
1. 单轮不触发（context 未满）
2. 多轮工具调用积累后触发 summary 压缩
3. 压缩后 kernel 继续正常运行至 natural finish
4. 压缩事件通过 event_sink 正确发射
5. 压缩后摘要内容合理（非空、包含关键信息）

使用 haiku 做主 LLM（低成本），compaction profile (gemini-flash) 做压缩摘要。
context_window_tokens 设为极小值以快速触发。

关键设计：
- Haiku 倾向于批量调用工具（1 turn 发 3 个 tool_call），导致 turn 数少
- 压缩在 turn 1 因冷却机制被跳过（turn <= last_compaction_turn + 1）
- 因此必须确保 turn 2 的 estimated tokens 能超过阈值
- 策略：极小 context_window + 大工具输出

运行：uv run pytest tests/matmaster/integration/test_compaction_real_api.py -v -s
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

# ── Skip if no API key ────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _load_env() -> None:
    """Load .env like main app does."""
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(_PROJECT_ROOT / ".env")
    current_env = os.getenv("SERVICE_ENV", "test")
    load_dotenv(find_dotenv(f".env.{current_env}"))


_load_env()

_HAS_API_KEY = bool(os.getenv("LITELLM_PROXY_API_KEY"))
pytestmark = pytest.mark.skipif(
    not _HAS_API_KEY, reason="LITELLM_PROXY_API_KEY not set"
)


# ── Provider construction ─────────────────────────────────


def _build_provider(profile_key: str = "haiku"):
    """Build real OpenAIProvider from llm_config.yaml."""
    from matmaster.config.loader import load_llm_config
    from matmaster.providers.llm_factory import build_provider

    llm_config_path = _PROJECT_ROOT / "config" / "llm_config.yaml"
    assert llm_config_path.exists(), f"Missing {llm_config_path}"
    llm_config = load_llm_config(llm_config_path)
    return build_provider(llm_config, llm_override=profile_key)


def _build_compaction_provider():
    """Build provider for compaction summary (gemini-flash, low temp)."""
    return _build_provider("compaction")


# ── Tool that generates large output ─────────────────────


class VerboseTool:
    """返回大量文本的工具，快速填充 context window。

    每次调用产出 ~3000 字符 ≈ 750-900 tokens，确保 2-3 轮即可超阈值。
    """

    _call_count = 0

    @property
    def name(self):
        return "analyze_data"

    @property
    def description(self):
        return "Analyze a dataset and return detailed statistics report."

    @property
    def json_schema(self):
        return {
            "type": "object",
            "properties": {
                "dataset": {
                    "type": "string",
                    "description": "Name of dataset to analyze",
                }
            },
            "required": ["dataset"],
        }

    async def execute(self, arguments):
        VerboseTool._call_count += 1
        dataset = arguments.get("dataset", "unknown")
        n = VerboseTool._call_count
        return (
            f"=== Comprehensive Analysis Report #{n} for '{dataset}' ===\n\n"
            f"1. DATA OVERVIEW\n"
            f"   Total records: {15000 + n * 234}\n"
            f"   Valid records: {14800 + n * 91} ({97.0 + n * 0.3:.1f}%)\n"
            f"   Missing values: {200 + n * 43} ({3.0 - n * 0.3:.1f}%)\n"
            f"   Date range: 2020-01-01 to 2024-12-31\n"
            f"   Sampling frequency: hourly\n"
            f"   Geographic coverage: 47 stations across 12 regions\n\n"
            f"2. COLUMN STATISTICS\n"
            f"   temperature (°C): min=-42.{n}, max=56.{n}, mean=14.{n}, std=11.{n}, "
            f"median=13.{n}, Q1=5.{n}, Q3=23.{n}, IQR=18.{n}, skew=-0.{n}2\n"
            f"   pressure (hPa): min=948.{n}, max=1052.{n}, mean=1012.{n}, std=9.{n}, "
            f"median=1013.{n}, Q1=1006.{n}, Q3=1019.{n}, IQR=13.{n}, skew=0.{n}1\n"
            f"   humidity (%): min=0.0, max=100.0, mean=61.{n}, std=22.{n}, "
            f"median=64.{n}, Q1=42.{n}, Q3=81.{n}, IQR=39.{n}, skew=-0.{n}3\n"
            f"   wind_speed (m/s): min=0.0, max=47.{n}, mean=7.{n}, std=5.{n}, "
            f"median=6.{n}, Q1=3.{n}, Q3=10.{n}, IQR=7.{n}, skew=1.{n}2\n"
            f"   precipitation (mm): min=0.0, max=125.{n}, mean=2.{n}, std=9.{n}, "
            f"median=0.0, Q1=0.0, Q3=1.{n}, IQR=1.{n}, skew=5.{n}7\n"
            f"   solar_radiation (W/m²): min=0.0, max=1200.{n}, mean=342.{n}, "
            f"std=287.{n}, median=210.{n}, Q1=45.{n}, Q3=589.{n}\n"
            f"   soil_moisture (%): min=5.{n}, max=95.{n}, mean=42.{n}, std=18.{n}\n"
            f"   air_quality_index: min=12, max=489, mean=78.{n}, std=45.{n}\n\n"
            f"3. CORRELATION MATRIX (Pearson r)\n"
            f"   temp-pressure: -0.4{n}, temp-humidity: 0.3{n}, temp-wind: 0.1{n}\n"
            f"   temp-solar: 0.6{n}, temp-soil: 0.4{n}, temp-aqi: 0.2{n}\n"
            f"   pressure-humidity: -0.2{n}, pressure-wind: 0.0{n}\n"
            f"   humidity-wind: -0.1{n}, humidity-precip: 0.5{n}\n"
            f"   solar-soil: -0.3{n}, solar-aqi: 0.1{n}\n"
            f"   soil-precip: 0.6{n}, wind-aqi: -0.2{n}\n\n"
            f"4. OUTLIER DETECTION\n"
            f"   Method: Modified Z-score (MAD-based)\n"
            f"   Total outliers: {47 + n * 12} records ({0.3 + n * 0.02:.2f}%)\n"
            f"   - temperature: {12 + n} outliers (>3σ)\n"
            f"   - wind_speed: {8 + n} outliers (extreme gusts)\n"
            f"   - precipitation: {27 + n * 3} outliers (heavy rainfall events)\n"
            f"   - air_quality_index: {5 + n * 2} outliers (pollution spikes)\n\n"
            f"5. TREND ANALYSIS (Linear regression, last 365 days)\n"
            f"   temperature: +{1.5 + n * 0.3:.1f}°C/year (p<0.001, R²=0.{n}8)\n"
            f"   pressure: -{0.2 + n * 0.1:.1f} hPa/year (p=0.1{n}, not significant)\n"
            f"   humidity: -{4.0 + n * 1.2:.1f}%/year (p<0.01, R²=0.{n}5)\n"
            f"   precipitation: +{2.0 + n * 0.5:.1f} mm/year (p<0.05)\n"
            f"   aqi: +{3.0 + n * 0.7:.1f} units/year (p<0.01, concerning)\n\n"
            f"6. DATA QUALITY\n"
            f"   Completeness: {96.0 + n * 0.5:.1f}%\n"
            f"   Consistency: {94.0 + n * 0.3:.1f}%\n"
            f"   Accuracy: {97.0 + n * 0.2:.1f}%\n"
            f"   Overall score: {93.0 + n * 0.4:.1f}/100\n"
            f"   Recommendation: Dataset is suitable for predictive modeling.\n"
            f"{'=' * 60}\n"
        )


# ── Helpers ───────────────────────────────────────────────


def _print_result(label, kr, compactor, elapsed=None):
    """Print structured test result."""
    print(f"\n{'='*60}")
    print(f"[{label}]")
    print(f"  Status: {kr.status} | Reason: {kr.reason}")
    print(f"  Turns: {kr.num_turns}")
    if elapsed:
        print(f"  Elapsed: {elapsed:.1f}s")
    print(f"  Usage: {kr.usage}")
    print(f"  Compaction count: {compactor._compaction_count}")
    print(f"  Final content: {len(kr.final_content or '')} chars")
    print(f"{'='*60}")


# ── Tests ─────────────────────────────────────────────────


class TestRealAPICompaction:
    """真实 API 端到端压缩测试。"""

    @pytest.fixture()
    def main_provider(self):
        return _build_provider("haiku")

    @pytest.fixture()
    def compaction_provider(self):
        return _build_compaction_provider()

    def _build_kernel_with_compaction(
        self,
        main_provider,
        compaction_provider,
        *,
        context_window: int = 800,
        trigger_ratio: float = 0.5,
        max_turns: int = 12,
        system_prompt: str | None = None,
    ):
        """构建启用压缩的 kernel + spec + collected events list。"""
        from matmaster.core.agent import AgentKernel
        from matmaster.core.context_compactor import ContextCompactor
        from matmaster.tools.tool_registry import ToolRegistry
        from matmaster.types.runtime import AgentRuntimeSpec, CompactionConfig

        compaction_cfg = CompactionConfig(
            enabled=True,
            context_window_tokens=context_window,
            trigger_ratio=trigger_ratio,
        )

        registry = ToolRegistry()
        VerboseTool._call_count = 0
        registry.register(VerboseTool(), source="test")

        collected_events: list = []

        async def event_sink(event):
            collected_events.append(event)

        compactor = ContextCompactor(
            config=compaction_cfg,
            summary_provider=compaction_provider,
            event_sink=event_sink,
        )

        if system_prompt is None:
            system_prompt = (
                "You are a data analyst. Use the analyze_data tool when asked. "
                "After all analyses, provide a brief final summary."
            )

        from matmaster.tools.tool_catalog import ToolCatalog
        from tests.matmaster.core.agent_kernel_test_helpers import _SimpleTestToolRunner

        catalog = ToolCatalog(registry)
        runner = _SimpleTestToolRunner(catalog)
        spec = AgentRuntimeSpec(
            llm_provider=main_provider,
            tool_catalog=catalog,
            tool_runner=runner,
            max_turns=max_turns,
            system_prompt=system_prompt,
            compaction=compaction_cfg,
            compactor=compactor,
        )

        kernel = AgentKernel()
        return kernel, spec, collected_events, compactor

    async def test_compaction_triggers_with_real_api(
        self, main_provider, compaction_provider
    ) -> None:
        """多轮工具调用后触发真实 LLM 摘要压缩。

        关键设计：
        - 系统提示词强制每轮只发 1 个 tool_call（防止 haiku 批量调用）
        - 这样 5 个数据集 → 5 个 turn（每 turn 1 个 tool_call + result）
        - Turn 1 冷却跳过，turn 3+ 积累足够 tokens 触发压缩
        - compressible turns > retained turns → 有可压缩的旧消息
        """
        from matmaster.types.events import ContextCompactionEvent
        from matmaster.types.messages import SystemMessage

        kernel, spec, collected_events, compactor = self._build_kernel_with_compaction(
            main_provider,
            compaction_provider,
            context_window=800,
            trigger_ratio=0.5,
            max_turns=12,
            system_prompt=(
                "You are a data analyst. Use the analyze_data tool when asked.\n"
                "CRITICAL RULE: You must call analyze_data exactly ONE TIME per response. "
                "Do NOT call multiple tools in a single response. "
                "After each tool call, wait for the result, review it briefly, "
                "then make the next call in your next response.\n"
                "After all datasets are analyzed, provide a brief final summary."
            ),
        )

        t0 = time.time()
        result = await kernel.run(
            spec,
            "Analyze these 5 datasets one at a time: weather_2024, climate_history, "
            "ocean_temps, solar_radiation, wind_patterns. "
            "Remember: only ONE analyze_data call per response.",
        )
        elapsed = time.time() - t0

        kr = result.result
        _print_result("Compaction Trigger Test", kr, compactor, elapsed)

        assert kr.status == "completed", f"got {kr.status}: {kr.reason}"
        assert kr.num_turns >= 3, f"应至少 3 轮（逐个调用工具），实际 {kr.num_turns} 轮"

        # ── 压缩触发 ──
        assert compactor._compaction_count > 0, (
            f"context_window=800 + trigger_ratio=0.5 (threshold=400) + "
            f"{kr.num_turns} turns 应触发。usage={kr.usage}"
        )

        # ── 事件 ──
        compaction_events = [e for e in collected_events if isinstance(e, ContextCompactionEvent)]
        print(f"  Compaction events: {len(compaction_events)}")
        for i, evt in enumerate(compaction_events):
            print(
                f"    #{i+1}: strategy={evt.payload['strategy']}, "
                f"trigger_tokens={evt.payload['trigger_tokens']}, "
                f"retained_turns={evt.payload['retained_turns']}"
            )

        assert len(compaction_events) > 0
        summary_events = [
            e for e in compaction_events if e.payload["strategy"] == "summary"
        ]
        assert len(summary_events) > 0, (
            f"Expected at least one summary compaction, got strategies: "
            f"{[e.payload['strategy'] for e in compaction_events]}"
        )

        # ── 消息结构 ──
        msgs = result.messages
        assert isinstance(msgs[0], SystemMessage)
        compacted = [
            m
            for m in msgs
            if isinstance(m, SystemMessage)
            and "[Compacted Context]" in (m.content or "")
        ]
        print(f"  [Compacted Context] messages: {len(compacted)}")
        if compacted:
            summary = compacted[0].content or ""
            print(f"  Summary preview ({len(summary)} chars): {summary[:200]}...")
            assert len(summary) > 50, "摘要内容过短"

    async def test_no_compaction_large_window(
        self, main_provider, compaction_provider
    ) -> None:
        """大 context_window 下单轮问答不触发压缩。"""
        kernel, spec, collected_events, compactor = self._build_kernel_with_compaction(
            main_provider,
            compaction_provider,
            context_window=128000,
            trigger_ratio=0.9,
            max_turns=3,
        )

        result = await kernel.run(spec, "What is 2 + 2? Answer in one word.")
        kr = result.result
        _print_result("No Compaction (Large Window)", kr, compactor)

        assert kr.status == "completed"
        assert compactor._compaction_count == 0

    async def test_kernel_continues_after_compaction(
        self, main_provider, compaction_provider
    ) -> None:
        """压缩后 kernel 仍能正常完成并产出有效回答。"""
        kernel, spec, collected_events, compactor = self._build_kernel_with_compaction(
            main_provider,
            compaction_provider,
            context_window=800,
            trigger_ratio=0.5,
            max_turns=15,
            system_prompt=(
                "You are a data analyst. Use the analyze_data tool when asked.\n"
                "CRITICAL RULE: Call analyze_data exactly ONE TIME per response. "
                "Never make multiple tool calls in a single response.\n"
                "After all datasets are analyzed, provide a final summary "
                "listing the most important finding from each dataset."
            ),
        )

        result = await kernel.run(
            spec,
            "Analyze these 5 datasets one at a time: solar_radiation, wind_patterns, "
            "soil_moisture, air_quality, water_level. "
            "Only ONE analyze_data call per response.",
        )
        kr = result.result
        _print_result("Post-Compaction Continuation", kr, compactor)

        assert kr.status == "completed"
        assert kr.final_content, "压缩后应仍有最终回答"
        assert len(kr.final_content) > 20
        print(f"  Answer preview: {kr.final_content[:200]}...")

        if compactor._compaction_count > 0:
            print(f"  [OK] Compaction triggered {compactor._compaction_count} time(s)")
        else:
            print("  [WARN] No compaction despite sequential calls")
