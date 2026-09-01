from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

# ─────────────────────────────────────────────────────────────────────────────
# ULTRON WebEngine Anti-Flicker Environment Configuration
# MUST be set BEFORE importing PyQt6 modules or creating QApplication!
# ─────────────────────────────────────────────────────────────────────────────
os.environ["QTWEBENGINE_DISABLE_NO_SANDBOX"] = "1"
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"

_ui_chrome_flags = [
    "--enable-usermedia-screen-capturing",
    "--allow-http-screen-capture",
    "--auto-select-desktop-capture-source=Entire screen",
    "--enable-media-stream",
    "--enable-gpu-rasterization",
    "--enable-accelerated-2d-canvas",
    "--enable-zero-copy",
    "--ignore-gpu-blocklist",
    "--disable-gpu-driver-bug-workarounds",
    "--gpu-no-context-lost",
    "--use-gl=angle",
    "--use-angle=d3d11",
]
_render_mode = os.environ.get("ULTRON_RENDER_MODE", "auto").lower()
if _render_mode == "software":
    _ui_chrome_flags.append("--disable-gpu")
    os.environ["QT_OPENGL"] = "software"
elif _render_mode == "angle":
    _ui_chrome_flags.extend(["--use-gl=angle", "--use-angle=d3d11"])
elif _render_mode == "desktop_gl":
    _ui_chrome_flags.append("--use-gl=desktop")
    os.environ["QT_OPENGL"] = "desktop"

if "QTWEBENGINE_CHROMIUM_FLAGS" not in os.environ:
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(_ui_chrome_flags)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    _WEBENGINE_OK = True
except ImportError:
    _WEBENGINE_OK = False

from PyQt6.QtCore import (
    Qt, QUrl, pyqtSignal, QCoreApplication, QTimer,
)
from PyQt6.QtGui import (
    QColor, QPalette, QSurfaceFormat, QIcon,
)
from PyQt6.QtWidgets import (
    QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget,
    QDialog, QLineEdit, QPushButton, QHBoxLayout, QMessageBox,
)

_qt_env_initialized = False

def _setup_qt_environment():
    global _qt_env_initialized
    if _qt_env_initialized:
        return
    _qt_env_initialized = True

    os.environ["QTWEBENGINE_DISABLE_NO_SANDBOX"] = "1"
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(_ui_chrome_flags)

    if platform.system() == "Windows":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Ultron.AI.System.1.0")
        except Exception:
            pass

    for switch in _ui_chrome_flags:
        if switch not in sys.argv:
            sys.argv.append(switch)

    try:
        if hasattr(Qt.ApplicationAttribute, "AA_ShareOpenGLContexts"):
            QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    except Exception as e:
        print(f"[ULTRON GUI] Qt attribute warning: {e}", file=sys.stderr)

    try:
        fmt = QSurfaceFormat()
        fmt.setSwapBehavior(QSurfaceFormat.SwapBehavior.DoubleBuffer)
        fmt.setSwapInterval(1)
        fmt.setDepthBufferSize(24)
        fmt.setStencilBufferSize(8)
        QSurfaceFormat.setDefaultFormat(fmt)
    except Exception as e:
        print(f"[ULTRON GUI] QSurfaceFormat warning: {e}", file=sys.stderr)

_setup_qt_environment()


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"


def _read_full_config() -> dict:
    """Read api_keys.json config dict. Returns {} on any error."""
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


_PLACEHOLDER_KEY = "YOUR_GEMINI_API_KEY_HERE"


def _needs_api_key() -> bool:
    """True if the config is missing, broken, or still has the placeholder key."""
    cfg = _read_full_config()
    key = str(cfg.get("gemini_api_key", "")).strip()
    return not key or key == _PLACEHOLDER_KEY


def _write_api_key(key: str) -> None:
    """Safely save the API key — always writes valid JSON, never hand-edited."""
    cfg = _read_full_config()
    if not cfg:
        cfg = {
            "os_system": "windows",
            "morning_brief_enabled": True,
            "assistant_name": "ULTRON",
            "user_name": "",
            "ui_color": "#00ff66",
        }
    cfg["gemini_api_key"] = key.strip()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    API_FILE.write_text(json.dumps(cfg, indent=4), encoding="utf-8")


class ApiKeyDialog(QDialog):
    """Popup asking for the Gemini API key — replaces manual Notepad editing."""

    def __init__(self, parent=None, error_message: str = ""):
        super().__init__(parent)
        self.setWindowTitle("ULTRON — API Key Required")
        self.setFixedSize(460, 230)
        self.setModal(True)
        self.setStyleSheet("""
            QDialog { background-color: #0a0e18; }
            QLabel { color: #d8e0ec; font-size: 13px; }
            QLineEdit {
                background-color: #131b2e; color: #00ff66;
                border: 1px solid #2a3550; border-radius: 4px;
                padding: 8px; font-size: 13px;
            }
            QPushButton {
                background-color: #b00020; color: white;
                border: none; border-radius: 4px;
                padding: 8px 16px; font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { background-color: #d4002a; }
            QPushButton#ghost { background-color: #232d45; }
            QPushButton#ghost:hover { background-color: #2f3b58; }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel("ULTRON needs a Gemini API key to start")
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #ff4d4d;")
        layout.addWidget(title)

        info = QLabel("Get a free key at aistudio.google.com/apikey, then paste it below.")
        info.setWordWrap(True)
        layout.addWidget(info)

        if error_message:
            err = QLabel(error_message)
            err.setStyleSheet("color: #ff8080; font-size: 12px;")
            err.setWordWrap(True)
            layout.addWidget(err)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Paste your Gemini API key here...")
        self.input.setEchoMode(QLineEdit.EchoMode.Password)
        self.input.returnPressed.connect(self._on_save)
        layout.addWidget(self.input)

        show_row = QHBoxLayout()
        show_row.addStretch()
        self.show_btn = QPushButton("Show")
        self.show_btn.setObjectName("ghost")
        self.show_btn.setCheckable(True)
        self.show_btn.setFixedWidth(70)
        self.show_btn.toggled.connect(self._toggle_visibility)
        show_row.addWidget(self.show_btn)
        layout.addLayout(show_row)

        btn_row = QHBoxLayout()
        quit_btn = QPushButton("Quit")
        quit_btn.setObjectName("ghost")
        quit_btn.clicked.connect(self._on_quit)
        save_btn = QPushButton("Save && Launch")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(quit_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        self.result_key: str | None = None

    def _toggle_visibility(self, checked: bool):
        self.input.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    def _on_save(self):
        key = self.input.text().strip()
        if not key or key == _PLACEHOLDER_KEY:
            QMessageBox.warning(self, "ULTRON", "Please paste a real API key.")
            return
        self.result_key = key
        self.accept()

    def _on_quit(self):
        self.result_key = None
        self.reject()


class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class UltronWebWindow(QMainWindow):
    _state_sig = pyqtSignal(str)
    _log_sig = pyqtSignal(str)
    _content_sig = pyqtSignal(str, str)
    _reconfig_sig = pyqtSignal()
    _camera_sig = pyqtSignal(bytes)
    _telemetry_sig = pyqtSignal(str)
    _toast_sig = pyqtSignal(str, str)

    def __init__(self, face_path: str = "face.png"):
        super().__init__()
        self.setWindowTitle("ULTRON OS — Next-Gen AI Operating System")
        icon_path = str(Path(__file__).resolve().parent / "config" / "ultron.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        cfg = _read_full_config()
        mode = str(cfg.get("default_mode", "desktop")).lower()
        if mode == "fullscreen":
            self.showFullScreen()
        elif mode == "windowed":
            self.resize(1280, 800)
        else:
            screen = QApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                self.setGeometry(geo)
            else:
                self.resize(1280, 800)
        self.setMinimumSize(900, 600)

        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        self._muted = False
        self._ready = True
        self._assistant_name = _read_full_config().get("assistant_name", "ULTRON") or "ULTRON"
        self.on_text_command = None
        self.on_remote_clicked = None
        self.on_interrupt = None
        self.on_toggle_mute = None

        import time
        self._last_reload_time = 0.0

        if _WEBENGINE_OK:
            self._web = QWebEngineView(self)
            if hasattr(self._web, "page") and self._web.page():
                self._web.page().setBackgroundColor(QColor(0, 0, 0))
                if hasattr(self._web.page(), "renderProcessTerminated"):
                    self._web.page().renderProcessTerminated.connect(self._on_render_process_terminated)
                if hasattr(self._web.page(), "featurePermissionRequested"):
                    self._web.page().featurePermissionRequested.connect(self._on_feature_permission_requested)
            self._web.titleChanged.connect(self._on_title_changed)
            settings = self._web.settings()
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
            
            # --- FIX: Allowing CORS and Local Files for HTML WebGL ---
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)

            self.setCentralWidget(self._web)
            
            # --- Pointing to current directory app.html ---
            target_path = BASE_DIR / "dashboard" / "static" / "app.html"

            self._web.setUrl(QUrl.fromLocalFile(str(target_path)))
        else:
            container = QWidget()
            lbl = QLabel("ULTRON OS — Loading GUI...", container)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout = QVBoxLayout(container)
            layout.addWidget(lbl)
            self.setCentralWidget(container)

        self._state_sig.connect(self._on_state)
        self._log_sig.connect(self._on_log)
        self._content_sig.connect(self._on_content)
        self._reconfig_sig.connect(self._on_reconfig)
        self._telemetry_sig.connect(self._on_telemetry)
        self._toast_sig.connect(self._on_toast)

        if _needs_api_key():
            self._ready = False
            QTimer.singleShot(300, lambda: self._on_reconfig(""))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if callable(self.on_interrupt):
                self.on_interrupt()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _on_title_changed(self, title: str):
        if title.startswith("CMD:"):
            cmd = title[4:].strip()
            if cmd and callable(self.on_text_command):
                self.on_text_command(cmd)

    def _on_render_process_terminated(self, termination_status, exit_code):
        import time
        now = time.time()
        print(f"[ULTRON GUI WARNING] WebEngine Render Process Terminated (status: {termination_status}, exit code: {exit_code}).", file=sys.stderr)
        if hasattr(self, "_web") and self._web and (now - getattr(self, "_last_reload_time", 0) > 5.0):
            self._last_reload_time = now
#            self._web.reload() # Temporarily disabled to debug blinking issue

    def _eval_js(self, js_code: str):
        if _WEBENGINE_OK and hasattr(self, "_web") and self._web.page():
            try:
                self._web.page().runJavaScript(js_code)
            except Exception as e:
                print(f"[ULTRON GUI] _eval_js warning: {e}", file=sys.stderr)

    def _on_state(self, state: str):
        js = f"if (typeof updateAIState === 'function') updateAIState('{state}');"
        self._eval_js(js)

    def _on_log(self, text: str):
        escaped = json.dumps(text)
        js = f"if (typeof addMemoryLog === 'function') addMemoryLog({escaped});"
        self._eval_js(js)

    def _on_content(self, title: str, text: str):
        escaped_title = json.dumps(title)
        escaped_text = json.dumps(text)
        js = f"if (typeof addChatMessage === 'function') addChatMessage({escaped_title}, {escaped_text});"
        self._eval_js(js)

    def _on_telemetry(self, data_json: str):
        js = f"if (typeof updateSystemMetrics === 'function') updateSystemMetrics({data_json});"
        self._eval_js(js)

    def _on_toast(self, title: str, msg: str):
        escaped_title = json.dumps(title)
        escaped_msg = json.dumps(msg)
        js = f"if (typeof showToast === 'function') showToast({escaped_title}, {escaped_msg});"
        self._eval_js(js)

    def _on_reconfig(self, error_message: str = ""):
        dlg = ApiKeyDialog(self, error_message)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_key:
            _write_api_key(dlg.result_key)
            self._assistant_name = _read_full_config().get("assistant_name", "ULTRON") or "ULTRON"
            self._ready = True
        else:
            QApplication.quit()

    def _toggle_mute(self):
        self._muted = not self._muted
        msg = "Mic was muted" if self._muted else "Mic was unmuted"
        self._toast_sig.emit("MIC STATUS", msg)

    def notify_phone_connected(self):
        self._eval_js("if (typeof showToast === 'function') showToast('PHONE CONNECTED', 'Remote device paired');")

    def _on_feature_permission_requested(self, security_origin, feature):
        print(f"[ULTRON GUI] Granting WebEngine feature permission: {feature}")
        try:
            from PyQt6.QtWebEngineCore import QWebEnginePage
            self._web.page().setFeaturePermission(
                security_origin,
                feature,
                QWebEnginePage.PermissionPolicy.PermissionGrantedByUser
            )
        except Exception as e:
            print(f"[ULTRON GUI] Failed to grant feature permission: {e}")

    def start_camera_stream(self):
        js = "if (typeof openModal === 'function') openModal('camera'); if (typeof startCameraStream === 'function') startCameraStream();"
        self._eval_js(js)

    def stop_camera_stream(self):
        js = "if (typeof stopCameraStream === 'function') stopCameraStream(); if (typeof closeModal === 'function') closeModal('camera');"
        self._eval_js(js)

    def start_screen_share(self):
        js = "if (typeof openModal === 'function') openModal('screenshare'); if (typeof startScreenShare === 'function') startScreenShare();"
        self._eval_js(js)

    def stop_screen_share(self):
        js = "if (typeof stopScreenShare === 'function') stopScreenShare(); if (typeof closeModal === 'function') closeModal('screenshare');"
        self._eval_js(js)


class UltronUI:
    def __init__(self, face_path: str = "face.png", size=None):
        _setup_qt_environment()
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        icon_path = str(Path(__file__).resolve().parent / "config" / "ultron.ico")
        if os.path.exists(icon_path):
            self._app.setWindowIcon(QIcon(icon_path))
        self._win = UltronWebWindow(face_path)
        self._win.showMaximized()
        self.root = _RootShim(self._app)

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return None

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_interrupt(self):
        return self._win.on_interrupt

    @on_interrupt.setter
    def on_interrupt(self, cb):
        self._win.on_interrupt = cb

    @property
    def on_toggle_mute(self):
        return self._win.on_toggle_mute

    @on_toggle_mute.setter
    def on_toggle_mute(self, cb):
        self._win.on_toggle_mute = cb

    def notify_phone_connected(self) -> None:
        self._win._toast_sig.emit("PHONE CONNECTED", "Remote device paired")

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def update_telemetry(self, data: dict):
        now = time.time()
        if now - getattr(self, "_last_telemetry_time", 0.0) < 2.0:
            return
        self._last_telemetry_time = now
        escaped = json.dumps(data)
        self._win._telemetry_sig.emit(escaped)

    def _eval_js(self, js_code: str):
        """Thread-safe: evaluate JavaScript code in the WebEngine view."""
        if hasattr(self, "_win") and self._win:
            try:
                from PyQt6.QtCore import QMetaObject, Qt, Q_ARG
                QMetaObject.invokeMethod(
                    self._win,
                    "_eval_js",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, js_code)
                )
            except Exception as e:
                pass

    def eval_js(self, js_code: str):
        self._eval_js(js_code)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def show_content(self, title: str, text: str):
        """Thread-safe: display content in the UI."""
        self._win._content_sig.emit(title[:48], text[:4000])

    def prompt_reconfig(self):
        """Thread-safe: show API key setup overlay if needed."""
        self._win._ready = False
        self._win._reconfig_sig.emit()

    def show_camera_frame(self, img_bytes: bytes):
        pass

    def start_camera_stream(self) -> None:
        self._win.start_camera_stream()

    def stop_camera_stream(self) -> None:
        self._win.stop_camera_stream()

    def start_screen_share(self) -> None:
        self._win.start_screen_share()

    def stop_screen_share(self) -> None:
        self._win.stop_screen_share()

    @property
    def assistant_name(self) -> str:
        return self._win._assistant_name

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")


# Backward compatibility alias
JarvisUI = UltronUI