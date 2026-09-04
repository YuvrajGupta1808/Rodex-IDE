"""Uploaded folders must not sweep in build artefacts.

Uploading a project directory included __pycache__/*.pyc and similar, which
consumed the 20-file budget, flooded the coordinator's prompt, and invited
findings about code the user never wrote.
"""

import pytest

from src.api.routes.review import is_reviewable


@pytest.mark.parametrize(
    "path",
    [
        "main.py",
        "src/app.py",
        "src/agents/coordinator.py",
        "tests/test_thing.py",
        "README.md",
    ],
)
def test_source_files_are_reviewed(path):
    assert is_reviewable(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "tests/__pycache__/test_x.cpython-311.pyc",
        "__pycache__/module.pyc",
        "src/app.pyo",
        "node_modules/lib/index.js",
        ".git/config",
        ".venv/lib/site-packages/x.py",
        "build/generated.py",
        "dist/bundle.js",
        ".pytest_cache/v/cache/nodeids",
        "native.so",
        ".env",
        "",
    ],
)
def test_artefacts_and_vendored_code_are_skipped(path):
    assert is_reviewable(path) is False


def test_windows_separators_are_handled():
    assert is_reviewable("tests\\__pycache__\\x.pyc") is False
