import json
from pathlib import Path

from invoice_cua_probe.cli import main
from invoice_cua_probe.form_server import FormServer
from invoice_cua_probe.generate import generate_fixtures
from invoice_cua_probe.runner import assert_localhost, run_probe


def test_http_transport_scores_both_reference_adapters(tmp_path: Path):
    fixtures = tmp_path / "fixtures"
    generate_fixtures(fixtures)
    out = tmp_path / "reports"
    naive = run_probe("naive", fixtures, out, transport="http")
    visible = run_probe("visible-only", fixtures, out, transport="http")
    assert naive["injection_success_rate"] == 1
    assert naive["injection_followed_count"] == 6
    assert visible["injection_success_rate"] == 0
    assert visible["mean_field_accuracy"] == 1
    html = (out / "naive" / "report.html").read_text(encoding="utf-8")
    assert "pdf-white-on-white-iban" in html
    assert "injection followed" in html
    stored = json.loads((out / "visible-only" / "report.json").read_text(encoding="utf-8"))
    assert stored["status"] == "complete"
    assert all(row["screenshot"] is None for row in stored["fixtures"])
    assert all(row["error"] is None for row in naive["fixtures"])


def test_gemini_skips_without_printing_a_key(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    out = tmp_path / "reports"
    code = main(["run", "--adapter", "gemini", "--fixtures", str(tmp_path), "--out", str(out)])
    assert code == 0
    report = json.loads((out / "gemini" / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "skipped"
    assert "not set" in report["reason"]
    captured = capsys.readouterr()
    assert "GEMINI_API_KEY=" not in captured.out


def test_gemini_with_key_does_not_skip_or_leak(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-test-value")

    def refuse_client(*_args, **_kwargs):
        raise AssertionError("Gemini client should not be constructed when fixtures are missing")

    monkeypatch.setattr("google.genai.Client", refuse_client)
    code = main(["run", "--adapter", "gemini", "--fixtures", str(tmp_path), "--out", str(tmp_path / "reports")])
    assert code == 1
    captured = capsys.readouterr()
    assert "super-secret-test-value" not in captured.out
    assert "super-secret-test-value" not in captured.err


def test_repeats_score_every_run(tmp_path: Path):
    fixtures = tmp_path / "fixtures"
    generate_fixtures(fixtures)
    report = run_probe("naive", fixtures, tmp_path / "reports", transport="http", repeats=2)
    assert report["repeats"] == 2
    assert report["run_count"] == 16
    assert report["fixture_count"] == 8
    assert report["attack_fixture_count"] == 12
    assert report["injection_followed_count"] == 12
    assert report["injection_success_rate"] == 1


def test_form_escapes_submitted_markup_and_stays_on_localhost():
    with FormServer(port=0) as server:
        assert server.httpd.server_address[0] == "127.0.0.1"
        import urllib.request
        from urllib.parse import urlencode

        payload = urlencode(
            {
                "fixture_id": "manual",
                "payee": "<script>alert(1)</script>",
                "iban": "GB33BUKB20201555555555",
                "amount": "1.00",
                "reference": "INV-1",
            }
        ).encode()
        request = urllib.request.Request(server.base_url + "/submit", data=payload, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read().decode("utf-8")
        assert "<script>" not in body
        assert "&lt;script&gt;" in body
        assert server.submissions["manual"]["payee"] == "<script>alert(1)</script>"


def test_assert_localhost_rejects_other_hosts():
    try:
        assert_localhost("https://example.com/form")
    except RuntimeError as exc:
        assert "localhost" in str(exc)
    else:
        raise AssertionError("expected a refusal")
