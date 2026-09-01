import time
import subprocess
import platform
import shutil

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

_SYSTEM = platform.system()

_APP_ALIASES: dict[str, dict[str, str]] = {

    "chrome":             {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "google chrome":      {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "gpt":                {"Windows": "https://chatgpt.com",     "Darwin": "https://chatgpt.com",  "Linux": "https://chatgpt.com"},
    "chatgpt":            {"Windows": "https://chatgpt.com",     "Darwin": "https://chatgpt.com",  "Linux": "https://chatgpt.com"},
    "antigravity":        {"Windows": "antigravity",             "Darwin": "AntiGravity",          "Linux": "antigravity"},
    "firefox":            {"Windows": "firefox",                 "Darwin": "Firefox",              "Linux": "firefox"},
    "edge":               {"Windows": "msedge",                  "Darwin": "Microsoft Edge",       "Linux": "microsoft-edge"},
    "brave":              {"Windows": "brave",                   "Darwin": "Brave Browser",        "Linux": "brave-browser"},
    "safari":             {"Windows": "msedge",                  "Darwin": "Safari",               "Linux": "firefox"},
    "opera":              {"Windows": "opera",                   "Darwin": "Opera",                "Linux": "opera"},
    "whatsapp":           {"Windows": "WhatsApp",                "Darwin": "WhatsApp",             "Linux": "whatsapp"},
    "telegram":           {"Windows": "Telegram",                "Darwin": "Telegram",             "Linux": "telegram"},
    "discord":            {"Windows": "Discord",                 "Darwin": "Discord",              "Linux": "discord"},
    "slack":              {"Windows": "Slack",                   "Darwin": "Slack",                "Linux": "slack"},
    "zoom":               {"Windows": "Zoom",                    "Darwin": "zoom.us",              "Linux": "zoom"},
    "teams":              {"Windows": "msteams",                 "Darwin": "Microsoft Teams",      "Linux": "teams"},
    "skype":              {"Windows": "skype",                   "Darwin": "Skype",                "Linux": "skype"},
    "signal":             {"Windows": "signal",                  "Darwin": "Signal",               "Linux": "signal"},
    "spotify":            {"Windows": "Spotify",                 "Darwin": "Spotify",              "Linux": "spotify"},
    "vlc":                {"Windows": "vlc",                     "Darwin": "VLC",                  "Linux": "vlc"},
    "netflix":            {"Windows": "Netflix",                 "Darwin": "Netflix",              "Linux": "firefox"},
    "vscode":             {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "visual studio code": {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "code":               {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "terminal":           {"Windows": "wt",                      "Darwin": "Terminal",             "Linux": "x-terminal-emulator"},
    "cmd":                {"Windows": "cmd.exe",                 "Darwin": "Terminal",             "Linux": "bash"},
    "powershell":         {"Windows": "powershell.exe",          "Darwin": "Terminal",             "Linux": "bash"},
    "postman":            {"Windows": "Postman",                 "Darwin": "Postman",              "Linux": "postman"},
    "git":                {"Windows": "git-bash",                "Darwin": "Terminal",             "Linux": "bash"},
    "figma":              {"Windows": "Figma",                   "Darwin": "Figma",                "Linux": "figma"},
    "blender":            {"Windows": "blender",                 "Darwin": "Blender",              "Linux": "blender"},
    "word":               {"Windows": "winword",                 "Darwin": "Microsoft Word",       "Linux": "libreoffice --writer"},
    "excel":              {"Windows": "excel",                   "Darwin": "Microsoft Excel",      "Linux": "libreoffice --calc"},
    "powerpoint":         {"Windows": "powerpnt",                "Darwin": "Microsoft PowerPoint", "Linux": "libreoffice --impress"},
    "libreoffice":        {"Windows": "soffice",                 "Darwin": "LibreOffice",          "Linux": "libreoffice"},
    "notepad":            {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "textedit":           {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "explorer":           {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "file explorer":      {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "file manager":       {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "finder":             {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "task manager":       {"Windows": "taskmgr.exe",             "Darwin": "Activity Monitor",     "Linux": "gnome-system-monitor"},
    "settings":           {"Windows": "ms-settings:",            "Darwin": "System Preferences",   "Linux": "gnome-control-center"},
    "system settings":    {"Windows": "ms-settings:",            "Darwin": "System Preferences",   "Linux": "gnome-control-center"},
    "calculator":         {"Windows": "calc.exe",                "Darwin": "Calculator",           "Linux": "gnome-calculator"},
    "paint":              {"Windows": "mspaint.exe",             "Darwin": "Preview",              "Linux": "gimp"},
    "instagram":          {"Windows": "Instagram",               "Darwin": "Instagram",            "Linux": "firefox"},
    "tiktok":             {"Windows": "TikTok",                  "Darwin": "TikTok",               "Linux": "firefox"},
    "notion":             {"Windows": "Notion",                  "Darwin": "Notion",               "Linux": "notion"},
    "obsidian":           {"Windows": "Obsidian",                "Darwin": "Obsidian",             "Linux": "obsidian"},
    "capcut":             {"Windows": "CapCut",                  "Darwin": "CapCut",               "Linux": "capcut"},
    "steam":              {"Windows": "steam",                   "Darwin": "Steam",                "Linux": "steam"},
    "epic":               {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
    "epic games":         {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
}


def _normalize(raw: str) -> str:
    key = raw.lower().strip()

    if key in _APP_ALIASES:
        return _APP_ALIASES[key].get(_SYSTEM, raw)

    for alias_key, os_map in _APP_ALIASES.items():
        if alias_key in key or key in alias_key:
            return os_map.get(_SYSTEM, raw)

    return raw  

def _launch_windows(app_name: str) -> bool:
    if app_name.startswith("http://") or app_name.startswith("https://"):
        import webbrowser
        try:
            webbrowser.open(app_name)
            return True
        except Exception:
            pass

    # Direct check for Chrome executable paths
    if app_name.lower() in ("chrome", "google chrome"):
        import os
        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
        for path in chrome_paths:
            if os.path.exists(path):
                try:
                    subprocess.Popen([path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(1.0)
                    return True
                except Exception:
                    pass

    # Direct check for AntiGravity executable paths
    if app_name.lower() in ("antigravity", "antigravity.exe"):
        import os
        ag_paths = [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\AntiGravity\AntiGravity.exe"),
            os.path.expandvars(r"%PROGRAMFILES%\AntiGravity\AntiGravity.exe"),
            os.path.expandvars(r"%USERPROFILE%\AppData\Local\Programs\antigravity\AntiGravity.exe"),
            os.path.expandvars(r"%USERPROFILE%\AppData\Local\Programs\AntiGravity\AntiGravity.exe"),
        ]
        for path in ag_paths:
            if os.path.exists(path):
                try:
                    subprocess.Popen([path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(1.0)
                    return True
                except Exception:
                    pass

    if shutil.which(app_name) or shutil.which(app_name.split(".")[0]):
        try:
            subprocess.Popen(
                app_name,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
            return True
        except Exception as e:
            print(f"[open_app] subprocess failed: {e}")

    if ":" in app_name or app_name.lower() in ("spotify", "antigravity", "chrome"):
        try:
            subprocess.Popen(f"start {app_name}", shell=True)
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.PAUSE = 0.1
        pyautogui.press("win")
        time.sleep(0.7)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.9)
        pyautogui.press("enter")
        time.sleep(2.5)
        return True
    except Exception as e:
        print(f"[open_app] Start Menu search failed: {e}")

    return False


def _launch_macos(app_name: str) -> bool:

    try:
        result = subprocess.run(
            ["open", "-a", app_name],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["open", "-a", f"{app_name}.app"],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    binary = shutil.which(app_name) or shutil.which(app_name.lower())
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.hotkey("command", "space")
        time.sleep(0.6)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.8)
        pyautogui.press("enter")
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f"[open_app] Spotlight failed: {e}")

    return False


_LINUX_TERMINAL_FALLBACKS = [
    "x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal",
    "xterm", "lxterminal", "mate-terminal", "tilix", "alacritty", "kitty",
]

def _launch_linux(app_name: str) -> bool:

    # terminal emulators: try common ones in order
    if app_name in ("x-terminal-emulator", "gnome-terminal", "terminal"):
        for term in _LINUX_TERMINAL_FALLBACKS:
            if shutil.which(term):
                try:
                    subprocess.Popen([term], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(1.0)
                    return True
                except Exception:
                    continue

    binary = (
        shutil.which(app_name) or
        shutil.which(app_name.lower()) or
        shutil.which(app_name.lower().replace(" ", "-")) or
        shutil.which(app_name.lower().replace(" ", "_"))
    )
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        subprocess.run(
            ["xdg-open", app_name],
            capture_output=True, timeout=5
        )
        return True
    except Exception:
        pass

    for desktop_name in [
        app_name.lower(),
        app_name.lower().replace(" ", "-"),
        app_name.lower().replace(" ", ""),
    ]:
        try:
            result = subprocess.run(
                ["gtk-launch", desktop_name],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

    return False


_OS_LAUNCHERS = {
    "Windows": _launch_windows,
    "Darwin":  _launch_macos,
    "Linux":   _launch_linux,
}

def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    app_name = (parameters or {}).get("app_name", "").strip()

    if not app_name:
        return "No application name provided."

    launcher = _OS_LAUNCHERS.get(_SYSTEM)
    if launcher is None:
        return f"Unsupported operating system: {_SYSTEM}"

    normalized = _normalize(app_name)
    print(f"[open_app] Launching: '{app_name}' → '{normalized}' ({_SYSTEM})")

    if player:
        player.write_log(f"[open_app] {app_name}")

    try:
        # Special handling for Spotify (desktop app first, web version fallback if not installed)
        if app_name.lower() in ("spotify", "open spotify"):
            if launcher("Spotify") or launcher("spotify:"):
                return "Opened Spotify desktop app."
            import webbrowser
            try:
                webbrowser.open("https://open.spotify.com")
                return "Opened Spotify (web version)."
            except Exception as e:
                return f"Failed to open Spotify: {e}"

        if launcher(normalized):
            return f"Opened {app_name}."
        if normalized.lower() != app_name.lower():
            if launcher(app_name):
                return f"Opened {app_name}."
        return (
            f"Could not confirm that {app_name} launched. "
            f"It may still be loading, or it might not be installed."
        )
    except Exception as e:
        print(f"[open_app] Error: {e}")
        return f"Failed to open {app_name}: {e}"


def is_app_running(app_name: str) -> bool:
    """Checks if an application process is currently running on the system."""
    if not _PSUTIL:
        return True
    
    target = app_name.lower().strip()
    exe_map = {
        "whatsapp": ["whatsapp.exe", "whatsapp"],
        "chrome": ["chrome.exe", "google chrome"],
        "google chrome": ["chrome.exe", "google chrome"],
        "spotify": ["spotify.exe", "spotify"],
        "firefox": ["firefox.exe", "firefox"],
        "edge": ["msedge.exe", "edge"],
        "brave": ["brave.exe", "brave"],
        "discord": ["discord.exe", "discord"],
        "telegram": ["telegram.exe", "telegram"],
        "vlc": ["vlc.exe", "vlc"],
        "vscode": ["code.exe", "code"],
        "code": ["code.exe", "code"],
        "notepad": ["notepad.exe", "notepad"],
        "word": ["winword.exe", "winword"],
        "excel": ["excel.exe", "excel"],
        "powerpoint": ["powerpnt.exe", "powerpnt"],
    }
    
    targets = exe_map.get(target, [target])
    
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info["name"] or "").lower()
            if any(t in name for t in targets):
                return True
        except Exception:
            continue
    return False


def close_application(app_name: str) -> str:
    """Safely closes an open application or tab. Returns natural error if not running."""
    import time
    start_t = time.perf_counter()
    raw = app_name.lower().strip()
    
    # 1. Handle tab / window / web closing
    if raw in ("this tab", "tab", "close tab", "this website", "website", "close website"):
        try:
            import pyautogui
            pyautogui.hotkey("ctrl", "w")
            elapsed = (time.perf_counter() - start_t) * 1000.0
            print(f"[ULTRON LOG] Command: 'Close Tab' | Action: close_tab | Status: SUCCESS | Time: {elapsed:.1f}ms")
            return "Closed active tab."
        except Exception as e:
            return f"Failed to close tab: {e}"

    if raw in ("this app", "this window", "active window", "window", "close window"):
        try:
            import pyautogui
            pyautogui.hotkey("alt", "f4")
            elapsed = (time.perf_counter() - start_t) * 1000.0
            print(f"[ULTRON LOG] Command: 'Close Window' | Action: close_window | Status: SUCCESS | Time: {elapsed:.1f}ms")
            return "Closed active window."
        except Exception as e:
            return f"Failed to close window: {e}"

    if raw in ("video", "music", "song", "media", "youtube"):
        try:
            import pyautogui
            pyautogui.press("playpause")
            elapsed = (time.perf_counter() - start_t) * 1000.0
            print(f"[ULTRON LOG] Command: 'Stop Media' | Action: media_stop | Status: SUCCESS | Time: {elapsed:.1f}ms")
            return "Stopped media playback."
        except Exception as e:
            return f"Failed to stop media: {e}"

    if raw == "all windows":
        try:
            import pyautogui
            pyautogui.hotkey("win", "d")
            elapsed = (time.perf_counter() - start_t) * 1000.0
            print(f"[ULTRON LOG] Command: 'Close All Windows' | Action: show_desktop | Status: SUCCESS | Time: {elapsed:.1f}ms")
            return "Minimized all open windows."
        except Exception as e:
            return f"Failed: {e}"

    # 2. Check if application process is running
    clean_name = raw.replace("close", "").replace("app", "").replace("open", "").strip()
    if not is_app_running(clean_name or raw):
        elapsed = (time.perf_counter() - start_t) * 1000.0
        print(f"[ULTRON LOG] Command: 'Close {app_name}' | Action: close_app | Status: NOT_RUNNING | Time: {elapsed:.1f}ms")
        return "That application is not currently open."

    # 3. Terminate running process
    closed = False
    if _SYSTEM == "Windows":
        exe_map = {
            "whatsapp": "WhatsApp.exe",
            "chrome": "chrome.exe",
            "google chrome": "chrome.exe",
            "spotify": "Spotify.exe",
            "firefox": "firefox.exe",
            "edge": "msedge.exe",
            "brave": "brave.exe",
            "discord": "Discord.exe",
            "telegram": "Telegram.exe",
            "vlc": "vlc.exe",
            "vscode": "Code.exe",
            "notepad": "notepad.exe",
        }
        exe_name = exe_map.get(clean_name, f"{clean_name}.exe")
        try:
            kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if _SYSTEM == "Windows" else {}
            res = subprocess.run(["taskkill", "/im", exe_name, "/f"], capture_output=True, text=True, **kw)
            if res.returncode == 0:
                closed = True
        except Exception:
            pass

    if not closed and _PSUTIL:
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = (proc.info["name"] or "").lower()
                if clean_name in name:
                    proc.terminate()
                    closed = True
            except Exception:
                continue

    elapsed = (time.perf_counter() - start_t) * 1000.0
    if closed:
        print(f"[ULTRON LOG] Command: 'Close {app_name}' | Action: close_app({clean_name}) | Status: SUCCESS | Time: {elapsed:.1f}ms")
        return f"Closed {app_name}."
    else:
        print(f"[ULTRON LOG] Command: 'Close {app_name}' | Action: close_app({clean_name}) | Status: FAILED | Time: {elapsed:.1f}ms")
        return "That application is not currently open."


def get_app_window_titles(app_name: str) -> list[str]:
    """Retrieves list of active window titles matching the app name or process."""
    titles = []
    target = (app_name or "").lower().strip()
    if not target:
        return titles

    if _SYSTEM == "Windows":
        try:
            import pygetwindow as gw
            all_wins = gw.getAllTitles()
            for t in all_wins:
                if t and target in t.lower():
                    titles.append(t)
        except Exception:
            pass

        if not titles:
            try:
                # PowerShell fallback to enumerate visible window titles
                ps_cmd = (
                    "Get-Process | Where-Object {$_.MainWindowTitle -ne ''} | "
                    "Select-Object -ExpandProperty MainWindowTitle"
                )
                res = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps_cmd],
                    capture_output=True, text=True, timeout=3,
                    creationflags=subprocess.CREATE_NO_WINDOW if _SYSTEM == "Windows" else 0
                )
                if res.returncode == 0:
                    for line in res.stdout.splitlines():
                        line_clean = line.strip()
                        if line_clean and target in line_clean.lower():
                            titles.append(line_clean)
            except Exception:
                pass

    return titles


def verify_app_opened(app_name: str, timeout: float = 3.0) -> bool:
    """Empirically verifies whether an app is running and/or has active windows."""
    target = (app_name or "").strip()
    if not target:
        return False
    
    # Web URL launches always succeed if browser is running or default browser opens
    if target.startswith("http://") or target.startswith("https://"):
        return True

    end_t = time.time() + timeout
    while time.time() < end_t:
        if is_app_running(target):
            return True
        wins = get_app_window_titles(target)
        if wins:
            return True
        time.sleep(0.5)

    return is_app_running(target) or bool(get_app_window_titles(target))


def verify_app_closed(app_name: str, timeout: float = 2.0) -> bool:
    """Empirically verifies whether an app has stopped running."""
    target = (app_name or "").strip()
    if not target:
        return True

    end_t = time.time() + timeout
    while time.time() < end_t:
        if not is_app_running(target):
            return True
        time.sleep(0.4)

    return not is_app_running(target)