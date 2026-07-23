"""Measure real shell construction and idle cost without opening any tool."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from toolbox.discovery import discover
from toolbox.shell import ToolBoxShell


def _rss_bytes() -> int:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        process = kernel32.GetCurrentProcess()
        if not psapi.GetProcessMemoryInfo(
            process, ctypes.byref(counters), counters.cb
        ):
            raise OSError("GetProcessMemoryInfo failed")
        return int(counters.WorkingSetSize)
    if sys.platform.startswith("linux"):
        page_size = os.sysconf("SC_PAGE_SIZE")
        resident_pages = int(
            Path("/proc/self/statm").read_text(encoding="ascii").split()[1]
        )
        return resident_pages * page_size
    import resource

    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def main() -> int:
    start = time.perf_counter()
    app = ToolBoxShell(discover())
    app.withdraw()
    try:
        app.update()
        constructed_ms = (time.perf_counter() - start) * 1000
        assert app._active == app.HOME_ID
        assert not app._panels, "a tool or queue panel loaded before user request"

        cpu_start = time.process_time()
        idle_start = time.perf_counter()
        while time.perf_counter() - idle_start < 1.0:
            app.update()
            time.sleep(0.01)
        idle_cpu_ms = (time.process_time() - cpu_start) * 1000
        rss_mib = _rss_bytes() / (1024 * 1024)

        print(f"real shell construct + first paint : {constructed_ms:7.1f} ms")
        print(f"one-second idle UI CPU             : {idle_cpu_ms:7.1f} ms")
        print(f"working set after idle             : {rss_mib:7.1f} MiB")
        print(f"Python threads                     : {threading.active_count()}")
        print("tool/queue panels loaded            : 0")
    finally:
        app._on_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
