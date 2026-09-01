"""
core/diagnostics.py — ULTRON Performance Instrumentation & Diagnostics

Measures end-to-end command latency, STT/AI/TTS breakdown, local fast-path vs AI latency,
active intervals, and system execution health.
"""

import time
import json
from collections import deque
from threading import Lock

class UltronDiagnostics:
    def __init__(self, max_samples: int = 100):
        self._lock = Lock()
        self._samples = deque(maxlen=max_samples)
        self._active_timers_count = 0
        self._fast_path_hits = 0
        self._ai_hits = 0

    def record_command(
        self,
        command_text: str,
        total_ms: float,
        stt_ms: float = 0.0,
        intent_ms: float = 0.0,
        ai_ms: float = 0.0,
        action_ms: float = 0.0,
        tts_ms: float = 0.0,
        is_fast_path: bool = False
    ):
        with self._lock:
            if is_fast_path:
                self._fast_path_hits += 1
            else:
                self._ai_hits += 1

            self._samples.append({
                "timestamp": time.strftime("%H:%M:%S"),
                "command": command_text[:40],
                "total_ms": round(total_ms, 2),
                "stt_ms": round(stt_ms, 2),
                "intent_ms": round(intent_ms, 2),
                "ai_ms": round(ai_ms, 2),
                "action_ms": round(action_ms, 2),
                "tts_ms": round(tts_ms, 2),
                "is_fast_path": is_fast_path
            })

    def get_summary(self) -> dict:
        with self._lock:
            if not self._samples:
                return {
                    "total_commands": 0,
                    "avg_latency_ms": 0.0,
                    "worst_latency_ms": 0.0,
                    "fast_path_hits": self._fast_path_hits,
                    "ai_hits": self._ai_hits,
                    "recent_samples": []
                }

            totals = [s["total_ms"] for s in self._samples]
            avg_ms = sum(totals) / len(totals)
            worst_ms = max(totals)

            return {
                "total_commands": len(self._samples),
                "avg_latency_ms": round(avg_ms, 2),
                "worst_latency_ms": round(worst_ms, 2),
                "fast_path_hits": self._fast_path_hits,
                "ai_hits": self._ai_hits,
                "recent_samples": list(self._samples)[-10:]
            }

    def generate_report_text(self) -> str:
        summary = self.get_summary()
        if summary["total_commands"] == 0:
            return "ULTRON Performance Diagnostic Report: No commands recorded yet."

        lines = [
            "===================================================",
            "       ULTRON OS — PERFORMANCE DIAGNOSTIC REPORT",
            "===================================================",
            f"Total Commands Processed: {summary['total_commands']}",
            f"Average Command Latency:  {summary['avg_latency_ms']} ms",
            f"Worst Command Latency:    {summary['worst_latency_ms']} ms",
            f"Fast-Path Hits (<20ms):   {summary['fast_path_hits']}",
            f"AI Model Roundtrips:      {summary['ai_hits']}",
            "---------------------------------------------------",
            "Recent Command Breakdowns:"
        ]

        for s in summary["recent_samples"]:
            mode = "FAST-PATH" if s["is_fast_path"] else "AI-MODEL"
            lines.append(
                f"  [{s['timestamp']}] '{s['command']}' ({mode}) -> Total: {s['total_ms']}ms | AI: {s['ai_ms']}ms | Action: {s['action_ms']}ms"
            )

        lines.append("===================================================")
        return "\n".join(lines)


# Global singleton instance
diagnostics = UltronDiagnostics()
