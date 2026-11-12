#!/usr/bin/env python3
"""One-time setup for Lead Agent (macOS, Linux, Windows). Safe to run again.

    python3 setup.py          (Windows: py setup.py)

1. Creates .env with random passwords and asks 3 questions (skipped if .env exists).
2. Creates the Python environment (.venv) and installs the requirements.
3. Downloads the local AI model with Ollama.
4. Starts the WhatsApp gateway (WAHA) with Docker.
Use --yes to accept all defaults without questions.
"""
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV, EXAMPLE = ROOT / ".env", ROOT / ".env.example"
YES = "--yes" in sys.argv
WIN = os.name == "nt"


def say(msg):
    print(f"\n==> {msg}")


def ask(question, default=""):
    if YES:
        return default
    ans = input(f"{question}{f' [{default}]' if default else ''}: ").strip()
    return ans or default


def set_key(text, key, value):
    line = f"{key}={value}"
    return re.sub(rf"^{key}=.*$", line, text, flags=re.M) if re.search(rf"^{key}=", text, re.M) else text + line + "\n"


def make_env():
    if ENV.exists():
        say(".env already exists - keeping it (edit it by hand or in the dashboard's Settings).")
        return None
    say("Creating .env")
    text = EXAMPLE.read_text()
    password = secrets.token_urlsafe(12)
    arm = platform.machine().lower() in ("arm64", "aarch64")
    values = {
        "DASHBOARD_PASSWORD": password,
        "WAHA_API_KEY": secrets.token_urlsafe(24),
        "WEBHOOK_TOKEN": secrets.token_urlsafe(24),
        "WAHA_IMAGE": "devlikeapro/waha:gows-arm" if arm else "devlikeapro/waha:gows",
        "MY_BUSINESS": " ".join(ask("What do you sell? One or two sentences",
                                    "We build modern websites and WhatsApp automation for small local businesses.").split()),
        "BRAND_NAME": ask("Name shown in the dashboard", "Lead Agent"),
        "DEFAULT_COUNTRY_CODE": ask("Your country calling code, digits only (e.g. 44, 1, 91, 92, 971)", "").lstrip("+"),
    }
    for k, v in values.items():
        text = set_key(text, k, v)
    ENV.write_text(text)
    if not WIN:
        ENV.chmod(0o600)
    return password


def run(cmd, **kw):
    print("   $ " + " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, **kw).returncode == 0


def make_venv():
    py = ROOT / ".venv" / ("Scripts/python.exe" if WIN else "bin/python")
    if not py.exists():
        say("Creating the Python environment (.venv)")
        run([sys.executable, "-m", "venv", str(ROOT / ".venv")])
    say("Installing requirements")
    return run([str(py), "-m", "pip", "install", "-q", "-r", "requirements.txt"])


def env_value(key, default=""):
    m = re.search(rf"^{key}=(.*)$", ENV.read_text(), re.M)
    return m.group(1).strip() if m and m.group(1).strip() else default


def pull_model():
    model = env_value("OLLAMA_MODEL", "llama3.2:3b")
    if not shutil.which("ollama"):
        say(f"Ollama not found. Install it from https://ollama.com/download, open it, then run: ollama pull {model}")
        return False
    say(f"Downloading the AI model {model} (about 2 GB the first time)")
    return run(["ollama", "pull", model])


def start_waha():
    if not shutil.which("docker"):
        say("Docker not found. Install Docker Desktop (or Colima on a Mac), start it, then run: "
            "docker compose --env-file .env up -d")
        return False
    say("Starting the WhatsApp gateway (WAHA) in Docker")
    if run(["docker", "compose", "--env-file", ".env", "up", "-d"]):
        return True
    say("Docker did not start WAHA. Is Docker running? (Mac with Colima: run 'colima start' first.)")
    return False


def main():
    if sys.version_info < (3, 11):
        sys.exit("Python 3.11 or newer is needed. Get it from https://www.python.org/downloads/")
    password = make_env()
    ok = [make_venv(), pull_model(), start_waha()]
    start = r"powershell -ExecutionPolicy Bypass -File .\run.ps1" if WIN else "./run.sh"
    say("Done." if all(ok) else "Setup finished with warnings (see above). Fix them and run this again.")
    print(f"""
   Next:
   1. Start the dashboard:   {start}
   2. Open http://localhost:8000 and sign in""" + (f" with this password: {password}\n"
                                                   "      (saved in .env as DASHBOARD_PASSWORD; change it there any time)"
                                                   if password else " with DASHBOARD_PASSWORD from .env") + """
   3. More -> WhatsApp: enter your number and press "Get pairing code",
      then on your phone: WhatsApp -> Settings -> Linked devices -> Link a device ->
      "Link with phone number instead" and type the code.
""")


if __name__ == "__main__":
    main()
