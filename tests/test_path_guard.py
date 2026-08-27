"""Tests for workspace path confinement."""

from pathlib import Path

import pytest

from tools.filesystem import PathViolation, resolve_in_workspace


def test_resolve_inside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "src" / "module.py"

    resolved = resolve_in_workspace(workspace, "src/module.py")

    assert resolved == target.resolve()


def test_rejects_parent_directory_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")

    with pytest.raises(PathViolation, match="escapes workspace"):
        resolve_in_workspace(workspace, "../secret.txt", must_exist=True)


def test_rejects_absolute_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    absolute_target = tmp_path / "outside.txt"

    with pytest.raises(PathViolation, match="absolute paths"):
        resolve_in_workspace(workspace, str(absolute_target))


def test_rejects_symlink_escape_when_supported(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = workspace / "outside-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks is not permitted on this system")

    with pytest.raises(PathViolation, match="escapes workspace"):
        resolve_in_workspace(workspace, "outside-link/secret.txt", must_exist=True)

