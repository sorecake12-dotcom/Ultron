import json
import os
import sys
from datetime import datetime
from threading import Lock
from pathlib import Path

try:
    from cryptography.fernet import Fernet
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = get_base_dir()
MEMORY_PATH      = BASE_DIR / "memory" / "long_term.json"
KEY_PATH         = BASE_DIR / "config" / "secret.key"
_lock            = Lock()
MAX_VALUE_LENGTH = 380
MEMORY_MAX_CHARS = 4000

VALID_CATEGORIES = {
    "name", "dob", "contact", "favorite_songs", "favorite_apps",
    "preferences", "notes", "custom", "identity", "projects",
    "relationships", "wishes"
}

SENSITIVE_CATEGORIES = {"dob", "contact"}
SENSITIVE_KEYS = {"dob", "date_of_birth", "birthday", "contact", "phone", "phone_number", "mobile", "email", "address"}


def _safe_print(text: str):
    try:
        print(text)
    except Exception:
        try:
            print(text.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


# ── Encryption Helper ────────────────────────────────────────────────────────

def _get_cipher():
    if not _CRYPTO_AVAILABLE:
        return None
    try:
        if not KEY_PATH.exists():
            KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            KEY_PATH.write_bytes(key)
        else:
            key = KEY_PATH.read_bytes()
        return Fernet(key)
    except Exception as e:
        _safe_print(f"[Memory Log] Encryption setup warning: {e}")
        return None


def _encrypt_val(val: str) -> str:
    cipher = _get_cipher()
    if not cipher:
        return val
    try:
        return cipher.encrypt(val.encode("utf-8")).decode("utf-8")
    except Exception:
        return val


def _decrypt_val(val: str) -> str:
    cipher = _get_cipher()
    if not cipher:
        return val
    try:
        return cipher.decrypt(val.encode("utf-8")).decode("utf-8")
    except Exception:
        return val


# ── Memory Core Functions ───────────────────────────────────────────────────

def _empty_memory() -> dict:
    return {
        "name":           {},
        "dob":            {},
        "contact":        {},
        "favorite_songs": {},
        "favorite_apps":  {},
        "preferences":    {},
        "notes":          {},
        "custom":         {},
        "identity":       {},
        "projects":       {},
        "relationships":  {},
        "wishes":         {},
    }


def load_memory() -> dict:
    if not MEMORY_PATH.exists():
        return _empty_memory()
    with _lock:
        try:
            data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                base = _empty_memory()
                for cat, items in data.items():
                    base[cat] = {}
                    if isinstance(items, dict):
                        for k, v in items.items():
                            if isinstance(v, dict):
                                val = v.get("value", "")
                                if v.get("encrypted", False) and "encrypted_val" in v:
                                    val = _decrypt_val(v["encrypted_val"])
                                base[cat][k] = {
                                    "value": val,
                                    "updated": v.get("updated", ""),
                                    "encrypted": v.get("encrypted", False)
                                }
                            else:
                                base[cat][k] = {"value": str(v), "updated": ""}
                return base
            return _empty_memory()
        except Exception as e:
            _safe_print(f"[Memory Log] Load error: {e}")
            return _empty_memory()


def _all_entries(memory: dict) -> list[tuple]:
    entries = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            if isinstance(entry, dict) and "value" in entry:
                entries.append((cat, key, entry))
    return entries


def _trim_to_limit(memory: dict) -> dict:
    if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
        return memory
    entries = _all_entries(memory)
    entries.sort(key=lambda t: t[2].get("updated", "0000-00-00"))
    for cat, key, _ in entries:
        if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
            break
        del memory[cat][key]
        _safe_print(f"[Memory Log] Trimmed {cat}/{key}")
    return memory


def _save_memory_file(memory: dict) -> None:
    if not isinstance(memory, dict):
        return
    memory = _trim_to_limit(memory)
    
    # Prepare serializable copy with sensitive entries encrypted at rest
    save_data = {}
    for cat, items in memory.items():
        save_data[cat] = {}
        if isinstance(items, dict):
            for k, entry in items.items():
                if isinstance(entry, dict):
                    val = entry.get("value", "")
                    is_sensitive = (cat in SENSITIVE_CATEGORIES) or (k in SENSITIVE_KEYS) or entry.get("encrypted", False)
                    if is_sensitive:
                        enc_v = _encrypt_val(val)
                        save_data[cat][k] = {
                            "encrypted_val": enc_v,
                            "updated": entry.get("updated", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                            "encrypted": True
                        }
                    else:
                        save_data[cat][k] = {
                            "value": val,
                            "updated": entry.get("updated", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                            "encrypted": False
                        }

    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        MEMORY_PATH.write_text(
            json.dumps(save_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _truncate_value(val: str) -> str:
    if isinstance(val, str) and len(val) > MAX_VALUE_LENGTH:
        return val[:MAX_VALUE_LENGTH].rstrip() + "…"
    return val


def save_memory(key: str = None, value: str = None, category: str = "notes", memory: dict = None) -> str | dict:
    """
    Save memory to persistent local storage.
    Encrypts sensitive fields (DOB, contact info, phone numbers) at rest.
    """
    if memory is not None and isinstance(memory, dict):
        _save_memory_file(memory)
        _safe_print(f"[Memory Log] Memory Save | Saved memory dict with categories: {list(memory.keys())}")
        return memory

    if not key or not value:
        return "Key and value must be provided to save memory."

    cat = category.lower().strip() if category else "notes"
    if cat not in VALID_CATEGORIES:
        cat = "notes"

    key_clean = key.lower().strip().replace(" ", "_")
    val_clean = str(value).strip()
    is_sensitive = (cat in SENSITIVE_CATEGORIES) or (key_clean in SENSITIVE_KEYS)

    mem = load_memory()
    if cat not in mem or not isinstance(mem[cat], dict):
        mem[cat] = {}

    mem[cat][key_clean] = {
        "value": val_clean,
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "encrypted": is_sensitive
    }

    _save_memory_file(mem)
    _safe_print(f"[Memory Log] Memory Save | Category: '{cat}' | Key: '{key_clean}' | Value: '{val_clean}' | Encrypted: {is_sensitive}")
    return f"Saved to memory: [{cat}] {key_clean} = {val_clean}"


def get_memory(category: str = None, key: str = None) -> dict | str | None:
    """
    Retrieve stored memory (automatically decrypted for active session).
    """
    mem = load_memory()
    _safe_print(f"[Memory Log] Memory Retrieve | Category: {category} | Key: {key}")
    
    if category and key:
        cat = category.lower().strip()
        k = key.lower().strip().replace(" ", "_")
        entry = mem.get(cat, {}).get(k)
        if isinstance(entry, dict):
            return entry.get("value")
        return entry

    if category:
        cat = category.lower().strip()
        return mem.get(cat, {})

    return mem


def search_memory(query: str) -> list[dict]:
    """
    Search stored memory for query matching category, key, or value.
    """
    if not query:
        return []

    q_lower = query.lower().strip()
    mem = load_memory()
    results = []

    for cat, items in mem.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            val = entry.get("value", "") if isinstance(entry, dict) else str(entry)
            if q_lower in cat.lower() or q_lower in key.lower() or q_lower in str(val).lower():
                results.append({
                    "category": cat,
                    "key": key,
                    "value": val,
                    "updated": entry.get("updated", "") if isinstance(entry, dict) else "",
                    "encrypted": entry.get("encrypted", False) if isinstance(entry, dict) else False
                })

    _safe_print(f"[Memory Log] Memory Search | Query: '{query}' | Matches: {len(results)}")
    return results


def update_memory(memory_update: dict) -> dict:
    """
    Update memory dict.
    """
    if not isinstance(memory_update, dict) or not memory_update:
        return load_memory()
    
    mem = load_memory()
    for cat, items in memory_update.items():
        if isinstance(items, dict):
            if cat not in mem:
                mem[cat] = {}
            for k, v in items.items():
                val = v.get("value") if isinstance(v, dict) else v
                if val:
                    save_memory(key=k, value=val, category=cat)

    _safe_print(f"[Memory Log] Memory Update | Categories updated: {list(memory_update.keys())}")
    return load_memory()


def delete_memory(key: str, category: str = None) -> bool:
    """
    Delete a specific memory key.
    """
    if not key:
        return False

    mem = load_memory()
    key_clean = key.lower().strip().replace(" ", "_")
    deleted = False

    if category:
        cat = category.lower().strip()
        if cat in mem and key_clean in mem[cat]:
            del mem[cat][key_clean]
            deleted = True
    else:
        # Search across all categories
        for cat in mem:
            if isinstance(mem[cat], dict) and key_clean in mem[cat]:
                del mem[cat][key_clean]
                deleted = True

    if deleted:
        _save_memory_file(mem)
        _safe_print(f"[Memory Log] Memory Delete | Category: {category or 'ALL'} | Key: '{key_clean}' | Status: SUCCESS")
        return True

    _safe_print(f"[Memory Log] Memory Delete | Key '{key_clean}' not found")
    return False


def clear_all_memory() -> bool:
    """
    Clears all persistent memory.
    """
    empty = _empty_memory()
    _save_memory_file(empty)
    _safe_print(f"[Memory Log] Memory Clear All | Status: Cleared all persistent memory")
    return True


def remember(key: str, value: str, category: str = "notes") -> str:
    return save_memory(key=key, value=value, category=category)


def forget(key: str, category: str = None) -> str:
    if not key:
        return "Please specify what information to forget."
    
    mem = load_memory()
    key_clean = key.lower().strip().replace(" ", "_")
    
    success = delete_memory(key_clean, category=category)
    if success:
        return f"Forgotten: {key}"

    # Search substring
    matches = search_memory(key)
    if matches:
        for m in matches:
            delete_memory(m["key"], category=m["category"])
        return f"Forgotten information matching: '{key}'"

    return f"I don't have any record of '{key}' in my memory."


forget_memory = forget


def format_memory_for_prompt(memory: dict | None) -> str:
    if not memory:
        return ""

    lines = []

    # 1. Identity & Name
    name_entry = memory.get("name", {}).get("name") or memory.get("identity", {}).get("name")
    if name_entry:
        val = name_entry.get("value") if isinstance(name_entry, dict) else name_entry
        lines.append(f"Name: {val}")

    # 2. Date of Birth
    dob_entry = memory.get("dob", {}).get("dob") or memory.get("identity", {}).get("dob") or memory.get("dob", {}).get("date_of_birth")
    if dob_entry:
        val = dob_entry.get("value") if isinstance(dob_entry, dict) else dob_entry
        lines.append(f"Date of Birth: {val}")

    # 3. Contact Info
    contact = memory.get("contact", {})
    if contact:
        for k, v in contact.items():
            val = v.get("value") if isinstance(v, dict) else v
            lines.append(f"Contact ({k.title()}): {val}")

    # 4. Favorite Songs
    fav_songs = memory.get("favorite_songs", {})
    if fav_songs:
        lines.append("\nFavorite Songs:")
        for k, v in fav_songs.items():
            val = v.get("value") if isinstance(v, dict) else v
            lines.append(f"  - {val}")

    # 5. Favorite Apps
    fav_apps = memory.get("favorite_apps", {})
    if fav_apps:
        lines.append("\nFavorite Apps:")
        for k, v in fav_apps.items():
            val = v.get("value") if isinstance(v, dict) else v
            lines.append(f"  - {val}")

    # 6. Preferences
    prefs = memory.get("preferences", {})
    if prefs:
        lines.append("\nPreferences:")
        for k, v in list(prefs.items())[:10]:
            val = v.get("value") if isinstance(v, dict) else v
            lines.append(f"  - {k.replace('_', ' ').title()}: {val}")

    # 7. Personal Notes & Custom
    notes = memory.get("notes", {})
    if notes:
        lines.append("\nPersonal Notes:")
        for k, v in list(notes.items())[:8]:
            val = v.get("value") if isinstance(v, dict) else v
            lines.append(f"  - {k.title()}: {val}")

    custom = memory.get("custom", {})
    if custom:
        lines.append("\nCustom Memories:")
        for k, v in list(custom.items())[:8]:
            val = v.get("value") if isinstance(v, dict) else v
            lines.append(f"  - {k.title()}: {val}")

    if not lines:
        return ""

    header = "[PERSISTENT USER MEMORY - use naturally when relevant, never recite like a list]\n"
    result = header + "\n".join(lines)
    if len(result) > 2500:
        result = result[:2497] + "…"

    return result + "\n"