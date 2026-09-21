from datetime import datetime, timezone
import hashlib
from pathlib import Path
import subprocess

import pytest

from app.family.storage import (
    ImageStorageError,
    ImageTooLarge,
    LocalImageStore,
    UnsupportedImage,
)


def test_storage_module_exposes_local_image_store():
    assert LocalImageStore is not None


IMAGES = [
    ("jpg", "image/jpeg", b"\xff\xd8\xffrest-of-jpeg"),
    ("png", "image/png", b"\x89PNG\r\n\x1a\nrest-of-png"),
    ("webp", "image/webp", b"RIFF\x00\x00\x00\x00WEBPrest-of-webp"),
]


@pytest.mark.parametrize("extension,mime,data", IMAGES)
def test_save_recognizes_real_image_format_and_records_hash(tmp_path, extension, mime, data):
    store = LocalImageStore(tmp_path / "images")
    saved = store.save("student_1", "image-1", [data], datetime(2025, 2, 3, tzinfo=timezone.utc))

    assert saved.relative_path == f"student_1/2025/02/image-1.{extension}"
    assert saved.mime_type == mime
    assert saved.extension == extension
    assert saved.byte_size == len(data)
    assert saved.sha256 == hashlib.sha256(data).hexdigest()
    assert store.open(saved.relative_path).read() == data


def test_save_streams_chunks_and_hashes_combined_bytes(tmp_path):
    chunks = [b"\xff\xd8", b"\xffmiddle", b"tail"]
    stored = LocalImageStore(tmp_path / "images").save("s", "i", iter(chunks))
    combined = b"".join(chunks)
    assert stored.byte_size == len(combined)
    assert stored.sha256 == hashlib.sha256(combined).hexdigest()


def test_too_large_deletes_pending_file(tmp_path):
    root = tmp_path / "images"
    with pytest.raises(ImageTooLarge):
        LocalImageStore(root, max_bytes=5).save("s", "i", [b"\xff\xd8\xff", b"123"])
    assert list(root.rglob("*.pending")) == []
    assert list(root.rglob("i.*")) == []


@pytest.mark.parametrize("chunks", [[b"not an image"], [b"\xff\xd8"], [b"RIFF1234NOPE"], ["text"]])
def test_invalid_or_short_header_is_rejected_and_cleaned(tmp_path, chunks):
    root = tmp_path / "images"
    with pytest.raises(UnsupportedImage):
        LocalImageStore(root).save("s", "i", chunks)
    assert list(root.rglob("*.pending")) == []


def test_naive_datetime_rejected(tmp_path):
    with pytest.raises(ImageStorageError):
        LocalImageStore(tmp_path / "images").save("s", "i", [IMAGES[0][2]], datetime(2025, 1, 1))


@pytest.mark.parametrize("student,image", [("", "i"), ("..", "i"), ("a/b", "i"), ("s", ""), ("s", "../i"), ("s", "a\\b")])
def test_identifiers_reject_path_syntax(tmp_path, student, image):
    with pytest.raises(ImageStorageError):
        LocalImageStore(tmp_path / "images").save(student, image, [IMAGES[0][2]])


def test_quarantine_restore_and_remove_use_isolated_root(tmp_path):
    root, quarantine = tmp_path / "images", tmp_path / "quarantine"
    store = LocalImageStore(root, quarantine_root=quarantine)
    saved = store.save("s", "i", [IMAGES[0][2]])
    qpath = store.quarantine(saved.relative_path)
    assert not (root / saved.relative_path).exists()
    assert (quarantine / qpath).read_bytes() == IMAGES[0][2]
    store.restore(qpath, saved.relative_path)
    assert store.open(saved.relative_path).read() == IMAGES[0][2]
    qpath = store.quarantine(saved.relative_path)
    store.remove_quarantined(qpath)
    assert not (quarantine / qpath).exists()


def test_restore_never_overwrites_existing_file(tmp_path):
    store = LocalImageStore(tmp_path / "images", quarantine_root=tmp_path / "q")
    saved = store.save("s", "i", [IMAGES[0][2]])
    qpath = store.quarantine(saved.relative_path)
    restored_path = store.root / saved.relative_path
    restored_path.parent.mkdir(parents=True, exist_ok=True)
    restored_path.write_bytes(b"keep me")
    with pytest.raises(ImageStorageError):
        store.restore(qpath, saved.relative_path)
    assert restored_path.read_bytes() == b"keep me"


def test_remove_temporary_requires_safe_relative_path(tmp_path):
    root = tmp_path / "images"
    store = LocalImageStore(root)
    (root / "scratch.tmp").write_bytes(b"temp")
    store.remove_temporary("scratch.tmp")
    with pytest.raises(ImageStorageError):
        store.remove_temporary("../outside")


def test_roots_must_be_distinct_and_disjoint(tmp_path):
    with pytest.raises(ImageStorageError):
        LocalImageStore(tmp_path / "same", quarantine_root=tmp_path / "same")
    with pytest.raises(ImageStorageError):
        LocalImageStore(tmp_path / "images", quarantine_root=tmp_path / "images" / "q")


@pytest.mark.parametrize("method", ["open", "quarantine"])
def test_symlink_escape_is_rejected(tmp_path, method):
    root = tmp_path / "images"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    target = outside / "secret.jpg"
    target.write_bytes(b"secret")
    try:
        (root / "link.jpg").symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")
    store = LocalImageStore(root, quarantine_root=tmp_path / "q")
    with pytest.raises(ImageStorageError):
        getattr(store, method)("link.jpg")


def test_save_rejects_symlink_parent_escape(tmp_path):
    root, outside = tmp_path / "images", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "student").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        if __import__("os").name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "student"), str(outside)], capture_output=True)
            if result.returncode != 0:
                pytest.skip("symlinks and directory junctions are unavailable")
        else:
            pytest.skip("symlinks are unavailable")
    with pytest.raises(ImageStorageError):
        LocalImageStore(root).save("student", "image", [IMAGES[0][2]])
    assert list(outside.iterdir()) == []


def test_open_rejects_absolute_and_parent_paths(tmp_path):
    store = LocalImageStore(tmp_path / "images")
    for bad in ["../secret", str(tmp_path / "secret")]:
        with pytest.raises(ImageStorageError):
            store.open(bad)
