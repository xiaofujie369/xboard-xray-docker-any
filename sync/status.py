import time
import shutil
from pathlib import Path

def read_cpu_snapshot(path="/proc/stat"):
    try:
        line = Path(path).read_text().splitlines()[0]
    except Exception:
        return None

    parts = line.split()
    if not parts or parts[0] != "cpu":
        return None

    values = []
    for item in parts[1:]:
        try:
            values.append(int(item))
        except Exception:
            values.append(0)

    if len(values) < 4:
        return None

    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    return total, idle

def collect_cpu_percent():
    first = read_cpu_snapshot()
    if not first:
        return 0.0

    time.sleep(0.1)

    second = read_cpu_snapshot()
    if not second:
        return 0.0

    total_delta = second[0] - first[0]
    idle_delta = second[1] - first[1]
    if total_delta <= 0:
        return 0.0

    return round(max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100)), 2)

def collect_memory_status(path="/proc/meminfo"):
    try:
        lines = Path(path).read_text().splitlines()
    except Exception:
        return (0, 0), (0, 0)

    values = {}
    for line in lines:
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        parts = raw.strip().split()
        if not parts:
            continue
        try:
            values[key] = int(parts[0]) * 1024
        except Exception:
            continue

    mem_total = values.get("MemTotal", 0)
    mem_available = values.get("MemAvailable", values.get("MemFree", 0))
    swap_total = values.get("SwapTotal", 0)
    swap_free = values.get("SwapFree", 0)

    mem_used = max(0, mem_total - mem_available)
    swap_used = max(0, swap_total - swap_free)
    return (mem_total, mem_used), (swap_total, swap_used)

def collect_disk_status(path="/"):
    try:
        usage = shutil.disk_usage(path)
        return usage.total, usage.used
    except Exception:
        return 0, 0

def collect_status():
    mem, swap = collect_memory_status()
    disk = collect_disk_status()
    return {
        "cpu": collect_cpu_percent(),
        "mem": {"total": mem[0], "used": mem[1]},
        "swap": {"total": swap[0], "used": swap[1]},
        "disk": {"total": disk[0], "used": disk[1]},
    }
