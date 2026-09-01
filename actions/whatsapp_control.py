# whatsapp_control.py
import json
import re
import os
import sys
import time
import subprocess
from pathlib import Path

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False


def _paste_text(text: str) -> None:
    if not _PYAUTOGUI:
        return
    if _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.12)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.1)
    else:
        pyautogui.write(text, interval=0.03)


def _is_whatsapp_running() -> bool:
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = (proc.info["name"] or "").lower()
                if "whatsapp" in name:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _open_whatsapp() -> bool:
    """Launches or focuses WhatsApp Desktop application."""
    if not _PYAUTOGUI:
        return False
    try:
        subprocess.Popen(["cmd", "/c", "start", "whatsapp:"], shell=False, creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(2.0)
        return True
    except Exception as e:
        print(f"[WhatsApp] Failed to launch via protocol: {e}")
        try:
            pyautogui.press("win")
            time.sleep(0.4)
            _paste_text("WhatsApp")
            time.sleep(0.5)
            pyautogui.press("enter")
            time.sleep(2.5)
            return True
        except Exception as e2:
            print(f"[WhatsApp] Failed to launch via Start Menu: {e2}")
            return False


def _open_number_direct(phone_number: str) -> bool:
    """Opens WhatsApp chat directly for a phone number using whatsapp://send?phone=... protocol."""
    clean_num = re.sub(r"[^\d+]", "", phone_number)
    if not clean_num:
        return False
    try:
        uri = f"whatsapp://send?phone={clean_num}"
        subprocess.Popen(["cmd", "/c", "start", "", uri], shell=False, creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(2.5)
        return True
    except Exception as e:
        print(f"[WhatsApp] Direct URI open failed: {e}")
        return False


def _search_contact(contact: str) -> bool:
    """Searches for a contact or phone number in WhatsApp Desktop search bar."""
    if not _PYAUTOGUI:
        return False
    
    # Focus search bar (Ctrl + F)
    pyautogui.hotkey("ctrl", "f")
    time.sleep(0.5)
    
    # Clear existing search input
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.1)
    pyautogui.press("backspace")
    time.sleep(0.1)
    
    # Paste target contact name / number
    _paste_text(contact)
    time.sleep(1.2)
    
    # Select first search result
    pyautogui.press("down")
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.8)
    return True


def start_voice_call(contact: str) -> str:
    """Starts a WhatsApp voice call for a contact name or phone number."""
    start_t = time.perf_counter()
    if not contact:
        return "Please specify a contact or phone number to call."
    
    is_num = bool(re.search(r"\d{7,}", contact))
    if is_num:
        _open_number_direct(contact)
    else:
        _open_whatsapp()
        _search_contact(contact)
        
    time.sleep(1.0)
    
    # Trigger Voice Call shortcut in WhatsApp Desktop (Ctrl + Shift + C)
    pyautogui.hotkey("ctrl", "shift", "c")
    elapsed = (time.perf_counter() - start_t) * 1000.0
    
    print(f"[ULTRON LOG] Action: whatsapp_voice_call | Target: '{contact}' | Status: SUCCESS | Time: {elapsed:.1f}ms")
    return f"Starting WhatsApp voice call with {contact}."


def start_video_call(contact: str) -> str:
    """Starts a WhatsApp video call for a contact name or phone number."""
    start_t = time.perf_counter()
    if not contact:
        return "Please specify a contact or phone number for the video call."
    
    is_num = bool(re.search(r"\d{7,}", contact))
    if is_num:
        _open_number_direct(contact)
    else:
        _open_whatsapp()
        _search_contact(contact)
        
    time.sleep(1.0)
    
    # Trigger Video Call shortcut in WhatsApp Desktop (Ctrl + Shift + V)
    pyautogui.hotkey("ctrl", "shift", "v")
    elapsed = (time.perf_counter() - start_t) * 1000.0
    
    print(f"[ULTRON LOG] Action: whatsapp_video_call | Target: '{contact}' | Status: SUCCESS | Time: {elapsed:.1f}ms")
    return f"Starting WhatsApp video call with {contact}."


def end_call() -> str:
    """Immediately ends an active WhatsApp call."""
    start_t = time.perf_counter()
    if not _PYAUTOGUI:
        return "PyAutoGUI is not installed."
        
    try:
        # Send Ctrl+Shift+E and Alt+F4 to terminate active WhatsApp call window
        pyautogui.hotkey("ctrl", "shift", "e")
        time.sleep(0.2)
        pyautogui.hotkey("alt", "f4")
        elapsed = (time.perf_counter() - start_t) * 1000.0
        print(f"[ULTRON LOG] Action: whatsapp_end_call | Status: SUCCESS | Time: {elapsed:.1f}ms")
        return "WhatsApp call ended."
    except Exception as e:
        elapsed = (time.perf_counter() - start_t) * 1000.0
        print(f"[ULTRON LOG] Action: whatsapp_end_call | Status: FAILED ({e}) | Time: {elapsed:.1f}ms")
        return f"Failed to end call: {e}"


def send_whatsapp_msg(contact: str, message: str) -> str:
    """Sends a WhatsApp text message to a contact or phone number."""
    start_t = time.perf_counter()
    if not contact:
        return "Please specify a recipient."
    if not message:
        return "What message would you like to send?"
        
    is_num = bool(re.search(r"\d{7,}", contact))
    if is_num:
        _open_number_direct(contact)
    else:
        _open_whatsapp()
        _search_contact(contact)
        
    time.sleep(0.8)
    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    
    elapsed = (time.perf_counter() - start_t) * 1000.0
    print(f"[ULTRON LOG] Action: whatsapp_send_message | Target: '{contact}' | Message: '{message[:30]}' | Status: SUCCESS | Time: {elapsed:.1f}ms")
    return f"Sent message to {contact} on WhatsApp."


def whatsapp_control(parameters: dict, player=None) -> str:
    """Main execution router for WhatsApp automation actions."""
    params = parameters or {}
    action = params.get("action", "").lower().strip()
    contact = params.get("contact", "").strip() or params.get("receiver", "").strip()
    message = params.get("message", "").strip() or params.get("message_text", "").strip()
    call_type = params.get("call_type", "voice").lower().strip()
    
    if player:
        player.write_log(f"[WhatsApp] {action} -> {contact}")
        
    if action in ("voice_call", "call", "voice") or (action == "call" and call_type != "video"):
        return start_voice_call(contact)
    elif action in ("video_call", "video") or (action == "call" and call_type == "video"):
        return start_video_call(contact)
    elif action in ("end_call", "cut_call", "hang_up", "disconnect"):
        return end_call()
    elif action in ("send_message", "message", "type_message"):
        return send_whatsapp_msg(contact, message)
    elif action in ("search_contact", "search"):
        _open_whatsapp()
        _search_contact(contact)
        return f"Searched for {contact} in WhatsApp."
    else:
        return f"Unknown WhatsApp action: {action}"
