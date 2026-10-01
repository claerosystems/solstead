"""The sealed recordings module: writes, sealing older footage, header swaps and removal."""

from __future__ import annotations

import os
from io import BytesIO

import pytest

from solstead import sealing
from solstead.sealed import SealedRecordings, is_sealed


def keypair():
    identity, recipient = sealing.generate_identity()
    return sealing.parse_identity(identity), sealing.parse_recipient(recipient)


@pytest.fixture
def keys():
    return []


@pytest.fixture
def sealed(tmp_path, keys):
    return SealedRecordings(lambda: keys, tmp_path / "ram")


def files(folder):
    return sorted(p.name for p in folder.rglob("*") if p.is_file()) if folder.exists() else []


# ---- writing ---------------------------------------------------------------------------------


def test_without_keys_recordings_are_stored_plain(tmp_path, sealed):
    target = tmp_path / "ssd" / "clip.mp4"
    assert sealed.write(target, lambda path: path.write_bytes(b"video")) == target
    assert sealed.write_bytes(tmp_path / "ssd" / "090000.jpg", b"frame").name == "090000.jpg"
    assert files(tmp_path / "ssd") == ["090000.jpg", "clip.mp4"]
    assert target.read_bytes() == b"video" and not is_sealed(target)


def test_with_keys_only_the_sealed_file_reaches_the_ssd(tmp_path, sealed, keys):
    secret, public = keypair()
    keys.append(public)
    staged = []

    def produce(path):
        staged.append(path)
        path.write_bytes(b"video")

    clip = sealed.write(tmp_path / "ssd" / "clip.mp4", produce)
    frame = sealed.write_bytes(tmp_path / "ssd" / "090000.jpg", b"frame")
    assert (clip.name, frame.name) == ("clip.mp4.age", "090000.jpg.age")
    assert staged[0].parent == tmp_path / "ram"  # plaintext only ever in RAM
    assert files(tmp_path / "ssd") == ["090000.jpg.age", "clip.mp4.age"]
    assert files(tmp_path / "ram") == []
    assert sealing.open_bytes(clip.read_bytes(), secret) == b"video"
    assert sealing.open_bytes(frame.read_bytes(), secret) == b"frame"


def test_a_failing_producer_leaves_nothing_anywhere(tmp_path, sealed, keys):
    def broken(path):
        path.write_bytes(b"half")
        raise OSError("camera went away")

    for enrolled in (False, True):
        if enrolled:
            keys.append(keypair()[1])
        with pytest.raises(OSError):
            sealed.write(tmp_path / "ssd" / "clip.mp4", broken)
        assert files(tmp_path / "ssd") == [] and files(tmp_path / "ram") == []


def test_keys_without_a_ram_work_folder_refuse_rather_than_stage_on_the_ssd(tmp_path):
    no_ram = SealedRecordings(lambda: [keypair()[1]])
    with pytest.raises(sealing.SealingError, match="RAM"):
        no_ram.write(tmp_path / "clip.mp4", lambda path: path.write_bytes(b"video"))
    assert files(tmp_path) == []


# ---- older footage ---------------------------------------------------------------------------


def plain_files(folder, *names, size=1000):
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, name in enumerate(names):
        path = folder / name
        path.write_bytes(bytes([i]) * size)
        os.utime(path, (1_700_000_000 + i, 1_700_000_000 + i))
        paths.append(path)
    return paths


def test_older_footage_of_every_kind_is_sealed_keeping_its_time(tmp_path, sealed, keys):
    secret, public = keypair()
    events = plain_files(tmp_path / "events", "a.mp4", "b.mp4")
    frames = plain_files(tmp_path / "timelapse", "090000.jpg")
    manual = plain_files(tmp_path / "manual", "m.mp4")
    for folder in ("events", "timelapse", "manual"):
        sealed.add_source(lambda f=tmp_path / folder: sorted(p for p in f.iterdir()
                                                            if not is_sealed(p)))  # fmt: skip
    assert sealed.seal_older() == 0 and sealed.pending  # nothing to seal to without keys

    keys.append(public)
    assert sealed.seal_older(budget_bytes=1500) == 2  # a budget's worth, at least one
    assert sealed.pending  # stopped at the budget with footage left
    assert sealed.seal_older(budget_bytes=1500) == 2
    assert not sealed.pending  # every source came up empty: stop looking each tick
    assert sealed.seal_older() == 0
    expected = {events[0]: b"\x00", events[1]: b"\x01", frames[0]: b"\x00", manual[0]: b"\x00"}
    for path, byte in expected.items():
        sealed_path = path.with_name(path.name + ".age")
        assert not path.exists()
        assert sealing.open_bytes(sealed_path.read_bytes(), secret) == byte * 1000
    assert [int(p.with_name(p.name + ".age").stat().st_mtime) for p in events] == [
        1_700_000_000, 1_700_000_001,
    ]  # fmt: skip


def test_a_crash_between_sealing_and_unlinking_resolves_to_the_sealed_copy(tmp_path, sealed, keys):
    keys.append(keypair()[1])
    (plain,) = plain_files(tmp_path / "events", "a.mp4")
    plain.with_name("a.mp4.age").write_bytes(b"sealed earlier")
    sealed.add_source(lambda: [plain] if plain.exists() else [])
    assert sealed.seal_older() == 1
    assert files(tmp_path / "events") == ["a.mp4.age"]
    assert (tmp_path / "events" / "a.mp4.age").read_bytes() == b"sealed earlier"


def test_a_recording_removed_while_it_is_sealed_never_comes_back(
    tmp_path, sealed, keys, monkeypatch
):
    keys.append(keypair()[1])
    (plain,) = plain_files(tmp_path / "events", "a.mp4")
    sealed.add_source(lambda: [plain] if plain.exists() else [])
    original = sealing.seal_file

    def owner_deletes_meanwhile(source, target, recipients):
        original(source, target, recipients)
        sealed.remove(source)  # the owner's delete lands mid-seal

    monkeypatch.setattr("solstead.sealed.seal_file", owner_deletes_meanwhile)
    assert sealed.seal_older() == 0
    assert files(tmp_path / "events") == []
    assert sealed.last_error is None and not sealed.pending


def test_an_error_stops_sealing_until_keys_change(tmp_path, sealed, keys):
    keys.append(keypair()[1])
    (plain,) = plain_files(tmp_path / "events", "a.mp4")
    failing = {"on": True}

    def source():
        if failing["on"]:
            raise OSError("SSD read error")
        return [plain] if plain.exists() else []

    sealed.add_source(source)
    assert sealed.seal_older() == 0 and sealed.last_error == "SSD read error"
    failing["on"] = False
    assert sealed.seal_older() == 0  # latched: no retry every tick
    sealed.keys_changed()
    assert sealed.last_error is None and sealed.seal_older() == 1


# ---- restoring access and removal ------------------------------------------------------------


def test_header_swap_keeps_every_key_and_skips_what_is_gone(tmp_path, sealed, keys):
    (old, old_pub), (recovery, recovery_pub), (new, new_pub) = keypair(), keypair(), keypair()
    keys.extend([old_pub, recovery_pub])
    clip = sealed.write_bytes(tmp_path / "clip.mp4", b"footage" * 1000)
    header = sealed.header(clip)
    assert clip.read_bytes().startswith(header)
    assert sealed.header(tmp_path / "clip.mp4") is None  # the plain form is never sealed

    dropped = sealing.read_header(BytesIO(sealing.seal_bytes(b"x", [recovery_pub])))[0]
    with pytest.raises(sealing.SealingError, match="every existing key"):
        sealed.replace_header(clip, dropped)

    rewrapped = sealing.rewrap_header(header, recovery, new_pub)
    assert sealed.replace_header(clip, rewrapped) == clip.stat().st_size
    for secret in (old, recovery, new):
        assert sealing.open_bytes(clip.read_bytes(), secret) == b"footage" * 1000
    assert files(tmp_path) == ["clip.mp4.age"]  # no partials left

    sealed.remove(clip)
    assert sealed.replace_header(clip, rewrapped) is None and sealed.header(clip) is None


def test_remove_takes_every_form(tmp_path, sealed):
    folder = tmp_path / "events"
    folder.mkdir()
    for name in ("a.mp4", "a.mp4.age", "a.mp4.age.1234abcd.partial", "a.mp4.partial", "b.mp4"):
        (folder / name).write_bytes(b"12345")
    assert sealed.remove(folder / "a.mp4.age") == 20
    assert files(folder) == ["b.mp4"]
