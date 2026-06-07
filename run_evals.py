#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "anthropic[bedrock]",
# ]
# ///
#
# Only anthropic[bedrock] is declared here so `uv run run_evals.py` stays fast —
# the default (Bedrock / Anthropic API) path needs nothing else. The local-model
# backends are heavy (llama-cpp-python compiles from source) and opt-in, so they
# are installed on demand with --with rather than baked into every run:
#   uv run --with huggingface-hub \
#           --with 'llama-cpp-python' \
#           --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu \
#           run_evals.py --local                 # Linux / Intel Mac (GGUF)
#   uv run --with mlx-lm run_evals.py --local    # Apple Silicon (MLX)
"""
Paradex Skills Eval Runner

Runs output-quality evals for one or all skills by loading each SKILL.md as
system context, sending eval prompts to an agent model, then grading each
assertion with a cheaper grader model (LLM-as-judge).

Requirements:
    export ANTHROPIC_API_KEY=sk-ant-...

For skills with requires_auth=true (account data), also set:
    export PARADEX_ACCOUNT_PRIVATE_KEY=...

Without credentials, auth-required skills run in --simulate mode automatically:
the agent is told to produce a realistic example response to test format/structure.

Usage:
    uv run run_evals.py                          # all skills (simulate mode by default)
    uv run run_evals.py market-analyst           # one skill
    uv run run_evals.py market-analyst trading-recap   # multiple
    uv run run_evals.py --simulate               # force simulation mode
    uv run run_evals.py --live-mcp               # disable auto-simulation (real MCP)
    uv run run_evals.py --with-baseline          # also run without skill, show Δ delta
    uv run run_evals.py -v                       # verbose: show per-assertion detail
    uv run run_evals.py --output results.json    # save JSON results
    uv run run_evals.py --smoke                  # first case only (fastest)
    uv run run_evals.py --local                  # local Gemma 3 1B (MLX on Apple Silicon, GGUF elsewhere)
"""

import argparse
import json
import os
import platform
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def _load_env_file(*paths: str) -> None:
    """Load KEY=VALUE pairs from env files into os.environ (first file found wins)."""
    for path in paths:
        p = Path(path)
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())
        return


_load_env_file(".env.local", ".env")

SKILLS_DIR = Path(__file__).parent / "skills"

# Total model requests allowed in flight at once across ALL skills/cases/
# assertions. Skills, cases and assertions each fan out into their own thread
# pools, but every real API call must pass through this single gate — so this is
# the one knob that bounds true concurrency against Bedrock/Anthropic per-region
# RPM/TPM quotas. Raise it where the account has headroom; lower it on throttling.
MAX_CONCURRENCY   = int(os.environ.get("EVAL_MAX_CONCURRENCY", "12"))
# How many skills to evaluate at once (remote clients only). The global gate
# above is the real limiter; this just caps thread creation.
SKILL_PARALLELISM = int(os.environ.get("EVAL_SKILL_PARALLELISM", "8"))
_API_GATE = threading.BoundedSemaphore(MAX_CONCURRENCY)

# Whether the active client honours prompt caching (set in main()). The direct
# Anthropic API does; Bedrock silently drops cache_control, so priming there only
# serialises the run. Local backends use the sequential path and ignore this.
_PRIME_CACHE = False

DEFAULT_AGENT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_GRADER_MODEL = "claude-sonnet-4-6"

# Bedrock model IDs (different naming scheme from direct API)
DEFAULT_BEDROCK_AGENT_MODEL  = "jp.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_BEDROCK_GRADER_MODEL = "jp.anthropic.claude-sonnet-4-6"

# Local GGUF model defaults (used with --local on Linux / Intel Mac)
DEFAULT_LOCAL_MODEL_REPO = "bartowski/google_gemma-3-1b-it-GGUF"
DEFAULT_LOCAL_MODEL_FILE = "google_gemma-3-1b-it-Q4_K_M.gguf"
# Local MLX model default (used with --local on Apple Silicon)
DEFAULT_LOCAL_MLX_MODEL  = "mlx-community/gemma-3-1b-it-4bit"


def _is_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class _LocalResponse:
    """Mimics the subset of anthropic.Message used by run_agent and grade_assertion."""
    def __init__(self, text: str, usage: dict) -> None:
        self.content = [type("_C", (), {"text": text})()]
        self.usage = type("_U", (), {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        })()


class _LocalMessages:
    def __init__(self, llm, lock: threading.Lock) -> None:
        self._llm = llm
        self._lock = lock

    def create(self, *, model, max_tokens, messages, system=None, **kwargs):
        chat: list[dict] = []
        if system:
            if isinstance(system, list):
                sys_text = "\n\n".join(
                    b["text"] for b in system if b.get("type") == "text"
                )
            else:
                sys_text = str(system)
            if sys_text.strip():
                chat.append({"role": "system", "content": sys_text})
        for msg in messages:
            content = msg["content"]
            # Suppress chain-of-thought for grader calls (short, no system prompt)
            if msg["role"] == "user" and not system:
                content = f"/no_think\n\n{content}"
            chat.append({"role": msg["role"], "content": content})
        with self._lock:
            result = self._llm.create_chat_completion(
                messages=chat,
                max_tokens=max_tokens,
                temperature=0.0,
            )
        text = _THINK_RE.sub("", result["choices"][0]["message"]["content"] or "").strip()
        return _LocalResponse(text, result.get("usage", {}))


class LocalClient:
    parallel = False  # single Llama instance; lock serialises all calls

    def __init__(self, llm) -> None:
        self.messages = _LocalMessages(llm, threading.Lock())


class _MLXMessages:
    def __init__(self, model, tokenizer) -> None:
        self._model = model
        self._tokenizer = tokenizer

    def create(self, *, model, max_tokens, messages, system=None, **kwargs):
        from mlx_lm import generate
        chat: list[dict] = []
        if system:
            if isinstance(system, list):
                sys_text = "\n\n".join(b["text"] for b in system if b.get("type") == "text")
            else:
                sys_text = str(system)
            if sys_text.strip():
                chat.append({"role": "system", "content": sys_text})
        for msg in messages:
            chat.append({"role": msg["role"], "content": msg["content"]})
        prompt = self._tokenizer.apply_chat_template(
            chat, tokenize=False, add_generation_prompt=True
        )
        text = generate(self._model, self._tokenizer, prompt=prompt, max_tokens=max_tokens, verbose=False)
        text = _THINK_RE.sub("", text).strip()
        return _LocalResponse(text, {})


class MLXClient:
    parallel = False  # sequential inference on Apple Silicon

    def __init__(self, model, tokenizer) -> None:
        self.messages = _MLXMessages(model, tokenizer)


# Injected at end of system prompt when running without live MCP tools
SIMULATE_SUFFIX = """
---
**EVAL SIMULATION MODE — MCP tools unavailable**

Produce a realistic, well-structured response as if you had access to live
Paradex data. Use plausible example values (realistic prices, P&L figures,
position sizes). This run tests skill instructions and output format, not
live data accuracy. Follow the output templates in this skill exactly.

If the prompt implies an inherently empty scenario (e.g., a narrow 1-hour
window that likely had no trades, a market never traded, or a request that
explicitly describes zero activity), produce the appropriate graceful
empty-state response rather than fabricating data to fill it.
""".strip()


def load_skill(skill_dir: Path) -> tuple[str, dict]:
    skill_md = (skill_dir / "SKILL.md").read_text()
    references_dir = skill_dir / "references"
    if references_dir.exists():
        for ref_file in sorted(references_dir.glob("*.md")):
            skill_md += f"\n\n---\n\n# Reference: {ref_file.stem}\n\n"
            skill_md += ref_file.read_text()
    evals_path = skill_dir / "evals" / "evals.json"
    evals = json.loads(evals_path.read_text())
    return skill_md, evals


def resolve_context(case: dict, skill_dir: Path) -> str:
    """
    Return the <market_data> context to prepend for fixture-backed cases.

    A case with "context": "fixture:some_file.json" loads that file from
    evals/fixtures/ and injects it as the agent's sole source of truth. A case
    with no "context" field returns "" (and runs in normal simulate mode).

    Injection is focused, not a raw dump of the whole fixture:
      * `derived` — the fixture's precomputed reads (realized vol, flow greeks,
        vol surface). Keyed as `derived` to match the SKILL.md, which tells the
        agent to read `derived.realized_vol` / `derived.flow_greeks` /
        `derived.vol_surface` directly rather than recompute them — these are
        exactly the figures it would otherwise hallucinate.
      * raw `dvol` / `spot` / `trades` — the tape the agent reads itself to
        extract DVOL open/close, the spot range, and block structures.
    `tickers` is intentionally omitted: the vol surface already lives in
    `derived`, so injecting raw per-strike IVs would only invite the agent to
    re-derive (and re-hallucinate) the surface.
    """
    ctx = case.get("context", "")
    if not ctx:
        return ""
    if ctx.startswith("fixture:"):
        fixture_name = ctx[len("fixture:"):]
        fixture_path = skill_dir / "evals" / "fixtures" / fixture_name
        raw = json.loads(fixture_path.read_text())

        derived = raw.get("derived")
        tape: dict = {}
        for key in ("dvol", "spot", "spot_price_at_fetch", "trades"):
            if key in raw:
                tape[key] = raw[key]

        # `derived` (the figures the agent must read verbatim) is pretty-printed
        # for legibility; the bulky raw arrays are minified to keep the injected
        # context compact (~1k trades would otherwise dominate).
        derived_json = json.dumps({"derived": derived}, indent=2)
        tape_json = json.dumps(tape, separators=(",", ":"))
        return (
            "<market_data>\n"
            "Real Deribit data for this window — the sole source of truth. Do "
            "not fabricate, estimate, or supplement it, and do not add a "
            "simulated-data disclaimer.\n\n"
            "`derived` holds values already computed deterministically by the "
            "bundled script (realized vol, vol risk premium, flow greeks, vol "
            "surface). Report those figures directly — do NOT recompute them:\n"
            f"{derived_json}\n\n"
            "Raw tape — read DVOL open/close, the spot range, and block "
            "structures (cluster trades by block_trade_id) from here:\n"
            f"{tape_json}\n"
            "</market_data>\n\n"
        )
    return ctx


def run_agent(client, model: str, skill_md: str, prompt: str, simulate: bool) -> tuple[str, dict]:
    """
    Cache-aware agent invocation.

    The SKILL.md is reused on every case (and again for the baseline pass when
    enabled). Tagging it with cache_control: ephemeral makes calls 2..N hit the
    Anthropic prompt cache — ~80% input-token reduction, large latency win.

    Layout:
      [0]  SKILL.md           (cached if non-empty)
      [1]  SIMULATE_SUFFIX    (uncached — short, cache-key churn would defeat 0)
    Baseline runs (skill_md == "") send only the suffix as a plain string.

    Note: as of 2026-05, Bedrock in ap-northeast-1 silently drops
    cache_control across all Claude 4.x models — the field is accepted but
    cache_creation/cache_read in the response stay at 0. The block is still
    sent because (a) it is harmless on Bedrock and (b) the direct Anthropic
    API honours it. The real speed win for this repo comes from
    parallel-cases inside run_cases().
    """
    system: str | list
    if skill_md:
        blocks: list[dict] = [{
            "type": "text",
            "text": skill_md,
            "cache_control": {"type": "ephemeral"},
        }]
        if simulate:
            blocks.append({"type": "text", "text": SIMULATE_SUFFIX})
        system = blocks
    else:
        system = SIMULATE_SUFFIX if simulate else ""

    with _API_GATE:
        t0 = time.monotonic()
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        duration_ms = round((time.monotonic() - t0) * 1000)
    usage = response.usage
    timing = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "total_tokens": usage.input_tokens + usage.output_tokens,
        "duration_ms": duration_ms,
    }
    return response.content[0].text, timing


def grade_assertion(client, model: str, assertion: str, output: str, prompt: str) -> dict:
    grading_prompt = f"""Grade an AI agent's response against one assertion.

User prompt: {prompt}

Agent response:
{output}

Assertion: {assertion}

Put your verdict on the FIRST line — exactly `PASS` or `FAIL: <one-sentence reason>` —
with nothing before it. You may add reasoning on later lines if helpful."""

    with _API_GATE:
        response = client.messages.create(
            model=model,
            # Generous budget: a tight cap (e.g. 120) truncates graders that
            # reason before answering, so the verdict never lands and the result
            # silently defaults to FAIL — a spurious failure, not a real one.
            max_tokens=512,
            messages=[{"role": "user", "content": grading_prompt}],
        )
    verdict = response.content[0].text.strip()
    # Parse the first non-empty line (the verdict), not the raw blob — robust to
    # a model that emits a leading blank line or trails reasoning afterwards.
    first_line = next((ln.strip() for ln in verdict.splitlines() if ln.strip()), "")
    passed = first_line.upper().startswith("PASS")
    return {"assertion": assertion, "passed": passed, "verdict": verdict}


CASE_PARALLELISM = 8


def _run_one_case(client, agent_model: str, grader_model: str,
                  skill_md: str | None, case: dict, simulate: bool,
                  skill_dir: Path | None = None) -> dict:
    """Agent call + assertion grading for a single case. Thread-safe."""
    context = resolve_context(case, skill_dir) if skill_dir else ""
    prompt = context + case["prompt"] if context else case["prompt"]
    # Fixture cases carry real data, so suppress simulate mode for them — the
    # agent must read the injected values, not fabricate (and not disclaim).
    effective_simulate = simulate and not context
    output, timing = run_agent(client, agent_model, skill_md or "", prompt, effective_simulate)
    assertions = case["assertions"]
    graded: list = [None] * len(assertions)
    if assertions:
        if getattr(client, "parallel", True):
            with ThreadPoolExecutor(max_workers=len(assertions)) as pool:
                futures = {
                    pool.submit(grade_assertion, client, grader_model, a, output, case["prompt"]): ai
                    for ai, a in enumerate(assertions)
                }
                for fut in as_completed(futures):
                    graded[futures[fut]] = fut.result()
        else:
            for ai, a in enumerate(assertions):
                graded[ai] = grade_assertion(client, grader_model, a, output, case["prompt"])
    passed = sum(1 for r in graded if r and r["passed"])
    total = len(graded)
    return {
        "id": case["id"],
        "prompt": case["prompt"],
        "passed": passed,
        "total": total,
        "score": round(passed / total, 3) if total else 0,
        "assertions": graded,
        "output": output,
        "timing": timing,
    }


def run_cases(client, agent_model: str, grader_model: str,
              skill_md: str | None, cases: list, simulate: bool,
              on_progress=None, tag: str = "", skill_dir: Path | None = None) -> list:
    """
    Run cases for a skill.

    Local model (client.parallel=False): sequential — the single Llama/MLX
    instance can't service concurrent calls.

    Remote (Anthropic / Bedrock): cases run concurrently, bounded globally by the
    shared API gate. When the client honours prompt caching (_PRIME_CACHE — the
    direct Anthropic API) the first case runs alone to prime the SKILL.md cache
    before the rest fan out; on Bedrock caching is dropped, so priming would only
    serialise the run and every case fans out immediately.
    """
    n = len(cases)
    results: list = [None] * n
    if not cases:
        return results

    done = 0
    if on_progress:
        on_progress(f"{tag}cases {done}/{n}")

    if not getattr(client, "parallel", True):
        # Local model: sequential execution, no thread overhead
        for i, case in enumerate(cases):
            results[i] = _run_one_case(client, agent_model, grader_model, skill_md, case, simulate, skill_dir)
            done += 1
            if on_progress:
                on_progress(f"{tag}cases {done}/{n}")
        return results

    start = 0
    if _PRIME_CACHE and n > 1:
        results[0] = _run_one_case(client, agent_model, grader_model, skill_md, cases[0], simulate, skill_dir)
        done = start = 1
        if on_progress:
            on_progress(f"{tag}cases {done}/{n}")

    if start < n:
        with ThreadPoolExecutor(max_workers=min(CASE_PARALLELISM, n - start)) as pool:
            futures = {
                pool.submit(_run_one_case, client, agent_model, grader_model,
                            skill_md, cases[i], simulate, skill_dir): i
                for i in range(start, n)
            }
            for fut in as_completed(futures):
                idx = futures[fut]
                results[idx] = fut.result()
                done += 1
                if on_progress:
                    on_progress(f"{tag}cases {done}/{n}")
    return results


def run_skill(client, skill_name: str, agent_model: str, grader_model: str,
              force_simulate: bool, live_mcp: bool, smoke: bool,
              with_baseline: bool = False, on_progress=None) -> dict:
    skill_dir = SKILLS_DIR / skill_name
    if not skill_dir.exists():
        return {"skill": skill_name, "status": "error", "reason": f"directory not found: {skill_dir}"}

    evals_path = skill_dir / "evals" / "evals.json"
    if not evals_path.exists():
        return {"skill": skill_name, "status": "error", "reason": "evals/evals.json not found"}

    skill_md, evals_data = load_skill(skill_dir)
    requires_auth = evals_data.get("requires_auth", False)
    has_key = bool(os.environ.get("PARADEX_ACCOUNT_PRIVATE_KEY"))

    if force_simulate:
        simulate = True
    elif live_mcp:
        # Live MCP mode: only simulate auth-required skills that still lack credentials
        simulate = requires_auth and not has_key
    else:
        # Default: simulate everything — MCP tools are not available in the eval runner
        simulate = True

    # Cases tagged "requires_live": true exercise behaviour that only exists with
    # real, multi-turn tool execution (e.g. an actual post-confirmation tool
    # invocation, or a second user turn after an `adjust`). They cannot be
    # validly judged in a single-turn simulate run, so skip them when simulating.
    all_cases = evals_data["evals"]
    skipped_live = 0
    if simulate:
        live = [c for c in all_cases if c.get("requires_live")]
        skipped_live = len(live)
        all_cases = [c for c in all_cases if not c.get("requires_live")]

    cases_to_run = all_cases[:1] if smoke else all_cases

    # With-skill run
    case_results = run_cases(client, agent_model, grader_model, skill_md, cases_to_run, simulate,
                             on_progress=on_progress, skill_dir=skill_dir)

    overall_passed = sum(c["passed"] for c in case_results)
    overall_total = sum(c["total"] for c in case_results)

    result = {
        "skill": evals_data["skill_name"],
        "dir": skill_name,
        "requires_auth": requires_auth,
        "simulated": simulate,
        "skipped_live": skipped_live,
        "cases": case_results,
        "passed": overall_passed,
        "total": overall_total,
        "score": round(overall_passed / overall_total, 3) if overall_total else 0,
    }

    # Optional baseline (without skill)
    if with_baseline:
        baseline_results = run_cases(client, agent_model, grader_model, None, cases_to_run, simulate,
                                     on_progress=on_progress, tag="baseline ", skill_dir=skill_dir)
        bl_passed = sum(c["passed"] for c in baseline_results)
        bl_total = sum(c["total"] for c in baseline_results)
        result["baseline"] = {
            "cases": baseline_results,
            "passed": bl_passed,
            "total": bl_total,
            "score": round(bl_passed / bl_total, 3) if bl_total else 0,
        }
        result["delta"] = round(result["score"] - result["baseline"]["score"], 3)

    return result


def bar(score: float, width: int = 10) -> str:
    filled = round(score * width)
    return "█" * filled + "░" * (width - filled)


def print_summary(results: list[dict], verbose: bool) -> None:
    print()
    print("━" * 62)
    print("  PARADEX SKILLS EVAL RESULTS")
    print("━" * 62)

    scored = []
    for r in results:
        if r.get("status") in ("error", "skipped"):
            icon = "⊘"
            print(f"\n{icon}  {r.get('skill', r.get('dir'))}  —  {r['reason']}")
            continue

        pct = r["score"] * 100
        icon = "✓" if pct >= 80 else ("~" if pct >= 60 else "✗")
        auth = " 🔐" if r["requires_auth"] else "   "
        sim = " [sim]" if r["simulated"] else "      "
        delta_str = ""
        if "delta" in r:
            delta_pct = r["delta"] * 100
            bl_pct = r["baseline"]["score"] * 100
            delta_str = f"  Δ{delta_pct:+.0f}% (baseline {bl_pct:.0f}%)"
        if r.get("skipped_live"):
            delta_str += f"  ({r['skipped_live']} live-only skipped)"
        print(
            f"\n{icon}{auth}  {r['skill']:<38}"
            f"  {bar(r['score'])}  {r['passed']}/{r['total']}  {pct:.0f}%{sim}{delta_str}"
        )

        if verbose:
            # Build baseline assertion lookup: case_id -> [passed, ...]
            bl_lookup: dict[int, list[bool]] = {}
            if "baseline" in r:
                for bl_case in r["baseline"]["cases"]:
                    bl_lookup[bl_case["id"]] = [a["passed"] for a in bl_case["assertions"]]

            for case in r["cases"]:
                cpct = case["score"] * 100
                bl_case_results = bl_lookup.get(case["id"], [])
                print(f"\n      [{case['id']}] \"{case['prompt'][:55]}\"  →  {case['passed']}/{case['total']} ({cpct:.0f}%)")
                for ai, a in enumerate(case["assertions"]):
                    mark = "    ✓" if a["passed"] else "    ✗"
                    bl_tag = ""
                    if bl_case_results and ai < len(bl_case_results):
                        bl_passed = bl_case_results[ai]
                        if a["passed"] and bl_passed:
                            bl_tag = "  ·non-discriminating"
                        elif a["passed"] and not bl_passed:
                            bl_tag = "  ·skill adds value"
                        elif not a["passed"] and bl_passed:
                            bl_tag = "  ·regressed vs baseline"
                    a_text = a["assertion"]
                    if isinstance(a_text, dict):
                        a_text = a_text.get("name") or a_text.get("description") or ""
                    print(f"{mark}  {a_text[:68]}{bl_tag}")
                    if not a["passed"]:
                        reason = a["verdict"].replace("FAIL:", "").strip()
                        print(f"         ↳ {reason}")

        scored.append(r["score"])

    if scored:
        avg = sum(scored) / len(scored) * 100
        passing = sum(1 for s in scored if s >= 0.8)
        print()
        print("━" * 62)
        print(f"  Average: {avg:.0f}%   Skills ≥80%: {passing}/{len(scored)}")
    print("━" * 62)
    print()


def _check_evals_exist() -> None:
    """Verify every skill directory has evals/evals.json with at least 2 cases."""
    failures: list[str] = []
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        evals_path = skill_dir / "evals" / "evals.json"
        if not evals_path.exists():
            failures.append(f"  {skill_dir.name}: missing evals/evals.json")
            continue
        try:
            data = json.loads(evals_path.read_text())
            count = len(data.get("evals", []))
        except Exception as exc:
            failures.append(f"  {skill_dir.name}: evals/evals.json is invalid JSON ({exc})")
            continue
        if count < 2:
            failures.append(f"  {skill_dir.name}: only {count} eval case(s), need ≥2")
    if failures:
        print("Pre-run check failed — fix these before running evals:", file=sys.stderr)
        for msg in failures:
            print(msg, file=sys.stderr)
        print("(skip with --no-check)", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    global _PRIME_CACHE
    parser = argparse.ArgumentParser(
        description="Run output-quality evals for Paradex skills",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("skills", nargs="*",
                        help="Skill directory names to run (default: all with evals)")
    parser.add_argument("--simulate", action="store_true",
                        help="Force simulation mode (no live MCP) for all skills")
    parser.add_argument("--live-mcp", action="store_true",
                        help="Disable auto-simulation: run non-auth skills against real MCP tools")
    parser.add_argument("--with-baseline", action="store_true",
                        help="Also run each eval without the skill to measure skill delta")
    parser.add_argument("--smoke", action="store_true",
                        help="Run only the first eval case per skill")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show per-assertion pass/fail detail")
    parser.add_argument("--output", "-o", metavar="FILE",
                        help="Write full JSON results to file")
    parser.add_argument("--agent-model", default=DEFAULT_AGENT_MODEL,
                        metavar="MODEL", help=f"Model for the agent (default: {DEFAULT_AGENT_MODEL})")
    parser.add_argument("--grader-model", default=DEFAULT_GRADER_MODEL,
                        metavar="MODEL", help=f"Model for grading assertions (default: {DEFAULT_GRADER_MODEL})")
    parser.add_argument("--fail-below", type=float, default=None, metavar="THRESHOLD",
                        help="Exit 1 if any skill scores below THRESHOLD (0.0–1.0). E.g. --fail-below 0.8")
    parser.add_argument("--local", action="store_true",
                        help="Use a local GGUF model instead of the Anthropic API (no API key needed)")
    parser.add_argument("--local-model-repo", default=DEFAULT_LOCAL_MODEL_REPO, metavar="REPO",
                        help=f"HuggingFace repo for the local GGUF model (default: {DEFAULT_LOCAL_MODEL_REPO})")
    parser.add_argument("--local-model-file", default=DEFAULT_LOCAL_MODEL_FILE, metavar="FILE",
                        help=f"GGUF filename inside the repo (default: {DEFAULT_LOCAL_MODEL_FILE})")
    parser.add_argument("--no-mlx", action="store_true",
                        help="Force GGUF/llama-cpp path even on Apple Silicon (disable MLX auto-detection)")
    parser.add_argument("--no-check", action="store_true",
                        help="Skip the pre-run check that every skill has evals/evals.json with ≥2 cases")
    args = parser.parse_args()

    if not args.no_check:
        _check_evals_exist()

    if args.local:
        if _is_apple_silicon() and not args.no_mlx:
            try:
                from mlx_lm import load
            except ImportError:
                print(
                    "Error: --local on Apple Silicon requires mlx-lm. Re-run with:\n"
                    "  uv run --with mlx-lm run_evals.py --local\n"
                    "Use --no-mlx to fall back to llama-cpp-python instead.",
                    file=sys.stderr,
                )
                sys.exit(1)
            mlx_repo = (
                DEFAULT_LOCAL_MLX_MODEL
                if args.local_model_repo == DEFAULT_LOCAL_MODEL_REPO
                else args.local_model_repo
            )
            print(f"Loading MLX model {mlx_repo} …", file=sys.stderr)
            mlx_model, mlx_tokenizer = load(mlx_repo)
            client = MLXClient(mlx_model, mlx_tokenizer)
            label = mlx_repo.split("/")[-1]
        else:
            try:
                from llama_cpp import Llama
                from huggingface_hub import hf_hub_download
            except ImportError:
                print(
                    "Error: --local requires llama-cpp-python and huggingface-hub. Re-run with:\n"
                    "  uv run --with huggingface-hub --with llama-cpp-python \\\n"
                    "    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu \\\n"
                    "    run_evals.py --local",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(f"Downloading {args.local_model_repo}/{args.local_model_file} …", file=sys.stderr)
            model_path = hf_hub_download(repo_id=args.local_model_repo, filename=args.local_model_file)
            print("Loading model …", file=sys.stderr)
            llm = Llama(model_path=model_path, n_ctx=16384, n_threads=4, verbose=False)
            client = LocalClient(llm)
            label = args.local_model_file
        if args.agent_model == DEFAULT_AGENT_MODEL:
            args.agent_model = f"local:{label}"
        if args.grader_model == DEFAULT_GRADER_MODEL:
            args.grader_model = f"local:{label}"
    else:
        try:
            import anthropic
        except ImportError:
            print("Error: anthropic package not installed. Run: uv run run_evals.py", file=sys.stderr)
            sys.exit(1)

        bedrock_token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
        region        = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION")
        # OIDC / SSO / static AWS credentials all surface through the standard
        # boto3 credential chain. In CI, aws-actions/configure-aws-credentials
        # mints short-lived OIDC credentials and exports AWS_ACCESS_KEY_ID +
        # AWS_REGION — so a bearer token is not the only way onto Bedrock.
        aws_creds     = os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE")
        use_bedrock   = bool(bedrock_token or (aws_creds and region))
        api_key       = os.environ.get("ANTHROPIC_API_KEY")

        # Higher retry budget: with many requests in flight we will occasionally
        # hit Bedrock/Anthropic throttling (429); the SDK backs off and retries.
        if use_bedrock:
            client = (anthropic.AnthropicBedrock(aws_region=region, max_retries=8)
                      if region else anthropic.AnthropicBedrock(max_retries=8))
            # Bedrock silently drops cache_control — priming would only serialise.
            _PRIME_CACHE = False
            if args.agent_model == DEFAULT_AGENT_MODEL:
                args.agent_model = DEFAULT_BEDROCK_AGENT_MODEL
            if args.grader_model == DEFAULT_GRADER_MODEL:
                args.grader_model = DEFAULT_BEDROCK_GRADER_MODEL
        elif api_key:
            client = anthropic.Anthropic(api_key=api_key, max_retries=8)
            _PRIME_CACHE = True  # direct API honours prompt caching
        else:
            print(
                "Error: set ANTHROPIC_API_KEY, AWS_BEARER_TOKEN_BEDROCK, or "
                "AWS credentials together with AWS_REGION",
                file=sys.stderr,
            )
            sys.exit(1)

    # Resolve skill list
    if args.skills:
        skill_names = args.skills
    else:
        skill_names = sorted(
            d.name for d in SKILLS_DIR.iterdir()
            if d.is_dir() and (d / "evals" / "evals.json").exists()
        )

    if not skill_names:
        print("No skills with evals found.", file=sys.stderr)
        sys.exit(1)

    n_skills = len(skill_names)

    def result_label(result: dict) -> str:
        if result.get("status") in ("error", "skipped"):
            return result.get("reason", "")
        sim = " (simulated)" if result["simulated"] else ""
        delta = f"  Δ{result['delta']*100:+.0f}%" if "delta" in result else ""
        return f"{result['score']*100:.0f}%{sim}{delta}"

    def evaluate(name: str) -> dict:
        return run_skill(
            client, name,
            args.agent_model, args.grader_model,
            args.simulate, args.live_mcp, args.smoke,
            with_baseline=args.with_baseline,
        )

    all_results: list = [None] * n_skills

    if getattr(client, "parallel", True) and n_skills > 1:
        # Remote client: evaluate skills concurrently. The global API gate caps
        # true in-flight requests, so completions arrive out of order — print one
        # line per skill as it finishes rather than an in-place progress bar.
        workers = min(SKILL_PARALLELISM, n_skills)
        print(f"  Running {n_skills} skills "
              f"({workers} at a time, ≤{MAX_CONCURRENCY} concurrent requests)…\n",
              flush=True)
        done = 0
        lock = threading.Lock()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(evaluate, name): i for i, name in enumerate(skill_names)}
            for fut in as_completed(futures):
                idx = futures[fut]
                result = fut.result()
                all_results[idx] = result
                with lock:
                    done += 1
                    print(f"  [{done:>2}/{n_skills}] {skill_names[idx].ljust(26)} "
                          f"{result_label(result)}", flush=True)
    else:
        # Local (sequential) client: keep the in-place per-skill progress bar.
        for skill_idx, name in enumerate(skill_names):
            prefix = f"  [{skill_idx + 1}/{n_skills}] {name.ljust(26)} "
            print(prefix, end="", flush=True)

            def on_progress(status: str, _prefix: str = prefix) -> None:
                print(f"\r{_prefix}{status:<32}", end="", flush=True)

            result = run_skill(
                client, name,
                args.agent_model, args.grader_model,
                args.simulate, args.live_mcp, args.smoke,
                with_baseline=args.with_baseline,
                on_progress=on_progress,
            )
            all_results[skill_idx] = result
            # Overwrite progress text with final label; pad to erase leftovers.
            print(f"\r{prefix}{result_label(result):<32}".rstrip())

    print_summary(all_results, verbose=args.verbose)

    if args.fail_below is not None:
        evaluated = [r for r in all_results if r.get("status") not in ("error", "skipped")]
        below = [r for r in evaluated if r["score"] < args.fail_below]
        if below:
            names = ", ".join(r["skill"] for r in below)
            print(f"\nFAIL: {len(below)} skill(s) scored below {args.fail_below * 100:.0f}%: {names}",
                  file=sys.stderr)
            sys.exit(1)

    if args.output:
        Path(args.output).write_text(json.dumps(all_results, indent=2))
        print(f"Results written to {args.output}\n")


if __name__ == "__main__":
    main()
