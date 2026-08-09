from __future__ import annotations

import ctypes
import os
import sys

from .models import AppSettings


def total_physical_memory() -> int | None:
    if sys.platform == "win32":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
        return None

    try:
        return int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        return None


def recommended_concurrency(
    cpu_count: int | None = None,
    memory_bytes: int | None = None,
) -> int:
    processors = max(1, cpu_count if cpu_count is not None else (os.cpu_count() or 1))
    memory = memory_bytes if memory_bytes is not None else total_physical_memory()

    if processors <= 4:
        cpu_limit = 1
    elif processors <= 8:
        cpu_limit = 2
    elif processors <= 16:
        cpu_limit = 3
    else:
        cpu_limit = 4

    if memory is None:
        memory_limit = 2
    else:
        gib = memory / (1024 ** 3)
        if gib < 6:
            memory_limit = 1
        elif gib < 10:
            memory_limit = 2
        elif gib < 16:
            memory_limit = 3
        else:
            memory_limit = 4
    return max(1, min(cpu_limit, memory_limit, 4))


def effective_concurrency(settings: AppSettings) -> int:
    if settings.concurrency_auto:
        return recommended_concurrency()
    return max(1, min(int(settings.concurrency), 4))
