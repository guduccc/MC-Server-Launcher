"""跨平台采集进程 CPU / 内存占用（纯标准库，不依赖 psutil）。

Windows 走 psapi + kernel32，Linux/macOS 走 /proc 或 ps 命令。
采集不到时返回 None，面板会显示 "-"。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

# --------------------------------------------------------------- Windows

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
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
            ("PrivateUsage", ctypes.c_size_t),
        ]

    class _MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def _open_process(pid: int):
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            handle = k32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
        return handle or None

    def _rss_windows(pid: int):
        handle = _open_process(pid)
        if not handle:
            return None
        try:
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            counters = _PROCESS_MEMORY_COUNTERS_EX()
            counters.cb = ctypes.sizeof(counters)
            ok = psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb
            )
            if not ok:
                return None
            return int(counters.WorkingSetSize)
        finally:
            ctypes.WinDLL("kernel32").CloseHandle(handle)

    def _cpu_seconds_windows(pid: int):
        handle = _open_process(pid)
        if not handle:
            return None
        try:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)

            class FILETIME(ctypes.Structure):
                _fields_ = [("dwLowDateTime", wintypes.DWORD),
                            ("dwHighDateTime", wintypes.DWORD)]

            def _to_int(ft):
                return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

            created, exited, kernel, user = (FILETIME(), FILETIME(), FILETIME(), FILETIME())
            ok = k32.GetProcessTimes(
                handle,
                ctypes.byref(created), ctypes.byref(exited),
                ctypes.byref(kernel), ctypes.byref(user),
            )
            if not ok:
                return None
            # FILETIME 单位是 100 纳秒
            return (_to_int(kernel) + _to_int(user)) / 1e7
        finally:
            ctypes.WinDLL("kernel32").CloseHandle(handle)

    def _children_windows(pid: int):
        """Java 服务端有时是 cmd/bat 的子进程，Windows 上简单返回空。"""
        return []

    def system_memory():
        st = _MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(st)
        if ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx(
            ctypes.byref(st)
        ):
            return {"total": int(st.ullTotalPhys),
                    "used": int(st.ullTotalPhys - st.ullAvailPhys)}
        return None

# --------------------------------------------------------------- POSIX


else:

    def _rss_posix(pid: int):
        try:
            with open(f"/proc/{pid}/statm", "r", encoding="ascii") as fp:
                pages = int(fp.read().split()[1])
            return pages * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, IndexError):
            try:
                out = subprocess.run(
                    ["ps", "-o", "rss=", "-p", str(pid)],
                    capture_output=True, text=True, timeout=3,
                ).stdout.strip()
                return int(out) * 1024 if out else None
            except (OSError, ValueError, subprocess.SubprocessError):
                return None

    def _cpu_seconds_posix(pid: int):
        try:
            with open(f"/proc/{pid}/stat", "r", encoding="ascii") as fp:
                parts = fp.read().rsplit(")", 1)[1].split()
            ticks = int(parts[11]) + int(parts[12])  # utime + stime
            return ticks / os.sysconf("SC_CLK_TCK")
        except (OSError, ValueError, IndexError):
            try:
                out = subprocess.run(
                    ["ps", "-o", "time=", "-p", str(pid)],
                    capture_output=True, text=True, timeout=3,
                ).stdout.strip()
                if not out:
                    return None
                h, m, s = (out.split(":") + ["0", "0"])[:3]
                return int(h) * 3600 + int(m) * 60 + float(s)
            except (OSError, ValueError, subprocess.SubprocessError):
                return None

    def _children_posix(pid: int):
        try:
            out = subprocess.run(
                ["ps", "-o", "pid=", "--ppid", str(pid)],
                capture_output=True, text=True, timeout=3,
            ).stdout
            return [int(x) for x in out.split() if x.strip().isdigit()]
        except (OSError, subprocess.SubprocessError):
            return []

    def system_memory():
        try:
            info = {}
            with open("/proc/meminfo", "r", encoding="ascii") as fp:
                for line in fp:
                    key, _, rest = line.partition(":")
                    info[key] = int(rest.split()[0]) * 1024
            total = info.get("MemTotal")
            avail = info.get("MemAvailable", info.get("MemFree"))
            if total:
                return {"total": total, "used": total - (avail or 0)}
        except (OSError, ValueError, IndexError):
            pass
        if sys.platform == "darwin":
            try:
                total = int(subprocess.run(["sysctl", "-n", "hw.memsize"],
                                           capture_output=True, text=True).stdout)
                vm = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
                page = 4096
                free = sum(int(l.split(":")[1].strip().rstrip("."))
                           for l in vm.splitlines() if l.startswith(("Pages free", "Pages inactive")))
                return {"total": total, "used": total - free * page}
            except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                return None
        return None


# --------------------------------------------------------------- 对外接口

_cache: dict[int, tuple[float, float]] = {}


def process_stats(pid: int | None) -> dict:
    """返回 {rss, cpu, cpu_seconds}。

    cpu 是相对单核的百分比，需要两次采样才有意义，内部做了缓存。
    """
    if not pid:
        return {"rss": None, "cpu": None}
    if os.name == "nt":
        rss = _rss_windows(pid)
        cpu_s = _cpu_seconds_windows(pid)
    else:
        rss = _rss_posix(pid)
        cpu_s = _cpu_seconds_posix(pid)

    cpu_pct = None
    if cpu_s is not None:
        ts = time.time()
        prev = _cache.get(pid)
        _cache[pid] = (ts, cpu_s)
        if prev and ts > prev[0]:
            cpu_pct = max(0.0, (cpu_s - prev[1]) / (ts - prev[0]) * 100.0)
    else:
        _cache.pop(pid, None)

    return {"rss": rss, "cpu": round(cpu_pct, 1) if cpu_pct is not None else None}


def forget(pid: int | None) -> None:
    if pid:
        _cache.pop(pid, None)


def cpu_count() -> int:
    return os.cpu_count() or 1
