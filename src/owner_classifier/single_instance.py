from __future__ import annotations

import ctypes
import os


ERROR_ALREADY_EXISTS = 183


class SingleInstanceGuard:
    def __init__(self, name: str) -> None:
        self.name = name
        self._handle: int | None = None
        self._kernel32 = None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        if os.name != "nt":
            self._handle = 1
            return True

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_bool

        handle = kernel32.CreateMutexW(None, False, self.name)
        error = ctypes.get_last_error()
        if not handle:
            raise OSError(error, "无法创建程序单实例锁")
        if error == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._kernel32 = kernel32
        self._handle = int(handle)
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        if os.name == "nt" and self._kernel32 is not None:
            self._kernel32.CloseHandle(self._handle)
        self._kernel32 = None
        self._handle = None

    def __del__(self) -> None:
        self.release()
