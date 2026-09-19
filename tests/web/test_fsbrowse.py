"""In-app folder browser: GET /api/fs/list."""

from __future__ import annotations

import sys
from pathlib import Path

from atomik_meme_web import fsbrowse

_FILE_ATTRIBUTE_HIDDEN = 0x2


def _set_windows_hidden(path: Path) -> None:
    import ctypes

    ctypes.windll.kernel32.SetFileAttributesW(str(path), _FILE_ATTRIBUTE_HIDDEN)


def test_roots_only_when_no_path_given():
    result = fsbrowse.list_directory(None)
    assert result["path"] is None
    assert result["parent"] is None
    assert result["entries"] == []
    assert len(result["roots"]) >= 1


def test_lists_child_directories_only_not_files(tmp_path: Path):
    (tmp_path / "sub1").mkdir()
    (tmp_path / "sub2").mkdir()
    (tmp_path / "a_file.txt").write_text("x", encoding="utf-8")

    result = fsbrowse.list_directory(str(tmp_path))
    names = {e["name"] for e in result["entries"]}
    assert names == {"sub1", "sub2"}
    assert result["path"] == str(tmp_path)
    assert result["parent"] == str(tmp_path.parent)


def test_has_children_reflects_nested_subdirectories(tmp_path: Path):
    (tmp_path / "with_child" / "grandchild").mkdir(parents=True)
    (tmp_path / "without_child").mkdir()

    result = fsbrowse.list_directory(str(tmp_path))
    by_name = {e["name"]: e for e in result["entries"]}
    assert by_name["with_child"]["has_children"] is True
    assert by_name["without_child"]["has_children"] is False


def test_dot_prefixed_dirs_are_skipped_on_every_platform(tmp_path: Path):
    """`.`-prefixed is skipped everywhere, including Windows -- e.g. `.pytest_cache`,
    `.ruff_cache`, `.venv` must never show up in the picker there either, even though Windows
    itself attaches no special meaning to a leading dot (unlike the hidden *attribute*, which is
    a separate, additional check -- see the next test)."""
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / ".venv").mkdir()
    (tmp_path / "visible").mkdir()
    result = fsbrowse.list_directory(str(tmp_path))
    names = {e["name"] for e in result["entries"]}
    assert names == {"visible"}


def test_windows_hidden_attribute_is_skipped_independent_of_naming(tmp_path: Path):
    """On Windows, a directory with no dot-prefix but the actual hidden/system attribute set
    must also be skipped (the attribute check is additional to, not a replacement for, the
    `.`-prefix check above)."""
    if not sys.platform.startswith("win"):
        return
    (tmp_path / "secretly_hidden").mkdir()
    (tmp_path / "visible").mkdir()
    _set_windows_hidden(tmp_path / "secretly_hidden")
    result = fsbrowse.list_directory(str(tmp_path))
    names = {e["name"] for e in result["entries"]}
    assert names == {"visible"}


def test_known_windows_system_dirs_are_skipped(tmp_path: Path):
    (tmp_path / "System Volume Information").mkdir()
    (tmp_path / "$RECYCLE.BIN").mkdir()
    (tmp_path / "normal").mkdir()
    result = fsbrowse.list_directory(str(tmp_path))
    names = {e["name"] for e in result["entries"]}
    assert names == {"normal"}


def test_relative_path_is_400():
    try:
        fsbrowse.list_directory("relative/path")
        raised = False
    except fsbrowse.FsListError as exc:
        raised = True
        assert exc.status_code == 400
    assert raised


def test_missing_path_is_404(tmp_path: Path):
    try:
        fsbrowse.list_directory(str(tmp_path / "does-not-exist"))
        raised = False
    except fsbrowse.FsListError as exc:
        raised = True
        assert exc.status_code == 404
    assert raised


def test_file_path_is_404_not_a_directory(tmp_path: Path):
    a_file = tmp_path / "a.txt"
    a_file.write_text("x", encoding="utf-8")
    try:
        fsbrowse.list_directory(str(a_file))
        raised = False
    except fsbrowse.FsListError as exc:
        raised = True
        assert exc.status_code == 404
    assert raised


def test_results_are_sorted_case_insensitively(tmp_path: Path):
    for name in ("banana", "Apple", "cherry", "apple2"):
        (tmp_path / name).mkdir()
    result = fsbrowse.list_directory(str(tmp_path))
    names = [e["name"] for e in result["entries"]]
    assert names == sorted(names, key=str.lower)


def test_results_are_capped_at_500_with_truncated_flag(tmp_path: Path):
    for i in range(520):
        (tmp_path / f"d{i:04d}").mkdir()
    result = fsbrowse.list_directory(str(tmp_path))
    assert len(result["entries"]) == 500
    assert result["truncated"] is True


def test_not_truncated_when_under_the_cap(tmp_path: Path):
    (tmp_path / "only_one").mkdir()
    result = fsbrowse.list_directory(str(tmp_path))
    assert "truncated" not in result or result["truncated"] is False


def test_api_fs_list_endpoint(client, tmp_path: Path):
    (tmp_path / "child").mkdir()
    resp = client.get("/api/fs/list", params={"path": str(tmp_path)})
    assert resp.status_code == 200
    data = resp.json()
    assert any(e["name"] == "child" for e in data["entries"])


def test_api_fs_list_relative_path_400(client):
    resp = client.get("/api/fs/list", params={"path": "relative"})
    assert resp.status_code == 400
    assert "detail" in resp.json()


def test_api_fs_list_missing_path_404(client, tmp_path: Path):
    resp = client.get("/api/fs/list", params={"path": str(tmp_path / "nope")})
    assert resp.status_code == 404


def test_api_fs_list_no_path_returns_roots(client):
    resp = client.get("/api/fs/list")
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] is None
    assert len(data["roots"]) >= 1
