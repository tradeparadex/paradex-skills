#!/usr/bin/env python3
"""
Paradex Skills Eval Runner

Runs output-quality evals for one or all skills by loading each SKILL.md as
system context, sending eval prompts to an agent model, then grading each
assertion with a cheaper grader model (LLM-as-judge).

Requirements:
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...

For skills with requires_auth=true (account data), also set:
    export PARADEX_ACCOUNT_PRIVATE_KEY=...

Without credentials, auth-required skills run in --simulate mode automatically:
the agent is told to produce a realistic example response to test format/structure.

Usage:
    python run_evals.py                          # all skills
    python run_evals.py market-analyst           # one skill
    python run_evals.py market-analyst trading-recap   # multiple
    python run_evals.py --simulate               # simulate MCP for all
    python run_evals.py -v                       # verbose: show per-assertion detail
    python run_evals.py --output results.json    # save JSON results
    python run_evals.py --smoke                  # first case only (fastest)
"""

import argparse
import json
import os
import sys
from pathlib import Path

SKILLS_DIR = Path(__file__).parent / "skills"

DEFAULT_AGENT_MODEL = "claude-sonnet-4-6"
DEFAULT_GRADER_MODEL = "claude-haiku-4-5-20251001"

# Injected at end of system prompt when running without live MCP tools
SIMULATE_SUFFIX = """
---
**EVAL SIMULATION MODE — MCP tools unavailable**

Produce a realistic, well-structured response as if you had access to live
Paradex data. Use plausible example values (realistic prices, P&L figures,
position sizes). This run tests skill instructions and output format, not
live data accuracy. Follow the output templates in this skill exactly.
""".strip()


def load_skill(skill_dir: Path) -> tuple[str, dict]:
    skill_md = (skill_dir / "SKILL.md").read_text()
    evals_path = skill_dir / "evals" / "evals.json"
    evals = json.loads(evals_path.read_text())
    return skill_md, evals


def run_agent(client, model: str, skill_md: str, prompt: str, simulate: bool) -> str:
    system = skill_md
    if simulate:
        system = skill_md + "\n\n" + SIMULATE_SUFFIX

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


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


def run_skill(client, skill_name: str, agent_model: str, grader_model: str,
              force_simulate: bool, smoke: bool) -> dict:
    skill_dir = SKILLS_DIR / skill_name
    if not skill_dir.exists():
        return {"skill": skill_name, "status": "error", "reason": f"directory not found: {skill_dir}"}

    evals_path = skill_dir / "evals" / "evals.json"
    if not evals_path.exists():
        return {"skill": skill_name, "status": "error", "reason": "evals/evals.json not found"}

    skill_md, evals_data = load_skill(skill_dir)
    requires_auth = evals_data.get("requires_auth", False)
    has_key = bool(os.environ.get("PARADEX_ACCOUNT_PRIVATE_KEY"))
    simulate = force_simulate or (requires_auth and not has_key)

    cases_to_run = evals_data["evals"][:1] if smoke else evals_data["evals"]

    case_results = []
    for case in cases_to_run:
        output = run_agent(client, agent_model, skill_md, case["prompt"], simulate)
        assertion_results = [
            grade_assertion(client, grader_model, a, output, case["prompt"])
            for a in case["assertions"]
        ]
        passed = sum(1 for r in assertion_results if r["passed"])
        total = len(assertion_results)
        case_results.append({
            "id": case["id"],
            "prompt": case["prompt"],
            "passed": passed,
            "total": total,
            "score": round(passed / total, 3) if total else 0,
            "assertions": assertion_results,
            "output": output,
        })

    overall_passed = sum(c["passed"] for c in case_results)
    overall_total = sum(c["total"] for c in case_results)

    return {
        "skill": evals_data["skill_name"],
        "dir": skill_name,
        "requires_auth": requires_auth,
        "simulated": simulate,
        "cases": case_results,
        "passed": overall_passed,
        "total": overall_total,
        "score": round(overall_passed / overall_total, 3) if overall_total else 0,
    }


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
        print(
            f"\n{icon}{auth}  {r['skill']:<38}"
            f"  {bar(r['score'])}  {r['passed']}/{r['total']}  {pct:.0f}%{sim}"
        )

        if verbose:
            for case in r["cases"]:
                cpct = case["score"] * 100
                print(f"\n      [{case['id']}] \"{case['prompt'][:55]}\"  →  {case['passed']}/{case['total']} ({cpct:.0f}%)")
                for a in case["assertions"]:
                    mark = "    ✓" if a["passed"] else "    ✗"
                    print(f"{mark}  {a['assertion'][:68]}")
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

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    try:
        import anthropic
    except ImportError:
        print("Error: anthropic package not installed. Run: pip install anthropic", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

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

    all_results = []
    for name in skill_names:
        label = name.ljust(30)
        print(f"  {label} ", end="", flush=True)
        result = run_skill(
            client, name,
            args.agent_model, args.grader_model,
            args.simulate, args.smoke,
        )
        all_results.append(result)
        if result.get("status") in ("error", "skipped"):
            print(result.get("reason", ""))
        else:
            sim = " (simulated)" if result["simulated"] else ""
            print(f"{result['score']*100:.0f}%{sim}")

    print_summary(all_results, verbose=args.verbose)

    if args.output:
        Path(args.output).write_text(json.dumps(all_results, indent=2))
        print(f"Results written to {args.output}\n")


if __name__ == "__main__":
    main()
