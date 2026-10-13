import os
import subprocess
import sys
from unittest import mock

import pytest

from ftja import osutil


def as_windows():
    return mock.patch.multiple(osutil, WINDOWS=True, MAC=False)


def test_a_running_process_is_alive_and_a_finished_one_is_not():
    assert osutil.pid_alive(os.getpid())
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    assert not osutil.pid_alive(done.pid)
    for not_a_pid in (None, "", "abc", 0, -1):
        assert not osutil.pid_alive(not_a_pid)


def test_windows_never_signals_the_process_it_asks_about():
    # os.kill(pid, 0) is CTRL_C_EVENT on Windows: it would interrupt the wait.
    with as_windows(), mock.patch.object(osutil, "_windows_pid_alive", return_value=True) as probe, \
            mock.patch.object(osutil.os, "kill") as kill:
        assert osutil.pid_alive("4242")
        probe.assert_called_once_with(4242)
        kill.assert_not_called()


def test_windows_opens_the_folder_in_explorer():
    with as_windows(), mock.patch.object(osutil.os, "startfile", create=True) as startfile, \
            mock.patch.object(osutil.subprocess, "run") as run:
        assert osutil.open_folder("C:\\FTJA") is None
        startfile.assert_called_once_with("C:\\FTJA")
        run.assert_not_called()
    with as_windows(), mock.patch.object(osutil.os, "startfile", create=True, side_effect=OSError("no such folder")):
        assert osutil.open_folder("C:\\gone") == "no such folder"


def test_replace_waits_for_a_reader_on_windows_and_fails_at_once_elsewhere(tmp_path):
    src, dst = tmp_path / "a.tmp", tmp_path / "a.json"
    src.write_text("{}", encoding="utf-8")
    real = os.replace
    busy = [PermissionError("in use"), PermissionError("in use")]

    def flaky(a, b):
        if busy:
            raise busy.pop()
        real(a, b)

    with as_windows(), mock.patch.object(osutil.os, "replace", side_effect=flaky), \
            mock.patch.object(osutil.time, "sleep"):
        osutil.replace(str(src), str(dst))
    assert dst.read_text(encoding="utf-8") == "{}"
    with mock.patch.multiple(osutil, WINDOWS=False), \
            mock.patch.object(osutil.os, "replace", side_effect=PermissionError("denied")) as replace:
        with pytest.raises(PermissionError):
            osutil.replace(str(src), str(dst))
        assert replace.call_count == 1


def test_text_files_are_always_read_and_written_as_utf8():
    # Windows opens text in the system code page unless told otherwise.
    import glob
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    call = re.compile(r"(?<![\w.])open\(((?:[^()]|\((?:[^()]|\([^()]*\))*\))*)\)")
    missing = []
    for path in glob.glob(os.path.join(root, "ftja", "*.py")):
        with open(path, encoding="utf-8") as f:
            for number, line in enumerate(f, 1):
                for inner in call.findall(line):
                    if "encoding" not in inner and not re.search(r"""["'][rwa]?b["']""", inner):
                        missing.append(f"{os.path.basename(path)}:{number}")
    assert not missing, missing
