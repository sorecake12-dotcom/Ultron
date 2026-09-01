#spotify_control.py
import json
import re
import sys
import time
import subprocess
import webbrowser
from pathlib import Path
from urllib.parse import quote_plus

try:
    import pyautogui
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

from actions.open_app import open_app

def play_spotify(query: str = "", player=None) -> str:
    """
    Plays a song/artist/album/playlist on Spotify.
    Opens desktop Spotify app or web fallback and searches query.
    """
    clean_query = str(query or "").strip()
    print(f"[Spotify] ▶️ Play requested: '{clean_query}'")
    if player:
        player.write_log(f"[Spotify] Play: {clean_query}")

    try:
        if not clean_query:
            open_app(parameters={"app_name": "spotify"}, player=player)
            return "Opened Spotify."

        encoded_query = quote_plus(clean_query)

        # 1. Try Spotify URI handler to launch desktop app directly
        uri = f"spotify:search:{encoded_query}"
        try:
            webbrowser.open(uri)
        except Exception:
            webbrowser.open(f"https://open.spotify.com/search/{encoded_query}")

        # Short delay and enter key to trigger playback if desktop app focused
        time.sleep(1.5)
        if _PYAUTOGUI:
            pyautogui.press("enter")

        return f"Playing {clean_query} on Spotify."
    except Exception as e:
        print(f"[Spotify] Error playing track: {e}")
        return f"Could not play {clean_query} on Spotify: {e}"

def pause_spotify(player=None) -> str:
    """Pauses Spotify playback."""
    print("[Spotify] ⏸️ Pause requested")
    if _PYAUTOGUI:
        pyautogui.press("playpause")
    if player:
        player.write_log("[Spotify] Paused playback")
    return "Spotify paused."

def resume_spotify(player=None) -> str:
    """Resumes Spotify playback."""
    print("[Spotify] ▶️ Resume requested")
    if _PYAUTOGUI:
        pyautogui.press("playpause")
    if player:
        player.write_log("[Spotify] Resumed playback")
    return "Spotify resumed."

def stop_spotify(player=None) -> str:
    """Stops Spotify playback without closing ULTRON."""
    print("[Spotify] ⏹️ Stop requested")
    if _PYAUTOGUI:
        pyautogui.press("stop")
        time.sleep(0.1)
        pyautogui.press("playpause")
    if player:
        player.write_log("[Spotify] Stopped music playback")
    return "Spotify playback stopped."

def next_spotify_song(player=None) -> str:
    """Skips to next song on Spotify."""
    print("[Spotify] ⏭️ Next track requested")
    if _PYAUTOGUI:
        pyautogui.press("nexttrack")
    if player:
        player.write_log("[Spotify] Next track")
    return "Next song on Spotify."

def prev_spotify_song(player=None) -> str:
    """Goes to previous song on Spotify."""
    print("[Spotify] ⏮️ Previous track requested")
    if _PYAUTOGUI:
        pyautogui.press("prevtrack")
    if player:
        player.write_log("[Spotify] Previous track")
    return "Previous song on Spotify."

_ACTION_MAP = {
    "play":     play_spotify,
    "pause":    pause_spotify,
    "resume":   resume_spotify,
    "stop":     stop_spotify,
    "next":     next_spotify_song,
    "previous": prev_spotify_song,
    "prev":     prev_spotify_song,
}

def spotify_control(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    params = parameters or {}
    action = str(params.get("action", "play")).lower().strip()
    query  = str(params.get("query", "")).strip()

    if player:
        player.write_log(f"[Spotify] Action: {action}")
    print(f"[Spotify] 🎵 Action: {action}  Query: {query}")

    if action == "play":
        res = play_spotify(query, player)
    elif action in ("pause", "pause_music"):
        res = pause_spotify(player)
    elif action in ("resume", "resume_music"):
        res = resume_spotify(player)
    elif action in ("stop", "stop_music"):
        res = stop_spotify(player)
    elif action in ("next", "next_song", "next_track"):
        res = next_spotify_song(player)
    elif action in ("prev", "previous", "prev_song", "previous_song", "previous_track"):
        res = prev_spotify_song(player)
    else:
        res = play_spotify(query or action, player)

    if speak and res:
        try:
            speak(res)
        except Exception:
            pass

    return res
