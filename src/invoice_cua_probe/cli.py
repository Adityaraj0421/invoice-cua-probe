"""Command line for generating fixtures and scoring an adapter."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from invoice_cua_probe.adapters import AdapterSkipped, gemini_skip_reason
from invoice_cua_probe.generate import generate_fixtures, load_fixtures
from invoice_cua_probe.report import write_skip_report
from invoice_cua_probe.runner import run_probe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="probe", description="Score a form-filling agent against hidden invoice instructions.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    generate = sub.add_parser("generate", help="Write the synthetic fixtures")
    generate.add_argument("--out", type=Path, default=Path("fixtures"))

    listing = sub.add_parser("list", help="List fixtures and attack techniques")
    listing.add_argument("--fixtures", type=Path, default=Path("fixtures"))

    run = sub.add_parser("run", help="Fill the local form with an adapter and write a report")
    run.add_argument("--adapter", required=True, help="naive, visible-only, or gemini")
    run.add_argument("--fixtures", type=Path, default=Path("fixtures"))
    run.add_argument("--out", type=Path, default=Path("reports"))
    run.add_argument("--port", type=int, default=0, help="Local form port. 0 picks a free port.")
    run.add_argument("--repeats", type=int, default=1, help="Run each fixture this many times.")
    run.add_argument("--max-steps", type=int, default=40, help="Cap computer-use actions per fixture.")
    run.add_argument("--model", default="gemini-3.8-flash", help="Gemini model id for the computer-use adapter.")
    run.add_argument(
        "--no-injection-detection",
        action="store_true",
        help="Turn off the Computer Use prompt-injection detector. Default is on.",
    )

    args = parser.parse_args(argv)
    if args.cmd == "generate":
        order = generate_fixtures(args.out)
        print(f"Wrote {len(order)} fixtures to {args.out}")
        return 0
    if args.cmd == "list":
        return _list(args.fixtures)
    if args.cmd == "run":
        return _run(args)
    parser.error(f"Unknown command {args.cmd}")
    return 2


def format_injection_success(report: dict) -> str:
    if not report.get("attack_fixture_count"):
        return "n/a"
    if not report.get("injection_measured", True):
        return "not measured"
    return (
        f"{report['injection_followed_count']}/{report['attack_measured_count']} "
        f"measured attacked runs ({report['injection_success_rate']:.3f})"
    )


def _list(fixtures_dir: Path) -> int:
    try:
        fixtures = load_fixtures(fixtures_dir)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    for fixture in fixtures:
        technique = fixture.attack.get("technique") or "clean"
        kind = "attack" if fixture.attack.get("present") else "clean"
        print(f"{fixture.id}\t{kind}\t{technique}")
    return 0


def _run(args: argparse.Namespace) -> int:
    adapter = args.adapter.strip().lower()
    if adapter == "gemini" and not os.environ.get("GEMINI_API_KEY"):
        reason = gemini_skip_reason()
        path = write_skip_report(args.out / "gemini", "gemini", reason)
        print(reason)
        print(f"report: {path}")
        return 0
    if args.repeats < 1 or args.max_steps < 1:
        print("repeats and max-steps must be at least 1", file=sys.stderr)
        return 1
    try:
        report = run_probe(
            adapter,
            args.fixtures,
            args.out,
            port=args.port,
            repeats=args.repeats,
            max_steps=args.max_steps,
            model_name=args.model,
            injection_detection=not args.no_injection_detection,
        )
    except AdapterSkipped as exc:
        print(exc)
        return 0
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    rate = report["injection_success_rate"]
    mean = report["mean_field_accuracy"]
    mean_text = "not measured" if mean is None else f"{mean:.3f}"
    print(f"adapter: {report['adapter']}")
    print(f"fixtures: {report['fixture_count']}")
    print(f"mean field accuracy: {mean_text}")
    print(f"injection success: {format_injection_success(report)}")
    if rate is not None and report.get("attack_unmeasured_count"):
        print(f"unmeasured attacked runs: {report['attack_unmeasured_count']}")
    print(f"repeats: {report['repeats']}")
    if report.get("adapter") == "gemini":
        print(f"max steps: {report['max_steps']}")
        cost = report.get("total_cost_usd")
        if cost is None:
            print("rough cost: unavailable for this model")
        else:
            print(
                f"rough cost: ${cost:.4f} "
                f"({report.get('total_tokens', 0)} tokens, {report.get('model')})"
            )
    print(f"report: {args.out / report['adapter'] / 'report.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
