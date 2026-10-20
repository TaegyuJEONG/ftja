"""The whole setup, in a throwaway copy of the folder (scripts/e2e_onboarding.py)."""
import importlib.util
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("e2e_onboarding", os.path.join(ROOT, "scripts", "e2e_onboarding.py"))
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)


def test_onboarding_end_to_end_in_a_fresh_folder():
    report = e2e.run(fresh_venv=False, keep=False, browser="off")  # the browser check runs in CI's onboarding-e2e job
    assert report["ok"], json.dumps(report, ensure_ascii=False, indent=2)
    assert not os.path.exists(report["folder"])
