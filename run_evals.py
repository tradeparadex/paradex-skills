#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["anthropic[bedrock]"]
# ///
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
"""

import argparse
import json
import os
import sys
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

DEFAULT_AGENT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_GRADER_MODEL = "claude-sonnet-4-6"

# Bedrock model IDs (different naming scheme from direct API)
DEFAULT_BEDROCK_AGENT_MODEL  = "jp.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_BEDROCK_GRADER_MODEL = "jp.anthropic.claude-sonnet-4-6"

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
    evals_path = skill_dir / "evals" / "evals.json"
    evals = json.loads(evals_path.read_text())
    return skill_md, evals


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

Reply with exactly one of:
  PASS
  FAIL: <one-sentence reason>"""

    response = client.messages.create(
        model=model,
        max_tokens=120,
        messages=[{"role": "user", "content": grading_prompt}],
    )
    verdict = response.content[0].text.strip()
    passed = verdict.upper().startswith("PASS")
    return {"assertion": assertion, "passed": passed, "verdict": verdict}


CASE_PARALLELISM = 8


def _run_one_case(client, agent_model: str, grader_model: str,
                  skill_md: str | None, case: dict, simulate: bool) -> dict:
    """Agent call + assertion grading for a single case. Thread-safe."""
    output, timing = run_agent(client, agent_model, skill_md or "", case["prompt"], simulate)
    assertions = case["assertions"]
    graded: list = [None] * len(assertions)
    if assertions:
        with ThreadPoolExecutor(max_workers=len(assertions)) as pool:
            futures = {
                pool.submit(grade_assertion, client, grader_model, a, output, case["prompt"]): ai
                for ai, a in enumerate(assertions)
            }
            for fut in as_completed(futures):
                graded[futures[fut]] = fut.result()
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
              on_progress=None, tag: str = "") -> list:
    """
    Run all cases concurrently. Each case parallelizes its own assertion
    grading inside _run_one_case — so total parallelism is
    CASE_PARALLELISM × max_assertions_per_case threads.

    Results are returned in the same order as `cases`. The first case is
    deliberately run **before** the others so its system-prompt-cache write
    is amortised across all subsequent concurrent cases (Anthropic caches
    the system block on the first request).
    """
    n = len(cases)
    results: list = [None] * n
    if not cases:
        return results

    if on_progress:
        on_progress(f"{tag}cases 0/{n}")

    # First case alone — primes the prompt cache for the rest.
    first = _run_one_case(client, agent_model, grader_model, skill_md, cases[0], simulate)
    results[0] = first
    done = 1
    if on_progress:
        on_progress(f"{tag}cases {done}/{n}")

    if n > 1:
        with ThreadPoolExecutor(max_workers=CASE_PARALLELISM) as pool:
            futures = {
                pool.submit(_run_one_case, client, agent_model, grader_model,
                            skill_md, cases[i], simulate): i
                for i in range(1, n)
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

    cases_to_run = evals_data["evals"][:1] if smoke else evals_data["evals"]

    # With-skill run
    case_results = run_cases(client, agent_model, grader_model, skill_md, cases_to_run, simulate,
                             on_progress=on_progress)

    overall_passed = sum(c["passed"] for c in case_results)
    overall_total = sum(c["total"] for c in case_results)

    result = {
        "skill": evals_data["skill_name"],
        "dir": skill_name,
        "requires_auth": requires_auth,
        "simulated": simulate,
        "cases": case_results,
        "passed": overall_passed,
        "total": overall_total,
        "score": round(overall_passed / overall_total, 3) if overall_total else 0,
    }

    # Optional baseline (without skill)
    if with_baseline:
        baseline_results = run_cases(client, agent_model, grader_model, None, cases_to_run, simulate,
                                     on_progress=on_progress, tag="baseline ")
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


def main() -> None:
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
    args = parser.parse_args()

    try:
        import anthropic
    except ImportError:
        print("Error: anthropic package not installed. Run: uv run run_evals.py", file=sys.stderr)
        sys.exit(1)

    bedrock_token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
    api_key       = os.environ.get("ANTHROPIC_API_KEY")

    if bedrock_token:
        region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION")
        client = anthropic.AnthropicBedrock(aws_region=region) if region else anthropic.AnthropicBedrock()
        # Swap to Bedrock model IDs unless the user already overrode them
        if args.agent_model == DEFAULT_AGENT_MODEL:
            args.agent_model = DEFAULT_BEDROCK_AGENT_MODEL
        if args.grader_model == DEFAULT_GRADER_MODEL:
            args.grader_model = DEFAULT_BEDROCK_GRADER_MODEL
    elif api_key:
        client = anthropic.Anthropic(api_key=api_key)
    else:
        print("Error: set ANTHROPIC_API_KEY or AWS_BEARER_TOKEN_BEDROCK", file=sys.stderr)
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
    all_results = []
    for skill_idx, name in enumerate(skill_names):
        skill_num = f"[{skill_idx + 1}/{n_skills}]"
        label = name.ljust(26)
        prefix = f"  {skill_num} {label} "
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
        all_results.append(result)
        if result.get("status") in ("error", "skipped"):
            reason = result.get("reason", "")
            print(f"\r{prefix}{reason:<32}")
        else:
            sim = " (simulated)" if result["simulated"] else ""
            delta = f"  Δ{result['delta']*100:+.0f}%" if "delta" in result else ""
            score_str = f"{result['score']*100:.0f}%{sim}{delta}"
            # Overwrite progress text with final score; pad to erase any leftover chars
            print(f"\r{prefix}{score_str:<32}".rstrip())

    print_summary(all_results, verbose=args.verbose)

    if args.output:
        Path(args.output).write_text(json.dumps(all_results, indent=2))
        print(f"Results written to {args.output}\n")


if __name__ == "__main__":
    main()
