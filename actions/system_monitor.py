"""
System Monitor — background metric checks with voice alert support.
Zero subprocess calls on all platforms — uses ctypes/pynvml/psutil/wmi only.
"""
import ctypes
import platform
import time

import psutil

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

DEFAULT_THRESHOLDS = {
    "cpu":  90.0,
    "ram":  90.0,
    "temp": 85.0,
    "gpu":  95.0,
}

_COOLDOWN   = 300
_CPU_STREAK = 3

# ── NVML DLL cache (Windows: nvml.dll, Linux: libnvidia-ml.so.1) ─────────────
_nvml_lib: object = None
_nvml_ok:  object = None   # None=untested  True=works  False=unavailable


def _nvml_gpu() -> float:
    """GPU utilisation via NVML — zero subprocess on all platforms."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            if _OS == "Windows":
                candidates = ("nvml", r"C:\Windows\System32\nvml.dll")
                _load = ctypes.WinDLL
            else:
                candidates = (
                    "libnvidia-ml.so.1",
                    "libnvidia-ml.so",
                    "libnvidia-ml.dylib",
                )
                _load = ctypes.CDLL
            for name in candidates:
                try:
                    lib = _load(name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            _nvml_ok = False
            return -1.0

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        u = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
        _nvml_ok = True
        return float(u.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


def _get_gpu_usage() -> float:
    # pynvml — subprocess-free, works everywhere if installed
    try:
        import pynvml  # type: ignore
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
    except Exception:
        pass

    return _nvml_gpu()


def _get_cpu_temp() -> float:
    # psutil — works on Linux; occasionally Windows with proper drivers
    try:
        temps = psutil.sensors_temperatures()
        for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                     "cpu-thermal", "zenpower", "it8688"]:
            if name in temps and temps[name]:
                return temps[name][0].current
        for entries in temps.values():
            if entries:
                return entries[0].current
    except Exception:
        pass

    # Windows: wmi module (pure Python COM, zero subprocess)
    if _OS == "Windows":
        try:
            import wmi  # type: ignore
            w = wmi.WMI(namespace="root/wmi")
            tz = w.MSAcpi_ThermalZoneTemperature()
            if tz:
                return (tz[0].CurrentTemperature / 10.0) - 273.15
        except Exception:
            pass

    return -1.0


_last_net_sent = 0
_last_net_recv = 0
_last_net_time = 0.0

def get_detailed_system_metrics() -> dict:
    """Detailed system performance metrics snapshot for Task Manager UI modal."""
    global _last_net_sent, _last_net_recv, _last_net_time
    import platform
    now = time.time()
    
    cpu = 0.0
    ram_used_gb, ram_total_gb, ram_pct = 0.0, 0.0, 0.0
    disk_used_gb, disk_total_gb, disk_pct = 0.0, 0.0, 0.0
    up_speed_kb, down_speed_kb = 0.0, 0.0
    temp, gpu = -1.0, -1.0
    battery_pct, battery_plugged = None, True
    uptime_str = "0h 0m 0s"
    proc_count = 0

    # CPU
    try:
        cpu = psutil.cpu_percent(interval=None)
    except Exception:
        pass
    
    # RAM
    try:
        ram = psutil.virtual_memory()
        ram_used_gb = round(ram.used / (1024 ** 3), 1)
        ram_total_gb = round(ram.total / (1024 ** 3), 1)
        ram_pct = round(ram.percent, 1)
    except Exception:
        pass

    # Disk
    try:
        path = "C:\\" if platform.system() == "Windows" else "/"
        disk = psutil.disk_usage(path)
        disk_used_gb = round(disk.used / (1024 ** 3), 1)
        disk_total_gb = round(disk.total / (1024 ** 3), 1)
        disk_pct = round(disk.percent, 1)
    except Exception:
        pass

    # Network Speed (Upload / Download in KB/s)
    try:
        net_io = psutil.net_io_counters()
        if _last_net_time > 0:
            dt = now - _last_net_time
            if dt > 0:
                up_speed_kb = round(((net_io.bytes_sent - _last_net_sent) / 1024.0) / dt, 1)
                down_speed_kb = round(((net_io.bytes_recv - _last_net_recv) / 1024.0) / dt, 1)
        _last_net_sent = net_io.bytes_sent
        _last_net_recv = net_io.bytes_recv
        _last_net_time = now
    except Exception:
        pass

    # Temperatures & GPU
    try:
        temp = _get_cpu_temp()
    except Exception:
        pass

    try:
        gpu = _get_gpu_usage()
    except Exception:
        pass

    # Battery
    try:
        bat = psutil.sensors_battery()
        if bat:
            battery_pct = round(bat.percent, 1)
            battery_plugged = bat.power_plugged
    except Exception:
        pass

    # Uptime
    try:
        boot_time = psutil.boot_time()
        uptime_secs = max(0, now - boot_time)
        u_h = int(uptime_secs // 3600)
        u_m = int((uptime_secs % 3600) // 60)
        u_s = int(uptime_secs % 60)
        uptime_str = f"{u_h}h {u_m}m {u_s}s"
    except Exception:
        pass

    # Processes count
    try:
        proc_count = len(psutil.pids())
    except Exception:
        pass

    return {
        "cpu_pct": round(cpu, 1),
        "ram_used_gb": ram_used_gb,
        "ram_total_gb": ram_total_gb,
        "ram_pct": ram_pct,
        "disk_used_gb": disk_used_gb,
        "disk_total_gb": disk_total_gb,
        "disk_pct": disk_pct,
        "gpu_pct": round(gpu, 1) if gpu >= 0 else None,
        "cpu_temp_c": round(temp, 1) if temp > 0 else None,
        "gpu_temp_c": round(gpu, 1) if (gpu >= 0 and temp > 0) else None,
        "up_speed_kb": up_speed_kb,
        "down_speed_kb": down_speed_kb,
        "battery_pct": battery_pct,
        "battery_plugged": battery_plugged,
        "uptime": uptime_str,
        "proc_count": proc_count,
    }

def get_system_status() -> dict:
    """Snapshot of current system metrics for the system_status tool."""
    return get_detailed_system_metrics()


class SystemMonitor:
    """
    Stateful monitor — cooldown state persists across session reconnections.
    Call check() periodically; returns a [SYSTEM_ALERT] string or None.
    """

    def __init__(self, thresholds: dict | None = None):
        self.thresholds   = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self._last_alert: dict[str, float] = {}
        self._cpu_streak  = 0

    def _can_alert(self, key: str) -> bool:
        return (time.monotonic() - self._last_alert.get(key, 0)) > _COOLDOWN

TARGET_HEAVY_PROCESSES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe",
    "spotify.exe", "discord.exe", "steam.exe", "epicgameslauncher.exe",
    "vlc.exe", "photoshop.exe", "premiere.exe", "afterfx.exe"
}


def auto_close_heavy_background_apps() -> list[str]:
    """
    Terminates non-essential heavy background applications to bring system usage down.
    Protects current Python PID and HUNNY core.
    """
    closed: set[str] = set()
    my_pid = os.getpid()
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            pid = proc.info["pid"]
            name = (proc.info["name"] or "").lower()
            if pid == my_pid or "python" in name:
                continue
            if name in TARGET_HEAVY_PROCESSES:
                proc.terminate()
                closed.add(name)
        except Exception:
            continue
    import gc
    gc.collect()
    return list(closed)


class SystemMonitor:
    """
    Stateful monitor — cooldown state persists across session reconnections.
    Call check() periodically; returns a tuple of (alert_prompt, is_emergency_90, is_overload_95, closed_apps).
    """

    def __init__(self, thresholds: dict | None = None):
        self.thresholds   = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self._last_alert: dict[str, float] = {}
        self._cpu_streak  = 0
        self._last_auto_close_time = 0.0

    def _can_alert(self, key: str) -> bool:
        return (time.monotonic() - self._last_alert.get(key, 0)) > _COOLDOWN

    def _record(self, key: str):
        self._last_alert[key] = time.monotonic()

    def check_emergency(self, metrics: dict | None = None) -> dict:
        """
        Evaluates CPU and RAM metrics for Emergency Siren (>=90%) and Auto-Close (>=95%).
        Reuses pre-fetched metrics if provided to prevent duplicate hardware polling.
        """
        if metrics and "cpu_pct" in metrics and "ram_pct" in metrics:
            cpu = metrics["cpu_pct"]
            ram = metrics["ram_pct"]
        else:
            try:
                cpu = psutil.cpu_percent(interval=None)
                ram = psutil.virtual_memory().percent
            except Exception:
                return {"is_emergency_90": False, "is_overload_95": False, "closed": [], "cpu": 0, "ram": 0}

        max_usage = max(cpu, ram)
        is_emergency_90 = max_usage >= 90.0
        is_overload_95 = max_usage >= 95.0
        closed_apps: list[str] = []

        now = time.monotonic()
        if is_overload_95 and (now - self._last_auto_close_time > 15.0):
            self._last_auto_close_time = now
            closed_apps = auto_close_heavy_background_apps()

        return {
            "is_emergency_90": is_emergency_90,
            "is_overload_95": is_overload_95,
            "closed": closed_apps,
            "cpu": round(cpu, 1),
            "ram": round(ram, 1),
        }

    def check(self, metrics: dict | None = None) -> str | None:
        if metrics and "cpu_pct" in metrics and "ram_pct" in metrics:
            cpu  = metrics["cpu_pct"]
            ram  = metrics["ram_pct"]
            temp = metrics.get("cpu_temp_c") or -1.0
            gpu  = metrics.get("gpu_pct") or -1.0
        else:
            try:
                cpu  = psutil.cpu_percent(interval=None)
                ram  = psutil.virtual_memory().percent
                temp = _get_cpu_temp()
                gpu  = _get_gpu_usage()
            except Exception:
                return None

        alerts: list[str] = []

        if cpu >= self.thresholds["cpu"]:
            self._cpu_streak += 1
            if self._cpu_streak >= _CPU_STREAK and self._can_alert("cpu"):
                alerts.append(
                    f"[SYSTEM_ALERT] CPU usage has been critically high ({cpu:.0f}%) "
                    "for several seconds. Warn the user in their language and suggest "
                    "closing heavy applications."
                )
                self._record("cpu")
                self._cpu_streak = 0
        else:
            self._cpu_streak = 0

        if ram >= self.thresholds["ram"] and self._can_alert("ram"):
            alerts.append(
                f"[SYSTEM_ALERT] RAM is at {ram:.0f}% — nearly exhausted. "
                "Warn the user in their language and suggest freeing memory."
            )
            self._record("ram")

        if temp > 0 and temp >= self.thresholds["temp"] and self._can_alert("temp"):
            alerts.append(
                f"[SYSTEM_ALERT] CPU temperature is {temp:.0f}°C — above the safe limit. "
                "Warn the user in their language and advise reducing system load "
                "or checking cooling."
            )
            self._record("temp")

        if gpu >= 0 and gpu >= self.thresholds["gpu"] and self._can_alert("gpu"):
            alerts.append(
                f"[SYSTEM_ALERT] GPU load is at {gpu:.0f}%. "
                "Briefly inform the user in their language."
            )
            self._record("gpu")

        return " ".join(alerts) if alerts else None
