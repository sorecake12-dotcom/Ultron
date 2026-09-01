"""
ULTRON Custom Automation Engine

Provides end-to-end natural-language command understanding, multi-step task planning,
sequential execution, empirical step verification, short-term context management,
local pattern learning, and structured telemetry logging.

Pipeline Workflow:
Voice/Text Input -> Intent Detection -> Task Planner -> Action Executor -> Verification -> Response/TTS
"""

import json
import os
import re
import sys
import time
import threading
import subprocess
from pathlib import Path
from typing import Callable, Any, Optional, List, Dict

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
PATTERNS_FILE = BASE_DIR / "memory" / "learned_patterns.json"


# ── 1. SHORT-TERM TASK CONTEXT ──────────────────────────────────────────────────

class TaskContext:
    """Maintains short-term conversation & execution state for referent resolution."""

    def __init__(self):
        self.active_app: Optional[str] = None
        self.last_target_contact: Optional[str] = None
        self.last_query: Optional[str] = None
        self.last_action: Optional[str] = None
        self.last_command: Optional[str] = None
        self.history: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def update(self, command: str, action: str, app: Optional[str] = None,
               contact: Optional[str] = None, query: Optional[str] = None, result: Optional[str] = None):
        with self._lock:
            self.last_command = command
            self.last_action = action
            if app:
                self.active_app = app
            if contact:
                self.last_target_contact = contact
            if query:
                self.last_query = query
            self.history.append({
                "timestamp": time.time(),
                "command": command,
                "action": action,
                "app": app,
                "contact": contact,
                "query": query,
                "result": result
            })
            # Limit history to 20 items
            if len(self.history) > 20:
                self.history.pop(0)

    def resolve_referents(self, command: str) -> str:
        """Resolves pronouns like 'it', 'that', 'this' based on short-term context."""
        if not command:
            return command
        
        cmd_clean = re.sub(r"[.!?]+$", "", command.strip())
        cmd_lower = cmd_clean.lower().strip()
        with self._lock:
            active_app = self.active_app
            last_cmd = self.last_command
            last_query = self.last_query

        # "close it" / "close this" / "close the app"
        if cmd_lower in ("close it", "close this", "close app", "close the app", "band karo", "isey band karo"):
            if active_app:
                return f"close {active_app}"

        # "open it" / "open this"
        if cmd_lower in ("open it", "open this", "isey kholo"):
            if active_app:
                return f"open {active_app}"

        # "do that again" / "again" / "repeat that"
        if cmd_lower in ("do that again", "again", "repeat", "repeat that", "fir se karo", "phir se karo"):
            if last_cmd and last_cmd not in ("do that again", "again", "repeat"):
                return last_cmd

        # Replace " close it " in compound commands e.g. "open chrome and then close it"
        if active_app and re.search(r"\bclose it\b", cmd_clean, flags=re.IGNORECASE):
            cmd_clean = re.sub(r"\bclose it\b", f"close {active_app}", cmd_clean, flags=re.IGNORECASE)

        if active_app and re.search(r"\bopen it\b", cmd_clean, flags=re.IGNORECASE):
            cmd_clean = re.sub(r"\bopen it\b", f"open {active_app}", cmd_clean, flags=re.IGNORECASE)

        return cmd_clean



# ── 2. APPLICATION DETECTOR ────────────────────────────────────────────────────

class AppDetector:
    """Detects and resolves target applications using aliases, process names, and window titles."""

    ALIASES = {
        "chrome": {"canonical": "Google Chrome", "exe": "chrome.exe", "web": "https://www.google.com"},
        "google chrome": {"canonical": "Google Chrome", "exe": "chrome.exe", "web": "https://www.google.com"},
        "spotify": {"canonical": "Spotify", "exe": "Spotify.exe", "web": "https://open.spotify.com"},
        "whatsapp": {"canonical": "WhatsApp", "exe": "WhatsApp.exe", "uri": "whatsapp:"},
        "antigravity": {"canonical": "AntiGravity", "exe": "AntiGravity.exe"},
        "youtube": {"canonical": "YouTube", "web": "https://www.youtube.com"},
        "vscode": {"canonical": "Visual Studio Code", "exe": "Code.exe"},
        "code": {"canonical": "Visual Studio Code", "exe": "Code.exe"},
        "edge": {"canonical": "Microsoft Edge", "exe": "msedge.exe"},
        "firefox": {"canonical": "Firefox", "exe": "firefox.exe"},
        "notepad": {"canonical": "Notepad", "exe": "notepad.exe"},
        "discord": {"canonical": "Discord", "exe": "Discord.exe"},
        "telegram": {"canonical": "Telegram", "exe": "Telegram.exe"},
        "vlc": {"canonical": "VLC Media Player", "exe": "vlc.exe"},
    }

    @classmethod
    def resolve_app(cls, name: str) -> Dict[str, str]:
        raw = (name or "").lower().strip()
        if raw in cls.ALIASES:
            info = cls.ALIASES[raw].copy()
            info["key"] = raw
            return info
        for key, info in cls.ALIASES.items():
            if key in raw or raw in key:
                res = info.copy()
                res["key"] = key
                return res
        return {"canonical": name, "exe": f"{raw}.exe", "key": raw}

    @classmethod
    def is_running(cls, name: str) -> bool:
        if not _PSUTIL:
            return True
        info = cls.resolve_app(name)
        exe_target = info.get("exe", "").lower()
        key_target = info.get("key", "").lower()
        
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                pname = (proc.info["name"] or "").lower()
                if exe_target and exe_target in pname:
                    return True
                if key_target and key_target in pname:
                    return True
            except Exception:
                continue
        return False


# ── 3. INTENT DETECTION & TASK PLANNER ──────────────────────────────────────────

class ActionStep:
    """Represents an individual executable step in a multi-step task plan."""

    def __init__(self, step_num: int, action_type: str, parameters: Dict[str, Any], description: str):
        self.step_num = step_num
        self.action_type = action_type
        self.parameters = parameters
        self.description = description
        self.status = "PENDING"  # PENDING | EXECUTING | SUCCESS | FAILED | CANCELLED
        self.result_detail: Optional[str] = None
        self.error_reason: Optional[str] = None
        self.alternative_attempted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_num": self.step_num,
            "action_type": self.action_type,
            "parameters": self.parameters,
            "description": self.description,
            "status": self.status,
            "result": self.result_detail,
            "error": self.error_reason
        }


class TaskPlan:
    """Represents a planned sequence of ActionSteps."""

    def __init__(self, original_command: str, resolved_command: str):
        self.original_command = original_command
        self.resolved_command = resolved_command
        self.steps: List[ActionStep] = []
        self.is_completed = False
        self.failed_step: Optional[ActionStep] = None

    def add_step(self, action_type: str, parameters: Dict[str, Any], description: str):
        step_num = len(self.steps) + 1
        self.steps.append(ActionStep(step_num, action_type, parameters, description))


class IntentDetector:
    """Decomposes natural language commands into structured multi-step plans."""

    CONJUNCTIONS = [
        r"\b(?:and then|after that|then|and also|plus|and)\b"
    ]

    @classmethod
    def decompose_command(cls, command: str) -> List[str]:
        """Splits a compound command into individual clause strings."""
        pattern = r"|".join(cls.CONJUNCTIONS)
        parts = re.split(pattern, command, flags=re.IGNORECASE)
        sub_cmds = [p.strip() for p in parts if p.strip()]
        return sub_cmds if sub_cmds else [command]

    @classmethod
    def parse_single_intent(cls, sub_cmd: str, last_app_in_plan: Optional[str] = None) -> Optional[Dict[str, Any]]:
        raw = re.sub(r"[.!?]+$", "", sub_cmd.strip().lower()).strip()

        # 1. Stop / Cancel
        if raw in ("stop", "ruko", "halt", "cancel", "stop it", "bas"):
            return {"action": "stop", "params": {}}

        # 2. Spotify control / play music
        if "spotify" in raw or last_app_in_plan == "spotify" or any(w in raw for w in ["play my music", "play music", "play song", "gaana chalao"]):
            if any(w in raw for w in ["play", "suno", "chalao", "listen"]):
                m = re.search(r"(?:play|suno|chalao|listen)\s+(?:to\s+)?(.+?)(?:\s+on\s+spotify|\s+in\s+spotify|$)", raw)
                query = m.group(1).strip() if m else "music"
                query = re.sub(r"\b(spotify|my|music|song|gaana|on|in|play|chalao|suno)\b", "", query, flags=re.IGNORECASE).strip() or "my music"
                return {"action": "spotify_control", "params": {"action": "play", "query": query}}

        # 3. WhatsApp control / find or call contact
        if "whatsapp" in raw or last_app_in_plan == "whatsapp":
            if any(w in raw for w in ["find", "search", "contact"]):
                m = re.search(r"(?:find|search|for)\s+([a-zA-Z0-9_\s]+)", raw)
                contact = m.group(1).replace("whatsapp", "").strip() if m else raw.replace("find", "").replace("search", "").strip()
                return {"action": "whatsapp_control", "params": {"action": "search_contact", "contact": contact}}
            if any(w in raw for w in ["call", "voice call"]):
                m = re.search(r"(?:call|voice call)\s+([a-zA-Z0-9_\s]+)", raw)
                contact = m.group(1).replace("whatsapp", "").strip() if m else raw.replace("call", "").strip()
                return {"action": "whatsapp_control", "params": {"action": "voice_call", "contact": contact}}
            if any(w in raw for w in ["message", "send message", "text"]):
                m = re.search(r"(?:message|text)\s+([a-zA-Z0-9_\s]+)", raw)
                contact = m.group(1).replace("whatsapp", "").strip() if m else ""
                return {"action": "whatsapp_control", "params": {"action": "send_message", "contact": contact}}

        # 4. Search on YouTube / Play YouTube
        if "youtube" in raw and any(w in raw for w in ["search", "play", "find", "look for", "chalao", "watch"]):
            m = re.search(r"(?:search|play|find|look for|watch)\s+(?:for\s+)?(.+?)(?:\s+on\s+youtube|\s+in\s+youtube|$)", raw)
            query = m.group(1).strip() if m else ""
            query = re.sub(r"\b(youtube|on|in|video|song|chalao)\b", "", query, flags=re.IGNORECASE).strip() or "trending"
            return {"action": "youtube_video", "params": {"action": "play", "query": query}}

        # 5. Search on Chrome / Browser search
        if any(kw in raw for kw in ["search for", "search about", "search ", "look up", "google"]):
            m = re.search(r"(?:search for|search about|search|look up|google)\s+(.+)", raw)
            if m:
                query = m.group(1).strip()
                return {"action": "browser_control", "params": {"action": "search", "query": query}}

        # 6. Close App
        if raw.startswith("close ") or raw.startswith("exit ") or raw.startswith("kill "):
            app_name = re.sub(r"^(close|exit|kill)\s+", "", raw).strip()
            return {"action": "close_app", "params": {"app_name": app_name}}

        # 7. Open App / Website
        if raw.startswith("open ") or raw.startswith("launch ") or raw.startswith("start "):
            app_name = re.sub(r"^(open|launch|start)\s+", "", raw).strip()
            return {"action": "open_app", "params": {"app_name": app_name}}

        # Generic action matching for second clause
        if any(w in raw for w in ["find ", "call "]):
            m = re.search(r"(?:find|call)\s+([a-zA-Z0-9_\s]+)", raw)
            target = m.group(1).strip() if m else ""
            if target:
                return {"action": "whatsapp_control", "params": {"action": "search_contact", "contact": target}}

        return None


    @classmethod
    def create_plan(cls, command: str, context: TaskContext) -> TaskPlan:
        resolved = context.resolve_referents(command)
        plan = TaskPlan(original_command=command, resolved_command=resolved)

        clauses = cls.decompose_command(resolved)
        last_app_in_plan = None

        for clause in clauses:
            intent = cls.parse_single_intent(clause, last_app_in_plan=last_app_in_plan)
            if intent:
                act = intent["action"]
                params = intent["params"]

                if act == "stop":
                    plan.add_step("stop", {}, "Stop active automation")
                elif act == "open_app":
                    app_name = params.get("app_name", "")
                    last_app_in_plan = app_name.lower().strip()
                    plan.add_step("open_app", {"app_name": app_name}, f"Open application '{app_name}'")
                elif act == "close_app":
                    app_name = params.get("app_name", "")
                    plan.add_step("close_app", {"app_name": app_name}, f"Close application '{app_name}'")
                elif act == "spotify_control":
                    query = params.get("query", "")
                    plan.add_step("spotify_control", {"action": "play", "query": query}, f"Play music '{query}' on Spotify")
                elif act == "whatsapp_control":
                    w_act = params.get("action", "")
                    contact = params.get("contact", "")
                    plan.add_step("whatsapp_control", params, f"WhatsApp {w_act} for '{contact}'")
                elif act == "youtube_video":
                    query = params.get("query", "")
                    plan.add_step("youtube_video", {"action": "play", "query": query}, f"Play YouTube video '{query}'")
                elif act == "browser_control":
                    b_act = params.get("action", "search")
                    query = params.get("query", "")
                    plan.add_step("browser_control", params, f"Browser {b_act}: '{query}'")

        # Fallback if rule-based parsing did not produce steps
        if not plan.steps:
            if "close" in resolved.lower():
                app = resolved.lower().replace("close", "").strip()
                plan.add_step("close_app", {"app_name": app}, f"Close {app}")
            else:
                app = resolved.lower().replace("open", "").strip()
                plan.add_step("open_app", {"app_name": app}, f"Open {app}")

        return plan



# ── 4. STEP VERIFICATION ───────────────────────────────────────────────────────

class StepVerifier:
    """Verifies whether an executed action step actually succeeded empirically."""

    @classmethod
    def verify(cls, step: ActionStep, raw_result: Any) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Returns: (success: bool, detail: str, safe_alternative: dict | None)
        """
        act = step.action_type
        params = step.parameters
        res_str = str(raw_result) if raw_result is not None else ""

        if act == "stop":
            return True, "Automation cancelled by user.", None

        if act == "open_app":
            app_name = params.get("app_name", "")
            from actions.open_app import verify_app_opened
            opened = verify_app_opened(app_name, timeout=2.5)
            if opened:
                return True, f"Application '{app_name}' confirmed open and active.", None
            
            # Safe alternative setup
            info = AppDetector.resolve_app(app_name)
            if "web" in info:
                alt = {"action": "open_app", "params": {"app_name": info["web"]}}
                return False, f"Could not verify desktop launch of '{app_name}'. Attempting web version fallback.", alt
            
            return False, f"Could not verify that '{app_name}' launched.", None

        if act == "close_app":
            app_name = params.get("app_name", "")
            from actions.open_app import verify_app_closed
            closed = verify_app_closed(app_name, timeout=2.0)
            if closed or "not currently open" in res_str.lower() or "closed" in res_str.lower():
                return True, f"Application '{app_name}' confirmed closed.", None
            return False, f"Failed to confirm closure of '{app_name}'.", None

        if act in ("spotify_control", "whatsapp_control", "youtube_video", "browser_control"):
            if "fail" in res_str.lower() or "error" in res_str.lower():
                return False, f"Action failed: {res_str}", None
            return True, res_str or "Action completed successfully.", None

        return True, res_str or "Step executed.", None


# ── 5. PATTERN LEARNER (LOCAL & PRIVACY PRESERVING) ────────────────────────────

class PatternLearner:
    """Learns local command patterns without storing sensitive personal data."""

    def __init__(self, filepath: Path = PATTERNS_FILE):
        self.filepath = filepath
        self.patterns: Dict[str, int] = self._load()

    def _load(self) -> Dict[str, int]:
        try:
            if self.filepath.exists():
                return json.loads(self.filepath.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save(self):
        try:
            self.filepath.parent.mkdir(parents=True, exist_ok=True)
            self.filepath.write_text(json.dumps(self.patterns, indent=2), encoding="utf-8")
        except Exception:
            pass

    def record_pattern(self, plan: TaskPlan):
        """Sanitizes and records action sequence frequency."""
        step_types = [step.action_type for step in plan.steps]
        if not step_types:
            return
        seq_key = " -> ".join(step_types)
        self.patterns[seq_key] = self.patterns.get(seq_key, 0) + 1
        self._save()

    def get_predictive_suggestion(self, current_action: str) -> Optional[str]:
        """Provides useful next-action predictive suggestion based on past patterns."""
        best_next = None
        highest_count = 0
        for pattern, count in self.patterns.items():
            parts = pattern.split(" -> ")
            if parts[0] == current_action and len(parts) > 1:
                if count > highest_count and count >= 3:
                    highest_count = count
                    best_next = parts[1]
        
        if best_next == "browser_control":
            return "Would you like to search the web?"
        elif best_next == "spotify_control":
            return "Would you like to play music?"
        elif best_next == "whatsapp_control":
            return "Would you like to check WhatsApp?"
        return None


# ── 6. ACTION EXECUTOR & AUTOMATION ENGINE ──────────────────────────────────────

class UltronAutomationEngine:
    """Central Automation Engine orchestrating detection, planning, execution, verification, and logging."""

    def __init__(self):
        self.context = TaskContext()
        self.learner = PatternLearner()
        self._active_plan: Optional[TaskPlan] = None
        self._stop_requested = False
        self._lock = threading.Lock()

    def stop_active_task(self):
        """Immediately halts any currently executing multi-step automation task."""
        with self._lock:
            self._stop_requested = True
            if self._active_plan:
                for step in self._active_plan.steps:
                    if step.status in ("PENDING", "EXECUTING"):
                        step.status = "CANCELLED"
            print("[AUTOMATION LOG] 🛑 STOP SIGNAL RECEIVED -> Active automation task cancelled.")

    def is_stop_requested(self) -> bool:
        with self._lock:
            return self._stop_requested

    def process_command(
        self,
        command: str,
        ui: Any = None,
        speak_fn: Optional[Callable[[str], None]] = None,
        executor_map: Optional[Dict[str, Callable]] = None
    ) -> str:
        """
        Executes a natural-language command through the complete automation engine pipeline.
        """
        start_t = time.perf_counter()
        with self._lock:
            self._stop_requested = False

        clean_cmd = str(command).strip()
        if not clean_cmd:
            return "Empty command received."

        # Check instant stop command
        if clean_cmd.lower() in ("stop", "ruko", "halt", "cancel", "stop music", "chup"):
            self.stop_active_task()
            if speak_fn:
                try:
                    speak_fn("Automation halted.")
                except Exception:
                    pass
            return "Automation stopped."

        # Step 1: Intent Detection & Task Planning
        plan = IntentDetector.create_plan(clean_cmd, self.context)
        self._active_plan = plan

        log_intent = f"Detected Intent: {len(plan.steps)} step(s) planned for '{plan.resolved_command}'"
        log_steps = " | ".join([f"Step {s.step_num}: {s.description}" for s in plan.steps])
        
        print(f"\n[AUTOMATION LOG] 📥 Command: '{clean_cmd}'")
        print(f"[AUTOMATION LOG] 🎯 Intent: {log_intent}")
        print(f"[AUTOMATION LOG] 📋 Plan: {log_steps}")

        if ui and hasattr(ui, "write_log"):
            ui.write_log(f"AUTO: Command: '{clean_cmd}' → {len(plan.steps)} action step(s)")

        final_results = []
        
        # Step 2: Sequential Action Execution Loop
        for step in plan.steps:
            if self.is_stop_requested():
                step.status = "CANCELLED"
                print(f"[AUTOMATION LOG] 🛑 Step {step.step_num} cancelled due to Stop signal.")
                break

            step.status = "EXECUTING"
            print(f"[AUTOMATION LOG] ⚡ Executing Step {step.step_num}/{len(plan.steps)}: {step.description}")
            if ui and hasattr(ui, "write_log"):
                ui.write_log(f"AUTO: Step {step.step_num}/{len(plan.steps)} — {step.description}")

            raw_res = self._dispatch_action(step, ui, speak_fn, executor_map)

            # Step 3: Empirical Step Verification
            verified, detail, alt_action = StepVerifier.verify(step, raw_res)

            if verified:
                step.status = "SUCCESS"
                step.result_detail = detail
                print(f"[AUTOMATION LOG] ✅ Verified Step {step.step_num}: {detail}")
                final_results.append(detail)

                # Update context
                app_name = step.parameters.get("app_name") or step.parameters.get("app")
                contact_name = step.parameters.get("contact") or step.parameters.get("receiver")
                query_str = step.parameters.get("query")
                self.context.update(clean_cmd, step.action_type, app=app_name, contact=contact_name, query=query_str, result=detail)

            else:
                step.status = "FAILED"
                step.error_reason = detail
                print(f"[AUTOMATION LOG] ❌ Verification Failed Step {step.step_num}: {detail}")

                # Attempt Safe Alternative if available
                if alt_action and not step.alternative_attempted and not self.is_stop_requested():
                    step.alternative_attempted = True
                    print(f"[AUTOMATION LOG] 🔄 Attempting safe alternative action: {alt_action}")
                    if ui and hasattr(ui, "write_log"):
                        ui.write_log(f"AUTO: Step {step.step_num} failed — attempting alternative...")
                    
                    alt_step = ActionStep(step.step_num, alt_action["action"], alt_action["params"], f"Alternative for {step.description}")
                    alt_res = self._dispatch_action(alt_step, ui, speak_fn, executor_map)
                    alt_verified, alt_detail, _ = StepVerifier.verify(alt_step, alt_res)

                    if alt_verified:
                        step.status = "SUCCESS"
                        step.result_detail = f"Success via alternative: {alt_detail}"
                        print(f"[AUTOMATION LOG] ✅ Alternative Succeeded Step {step.step_num}: {alt_detail}")
                        final_results.append(step.result_detail)
                    else:
                        final_results.append(f"Step {step.step_num} failed: {detail}")
                        plan.failed_step = step
                        break
                else:
                    final_results.append(f"Step {step.step_num} failed: {detail}")
                    plan.failed_step = step
                    break

        plan.is_completed = (plan.failed_step is None and not self.is_stop_requested())
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0

        if plan.is_completed:
            self.learner.record_pattern(plan)
            summary = f"Automation completed successfully in {elapsed_ms:.0f}ms."
            print(f"[AUTOMATION LOG] 🏁 Result: SUCCESS | Time: {elapsed_ms:.1f}ms\n")
            if ui and hasattr(ui, "write_log"):
                ui.write_log(f"AUTO: Completed successfully ({elapsed_ms:.0f}ms).")
            return " ".join(final_results) or summary
        else:
            reason = plan.failed_step.error_reason if plan.failed_step else "Stopped by user"
            summary = f"Automation incomplete: {reason}"
            print(f"[AUTOMATION LOG] 🏁 Result: INCOMPLETE ({reason}) | Time: {elapsed_ms:.1f}ms\n")
            if ui and hasattr(ui, "write_log"):
                ui.write_log(f"AUTO: Incomplete — {reason}")
            return summary

    def _dispatch_action(
        self,
        step: ActionStep,
        ui: Any,
        speak_fn: Optional[Callable[[str], None]],
        executor_map: Optional[Dict[str, Callable]]
    ) -> Any:
        act = step.action_type
        params = step.parameters

        if executor_map and act in executor_map:
            try:
                return executor_map[act](params)
            except Exception as e:
                return f"Execution error: {e}"

        # Standard ULTRON Action Dispatcher
        try:
            if act == "open_app":
                from actions.open_app import open_app
                return open_app(parameters=params, player=ui)
            elif act == "close_app":
                from actions.open_app import close_application
                app_name = params.get("app_name", "")
                return close_application(app_name)
            elif act == "spotify_control":
                from actions.spotify_control import spotify_control
                return spotify_control(parameters=params, player=ui, speak=speak_fn)
            elif act == "whatsapp_control":
                from actions.whatsapp_control import whatsapp_control
                return whatsapp_control(parameters=params, player=ui)
            elif act == "youtube_video":
                from actions.youtube_video import youtube_video
                return youtube_video(parameters=params, player=ui)
            elif act == "browser_control":
                from actions.browser_control import browser_control
                return browser_control(parameters=params, player=ui)
            else:
                return f"Unknown action type: {act}"
        except Exception as e:
            return f"Action exception: {e}"
