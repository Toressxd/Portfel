"""Asks GitHub to record new reports now: commits and pushes .nagraj (a timestamp), which starts audio.yml.
Run by the report tasks on the owner's PC right after saving reports: py tools/nagraj.py"""
import os, subprocess, sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
open(os.path.join(HERE, ".nagraj"), "w", encoding="utf-8").write(datetime.now().isoformat(timespec="seconds") + "\n")
git = lambda *a: subprocess.run(["git", "-C", HERE, *a], capture_output=True, text=True)
git("add", ".nagraj")
c = git("-c", "user.email=Toressxd@users.noreply.github.com", "-c", "user.name=Toressxd", "commit", "-m", "Nagraj nowe raporty")
p = git("push", "-q")
print("nagrywanie uruchomione" if p.returncode == 0 else "push nie wyszedł: " + (p.stderr or c.stderr)[-300:])
sys.exit(p.returncode)
