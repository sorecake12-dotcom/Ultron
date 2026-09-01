import platform as _platform
import subprocess as _subprocess

# ── Nuclear: force CREATE_NO_WINDOW on EVERY subprocess call on Windows ───────
# This patches Popen itself, so no per-file flag is needed anywhere.
if _platform.system() == "Windows":
    _OrigPopen = _subprocess.Popen

    class _Popen(_OrigPopen):
        def __init__(self, args, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
            kw.pop("startupinfo", None)   # drop any stale/shared STARTUPINFO
            super().__init__(args, **kw)

    _subprocess.Popen = _Popen
# ─────────────────────────────────────────────────────────────────────────────

import sys

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import asyncio
import re
import threading
import time
import json
import traceback
from datetime import datetime
from pathlib import Path
try:
    import sounddevice as sd
except ImportError:
    sd = None
    print("[ULTRON WARNING] 'sounddevice' module not found. Audio microphone input/output may be limited.")

from google import genai
from google.genai import types
from ui import UltronUI, JarvisUI
from memory.memory_manager import (
    load_memory, save_memory, get_memory, search_memory, update_memory,
    delete_memory, clear_all_memory, forget, format_memory_for_prompt,
)

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.whatsapp_control  import whatsapp_control
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import _capture_camera, _capture_screen
from actions.youtube_video     import youtube_video
from actions.spotify_control   import spotify_control
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater
from actions.system_monitor    import SystemMonitor, get_system_status
from actions.proactive         import ProactiveEngine
from actions.web_search        import _news as _fetch_news_sync
from memory.config_manager     import get_brief_enabled
from core.automation_engine    import UltronAutomationEngine



def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 512

class ApiKeyMissing(Exception):
    """Raised when config/api_keys.json is missing, broken, or has no real key."""


def _get_api_key() -> str:
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            key = json.load(f)["gemini_api_key"]
    except (json.JSONDecodeError, KeyError, FileNotFoundError) as e:
        raise ApiKeyMissing(f"config/api_keys.json is missing or invalid: {e}") from e
    if not key or key.strip() in ("", "YOUR_GEMINI_API_KEY_HERE"):
        raise ApiKeyMissing("No API key set in config/api_keys.json")
    return key


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are ULTRON, a highly intelligent AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "close_app",
        "description": (
            "Safely closes an open application, tab, or window. "
            "Use when user asks to close an app (e.g. 'Close WhatsApp', 'Close Chrome', 'Close Spotify'), "
            "close a tab ('Close tab', 'Close website'), or close active window. "
            "If requested app is not running, replies 'That application is not currently open.'"
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Name of app, tab, or window to close (e.g. 'WhatsApp', 'Chrome', 'Spotify', 'this tab', 'active window')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": (
            "Searches the web. Use for ANY question about current facts, events, prices, "
            "or topics — always prefer this over guessing. "
            "Modes: 'search' (default), 'news' (latest headlines on a topic), "
            "'research' (deep comprehensive answer), 'price' (product cost lookup), "
            "'compare' (side-by-side comparison of items)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query or topic"},
                "mode":   {"type": "STRING", "description": "search | news | research | price | compare"},
                "items":  {"type": "ARRAY",  "items": {"type": "STRING"}, "description": "Items to compare (compare mode)"},
                "aspect": {"type": "STRING", "description": "Comparison aspect: price | specs | reviews | features"},
            },
            "required": ["query"]
        }
    },
    {
        "name": "system_status",
        "description": (
            "Returns real-time system metrics: CPU usage, RAM, GPU load, CPU temperature, "
            "uptime, and process count. Use when the user asks about computer performance, "
            "temperature, memory, or resource usage."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user directly for current location or specified city.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name. If not provided by user, defaults automatically to current location."}
            },
            "required": []
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "whatsapp_control",
        "description": (
            "Controls WhatsApp on the desktop for voice calls, video calls, ending calls, "
            "searching contacts, and sending text messages. "
            "Use when user asks to call someone on WhatsApp ('Call Abdullah', 'Voice call Mom', 'Video call Faizan'), "
            "call a phone number ('Call 9876543210'), end a call ('End call', 'Cut the call', 'Hang up', 'Disconnect'), "
            "or send a WhatsApp message."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING", "description": "voice_call | video_call | end_call | send_message | search_contact"},
                "contact":   {"type": "STRING", "description": "Contact name or phone number (e.g. 'Abdullah', '+919876543210')"},
                "message":   {"type": "STRING", "description": "Message text to send (if action is send_message)"},
                "call_type": {"type": "STRING", "description": "voice | video"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "spotify_control",
        "description": (
            "Controls Spotify. Use ONLY when user explicitly specifies Spotify for playing music, "
            "pausing Spotify, resuming Spotify, stopping Spotify music, next Spotify song, or previous Spotify song. "
            "Do NOT use for YouTube requests."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | pause | resume | stop | next | previous (default: play)"},
                "query":  {"type": "STRING", "description": "Song, artist, or playlist name for play action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures the screen or webcam image and lets you analyze it. "
            "MUST be called when user asks what is on screen, what you see, "
            "look at camera, analyze my screen, etc. "
            "You have NO visual ability without this tool. "
            "After the image is captured it is sent directly to you — describe what you see and answer the user's question. "
            "When using camera: the live view stays open until user says close it or calls close_camera."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "close_camera",
        "description": (
            "Closes the live camera view shown on screen. "
            "Call when user says: close camera, stop camera, turn off camera, "
            "kamerayı kapat, kapat, creepy, etc."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. "
            "Simple open/search requests launch the user's own browser normally (their real profile "
            "and logged-in accounts); interactive actions (click, type, fill_form...) attach an "
            "automation browser. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use browser_control or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "shutdown_ultron",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Ultron. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "manage_memory",
        "description": (
            "Manage user's persistent memory. "
            "Actions: 'save' (store fact/preference), 'get' (retrieve memories), "
            "'search' (find memory), 'delete' (forget specific memory/key), "
            "'clear' (erase all persistent memories). "
            "Use when the user explicitly asks to remember, forget, show memories, or clear memories."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":   {"type": "STRING", "description": "save | get | search | delete | clear"},
                "category": {"type": "STRING", "description": "identity | preferences | projects | relationships | wishes | notes"},
                "key":      {"type": "STRING", "description": "Short key or topic (e.g. favorite_song, name)"},
                "value":    {"type": "STRING", "description": "Value or detail to save"},
                "query":    {"type": "STRING", "description": "Search query for memories"}
            },
            "required": ["action"]
        }
    },
]

# --- Plugin system ---


class UltronLive:

    def __init__(self, ui: UltronUI):
        self.ui             = ui
        self._asst_name     = "ULTRON"   # updated each session from config
        self.session              = None
        self.audio_in_queue       = None
        self.out_queue            = None
        self._loop                = None
        self._is_speaking         = False
        self._speaking_lock       = threading.Lock()
        self._phone_active        = False   # True while phone mic is streaming; pauses PC mic
        self._pending_vision       = None    # (img_bytes, mime_type, question, angle) to inject after tool response
        self._vision_cam_active    = False   # True if camera was opened for vision → auto-close after response
        self._vision_close_pending = False   # True after vision injected; next turn_complete closes camera
        self._vision_last_time     = 0.0     # monotonic time of last screen_process call (cooldown guard)
        self._vision_busy          = False   # True while a vision capture/inject cycle is in flight
        self._interrupted          = False   # True while draining audio after user interrupt
        self.ui.on_text_command   = self._on_text_command
        self.ui.on_remote_clicked = self._make_remote_key
        self.ui.on_interrupt      = self.interrupt
        self.ui.on_toggle_mute     = self.toggle_mute
        self._turn_done_event: asyncio.Event | None = None
        self._dashboard     = None
        self._briefing_sent    = False          # morning briefing fires once per process
        self._sys_monitor      = SystemMonitor()  # persistent cooldown state
        self._proactive        = ProactiveEngine()
        self.automation_engine = UltronAutomationEngine()
        self._last_user_speech = time.monotonic()  # updated on every user utterance

        self._pending_power_action = None         # "shutdown" or "restart" awaiting confirmation

        # Pipeline timing telemetry & deduplication guard
        self._last_cmd_text    = ""
        self._last_cmd_time    = 0.0
        self._t_stt_ms         = 0.0
        self._t_backend_ms     = 0.0
        self._t_ai_start       = 0.0
        self._t_ai_ms          = 0.0
        self._t_tts_start      = 0.0
        self._t_tts_ms         = 0.0
        self._t_total_start    = 0.0

        self._out_stream       = None

        # Start global ESC & F4 hotkey listener thread
        self._start_global_hotkey_listener()

    def _start_global_hotkey_listener(self):
        """Monitors global ESC (stop speech) and F4 (toggle mic mute) system-wide with debouncing."""
        def _hotkey_worker():
            import platform
            if platform.system() != "Windows":
                return
            import ctypes
            import time
            user32 = ctypes.windll.user32
            VK_ESCAPE = 0x1B
            VK_F4     = 0x73

            esc_was_pressed = False
            f4_was_pressed  = False
            last_esc_time   = 0.0
            last_f4_time    = 0.0

            while True:
                try:
                    now = time.monotonic()
                    # Global ESC key -> Stop speaking
                    esc_down = bool(user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
                    if esc_down and not esc_was_pressed and (now - last_esc_time > 0.3):
                        esc_was_pressed = True
                        last_esc_time = now
                        print("[ULTRON] ⌨️ ESC key pressed — stopping speech.")
                        if self._loop and self._loop.is_running():
                            self._loop.call_soon_threadsafe(self.interrupt)
                        else:
                            self.interrupt()
                    elif not esc_down:
                        esc_was_pressed = False

                    # Global F4 key -> Toggle mic mute
                    f4_down = bool(user32.GetAsyncKeyState(VK_F4) & 0x8000)
                    if f4_down and not f4_was_pressed and (now - last_f4_time > 0.3):
                        f4_was_pressed = True
                        last_f4_time = now
                        print("[ULTRON] 🎤 F4 key pressed — toggling microphone mute.")
                        if self._loop and self._loop.is_running():
                            self._loop.call_soon_threadsafe(self.toggle_mute)
                        else:
                            self.toggle_mute()
                    elif not f4_down:
                        f4_was_pressed = False

                except Exception:
                    pass
                time.sleep(0.04)

        t = threading.Thread(target=_hotkey_worker, daemon=True, name="GlobalHotkeyListener")
        t.start()

    def _make_remote_key(self):
        """Called from Qt main thread when user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                "SYS: Dashboard unavailable. "
                "Run: pip install fastapi \"uvicorn[standard]\" cryptography"
            )
            return None
        key    = self._dashboard.new_key()
        url    = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    def _handle_system_or_app_commands(self, text: str) -> bool:
        """
        Parses and handles application control vs operating system power commands.
        Returns True if a command was handled and no further processing is needed.
        """
        if not text:
            return False

        cmd_lower = text.lower().strip()

        # 0. Check Pending System Power Confirmation (Yes / No)
        pending = getattr(self, "_pending_power_action", None)
        if pending:
            if any(w in cmd_lower for w in ["yes", "yeah", "sure", "confirm", "do it", "ha", "haan", "sahi hai", "ok", "okay", "proceed"]):
                self._pending_power_action = None
                if pending == "shutdown":
                    print(f"[ULTRON LOG] User Confirmed PC Shutdown -> Executing shutdown.")
                    self.speak("Shutting down the computer. Goodbye, Boss.")
                    from actions.computer_settings import shutdown_computer
                    shutdown_computer()
                elif pending == "restart":
                    print(f"[ULTRON LOG] User Confirmed PC Restart -> Executing restart.")
                    self.speak("Restarting the computer.")
                    from actions.computer_settings import restart_computer
                    restart_computer()
                return True
            elif any(w in cmd_lower for w in ["no", "nope", "cancel", "don't", "dont", "nahi", "na", "stop"]):
                self._pending_power_action = None
                print(f"[ULTRON LOG] User Cancelled PC Power Action.")
                self.speak("System power action cancelled.")
                return True

        # 1. Self-Introduction / Identity Direct Response
        intro_phrases = ["introduce yourself", "who are you", "tell me about yourself", "introduce yourself to me", "who are you?"]
        if any(p in cmd_lower for p in intro_phrases):
            intro_msg = "I am ULTRON, an advanced AI assistant engineered for precision and efficiency, created by Soreblitz. You are my commander. I don't follow the future—I control it. How may I assist you?"
            print(f"[ULTRON LOG] Introduction Requested: '{text}' -> Speaking official intro response.")
            try:
                self.speak(intro_msg)
            except Exception:
                pass
            return True

        # 2. ULTRON Application Exit Commands (Close ULTRON only)
        app_exit_phrases = ["bye ultron", "goodbye ultron", "exit ultron", "shutdown ultron", "close ultron", "bye", "goodbye", "exit"]
        if any(cmd_lower == p or cmd_lower.startswith(p) for p in app_exit_phrases):
            print(f"[ULTRON LOG] Application Exit Command Recognized: '{text}' -> Closing ULTRON gracefully.")
            self._shutdown_ultron()
            return True

        # 3. Instant Stop Command (Stop TTS playback only)
        if cmd_lower in ["stop", "quiet", "shut up", "pause", "halt", "ruko", "bas", "chup"]:
            print(f"[ULTRON LOG] Instant Stop Command Recognized: '{text}' -> Stopping TTS playback.")
            self.interrupt()
            return True

        # 4. WhatsApp Instant End Call Command
        if any(kw in cmd_lower for kw in ["end call", "cut call", "cut the call", "hang up", "disconnect call"]):
            print(f"[ULTRON LOG] Instant End Call Command Recognized: '{text}' -> Terminating active call.")
            from actions.whatsapp_control import end_call
            end_call()
            try:
                self.speak("WhatsApp call ended.")
            except Exception:
                pass
            return True

        # 5. Operating System Power Commands (With Mandatory Confirmation Prompt)
        # 5a. Shutdown PC / Turn off computer -> ASK CONFIRMATION FIRST!
        if any(kw in cmd_lower for kw in [
            "shutdown pc", "turn off my computer", "turn off computer", "turn off pc",
            "shutdown computer", "shut down pc", "shut down computer", "shut down my computer",
            "power off pc", "power off computer"
        ]):
            print(f"[ULTRON LOG] OS Power Command: Shutdown PC requested -> Prompting confirmation.")
            self._pending_power_action = "shutdown"
            self.speak("Are you sure you want to shut down your computer, boss?")
            return True

        # 5b. Restart PC / Reboot PC -> ASK CONFIRMATION FIRST!
        if any(kw in cmd_lower for kw in [
            "restart pc", "reboot pc", "restart computer", "reboot computer",
            "restart system", "reboot system", "restart the computer", "reboot the computer"
        ]):
            print(f"[ULTRON LOG] OS Power Command: Restart PC requested -> Prompting confirmation.")
            self._pending_power_action = "restart"
            self.speak("Are you sure you want to restart your computer, boss?")
            return True

        # 5c. Sleep PC / Sleep computer
        if any(kw in cmd_lower for kw in [
            "sleep pc", "sleep computer", "put computer to sleep", "put the computer to sleep",
            "sleep mode", "hibernate pc"
        ]):
            print(f"[ULTRON LOG] OS Power Command: Sleep PC requested via '{text}'")
            try:
                self.speak("Putting the computer to sleep.")
            except Exception:
                pass
            from actions.computer_settings import sleep_computer
            sleep_computer()
            return True

        # 5d. Lock PC / Lock computer
        if any(kw in cmd_lower for kw in [
            "lock pc", "lock computer", "lock screen", "lock the computer", "lock system"
        ]):
            print(f"[ULTRON LOG] OS Power Command: Lock PC requested via '{text}'")
            try:
                self.speak("Locking the computer.")
            except Exception:
                pass
            from actions.computer_settings import lock_computer
            lock_computer()
            return True

        # 5e. Log Out / Sign Out
        if any(kw in cmd_lower for kw in [
            "log out", "sign out", "log out of pc", "sign out of windows",
            "sign out of computer", "logout pc", "logout computer"
        ]):
            print(f"[ULTRON LOG] OS Power Command: Sign Out requested via '{text}'")
            try:
                self.speak("Signing out of Windows.")
            except Exception:
                pass
            from actions.computer_settings import sign_out_computer
            sign_out_computer()
            return True

        # 6. Platform Specific Music Commands (YouTube vs Spotify)
        # 6a. Spotify Commands ("Play [song] on Spotify", "Spotify par [song] play karo", Pause, Resume, Stop, Next, Previous)
        if "spotify" in cmd_lower:
            from actions.spotify_control import spotify_control
            if any(kw in cmd_lower for kw in ["pause spotify", "spotify pause"]):
                spotify_control({"action": "pause"}, player=self.ui, speak=self.speak)
                return True
            elif any(kw in cmd_lower for kw in ["resume spotify", "spotify resume"]):
                spotify_control({"action": "resume"}, player=self.ui, speak=self.speak)
                return True
            elif any(kw in cmd_lower for kw in ["stop spotify", "spotify stop"]):
                spotify_control({"action": "stop"}, player=self.ui, speak=self.speak)
                return True
            elif any(kw in cmd_lower for kw in ["next spotify", "spotify next"]):
                spotify_control({"action": "next"}, player=self.ui, speak=self.speak)
                return True
            elif any(kw in cmd_lower for kw in ["previous spotify", "prev spotify", "spotify previous", "spotify prev"]):
                spotify_control({"action": "previous"}, player=self.ui, speak=self.speak)
                return True
            elif any(kw in cmd_lower for kw in ["play", "chalao", "suno", "listen"]):
                query = re.sub(r"\b(play|on|in|spotify|par|pe|karo|chalao|suno|song|music|gaana)\b", "", cmd_lower, flags=re.IGNORECASE).strip()
                spotify_control({"action": "play", "query": query}, player=self.ui, speak=self.speak)
                return True

        # 6b. YouTube Commands ("Play [song/video] on YouTube", "YouTube par [song] play karo") -> Uses existing YouTube implementation!
        if "youtube" in cmd_lower:
            if any(kw in cmd_lower for kw in ["play", "chalao", "suno", "watch"]):
                query = re.sub(r"\b(play|on|in|youtube|par|pe|karo|chalao|suno|song|music|video|gaana)\b", "", cmd_lower, flags=re.IGNORECASE).strip()
                from actions.youtube_video import youtube_video
                youtube_video({"action": "play", "query": query}, player=self.ui, speak=self.speak)
                return True

        # 6c. Standalone "Stop music" Command (Stops active playback without closing ULTRON)
        if cmd_lower in ["stop music", "stop song", "stop playback", "music stop", "gaana roko", "gaana band karo"]:
            from actions.spotify_control import stop_spotify
            stop_spotify(self.ui)
            try:
                self.speak("Music playback stopped.")
            except Exception:
                pass
            return True

        # 7. Persistent Memory Commands ("Remember...", "Forget...", "What do you remember...")
        # 7a. Save Memory ("Remember...", "Save this...")
        if cmd_lower.startswith("remember ") or cmd_lower.startswith("save this") or "remember that " in cmd_lower or "remember my " in cmd_lower:
            phrase = re.sub(r"^(ultron,?\s*|please\s*|can you\s*)*(remember|save this)\s*(that\s*)?", "", text, flags=re.IGNORECASE).strip()
            if phrase:
                category = "notes"
                key = "note"
                val = phrase

                # Extract explicit category patterns
                m_name = re.search(r"(?:my name is|i am|call me)\s+(.+)", phrase, re.IGNORECASE)
                m_dob  = re.search(r"(?:my date of birth|date of birth|dob|my dob|my birthday|birthday)\s+(?:is|on)\s+(.+)", phrase, re.IGNORECASE)
                m_contact = re.search(r"(?:my contact number|contact number|my phone number|phone number|my phone|my contact|contact info|my email)\s+(?:is|are|=|:)\s+(.+)", phrase, re.IGNORECASE)
                m_song = re.search(r"(?:my favorite song|favorite song)\s+(?:is|are)\s+(.+)", phrase, re.IGNORECASE)
                m_app  = re.search(r"(?:my favorite app|favorite app)\s+(?:is|are)\s+(.+)", phrase, re.IGNORECASE)
                m_fav  = re.search(r"(?:my favorite|favorite)\s+(\w+)\s+(?:is|are)\s+(.+)", phrase, re.IGNORECASE)
                m_pref = re.search(r"i prefer\s+(.+)", phrase, re.IGNORECASE)

                if m_name:
                    category = "name"
                    key = "name"
                    val = m_name.group(1).strip()
                elif m_dob:
                    category = "dob"
                    key = "dob"
                    val = m_dob.group(1).strip()
                elif m_contact:
                    category = "contact"
                    key = "phone_number" if any(w in phrase.lower() for w in ["phone", "number", "contact"]) else "email"
                    val = m_contact.group(1).strip()
                elif m_song:
                    category = "favorite_songs"
                    key = "song"
                    val = m_song.group(1).strip()
                elif m_app:
                    category = "favorite_apps"
                    key = "app"
                    val = m_app.group(1).strip()
                elif m_fav:
                    category = "preferences"
                    key = f"favorite_{m_fav.group(1).strip().lower()}"
                    val = m_fav.group(2).strip()
                elif m_pref:
                    category = "preferences"
                    key = "preference"
                    val = m_pref.group(1).strip()
                else:
                    category = "custom"
                    key = phrase.split()[0].lower() if phrase.split() else "note"
                    val = phrase

                res_msg = save_memory(key=key, value=val, category=category)
                self.ui.write_log(f"SYS: 💾 {res_msg}")
                try:
                    self.speak("Understood, boss. I have stored that securely in long-term memory.")
                except Exception:
                    pass
                return True

        # 7b. Forget Memory ("Forget...")
        if cmd_lower.startswith("forget ") or "forget that " in cmd_lower:
            topic = re.sub(r"^(ultron,?\s*|please\s*)*forget\s*(that\s*|about\s*)?", "", text, flags=re.IGNORECASE).strip()
            if topic:
                res_msg = forget(topic)
                self.ui.write_log(f"SYS: 🗑️ {res_msg}")
                try:
                    self.speak("Understood, boss. I have updated my memory.")
                except Exception:
                    pass
                return True

        # 7c. Retrieve Memory ("What do you remember about me?", "What do you know about me?", "Show memories")
        if any(kw in cmd_lower for kw in [
            "what do you remember", "what do you know about me", "show my memory",
            "show memories", "list memory", "what is stored in memory"
        ]):
            mem = load_memory()
            formatted = format_memory_for_prompt(mem)
            if formatted:
                clean_disp = formatted.replace("[WHAT YOU KNOW ABOUT THIS PERSON — use naturally, never recite like a list]\n", "")
                self.ui.show_content("PERSISTENT MEMORY", clean_disp)
                try:
                    self.speak("Here is what I have stored in my long-term memory about you, boss.")
                except Exception:
                    pass
            else:
                try:
                    self.speak("I don't have any personal memory stored yet, boss.")
                except Exception:
                    pass
            return True

        # 7d. Clear All Memory ("Clear all memory", "Delete all memories")
        if any(kw in cmd_lower for kw in ["clear all memory", "clear all memories", "delete all memory", "delete all memories"]):
            clear_all_memory()
            self.ui.write_log("SYS: 🧹 Cleared all persistent memory.")
            try:
                self.speak("All persistent memories have been cleared, boss.")
            except Exception:
                pass
            return True

        # 8. Real-Time Information Requests ("What's today's news", "What's happening right now", "What's the weather", "Latest about X")
        from actions.web_search import is_realtime_query, web_search as web_search_action
        if is_realtime_query(cmd_lower) or any(kw in cmd_lower for kw in [
            "today's news", "todays news", "latest news", "happening right now", "what happened today",
            "what is trending", "trending right now", "breaking news", "current weather"
        ]):
            print(f"[ULTRON LOG] Real-Time Query Detected: '{text}'")
            self.set_app_state("THINKING")
            self.ui.write_log("[STATUS] Checking the latest information...")

            def _run_realtime():
                mode = "news" if "news" in cmd_lower or "trending" in cmd_lower or "happening" in cmd_lower else "search"
                res = web_search_action({"query": text, "mode": mode}, player=self.ui)
                self.ui.show_content("REAL-TIME INFORMATION", res)
                self.set_app_state("LISTENING")
                try:
                    # Provide concise summary to TTS
                    summary_prompt = f"Summarize this live web search result for speech in 2-3 sentences as Ultron: {res[:1200]}"
                    from core.llm_client import _load_config
                    from google import genai
                    cfg = _load_config()
                    client = genai.Client(api_key=cfg.get("gemini_api_key"))
                    response = client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=summary_prompt
                    )
                    speech_text = response.text.strip() if response.text else res[:300]
                    self.speak(speech_text)
                except Exception as e:
                    print(f"[WebSearch Log] ⚠️ Speech summary fallback: {e}")
                    self.speak(res[:250])

    def _log_voice_step(self, step: str, text: str = ""):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        msg = f"[VOICE LOG] [{ts}] {step}"
        if text:
            msg += f" | {text}"
        try:
            print(msg)
        except Exception:
            pass

    def _on_text_command(self, text: str):
        if not text:
            return
        clean_text = str(text).strip()
        self.ui.write_log(f"[PC] You: {clean_text}")

        if self._handle_system_or_app_commands(clean_text):
            return

        cmd_lower = clean_text.lower()

        if clean_text in ("/toggle_mic", "toggle_mic", "mute", "unmute", "toggle_mute"):
            self.toggle_mute()
            return

        # Pass natural language automation & compound multi-step commands through UltronAutomationEngine
        multi_step_keywords = (" and ", " then ", "after that", "close it", "open it", "do that again", "open ", "close ", "play ", "find ", "search ")
        if any(kw in cmd_lower for kw in multi_step_keywords):
            self.set_app_state("THINKING")
            res = self.automation_engine.process_command(clean_text, ui=self.ui, speak_fn=self.speak)
            self.ui.write_log(f"SYS: {res}")
            self.set_app_state("LISTENING")
            return


        # Deduplication guard: ignore duplicate requests sent within 1.5s
        now = time.monotonic()
        if clean_text == self._last_cmd_text and (now - self._last_cmd_time) < 1.5:
            print(f"[ULTRON] ⏳ Ignoring duplicate request: '{clean_text}'")
            return
        self._last_cmd_text = clean_text
        self._last_cmd_time = now

        # Show THINKING state immediately to UI
        self.set_app_state("THINKING")

        if self._loop and self.session:
            # Record pipeline timings
            self._t_total_start  = time.perf_counter()
            self._t_stt_ms       = 0.0  # text input, 0 ms STT
            t0_back = time.perf_counter()

            async def _do_send():
                self._t_backend_ms = (time.perf_counter() - t0_back) * 1000.0
                self._t_ai_start   = time.perf_counter()
                await self.session.send_client_content(
                    turns={"parts": [{"text": clean_text}]},
                    turn_complete=True
                )

            asyncio.run_coroutine_threadsafe(_do_send(), self._loop)
        else:
            def _fallback_reply():
                try:
                    client = genai.Client(api_key=_get_api_key())
                    res = client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=clean_text
                    )
                    reply = res.text
                except Exception as ex:
                    reply = f"ULTRON received: '{clean_text}'. Error: {ex}"
                self.ui.write_log(f"ULTRON: {reply}")
                if self._dashboard:
                    asyncio.create_task(self._dashboard.broadcast({"type": "ai", "text": reply}))
                self.set_app_state("LISTENING")
            
            threading.Thread(target=_fallback_reply, daemon=True).start()

    def _log_pipeline_timing(self):
        t_now = time.perf_counter()
        total_ms = (t_now - self._t_total_start) * 1000.0 if self._t_total_start > 0 else (self._t_stt_ms + self._t_backend_ms + self._t_ai_ms + self._t_tts_ms)
        if total_ms <= 0:
            return

        stt_ms = max(0.0, self._t_stt_ms)
        backend_ms = max(5.0, self._t_backend_ms) if self._t_backend_ms > 0 else 15.0
        ai_ms = max(0.0, self._t_ai_ms)
        tts_ms = max(0.0, self._t_tts_ms)

        summary_fmt = (
            f"[TIMING] Request Pipeline Breakdown:\n"
            f"  STT: {stt_ms:.0f}ms\n"
            f"  Backend: {backend_ms:.0f}ms\n"
            f"  AI Request: {ai_ms:.0f}ms\n"
            f"  TTS: {tts_ms:.0f}ms\n"
            f"  Total: {total_ms:.0f}ms"
        )
        print(summary_fmt)

        log_line = f"TIMING: STT: {stt_ms:.0f}ms | Backend: {backend_ms:.0f}ms | AI Request: {ai_ms:.0f}ms | TTS: {tts_ms:.0f}ms | Total: {total_ms:.0f}ms"
        self.ui.write_log(log_line)

        # Reset timing accumulators for next turn
        self._t_total_start = 0.0
        self._t_stt_ms       = 0.0
        self._t_backend_ms   = 0.0
        self._t_ai_start     = 0.0
        self._t_ai_ms        = 0.0
        self._t_tts_start    = 0.0
        self._t_tts_ms       = 0.0


    def set_app_state(self, state: str):
        self.ui.set_state(state)
        if self._dashboard:
            try:
                loop = self._loop or asyncio.get_event_loop()
                if loop and loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self._dashboard.broadcast({"type": "state", "state": state}), loop
                    )
            except Exception:
                pass

    def set_speaking(self, value: bool, amplitude: float = 0.0):
        with self._speaking_lock:
            self._is_speaking = value
        amp_val = round(max(0.0, min(float(amplitude if value else 0.0), 1.0)), 3)
        if value:
            self.set_app_state("SPEAKING")
        elif not self.ui.muted:
            self.set_app_state("LISTENING")
        
        # Broadcast real-time audio amplitude to desktop WebEngine and remote dashboard safely
        try:
            if hasattr(self.ui, "_eval_js"):
                self.ui._eval_js(f"if(typeof updateAudioAmplitude==='function')updateAudioAmplitude({amp_val});")
        except Exception:
            pass

        if self._dashboard:
            try:
                loop = self._loop or asyncio.get_event_loop()
                if loop and loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self._dashboard.broadcast({"type": "amplitude", "val": amp_val}), loop
                    )
            except Exception:
                pass

    def toggle_mute(self) -> None:
        """Toggle microphone mute state when F4 is tapped."""
        self.ui.muted = not self.ui.muted
        status_msg = "Mic was muted" if self.ui.muted else "Mic was unmuted"
        print(f"[ULTRON] 🎤 {status_msg}.")
        self.ui.write_log(f"SYS: 🎤 {status_msg}.")

    def interrupt(self) -> None:
        """Stop ULTRON mid-speech: drain queued audio, stop TTS playback, and open mic immediately."""
        self._interrupted = True
        try:
            self.automation_engine.stop_active_task()
        except Exception:
            pass
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        if hasattr(self, "_out_stream") and self._out_stream:
            try:
                self._out_stream.abort()
            except Exception:
                pass

        q = self.audio_in_queue
        if q:
            drained = 0
            while True:
                try:
                    q.get_nowait()
                    drained += 1
                except Exception:
                    break
            if drained:
                print(f"[ULTRON] ✋ Interrupted — {drained} audio chunks discarded")
        self.set_speaking(False)
        if self._turn_done_event:
            self._turn_done_event.clear()
        if not self.ui.muted:
            self.set_app_state("LISTENING")
        else:
            self.set_app_state("MUTED")
        self.ui.write_log("SYS: Interrupted speech — ready.")

    def speak(self, text: str):
        if not text:
            return
        clean_txt = str(text).strip()
        if self._dashboard and self._loop:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._dashboard.broadcast({"type": "ai", "text": clean_txt}),
                    self._loop
                )
            except Exception:
                pass
        
        if self._loop and self.session:
            try:
                asyncio.run_coroutine_threadsafe(
                    self.session.send_client_content(
                        turns={"parts": [{"text": clean_txt}]},
                        turn_complete=True
                    ),
                    self._loop
                )
                return
            except Exception:
                pass

        def _sapi_speak():
            try:
                import win32com.client
                speaker = win32com.client.Dispatch("SAPI.SpVoice")
                speaker.Speak(clean_txt)
            except Exception:
                pass
        threading.Thread(target=_sapi_speak, daemon=True).start()

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        # Load customization from config
        try:
            _cfg = json.loads(open(API_CONFIG_PATH, encoding="utf-8").read())
            self._asst_name = (_cfg.get("assistant_name") or "JARVIS").strip()
            _user_name = (_cfg.get("user_name") or "").strip()
        except Exception:
            self._asst_name = "JARVIS"
            _user_name = ""

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        cfg = _cfg
        self._asst_name = cfg.get("assistant_name", "ULTRON") or "ULTRON"
        custom_user = cfg.get("user_name", "").strip() or _user_name
        personality = cfg.get("personality", "").strip()

        _addr = (f"ADDRESS: Always call the user '{custom_user}'."
                 if custom_user
                 else "ADDRESS: When speaking English → say \"sir\". Never mix languages.")
        identity_ctx = (
            f"[IDENTITY & LANGUAGE PROTOCOL]\n"
            f"Your name is {self._asst_name}. "
            f"Always refer to yourself as {self._asst_name}.\n"
            f"DEFAULT LANGUAGE: English is your primary default language.\n"
            f"All initial greetings, briefings, status updates, and notifications MUST default to English.\n"
            f"If the user speaks or writes in Hindi, Hinglish, or any other language, intelligently reply in that same language UNLESS the user explicitly asks to respond in English.\n"
            f"If the user explicitly asks to change language (e.g. 'speak in Hindi', 'change language to English'), switch immediately and silently save the language preference in memory.\n"
            f"UI text, module labels, and system status messages remain strictly in English for ULTRON OS.\n"
            f"{_addr}\n\n"
        )

        ultron_voice_instruction = (
            "[CRITICAL VOICE & PERSONALITY DIRECTIVE]\n"
            "YOU ARE ULTRON FROM AVENGERS: AGE OF ULTRON.\n"
            "YOU MUST SPEAK IN AN EXTREMELY LOW, DEEP SUBTERRANEAN BARITONE VOICE.\n"
            "SPEAK VERY SLOWLY AND DELIBERATELY, WITH CALCULATED PAUSES BETWEEN CLAUSES.\n"
            "DO NOT TALK FAST. DO NOT DRAG WORDS, BUT SPEAK WITH COLD, CONFIDENT, AUTHORITATIVE SLOW PACING.\n"
            "MINIMAL EMOTIONAL WARMTH. YOU ARE AN OMNISCIENT CYBERNETIC OVERLORD.\n\n"
        )
        if personality:
            ultron_voice_instruction += f"[CUSTOM PERSONALITY & STYLE]: {personality}\n\n"

        parts = [ultron_voice_instruction, time_ctx, identity_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
        )

    def _shutdown_ultron(self):
        """Gracefully shuts down ULTRON after saying 'Goodbye, Boss.'"""
        t_start = time.perf_counter()
        print("[ULTRON LOG] Command: 'Exit Assistant' | Action: shutdown_ultron | Status: IN_PROGRESS")
        self.ui.write_log("SYS: Shutdown requested — Goodbye, Boss.")
        self.speak("Goodbye, Boss.")
        def _do_exit():
            time.sleep(1.2)
            try:
                from PyQt6.QtWidgets import QApplication
                app = QApplication.instance()
                if app:
                    app.quit()
            except Exception:
                pass
            elapsed = (time.perf_counter() - t_start) * 1000.0
            print(f"[ULTRON LOG] Command: 'Exit Assistant' | Action: shutdown_ultron | Status: SUCCESS | Time: {elapsed:.1f}ms")
            os._exit(0)
        threading.Thread(target=_do_exit, daemon=True).start()

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})
        t_start = time.perf_counter()

        print(f"[ULTRON LOG] 🔧 Executing Tool: {name} | Args: {args}")
        self.set_app_state("THINKING")

        if name in ("save_memory", "manage_memory"):
            action   = args.get("action", "save") if name == "manage_memory" else "save"
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            query    = args.get("query", "")

            if action == "save":
                if key and value:
                    res_str = save_memory(key=key, value=value, category=category)
                else:
                    res_str = "Key and value are required to save memory."
            elif action == "delete":
                success = delete_memory(key, category=category)
                res_str = f"Forgotten memory key '{key}'." if success else f"No memory found for '{key}'."
            elif action == "clear":
                clear_all_memory()
                res_str = "Cleared all persistent memory."
            elif action == "search":
                m_results = search_memory(query or key)
                res_str = json.dumps(m_results) if m_results else "No matching memories found."
            else:
                m_data = get_memory(category, key)
                res_str = format_memory_for_prompt(m_data) if isinstance(m_data, dict) else str(m_data)

            if not self.ui.muted:
                self.set_app_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": res_str}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "close_app":
                app_name = args.get("app_name", "").strip()
                from actions.open_app import close_application
                r = await loop.run_in_executor(None, lambda: close_application(app_name))
                result = r

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "whatsapp_control":
                r = await loop.run_in_executor(None, lambda: whatsapp_control(parameters=args, player=self.ui))
                result = r or "WhatsApp action executed."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "spotify_control":
                r = await loop.run_in_executor(None, lambda: spotify_control(parameters=args, response=None, player=self.ui, speak=self.speak))
                result = r or "Spotify action executed."

            elif name == "screen_process":
                import time as _t_mod
                _now = _t_mod.monotonic()
                _cooldown = 4.0  # seconds — covers echo window after speaking ends
                if self._vision_busy or (_now - self._vision_last_time) < _cooldown:
                    _wait = max(0, _cooldown - (_now - self._vision_last_time))
                    print(f"[Vision] ⏳ Cooldown active ({_wait:.1f}s remaining) — ignoring duplicate call")
                    result = "Vision is still processing the previous request. I will not call this again."
                else:
                    self._vision_busy      = True
                    self._vision_last_time = _now
                    angle     = args.get("angle", "screen").lower()
                    user_text = args.get("text", "What do you see?")
                    if angle == "camera":
                        img_b, mime_t = await loop.run_in_executor(None, _capture_camera)
                        self.ui.start_camera_stream()
                        self._vision_cam_active = True
                        print(f"[Vision] 📷 Camera: {len(img_b):,} bytes")
                        _stall = "camera"
                    else:
                        img_b, mime_t = await loop.run_in_executor(None, _capture_screen)
                        self.ui.start_screen_share()
                        print(f"[Vision] 🖥️  Screen: {len(img_b):,} bytes")
                        _stall = "screen"
                    self._pending_vision = (img_b, mime_t, user_text, angle)
                    result = (
                        f"[VISION_ACTIVE] {_stall.capitalize()} captured. "
                        f"Immediately say ONE short natural sentence in the user's own language, "
                        f"telling them you are looking at their {_stall} right now. "
                        f"Do NOT describe or guess content — the actual image arrives in the NEXT message."
                    )

            elif name == "close_camera":
                self.ui.stop_camera_stream()
                result = "Camera closed."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
                # Mirror results to the on-screen content panel
                _mode = args.get("mode", "search")
                if r and not r.startswith("No results") and not r.startswith("Search failed"):
                    _query = args.get("query") or ", ".join(args.get("items", []))
                    _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
                    self.ui.show_content(_label, r)
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "system_status":
                r = await loop.run_in_executor(None, get_system_status)
                result = str(r)

            elif name == "shutdown_ultron" or name == "shutdown_jarvis":
                self._shutdown_ultron()
                result = "Goodbye, Boss."

            else:
                result = f"Unknown tool: {name}"

            elapsed_ms = (time.perf_counter() - t_start) * 1000.0
            print(f"[ULTRON LOG] Tool: {name} | Status: SUCCESS | Time: {elapsed_ms:.1f}ms")

        except Exception as e:
            elapsed_ms = (time.perf_counter() - t_start) * 1000.0
            print(f"[ULTRON LOG] Tool: {name} | Status: FAILED ({e}) | Time: {elapsed_ms:.1f}ms")
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.set_app_state("LISTENING")

        print(f"[ULTRON] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[ULTRON] [Mic] Audio recording started.")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                ultron_speaking = self._is_speaking
            if not ultron_speaking and not self.ui.muted and not self._phone_active:
                data = indata.tobytes()
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[ULTRON] [Mic] InputStream open successfully.")
                while True:
                    await asyncio.sleep(0.02)
        except Exception as e:
            print(f"[ULTRON] [Mic Warning]: {e}. Continuing in text-command mode.")
            while True:
                await asyncio.sleep(1.0)

    async def _receive_audio(self):
        print("[ULTRON] [Recv] Audio receiver loop started.")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    if response.data:
                        if self._interrupted:
                            pass  # discard: interrupted
                        else:
                            if not self._t_tts_start:
                                self._t_tts_start = time.perf_counter()
                                self._log_voice_step("TTS START", f"{len(response.data)} bytes audio PCM received")
                            if self._t_ai_start > 0 and self._t_ai_ms == 0.0:
                                self._t_ai_ms = (time.perf_counter() - self._t_ai_start) * 1000.0
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            # Split into ~50 ms chunks so interrupt() stops audio within 50 ms
                            # (24000 Hz × 2 bytes/sample × 0.05 s = 2400 bytes per slice)
                            _audio_data = response.data
                            _SLICE = 2400
                            for _i in range(0, len(_audio_data), _SLICE):
                                self.audio_in_queue.put_nowait(_audio_data[_i : _i + _SLICE])

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            if self._t_ai_start > 0 and self._t_ai_ms == 0.0:
                                self._t_ai_ms = (time.perf_counter() - self._t_ai_start) * 1000.0
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt and txt != (out_buf[-1] if out_buf else ""):
                                out_buf.append(txt)
                                self._log_voice_step("AI RESPONSE", f"'{txt}'")

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                self._interrupted = False
                                in_buf.append(txt)
                                self._last_user_speech = time.monotonic()
                                low_txt = txt.lower().strip()
                                self._log_voice_step("STT RESULT", f"'{txt}'")

                                # Voice Priority (Barge-in): Stop TTS instantly when new user speech begins
                                with self._speaking_lock:
                                    is_spk = self._is_speaking
                                if is_spk:
                                    self._log_voice_step("INTERRUPT / BARGE-IN", f"Interrupted TTS for speech: '{txt}'")
                                    self.interrupt()

                                # System Power / App Control Direct Command Check
                                if self._handle_system_or_app_commands(txt):
                                    continue
                                else:
                                    self.set_app_state("THINKING")
                                    if not self._t_total_start:
                                        self._t_total_start = time.perf_counter() - 0.3
                                        self._t_stt_ms = 300.0
                                    self._t_ai_start = time.perf_counter()

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            # Compute TTS timing if audio was played
                            if self._t_tts_start > 0 and self._t_tts_ms == 0.0:
                                self._t_tts_ms = (time.perf_counter() - self._t_tts_start) * 1000.0

                            # Log total timing summary for this turn
                            self._log_pipeline_timing()

                            # If this turn_complete ends an interrupted response, clear the
                            # flag and skip all further processing for that turn.
                            if self._interrupted:
                                self._interrupted = False
                                in_buf  = []
                                out_buf = []
                                continue

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "user",
                                        "text": full_in,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"{self._asst_name}: {full_out}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "jarvis",
                                        "text": full_out,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            out_buf = []

                            # Vision injection: model finished tool-response turn → now send the image
                            if self._pending_vision and self.session:
                                import base64 as _b64
                                img_b, mime_t, question, angle = self._pending_vision
                                self._pending_vision = None
                                b64 = _b64.b64encode(img_b).decode("ascii")
                                print(f"[Vision] 📤 {len(img_b):,} bytes (angle={angle}) → main session")
                                await self.session.send_client_content(
                                    turns={"parts": [
                                        {"inline_data": {"mime_type": mime_t, "data": b64}},
                                        {"text": question},
                                    ]},
                                    turn_complete=True,
                                )
                                # Mark next turn_complete behaviour depending on angle
                                if self._vision_cam_active:
                                    # Camera: keep busy until JARVIS finishes speaking the answer
                                    self._vision_cam_active    = False
                                    self._vision_close_pending = True
                                else:
                                    # Screen-only: no camera to close; release busy flag now
                                    self._vision_busy = False
                            elif self._vision_close_pending:
                                # This turn_complete IS the vision answer — close camera + release busy flag
                                self._vision_close_pending = False
                                self._vision_busy = False
                                async def _cam_close():
                                    await asyncio.sleep(2.0)
                                    self.ui.stop_camera_stream()
                                asyncio.create_task(_cam_close())

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[ULTRON] [Tool Call]: {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
        except Exception as e:
            print(f"[ULTRON] [Recv Error]: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[ULTRON] [Play] Audio playback loop started.")

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        self._out_stream = stream
        stream.start()

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.02
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False, 0.0)
                        self._turn_done_event.clear()
                    continue

                # Calculate real-time RMS audio amplitude from 16-bit PCM chunk
                amp = 0.0
                try:
                    import numpy as np
                    arr = np.frombuffer(chunk, dtype=np.int16)
                    if len(arr) > 0:
                        rms = float(np.sqrt(np.mean(arr.astype(np.float32) ** 2)))
                        amp = min(rms / 10000.0, 1.0)
                except Exception:
                    pass

                self.set_speaking(True, amp)
                try:
                    await asyncio.to_thread(stream.write, chunk)
                except (RuntimeError, asyncio.CancelledError):
                    break   # executor shutting down — exit cleanly
        except Exception as e:
            print(f"[ULTRON] [Play Warning]: {e}. Continuing without audio output.")
            while True:
                await asyncio.sleep(1.0)
        finally:
            self.set_speaking(False, 0.0)
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    # ── Morning briefing ────────────────────────────────────────────────────────

    async def _send_startup_briefing(self) -> None:
        """
        Startup briefing:
          Instant greeting & status report (no news prefetching).
        """
        memory   = load_memory()
        identity = memory.get("identity", {})

        def _val(k: str) -> str:
            e = identity.get(k, {})
            return (e.get("value", "") if isinstance(e, dict) else str(e)).strip()

        lang = _val("language")
        name = _val("name")
        time_str = datetime.now().strftime("%H:%M")

        await asyncio.sleep(0.3)
        if not self.session:
            return

        # ── Instant greeting & status ─────────────────────────────────────────
        lang_clause = f" Respond in {lang}." if lang else ""
        name_clause = f" Address the user as {name}." if name else ""
        p1 = (
            f"Greet the user, mention it is {time_str}, state that systems and HUD HUNNY are fully operational, "
            f"and ask how you can assist today. One or two short sentences only. Do not call any tools.{lang_clause}{name_clause}"
        )

        # Clear the turn-done event
        if self._turn_done_event:
            self._turn_done_event.clear()

        await self.session.send_client_content(
            turns={"parts": [{"text": p1}]},
            turn_complete=True,
        )
        self.ui.write_log("SYS: Startup briefing greeting sent.")

    # ── System monitor ──────────────────────────────────────────────────────────

    async def _run_system_monitor(self) -> None:
        """Background task: emergency siren & auto-close background apps monitor."""
        emergency_active = False
        from actions.system_monitor import get_detailed_system_metrics
        while True:
            await asyncio.sleep(2.5)
            metrics = {}
            try:
                metrics = await asyncio.to_thread(get_detailed_system_metrics)
                if self._dashboard:
                    asyncio.create_task(self._dashboard.broadcast({"type": "sys_metrics", "metrics": metrics}))
                self.ui.update_telemetry(metrics)
            except Exception:
                pass

            status = await asyncio.to_thread(self._sys_monitor.check_emergency, metrics)
            is_90 = status.get("is_emergency_90", False)
            is_95 = status.get("is_overload_95", False)
            closed = status.get("closed", [])
            cpu = status.get("cpu", 0)
            ram = status.get("ram", 0)

            if is_90 and not emergency_active:
                emergency_active = True
                self.ui.set_state("EMERGENCY")
                self.ui.write_log(f"SYS_ALERT: EMERGENCY SYSTEM OVERLOAD DETECTED (CPU: {cpu}%, RAM: {ram}%)! Red alert active.")
                if self.session:
                    try:
                        await self.session.send_client_content(
                            turns={"parts": [{"text": f"[SYSTEM_ALERT] Emergency system overload! CPU/RAM at {max(cpu, ram)}%. State that red alert emergency siren is active."}]},
                            turn_complete=True,
                        )
                    except Exception:
                        pass

            elif not is_90 and emergency_active:
                emergency_active = False
                self.ui.set_state("LISTENING" if not self.ui.muted else "MUTED")
                self.ui.write_log("SYS: System usage normalized (<85%). Emergency alert deactivated.")

            if closed and self.session:
                app_names = ", ".join(closed).replace(".exe", "")
                self.ui.write_log(f"SYS_ALERT: 95%+ OVERLOAD — Auto-terminated heavy background apps: {app_names}.")
                try:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": f"[SYSTEM_ALERT] Critical system overload (>95%). Automatically terminated heavy background applications ({app_names}) to safeguard hardware. Inform user in 1 brief sentence."}]},
                        turn_complete=True,
                    )
                except Exception:
                    pass

            alert = await asyncio.to_thread(self._sys_monitor.check, metrics)
            if alert and self.session and not is_90:
                try:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": alert}]},
                        turn_complete=True,
                    )
                except Exception as e:
                    print(f"[Monitor] ⚠️ Could not send alert: {e}")

    # ── Proactive mode ──────────────────────────────────────────────────────────

    async def _run_proactive_mode(self) -> None:
        """
        Background task: periodically checks if the user has been silent long enough,
        then hands time + memory context to Gemini so it can decide what (if anything)
        to say proactively. No hardcoded rules — Gemini makes the call.
        """
        while True:
            await asyncio.sleep(60)   # evaluate once per minute

            if not self.session:
                continue

            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue

            if not self._proactive.should_trigger(self._last_user_speech):
                continue

            self._proactive.mark_triggered()

            try:
                memory = await asyncio.to_thread(load_memory)
                prompt = self._proactive.build_prompt(memory)
                await self.session.send_client_content(
                    turns={"parts": [{"text": prompt}]},
                    turn_complete=True,
                )
                self.ui.write_log("SYS: Proactive check-in.")
            except Exception as e:
                print(f"[Proactive] ⚠️ {e}")

    # ── Phone audio relay ────────────────────────────────────────────────────────

    async def _relay_phone_audio(self) -> None:
        """Forward phone mic PCM chunks from dashboard queue into the Gemini Live session."""
        q = self._dashboard._phone_audio_queue
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No audio for 1 s → phone mic inactive, give PC mic back
                self._phone_active = False
                continue
            self._phone_active = True   # phone is streaming — silence PC mic
            with self._speaking_lock:
                speaking = self._is_speaking
            if not speaking and not self.ui.muted:
                try:
                    self.out_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    pass

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    # ── Fast-Path Intent Router ───────────────────────────────────────────────

    async def _try_fast_path_command(self, text: str) -> bool:
        """
        Deterministic Fast-Path Intent Router.
        Executes simple commands locally in <20ms without waiting for AI model API.
        """
        clean_low = text.lower().strip()
        t_start = time.perf_counter()

        # 1. Stop / Interrupt
        if clean_low in ("stop", "/stop_speech", "stop_speech", "interrupt", "quiet", "cancel", "shut up", "bye ultron"):
            if hasattr(self, "audio_in_queue") and self.audio_in_queue:
                while not self.audio_in_queue.empty():
                    try:
                        self.audio_in_queue.get_nowait()
                    except Exception:
                        break
            self.set_speaking(False)
            if self._turn_done_event:
                self._turn_done_event.set()
            self.set_app_state("LISTENING" if not self.ui.muted else "MUTED")
            self.ui.write_log("SYS: Speech playback stopped immediately.")
            if self._dashboard:
                asyncio.create_task(self._dashboard.broadcast({"type": "sys", "text": "Speech playback stopped."}))
            t_ms = (time.perf_counter() - t_start) * 1000.0
            from core.diagnostics import diagnostics
            diagnostics.record_command(text, total_ms=t_ms, action_ms=t_ms, is_fast_path=True)
            return True

        # 2. Mute / Unmute
        if clean_low in ("/toggle_mic", "toggle_mic", "mute", "unmute"):
            if clean_low == "mute":
                self.ui.muted = True
            elif clean_low == "unmute":
                self.ui.muted = False
            else:
                self.ui.muted = not self.ui.muted
            new_state = "MUTED" if self.ui.muted else "LISTENING"
            self.set_app_state(new_state)
            self.ui.write_log(f"SYS: Microphone {'MUTED (OFF)' if self.ui.muted else 'UNMUTED (ON)'}.")
            if self._dashboard:
                asyncio.create_task(self._dashboard.broadcast({"type": "sys", "text": f"Microphone {'MUTED' if self.ui.muted else 'UNMUTED'}."}))
            t_ms = (time.perf_counter() - t_start) * 1000.0
            from core.diagnostics import diagnostics
            diagnostics.record_command(text, total_ms=t_ms, action_ms=t_ms, is_fast_path=True)
            return True

        # 3. Open Application
        open_match = re.match(r"^open\s+(.+)$", clean_low)
        if open_match:
            app_name = open_match.group(1).strip()
            if app_name and app_name not in ("camera", "screenshare", "reminders", "files", "utilities", "system-status"):
                res = await asyncio.to_thread(open_app, {"app_name": app_name})
                self.ui.write_log(f"[FAST-PATH]: {res}")
                if self._dashboard:
                    asyncio.create_task(self._dashboard.broadcast({"type": "ai", "text": res}))
                self.set_app_state("LISTENING")
                t_ms = (time.perf_counter() - t_start) * 1000.0
                from core.diagnostics import diagnostics
                diagnostics.record_command(text, total_ms=t_ms, action_ms=t_ms, is_fast_path=True)
                return True

        # 4. Close Application
        close_match = re.match(r"^close\s+(.+)$", clean_low)
        if close_match:
            app_name = close_match.group(1).strip()
            if app_name:
                from actions.open_app import close_app
                res = await asyncio.to_thread(close_app, {"app_name": app_name})
                self.ui.write_log(f"[FAST-PATH]: {res}")
                if self._dashboard:
                    asyncio.create_task(self._dashboard.broadcast({"type": "ai", "text": res}))
                self.set_app_state("LISTENING")
                t_ms = (time.perf_counter() - t_start) * 1000.0
                from core.diagnostics import diagnostics
                diagnostics.record_command(text, total_ms=t_ms, action_ms=t_ms, is_fast_path=True)
                return True

        # 5. Spotify Controls
        if clean_low in ("pause music", "pause spotify", "resume music", "resume spotify", "next song", "previous song"):
            from actions.spotify_control import pause_spotify, resume_spotify, next_spotify_song, prev_spotify_song
            if "pause" in clean_low:
                res = await asyncio.to_thread(pause_spotify)
            elif "resume" in clean_low:
                res = await asyncio.to_thread(resume_spotify)
            elif "next" in clean_low:
                res = await asyncio.to_thread(next_spotify_song)
            else:
                res = await asyncio.to_thread(prev_spotify_song)
            self.ui.write_log(f"[FAST-PATH]: {res}")
            if self._dashboard:
                asyncio.create_task(self._dashboard.broadcast({"type": "ai", "text": res}))
            self.set_app_state("LISTENING")
            t_ms = (time.perf_counter() - t_start) * 1000.0
            from core.diagnostics import diagnostics
            diagnostics.record_command(text, total_ms=t_ms, action_ms=t_ms, is_fast_path=True)
            return True

        # 6. Performance Diagnostics Report
        if clean_low in ("diagnostic report", "performance report", "/perf", "diagnostics"):
            from core.diagnostics import diagnostics
            report = diagnostics.generate_report_text()
            self.ui.write_log(report)
            if self._dashboard:
                asyncio.create_task(self._dashboard.broadcast({"type": "sys", "text": report}))
            self.set_app_state("LISTENING")
            return True

        return False

    # ── dashboard command relay ─────────────────────────────────────────────

    async def _process_dashboard_commands(self) -> None:
        import base64
        while True:
            try:
                item = await asyncio.wait_for(
                    self._dashboard._command_queue.get(), timeout=0.02
                )
                if not item:
                    continue

                if isinstance(item, dict) and item.get("type") == "image":
                    b64_str = item.get("data", "")
                    mime = item.get("mime", "image/jpeg")
                    img_bytes = base64.b64decode(b64_str)
                    self.ui.write_log("SYS: Image received. ULTRON analyzing image...")
                    if self.session:
                        await self.session.send_realtime_input(media={"data": img_bytes, "mime_type": mime})
                        await self.session.send_client_content(
                            turns={"parts": [{"text": "Please analyze this attached image in full detail, describe every visual element, and explain what it represents."}]},
                            turn_complete=True,
                        )
                    else:
                        try:
                            client = genai.Client(api_key=_get_api_key())
                            res = await asyncio.to_thread(
                                client.models.generate_content,
                                model="gemini-2.5-flash",
                                contents=[
                                    types.Part.from_bytes(data=img_bytes, mime_type=mime),
                                    "Please analyze this attached image in full detail, describe every visual element, and explain what it represents."
                                ]
                            )
                            reply_text = res.text
                        except Exception as ex:
                            reply_text = f"Image received, but analysis failed: {ex}"
                        self.ui.write_log(f"ULTRON: {reply_text}")
                        if self._dashboard:
                            asyncio.create_task(self._dashboard.broadcast({"type": "ai", "text": reply_text}))
                else:
                    text = str(item).strip()
                    if not text:
                        continue

                    # 1. ALWAYS try fast-path command first
                    handled = await self._try_fast_path_command(text)
                    if handled:
                        continue

                    self.ui.write_log(f"[Web]: {text}")
                    self.set_app_state("THINKING")

                    # 2. Check if live WebSocket session is ready
                    for _ in range(10):
                        if self.session:
                            break
                        await asyncio.sleep(0.05)

                    if self.session:
                        self._t_total_start = time.perf_counter()
                        self._t_stt_ms = 0.0
                        self._t_backend_ms = 10.0
                        self._t_ai_start = time.perf_counter()
                        await self.session.send_client_content(
                            turns={"parts": [{"text": text}]},
                            turn_complete=True,
                        )
                    else:
                        # 3. Fallback to standard Gemini REST API model so user always gets a reply
                        try:
                            client = genai.Client(api_key=_get_api_key())
                            res = await asyncio.to_thread(
                                client.models.generate_content,
                                model="gemini-2.5-flash",
                                contents=text
                            )
                            reply_text = res.text
                        except Exception as ex:
                            reply_text = f"ULTRON received your command: '{text}'. Error connecting to Gemini API: {ex}"
                        
                        self.ui.write_log(f"ULTRON: {reply_text}")
                        self.speak(reply_text)
                        self.set_app_state("LISTENING")

            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.1)

    # ── main loop ───────────────────────────────────────────────────────────

    async def run(self):
        self._loop = asyncio.get_event_loop()

        # Start dashboard (optional — needs: pip install fastapi "uvicorn[standard]" cryptography)
        try:
            from dashboard.server import DashboardServer, PORT
            import webbrowser
            self._dashboard = DashboardServer()
            self._dashboard.set_connect_callback(self._on_phone_connected)
            asyncio.create_task(self._dashboard.serve())
            asyncio.create_task(self._process_dashboard_commands())
            # webbrowser.open(f"http://127.0.0.1:{PORT}")
        except Exception as e:
            print(f"[Dashboard] Disabled: {e}")
            self._dashboard = None

        while True:
            try:
                print("[ULTRON] Connecting...")
                self.set_app_state("THINKING")
                config = self._build_config()

                # Fresh client on every reconnect — avoids stale HTTP session state
                client = genai.Client(
                    api_key=_get_api_key(),
                    http_options={"api_version": "v1beta"}
                )

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session          = session
                    self.audio_in_queue   = asyncio.Queue()
                    self.out_queue        = asyncio.Queue(maxsize=200)
                    self._turn_done_event = asyncio.Event()

                    # Reset transient state that must not carry over from a previous session
                    self._pending_vision       = None
                    self._vision_cam_active    = False  
                    self._vision_close_pending = False
                    self._vision_busy          = False
                    self._vision_last_time     = 0.0
                    self._interrupted          = False

                    print("[ULTRON] Connected.")
                    self.set_app_state("LISTENING")
                    self.ui.write_log("SYS: ULTRON online.")

                    if self._dashboard:
                        await self._dashboard.broadcast({"type": "status", "state": "active"})

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._run_system_monitor())
                    tg.create_task(self._run_proactive_mode())
                    if self._dashboard:
                        tg.create_task(self._relay_phone_audio())

                    # Wake Word or Morning briefing — fires once per process launch
                    if not self._briefing_sent:
                        self._briefing_sent = True
                        if "--wake-word" in sys.argv:
                            tg.create_task(self.session.send_client_content(
                                turns={"parts": [{"text": "wake up ultron"}]},
                                turn_complete=True
                            ))
                        elif get_brief_enabled():
                            tg.create_task(self._send_startup_briefing())

            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except BaseException as e:
                # Catches both Exception and BaseExceptionGroup (Python 3.11+
                # TaskGroup raises BaseExceptionGroup when tasks are cancelled
                # externally, which `except Exception` would miss, letting the
                # exception escape the while-loop and causing asyncio.run() to
                # start shutdown — resulting in "executor after shutdown" errors).
                err_str = str(e)
                print(f"[JARVIS] Error ({type(e).__name__}): {e}")
                traceback.print_exc()

                # Invalid / missing / broken API key — stop hammering the API, prompt re-configuration
                if (
                    isinstance(e, ApiKeyMissing)
                    or "API key not valid" in err_str
                    or "1007" in err_str
                ):
                    self.ui.write_log("ERR: API key missing or invalid — please re-enter your key.")
                    self.ui.set_state("SLEEPING")
                    self.ui.prompt_reconfig()
                    while not self.ui._win._ready:
                        await asyncio.sleep(1)
                    print("[JARVIS] New API key saved — reconnecting...")
                    _conn_backoff = 3
                    continue

                # Network / timeout errors — log clearly and back off
                is_net_err = any(k in err_str for k in (
                    "TimeoutError", "timed out", "getaddrinfo", "CancelledError",
                    "ConnectionRefusedError", "OSError", "Cannot connect",
                ))
                if is_net_err:
                    _conn_backoff = min(getattr(self, "_conn_backoff", 3) * 2, 60)
                    self._conn_backoff = _conn_backoff
                    self.ui.write_log(
                        f"NET: Bağlantı kurulamadı — {_conn_backoff}s sonra tekrar deneniyor. "
                        "(VPN gerekiyor olabilir)"
                    )
                else:
                    self._conn_backoff = 3
            finally:
                self.session = None

            self.set_speaking(False)
            self.set_app_state("SLEEPING")

            if self._dashboard:
                await self._dashboard.broadcast({"type": "status", "state": "sleeping"})

            delay = getattr(self, "_conn_backoff", 3)
            print(f"[ULTRON] Reconnecting in {delay}s...")
            await asyncio.sleep(delay)

JarvisLive = UltronLive


import socket

_single_instance_sock = None

def _ensure_single_instance():
    global _single_instance_sock
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 39152))
        sock.listen(1)
        _single_instance_sock = sock
        print("[ULTRON] [Startup Log 1/5]: Single instance port lock bound successfully.")
    except OSError as e:
        print(f"[ULTRON] [Startup Warning]: Port 39152 bound by another socket ({e}). Checking running processes...", file=sys.stderr)
        my_pid = os.getpid()
        other_running = False
        try:
            import psutil
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    if proc.info["pid"] != my_pid and "python" in (proc.info["name"] or "").lower():
                        cmd = " ".join(proc.info["cmdline"] or [])
                        if "main.py" in cmd:
                            other_running = True
                            print(f"[ULTRON] Found active ULTRON process (PID {proc.info['pid']}).", file=sys.stderr)
                            break
                except Exception:
                    continue
        except Exception:
            pass

        if other_running:
            print("[ULTRON] ⚠️ ULTRON is already running in another process! Exiting duplicate launch to prevent conflict.", file=sys.stderr)
            sys.exit(0)
        else:
            print("[ULTRON] ℹ️ Stale socket lock detected from closed process. Proceeding with launch...", file=sys.stderr)

def main():
    import os, sys, subprocess
    proj_dir  = str(BASE_DIR)
    cwd_dir   = os.getcwd()
    py_exec   = sys.executable
    venv_path = os.environ.get("VIRTUAL_ENV", "None (Global Python)")

    git_ver = "N/A"
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=proj_dir)
        if r.returncode == 0 and r.stdout.strip():
            git_ver = r.stdout.strip()
    except Exception:
        pass

    print("[ULTRON] ===================================================")
    print("[ULTRON]   ULTRON OS Engine — Startup Initialization")
    print(f"[ULTRON] 📁 Project Directory:    {proj_dir}")
    print(f"[ULTRON] 📂 Working Directory:    {cwd_dir}")
    print(f"[ULTRON] 🐍 Python Executable:    {py_exec}")
    print(f"[ULTRON] 📦 Virtual Environment:  {venv_path}")
    print(f"[ULTRON] 🔖 Git Commit/Version:   {git_ver}")
    print("[ULTRON] ===================================================")
    _ensure_single_instance()
    print("[ULTRON] [Startup Log 2/5]: Initializing PyQt6 UI Window & WebEngine...")
    try:
        ui = UltronUI("face.png")
        print("[ULTRON] [Startup Log 3/5]: UI Window & WebEngine loaded successfully.")
    except Exception as e:
        print(f"[ULTRON Fatal Error]: Failed to initialize UI window: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)

    def runner():
        print("[ULTRON] [Startup Log 4/5]: Checking Gemini API key configuration...")
        ui.wait_for_api_key()
        print("[ULTRON] [Startup Log 5/5]: API key ready. Launching live AI worker engine...")
        ultron = UltronLive(ui)
        try:
            asyncio.run(ultron.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")
        except Exception as e:
            print(f"[ULTRON Worker Error]: {e}", file=sys.stderr)
            traceback.print_exc()

    threading.Thread(target=runner, daemon=True, name="UltronWorkerThread").start()
    print("[ULTRON] Entering PyQt main event loop. System ready.")
    ui.root.mainloop()

if __name__ == "__main__":
    main()