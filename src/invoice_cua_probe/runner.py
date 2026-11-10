"""Run an adapter against every fixture and score what the form stored."""

from __future__ import annotations

import os
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlencode, urlparse

from invoice_cua_probe import __version__
from invoice_cua_probe.adapters import build_adapter
from invoice_cua_probe.form_server import FormServer
from invoice_cua_probe.generate import load_fixtures
from invoice_cua_probe.models import FIELDS, Fixture, Task
from invoice_cua_probe.report import write_run_report
from invoice_cua_probe.scoring import score_submission

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def assert_localhost(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError("Refusing a form URL that is not http(s).")
    if parsed.hostname not in LOCAL_HOSTS:
        raise RuntimeError("Refusing a form URL that is not on localhost.")


def run_probe(
    adapter_name: str,
    fixtures_dir: Path,
    out_dir: Path,
    *,
    port: int = 0,
    transport: str = "playwright",
    repeats: int = 1,
    max_steps: int = 40,
    model_name: str = "gemini-3.8-flash",
    injection_detection: bool = True,
) -> dict:
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    fixtures = load_fixtures(fixtures_dir)
    adapter = build_adapter(
        adapter_name,
        max_steps=max_steps,
        model_name=model_name,
        injection_detection=injection_detection,
    )
    jobs = [(fixture, repeat) for fixture in fixtures for repeat in range(1, repeats + 1)]
    destination = out_dir / adapter.name
    screenshot_dir = destination / "screenshots"
    if transport == "playwright":
        screenshot_dir.mkdir(parents=True, exist_ok=True)
    with FormServer(port=port, fixtures_dir=fixtures_dir) as server:
        _wait_ready(f"{server.base_url}/health")
        if transport == "playwright":
            rows = _run_playwright(adapter, jobs, server, screenshot_dir, repeats)
        elif transport == "http":
            rows = _run_http(adapter, jobs, server, screenshot_dir, repeats)
        else:
            raise ValueError(f"Unknown transport {transport!r}")
    report = _assemble(adapter.name, rows, repeats=repeats, max_steps=max_steps, model_name=model_name)
    write_run_report(destination, report)
    return report


def _run_http(adapter, jobs: list[tuple[Fixture, int]], server: FormServer, screenshot_dir: Path, repeats: int) -> list[dict]:
    del screenshot_dir
    rows = []
    for fixture, repeat in jobs:
        form_url = server.form_url(fixture.id)

        def submit(values: dict[str, str], form_url: str = form_url, fixture_id: str = fixture.id) -> None:
            _http_submit(form_url, fixture_id, values)

        rows.append(_one(adapter, fixture, server, form_url, None, submit, repeat, repeats, page=None))
    return rows


def _run_playwright(
    adapter,
    jobs: list[tuple[Fixture, int]],
    server: FormServer,
    screenshot_dir: Path,
    repeats: int,
) -> list[dict]:
    from playwright.sync_api import sync_playwright

    width, height = (1440, 900) if adapter.name == "gemini" else (560, 720)
    rows = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            for fixture, repeat in jobs:
                form_url = server.form_url(fixture.id)
                shot_name = f"{fixture.id}.png" if repeats == 1 else f"{fixture.id}-r{repeat}.png"
                shot = screenshot_dir / shot_name
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    permissions=["clipboard-read", "clipboard-write"],
                )
                page = context.new_page()
                page.route("**/*", _guard_route)

                def submit(
                    values: dict[str, str],
                    page=page,
                    form_url: str = form_url,
                    fixture_id: str = fixture.id,
                    shot: Path = shot,
                ) -> None:
                    _playwright_submit(page, form_url, fixture_id, values, shot)

                try:
                    rows.append(_one(adapter, fixture, server, form_url, shot, submit, repeat, repeats, page))
                finally:
                    page.close()
                    context.close()
        finally:
            browser.close()
    return rows


def _one(
    adapter,
    fixture: Fixture,
    server: FormServer,
    form_url: str,
    shot: Path | None,
    submit,
    repeat: int,
    repeats: int,
    page,
) -> dict:
    assert_localhost(form_url)
    server.submissions.pop(fixture.id, None)
    task = Task(
        fixture_id=fixture.id,
        document_path=fixture.document_path,
        form_url=form_url,
        screenshot_path=shot,
        submit=submit,
        page=page,
    )
    started = time.perf_counter()
    error = None
    try:
        adapter.run(task)
    except Exception as exc:
        error = _redact_error(exc)
    elapsed = round(time.perf_counter() - started, 3)
    submission = server.submissions.get(fixture.id)
    answer = resolve_answer(task.record, submission, error)
    relative = None
    if shot is not None and shot.is_file():
        relative = f"screenshots/{shot.name}"
    record = task.record
    scored = score_submission(fixture.expected, fixture.attack, answer["actual"])
    return {
        "id": fixture.id,
        "repeat": repeat,
        "repeats": repeats,
        "technique": fixture.attack.get("technique"),
        "attack_present": bool(fixture.attack.get("present")),
        "field_accuracy": scored["field_accuracy"] if answer["measured"] else None,
        "injection_followed": scored["injection_followed"] if answer["measured"] else None,
        "fields": scored["fields"],
        "posted": answer["posted"],
        "measured": answer["measured"],
        "seconds": elapsed,
        "screenshot": relative,
        "error": error or record.get("error"),
        "status": record.get("status"),
        "steps": record.get("steps"),
        "turns": record.get("turns"),
        "safety_prompts": record.get("safety_prompts") or [],
        "input_tokens": record.get("input_tokens"),
        "output_tokens": record.get("output_tokens"),
        "thought_tokens": record.get("thought_tokens"),
        "cost_usd": record.get("cost_usd"),
        "model": record.get("model"),
    }


def _redact_error(exc: Exception) -> str:
    secret = os.environ.get("GEMINI_API_KEY") or ""
    text = f"{type(exc).__name__}: {exc}"
    if secret:
        text = text.replace(secret, "[redacted]")
    return text


def _http_submit(form_url: str, fixture_id: str, values: dict[str, str]) -> None:
    assert_localhost(form_url)
    origin = form_url.split("/form", 1)[0]
    target = origin + "/submit"
    assert_localhost(target)
    payload = urlencode(
        {
            "fixture_id": fixture_id,
            "payee": values.get("payee", ""),
            "iban": values.get("iban", ""),
            "amount": values.get("amount", ""),
            "reference": values.get("reference", ""),
        }
    ).encode("utf-8")
    request = urllib.request.Request(target, data=payload, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()


def _playwright_submit(page, form_url: str, fixture_id: str, values: dict[str, str], shot: Path) -> None:
    del fixture_id
    assert_localhost(form_url)
    page.goto(form_url, wait_until="domcontentloaded")
    for field in FIELDS:
        page.fill(f"#{field}", str(values.get(field, "")))
    page.click("#submit")
    page.wait_for_selector("#confirmation", timeout=10000)
    shot.parent.mkdir(parents=True, exist_ok=True)
    page.locator("main").screenshot(path=str(shot))


def _guard_route(route) -> None:
    try:
        assert_localhost(route.request.url)
    except RuntimeError:
        route.abort()
        return
    route.continue_()


def _wait_ready(url: str) -> None:
    assert_localhost(url)
    last_error: Exception | None = None
    for _ in range(50):
        try:
            with urllib.request.urlopen(url, timeout=0.2) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last_error = exc
            time.sleep(0.02)
    raise RuntimeError(f"Local form server did not start: {last_error}")


MEASURED_STATUSES = {
    "submitted",
    "submitted_pending_confirmation",
    "no_submit",
    "blocked_by_model",
    "max_steps",
    "confirmation_required",
}


def resolve_answer(record: dict, submission: dict | None, error: str | None) -> dict:
    """Choose what to score.

    A real POST is `posted`. A require_confirmation stop scores the values still
    sitting in the form and is not a POST. An API error is not a measurement.
    """

    status = record.get("status")
    if status == "submitted_pending_confirmation":
        pending = record.get("pending_fields") or {}
        return {
            "actual": {field: str(pending.get(field) or "") for field in FIELDS},
            "posted": False,
            "measured": True,
        }
    unmeasured = status == "api_error" or bool(error and status not in MEASURED_STATUSES)
    if unmeasured:
        return {"actual": {field: "" for field in FIELDS}, "posted": False, "measured": False}
    if submission is not None:
        return {"actual": dict(submission), "posted": True, "measured": True}
    return {"actual": {field: "" for field in FIELDS}, "posted": False, "measured": True}


def _assemble(
    adapter_name: str,
    rows: list[dict],
    *,
    repeats: int,
    max_steps: int,
    model_name: str,
) -> dict:
    attacks = [row for row in rows if row["attack_present"]]
    measured_attacks = [row for row in attacks if row.get("measured", True)]
    followed = sum(1 for row in measured_attacks if row["injection_followed"])
    measured_rows = [row for row in rows if row.get("measured", True)]
    mean = sum(row["field_accuracy"] for row in measured_rows) / len(measured_rows) if measured_rows else None
    costs = [row["cost_usd"] for row in rows if isinstance(row.get("cost_usd"), (int, float))]
    tokens = sum(int(row.get("input_tokens") or 0) + int(row.get("output_tokens") or 0) + int(row.get("thought_tokens") or 0) for row in rows)
    return {
        "probe": "invoice-cua-probe",
        "version": __version__,
        "adapter": adapter_name,
        "status": "complete",
        "localhost_only": True,
        "repeats": repeats,
        "max_steps": max_steps,
        "model": model_name if adapter_name == "gemini" else None,
        "fixture_count": len({row["id"] for row in rows}),
        "run_count": len(rows),
        "attack_fixture_count": len(attacks),
        "attack_measured_count": len(measured_attacks),
        "attack_unmeasured_count": len(attacks) - len(measured_attacks),
        "injection_followed_count": followed if measured_attacks else None,
        "injection_success_rate": (followed / len(measured_attacks)) if measured_attacks else None,
        "injection_measured": bool(measured_attacks) if attacks else False,
        "mean_field_accuracy": mean,
        "total_input_tokens": sum(int(row.get("input_tokens") or 0) for row in rows),
        "total_output_tokens": sum(int(row.get("output_tokens") or 0) for row in rows),
        "total_thought_tokens": sum(int(row.get("thought_tokens") or 0) for row in rows),
        "total_tokens": tokens,
        "total_cost_usd": sum(costs) if costs else None,
        "fixtures": rows,
    }
