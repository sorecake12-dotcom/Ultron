import sys
import os
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

def main():
    os.chdir(BASE_DIR)
    setup_flag = BASE_DIR / ".ultron_setup_complete"
    if not setup_flag.exists():
        print("[ULTRON Setup] First-time setup detected. Running setup script...")
        res = subprocess.run([sys.executable, str(BASE_DIR / "ULTRON_SETUP.py")])
        if res.returncode != 0:
            print(f"[ULTRON Setup] Setup failed with exit code {res.returncode}")
            sys.exit(res.returncode)

    print("[ULTRON] Launching main application...")
    res = subprocess.run([sys.executable, str(BASE_DIR / "main.py")])
    if res.returncode != 0:
        print(f"[ULTRON] Application exited with code {res.returncode}")
        sys.exit(res.returncode)

if __name__ == "__main__":
    main()
