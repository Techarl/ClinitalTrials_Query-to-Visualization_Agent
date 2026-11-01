"""Atomic ledger replacement retries only transient Windows permission errors."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import evaluation.runtime as runtime


@pytest.fixture
def writer(monkeypatch):
    # fake_clock mutates this namespace; register restoration for every test.
    monkeypatch.setattr(runtime, "time", runtime.time)
    return runtime.__dict__


def fake_clock(writer):
    ticks, sleeps = [0.0], []
    def sleep(seconds):
        sleeps.append(seconds)
        ticks[0] += seconds
    writer["time"] = SimpleNamespace(monotonic=lambda: ticks[0], sleep=sleep)
    return ticks, sleeps


def test_atomic_replace_retries_transient_permission_and_keeps_old_content_until_success(writer, tmp_path, monkeypatch):
    target = tmp_path / "ledger.json"
    target.write_text("old")
    original, attempts = Path.replace, []
    ticks, sleeps = fake_clock(writer)
    def replacement(path, destination):
        attempts.append(1)
        assert target.read_text() == "old"
        if len(attempts) < 3:
            raise PermissionError("synthetic locked reader")
        return original(path, destination)
    monkeypatch.setattr(Path, "replace", replacement)
    writer["write"](target, {"new": 1})
    assert json.loads(target.read_text()) == {"new": 1}
    assert len(attempts) == 3 and sleeps == [0.025, 0.05]
    assert ticks[0] < 1 and not target.with_suffix(".pending").exists()


def test_atomic_replace_exhaustion_raises_first_exception_without_logging(writer, tmp_path, monkeypatch, capsys):
    target = tmp_path / "ledger.json"
    target.write_text("old")
    first, attempts = PermissionError("original locked reader"), []
    ticks, sleeps = fake_clock(writer)
    def replacement(*_):
        attempts.append(1)
        raise first if len(attempts) == 1 else PermissionError("later lock")
    monkeypatch.setattr(Path, "replace", replacement)
    with pytest.raises(PermissionError) as error:
        writer["write"](target, {"new": 1})
    assert error.value is first
    assert target.read_text() == "old" and target.with_suffix(".pending").exists()
    assert ticks[0] == pytest.approx(1) and len(attempts) <= 10 and sum(sleeps) == pytest.approx(1)
    assert capsys.readouterr() == ("", "")


def test_atomic_replace_does_not_retry_other_errors(writer, tmp_path, monkeypatch):
    ticks, sleeps = fake_clock(writer)
    error = OSError("different filesystem failure")
    def replacement(*_):
        raise error
    monkeypatch.setattr(Path, "replace", replacement)
    with pytest.raises(OSError) as result:
        writer["write"](tmp_path / "result.json", {})
    assert result.value is error and ticks[0] == 0 and sleeps == []


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows sharing lock")
def test_real_windows_reader_without_delete_sharing_is_tolerated(writer, tmp_path, monkeypatch):
    import ctypes
    import threading
    from ctypes import wintypes

    target = tmp_path / "windows-ledger.json"
    target.write_text("old")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(target), 0x80000000, 0x1, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value
    original, attempts, timers = Path.replace, [], []
    closed = threading.Event()
    def release():
        kernel.CloseHandle(handle)
        closed.set()
    def replacement(path, destination):
        try:
            return original(path, destination)
        except PermissionError:
            attempts.append(1)
            if not timers:
                timer = threading.Timer(0.06, release)
                timers.append(timer)
                timer.start()
            raise
    monkeypatch.setattr(Path, "replace", replacement)
    try:
        writer["write"](target, {"after_unlock": True})
        assert attempts and json.loads(target.read_text()) == {"after_unlock": True}
    finally:
        for timer in timers:
            timer.join(timeout=1)
        if not closed.is_set():
            release()
