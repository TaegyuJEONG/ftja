"""The few things macOS and Windows do differently, in one place: is a
process still running, renaming a file over one that is being read, and the
native "choose a file" and "open this folder" windows."""
import os
import subprocess
import sys
import time

WINDOWS = sys.platform == "win32"
MAC = sys.platform == "darwin"


def _windows_pid_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def pid_alive(pid) -> bool:
    """Is this process still running? Never `os.kill(pid, 0)` on Windows:
    signal 0 is CTRL_C_EVENT there, so the check itself would interrupt the
    process it asks about."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if WINDOWS:
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True  # it exists, it just isn't ours
    except OSError:
        return False
    return True


def replace(src: str, dst: str):
    """os.replace, retried briefly: Windows refuses to rename over a file
    another process has open, and the viewer polls the files a run rewrites."""
    for attempt in range(20):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if not WINDOWS or attempt == 19:
                raise
            time.sleep(0.05)


_MAC_PICK = {
    "file": 'POSIX path of (choose file with prompt "Select a source file")',
    "folder": 'POSIX path of (choose folder with prompt "Select a source folder")',
}
# A topmost owner window, or the dialog opens behind the browser and the
# page looks stuck. UTF-8 output, or a path with non-ASCII letters is garbled.
_WINDOWS_PICK_HEAD = (
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
    "Add-Type -AssemblyName System.Windows.Forms; "
    "$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true}; "
)
_WINDOWS_PICK = {
    "file": _WINDOWS_PICK_HEAD + (
        "$d = New-Object System.Windows.Forms.OpenFileDialog -Property @{Title = 'Select a source file'}; "
        "if ($d.ShowDialog($owner) -eq 'OK') { [Console]::Out.Write($d.FileName) }"
    ),
    "folder": _WINDOWS_PICK_HEAD + (
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog -Property @{Description = 'Select a source folder'}; "
        "if ($d.ShowDialog($owner) -eq 'OK') { [Console]::Out.Write($d.SelectedPath) }"
    ),
}


def can_pick() -> bool:
    return MAC or WINDOWS


def pick_path(kind: str) -> str | None:
    """Show the system's file (or folder) chooser. -> the chosen path, or
    None when the user cancelled."""
    if MAC:
        result = subprocess.run(["osascript", "-e", _MAC_PICK[kind]], capture_output=True, text=True)
    elif WINDOWS:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", _WINDOWS_PICK[kind]],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # no console flashing behind the dialog
    else:
        raise NotImplementedError("no file chooser on this system")
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def open_folder(path: str) -> str | None:
    """Open a folder in Finder or Explorer. -> None, or what went wrong."""
    if WINDOWS:
        try:
            os.startfile(path)
        except OSError as e:
            return str(e)
        return None
    result = subprocess.run(["open" if MAC else "xdg-open", path], capture_output=True, text=True)
    if result.returncode != 0:
        return result.stderr.strip() or "the folder could not be opened"
    return None
