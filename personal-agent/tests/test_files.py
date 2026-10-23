import zipfile
from pathlib import Path

import pytest

from hermes_personal.files import FileService, PathPolicy


@pytest.fixture
def service(tmp_path):
    return FileService(PathPolicy([tmp_path], protected=[tmp_path / "private"]))


def test_write_read_move_and_archive_preserve_actual_contents(service, tmp_path):
    original = tmp_path / "notes.txt"
    assert service.run("write", str(original), content="hello 世界")["ok"]
    assert service.run("read", str(original))["content"] == "hello 世界"
    target = tmp_path / "sorted" / "notes.txt"
    assert service.run("mkdir", str(target.parent))["ok"]
    assert service.run("move", str(original), destination=str(target))["ok"]
    assert not original.exists()
    assert target.read_text(encoding="utf-8") == "hello 世界"
    archive = tmp_path / "results.zip"
    assert service.run("archive", str(target.parent), destination=str(archive))["ok"]
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.read("notes.txt").decode("utf-8") == "hello 世界"


def test_writes_and_moves_refuse_existing_destination(service, tmp_path):
    first, second = tmp_path / "a.txt", tmp_path / "b.txt"
    first.write_text("keep a")
    second.write_text("keep b")
    assert not service.run("write", str(first), content="destroy")["ok"]
    assert not service.run("move", str(first), destination=str(second))["ok"]
    assert first.read_text() == "keep a"
    assert second.read_text() == "keep b"


def test_protected_paths_and_parent_traversal_are_denied(service, tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    secret = private / "key.txt"
    secret.write_text("private")
    assert not service.run("read", str(secret))["ok"]
    assert not service.run("write", str(tmp_path / ".." / "escape.txt"), content="no")["ok"]
    assert not service.run("read", "relative.txt")["ok"]


def test_directory_listing_and_archive_never_include_protected_files(service, tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    (private / "credentials.txt").write_text("secret")
    (tmp_path / "public.txt").write_text("public")
    assert "private" not in {item["name"] for item in service.run("list", str(tmp_path))["entries"]}
    result = service.run("archive", str(tmp_path), destination=str(tmp_path / "bundle.zip"))
    assert result["ok"]
    with zipfile.ZipFile(tmp_path / "bundle.zip") as zipped:
        assert zipped.namelist() == ["public.txt"]


def test_link_cannot_escape_allowed_roots(service, tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    try:
        link = tmp_path / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("Windows symlink creation requires developer mode")
        assert not service.run("write", str(link / "escape.txt"), content="no")["ok"]
        assert not (outside / "escape.txt").exists()
    finally:
        outside.rmdir()


def test_search_is_bounded_and_does_not_read_secrets(service, tmp_path):
    (tmp_path / "one.txt").write_text("needle")
    (tmp_path / "two.txt").write_text("needle")
    (tmp_path / ".env").write_text("needle=secret")
    result = service.run("search", str(tmp_path), pattern="*", query="needle", limit=1)
    assert result["ok"] and len(result["matches"]) == 1
    assert not any(Path(item["path"]).name == ".env" for item in result["matches"])


def test_permanent_delete_is_not_a_supported_routine_action(service, tmp_path):
    path = tmp_path / "keep.txt"
    path.write_text("keep")
    assert not service.run("delete", str(path))["ok"]
    assert path.exists()


def test_directory_copy_and_rename_verify_nested_contents_and_empty_folders(service, tmp_path):
    source = tmp_path / "folder"
    (source / "nested").mkdir(parents=True)
    (source / "empty").mkdir()
    (source / "nested" / "data.txt").write_text("important")
    copied = tmp_path / "copy"
    assert service.run("copy", str(source), destination=str(copied))["ok"]
    assert (copied / "empty").is_dir()
    assert (copied / "nested" / "data.txt").read_text() == "important"
    moved = tmp_path / "moved"
    assert service.run("rename", str(copied), destination=str(moved))["ok"]
    assert not copied.exists() and (moved / "nested" / "data.txt").read_text() == "important"


def test_archive_exclusive_creation_never_removes_a_racing_existing_file(service, tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text("data")
    target = tmp_path / "racing.zip"
    original = zipfile.ZipFile
    def racing_create(path, mode="r", *args, **kwargs):
        if mode == "x":
            Path(path).write_bytes(b"somebody else's data")
        return original(path, mode, *args, **kwargs)
    monkeypatch.setattr(zipfile, "ZipFile", racing_create)
    assert not service.run("archive", str(source), destination=str(target))["ok"]
    assert target.read_bytes() == b"somebody else's data"
