import os
import time

import pytest

from solstead.storage import Storage, StorageUnavailable


def test_missing_mount_is_unavailable(tmp_path):
    storage = Storage(tmp_path / "ssd", require_mount=True)
    with pytest.raises(StorageUnavailable, match="not mounted"):
        storage.check()
    assert storage.status()["ready"] is False


def test_usage_and_status(storage_root):
    storage = Storage(storage_root, require_mount=False)
    usage = storage.usage()
    assert usage["total_bytes"] > 0 and usage["free_bytes"] > 0
    assert storage.status()["ready"] is True


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permission bits")
def test_read_only_root_is_unavailable(storage_root):
    storage_root.chmod(0o500)
    try:
        with pytest.raises(StorageUnavailable, match="not writable"):
            Storage(storage_root, require_mount=False).check()
    finally:
        storage_root.chmod(0o700)


def test_reserve_clip_and_find_it_by_id(storage_root):
    storage = Storage(storage_root, require_mount=False)
    target = storage.reserve_clip()
    assert target.suffix == ".mp4" and target.parent == storage_root / "manual"
    assert storage.clip_path(target.name) is None  # nothing stored there yet
    target.write_bytes(b"video")
    assert storage.clip_path(target.name) == target
    assert storage.describe(target)["size_bytes"] == 5
    assert storage.plain_clips() == [target]
    for bad in ("../x.mp4", "x.mp4", target.name + ".partial"):
        assert storage.clip_path(bad) is None


def test_reserve_refuses_when_nearly_full(storage_root):
    storage = Storage(storage_root, require_mount=False, min_free_bytes=2**62)
    with pytest.raises(StorageUnavailable, match="free"):
        storage.reserve_clip()


def test_clips_newest_first_and_ignores_partials(storage_root):
    storage = Storage(storage_root, require_mount=False)
    folder = storage_root / "manual"
    folder.mkdir()
    old, new = folder / "old.mp4", folder / "new.mp4"
    old.write_bytes(b"1")
    os.utime(old, (time.time() - 100, time.time() - 100))
    new.write_bytes(b"22")
    (folder / "x.mp4.partial").write_bytes(b"3")
    ids = [c["id"] for c in storage.clips()]
    assert ids == ["new.mp4", "old.mp4"]


def test_clips_empty_without_folder(storage_root):
    assert Storage(storage_root, require_mount=False).clips() == []
