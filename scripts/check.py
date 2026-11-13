"""Run regression tests and the held-out benchmark without shell-specific setup."""
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".deps"))
import pytest

if __name__ == "__main__":
    (ROOT / "data").mkdir(exist_ok=True)
    status = pytest.main([str(ROOT / "tests"), "-q", "--tb=short", "--basetemp=" + str(ROOT / "data" / ("tests-" + uuid.uuid4().hex)), "--junitxml=" + str(ROOT / "evaluation/results/tests.xml")])
    if status:
        raise SystemExit(status)
    raise SystemExit(subprocess.call([sys.executable, str(ROOT / "evaluation/run.py"), "--split", "holdout"]))
