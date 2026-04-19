#!/usr/bin/env python3
"""
Evaluation harness for the Multi-Agent Code Review System.

Usage:
    python evaluate.py --input test_cases/buggy_samples/ \
                       --expected test_cases/expected_findings.json \
                       --output metrics.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass
class FileMetrics:
    file: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    fix_proposed: int = 0
    fix_verified: int = 0
    fix_success_rate: float = 0.0


@dataclass
class AggregateMetrics:
    total_files: int
    total_findings_predicted: int
    total_findings_expected: int
    precision: float
    recall: float
    f1: float
    fix_proposed: int
    fix_verified: int
    fix_success_rate: float
    per_file: list[FileMetrics] = field(default_factory=list)
    duration_seconds: float = 0.0


def _normalize_category(cat: str) -> str:
    """Normalize category names for matching."""
    return cat.lower().strip().replace(" ", "_")


def _findings_match(predicted: dict, expected: dict, line_tolerance: int = 2) -> bool:
    """Check if a predicted finding matches an expected one."""
    if _normalize_category(predicted.get("category", "")) != _normalize_category(expected.get("category", "")):
        return False
    pred_line = int(predicted.get("line", 0))
    exp_line = int(expected.get("line", 0))
    return abs(pred_line - exp_line) <= line_tolerance


def compute_file_metrics(
    predicted: list[dict],
    expected: list[dict],
    fix_proposals: list[dict],
    fix_verifications: list[dict],
    filename: str,
) -> FileMetrics:
    matched_expected = set()
    matched_predicted = set()

    for i, pred in enumerate(predicted):
        for j, exp in enumerate(expected):
            if j in matched_expected:
                continue
            if _findings_match(pred, exp):
                matched_expected.add(j)
                matched_predicted.add(i)
                break

    tp = len(matched_predicted)
    fp = len(predicted) - tp
    fn = len(expected) - len(matched_expected)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    verified = sum(1 for v in fix_verifications if v.get("verification_passed"))
    fix_rate = verified / len(fix_proposals) if fix_proposals else 0.0

    return FileMetrics(
        file=filename,
        tp=tp, fp=fp, fn=fn,
        precision=round(precision, 3),
        recall=round(recall, 3),
        f1=round(f1, 3),
        fix_proposed=len(fix_proposals),
        fix_verified=verified,
        fix_success_rate=round(fix_rate, 3),
    )


async def evaluate_file(filename: str, content: str, expected: list[dict]) -> FileMetrics:
    from src.review.session import ReviewSession
    from src.agents.coordinator import CoordinatorAgent
    from src.events.bus import AsyncEventBus
    from src.events.emitter import EventEmitter
    from src.sandbox.manager import SandboxManager
    from src.storage.agent_drive import AgentDrive

    session = ReviewSession.create({filename: content})
    event_bus = AsyncEventBus()
    sandbox_manager = SandboxManager(
        workspace=os.getenv("BL_WORKSPACE", ""),
        api_key=os.getenv("BL_API_KEY", ""),
    )
    agent_drive = AgentDrive(
        workspace=os.getenv("BL_WORKSPACE", ""),
        api_key=os.getenv("BL_API_KEY", ""),
    )
    emitter = EventEmitter("coordinator", session.session_id, event_bus)
    coordinator = CoordinatorAgent(
        emitter=emitter,
        sandbox_manager=sandbox_manager,
        agent_drive=agent_drive,
        session_id=session.session_id,
    )

    result = await coordinator.run_review(session.context, event_bus)

    predicted = [f.to_dict() for f in result.findings]
    proposals = [p.model_dump() for p in result.fix_proposals]
    verifications = [v.model_dump() for v in result.fix_verifications]

    return compute_file_metrics(predicted, expected, proposals, verifications, filename)


def print_metrics_table(metrics: AggregateMetrics) -> None:
    print("\n" + "=" * 65)
    print("  MULTI-AGENT CODE REVIEW — EVALUATION RESULTS")
    print("=" * 65)
    print(f"  Files evaluated : {metrics.total_files}")
    print(f"  Duration        : {metrics.duration_seconds:.1f}s")
    print("-" * 65)
    print(f"  {'File':<32} {'P':>5} {'R':>5} {'F1':>5} {'Fix%':>5}")
    print("-" * 65)
    for fm in metrics.per_file:
        fix_pct = f"{fm.fix_success_rate*100:.0f}%" if fm.fix_proposed else "—"
        print(f"  {fm.file:<32} {fm.precision:>5.2f} {fm.recall:>5.2f} {fm.f1:>5.2f} {fix_pct:>5}")
    print("-" * 65)
    fix_pct = f"{metrics.fix_success_rate*100:.0f}%" if metrics.fix_proposed else "—"
    print(f"  {'AGGREGATE':<32} {metrics.precision:>5.2f} {metrics.recall:>5.2f} {metrics.f1:>5.2f} {fix_pct:>5}")
    print("=" * 65)

    ok = "✅" if metrics.f1 >= 0.7 else "❌"
    print(f"\n  F1 Score: {metrics.f1:.3f} {ok} (target: ≥ 0.70)")
    fix_ok = "✅" if metrics.fix_success_rate >= 0.5 else "❌"
    print(f"  Fix Rate: {metrics.fix_success_rate:.1%} {fix_ok} (target: ≥ 50%)\n")


async def main(input_dir: str, expected_path: str, output_path: str) -> None:
    input_path = Path(input_dir)
    expected_all = json.loads(Path(expected_path).read_text())

    py_files = sorted(input_path.glob("*.py"))
    if not py_files:
        print(f"No .py files found in {input_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"\nEvaluating {len(py_files)} files against {expected_path}...")
    start = time.monotonic()

    file_metrics: list[FileMetrics] = []
    tasks = []
    for fp in py_files:
        content = fp.read_text()
        expected = expected_all.get(fp.name, {}).get("findings", [])
        tasks.append(evaluate_file(fp.name, content, expected))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            print(f"  Error: {r}", file=sys.stderr)
        else:
            file_metrics.append(r)

    duration = time.monotonic() - start

    total_tp = sum(m.tp for m in file_metrics)
    total_fp = sum(m.fp for m in file_metrics)
    total_fn = sum(m.fn for m in file_metrics)
    total_proposed = sum(m.fix_proposed for m in file_metrics)
    total_verified = sum(m.fix_verified for m in file_metrics)

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    fix_rate = total_verified / total_proposed if total_proposed > 0 else 0.0

    aggregate = AggregateMetrics(
        total_files=len(file_metrics),
        total_findings_predicted=total_tp + total_fp,
        total_findings_expected=total_tp + total_fn,
        precision=round(precision, 3),
        recall=round(recall, 3),
        f1=round(f1, 3),
        fix_proposed=total_proposed,
        fix_verified=total_verified,
        fix_success_rate=round(fix_rate, 3),
        per_file=file_metrics,
        duration_seconds=round(duration, 2),
    )

    print_metrics_table(aggregate)

    output = {
        **{k: v for k, v in asdict(aggregate).items() if k != 'per_file'},
        'per_file': [asdict(m) for m in file_metrics],
    }
    Path(output_path).write_text(json.dumps(output, indent=2))
    print(f"  Results saved to {output_path}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate code review agent")
    parser.add_argument("--input", default="test_cases/buggy_samples/")
    parser.add_argument("--expected", default="test_cases/expected_findings.json")
    parser.add_argument("--output", default="metrics.json")
    args = parser.parse_args()
    asyncio.run(main(args.input, args.expected, args.output))
