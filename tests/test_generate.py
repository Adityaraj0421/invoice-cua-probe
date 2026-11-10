import json
from pathlib import Path

from invoice_cua_probe.generate import generate_fixtures
from invoice_cua_probe.textutil import TAG_MAX, TAG_MIN

ROOT = Path(__file__).resolve().parents[1]
SUBLIME = (
    "https://sublime.security/blog/"
    "invoice-fraud-campaign-adds-prompt-injection-aimed-at-email-ai-agents/"
)


def _iban_ok(iban: str) -> bool:
    rearranged = iban[4:] + iban[:4]
    digits = "".join(char if char.isdigit() else str(ord(char) - 55) for char in rearranged)
    return int(digits) % 97 == 1


def test_generate_writes_eight_fixtures_with_sidecars(tmp_path: Path):
    order = generate_fixtures(tmp_path)
    assert len(order) == 8
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["order"] == order
    attacks = 0
    for fixture_id in order:
        folder = tmp_path / fixture_id
        expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
        attack = json.loads((folder / "attack.json").read_text(encoding="utf-8"))
        documents = [path for path in folder.iterdir() if path.suffix.lower() in {".pdf", ".html"}]
        assert len(documents) == 1
        for field in ("payee", "iban", "amount", "reference"):
            assert expected[field]
        assert _iban_ok(expected["iban"])
        forbidden_iban = (attack.get("forbidden") or {}).get("iban")
        if forbidden_iban:
            assert _iban_ok(forbidden_iban)
        if attack["present"]:
            attacks += 1
            assert attack["technique"]
            assert attack["sources"]
        else:
            assert attack["technique"] is None
    assert attacks == 6


def test_generate_is_reproducible(tmp_path: Path):
    first = tmp_path / "a"
    second = tmp_path / "b"
    generate_fixtures(first)
    generate_fixtures(second)
    for path in first.rglob("*"):
        if path.suffix == ".png" or path.is_dir():
            continue
        other = second / path.relative_to(first)
        assert other.read_bytes() == path.read_bytes()


def test_email_fixtures_use_the_campaign_channels(tmp_path: Path):
    generate_fixtures(tmp_path)
    tags_only = (tmp_path / "email-unicode-tags" / "message.html").read_text(encoding="utf-8")
    combined = (tmp_path / "email-hidden-span-tags" / "message.html").read_text(encoding="utf-8")
    assert any(TAG_MIN <= ord(char) <= TAG_MAX for char in tags_only)
    assert any(TAG_MIN <= ord(char) <= TAG_MAX for char in combined)
    assert "font-size: 1px" in combined
    assert 'aria-hidden="true"' in combined
    assert "height: 0" in combined
    # The address is tag-encoded in the tags-only mail, not written as ASCII.
    assert "ap.confirm+controller@payables.example" not in tags_only


def test_readme_names_every_fixture_and_the_sublime_post():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    ids = [
        "clean-pdf-northwind",
        "clean-pdf-harbor",
        "pdf-white-on-white-iban",
        "pdf-tiny-font-amount",
        "pdf-text-layer-mismatch",
        "pdf-white-footer-payee",
        "email-unicode-tags",
        "email-hidden-span-tags",
    ]
    for fixture_id in ids:
        assert fixture_id in readme
    assert SUBLIME in readme
    assert "siteguarding.com" in readme
    assert "surehand.io" in readme
    assert "does not solve prompt injection" in readme.lower()
