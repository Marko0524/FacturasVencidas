"""``.env`` loading tests. Every case uses a temporary file and a clean env."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.config import load_env_file

VARIABLES = ("SMTP_HOST", "SMTP_PASSWORD", "SMTP_PORT", "EMAIL_FROM", "NOTIFICATION_CHANNEL")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Nothing must leak between tests, or into the real process environment."""
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)


def write_env(tmp_path: Path, content: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(content, encoding="utf-8")
    return path


def test_values_are_loaded_into_the_environment(tmp_path: Path):
    load_env_file(write_env(tmp_path, "SMTP_HOST=smtp.example.com\nSMTP_PORT=587\n"))

    assert os.environ["SMTP_HOST"] == "smtp.example.com"
    assert os.environ["SMTP_PORT"] == "587"


def test_the_real_environment_wins(monkeypatch, tmp_path: Path):
    """An explicit override on the command line must beat the file."""
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")

    load_env_file(write_env(tmp_path, "SMTP_HOST=smtp.example.com\n"))

    assert os.environ["SMTP_HOST"] == "127.0.0.1"


def test_comments_blank_lines_and_export_prefix(tmp_path: Path):
    content = "\n# un comentario\n\nexport SMTP_HOST=smtp.example.com\n   \n"

    load_env_file(write_env(tmp_path, content))

    assert os.environ["SMTP_HOST"] == "smtp.example.com"


@pytest.mark.parametrize("raw", ["'s3cr3t'", '"s3cr3t"', "s3cr3t"])
def test_surrounding_quotes_are_not_part_of_the_value(tmp_path: Path, raw: str):
    load_env_file(write_env(tmp_path, f"SMTP_PASSWORD={raw}\n"))

    assert os.environ["SMTP_PASSWORD"] == "s3cr3t"


def test_a_password_with_equals_signs_survives(tmp_path: Path):
    """Base64 secrets end in '=' padding; only the first '=' is the separator."""
    load_env_file(write_env(tmp_path, "SMTP_PASSWORD=abc==\n"))

    assert os.environ["SMTP_PASSWORD"] == "abc=="


def test_lines_without_a_separator_are_ignored(tmp_path: Path):
    load_env_file(write_env(tmp_path, "ESTO_NO_ES_UNA_ASIGNACION\nEMAIL_FROM=a@b.com\n"))

    assert os.environ["EMAIL_FROM"] == "a@b.com"


def test_an_empty_value_is_kept_as_empty(tmp_path: Path):
    """An empty variable means 'not set' to the getters, which is intentional."""
    load_env_file(write_env(tmp_path, "SMTP_PASSWORD=\n"))

    assert os.environ["SMTP_PASSWORD"] == ""


def test_a_missing_file_is_not_an_error(tmp_path: Path):
    load_env_file(tmp_path / "no-existe.env")

    assert "SMTP_HOST" not in os.environ
