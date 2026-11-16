"""Guards against accidentally mangled templates (this happened once: a bad edit
inserted text between every character and the page still "worked")."""

from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "dashboard" / "templates"


def test_templates_are_a_sane_size():
    for f in TEMPLATES.glob("*.html"):
        lines = f.read_text(encoding="utf-8").count("\n")
        assert lines < 300, f"{f.name} has {lines} lines: probably corrupted"


def test_each_template_extends_base_at_most_once():
    for f in TEMPLATES.glob("*.html"):
        assert f.read_text(encoding="utf-8").count("{% extends") <= 1, f.name
