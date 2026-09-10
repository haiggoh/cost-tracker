import importlib.util
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load():
    """bin/cost-tracker has no .py extension (it is a CLI on PATH), so import it
    by path rather than renaming the executable users type."""
    spec = importlib.util.spec_from_loader(
        "cost_tracker",
        importlib.machinery.SourceFileLoader("cost_tracker", str(ROOT / "bin" / "cost-tracker")),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_ct(tmp_path, today="2026-09-03", cap=None, savings_cmd="/bin/false",
            make_dirs=True):
    """Fresh module bound to a throwaway ledger dir. The module reads its paths at
    IMPORT time, so every test that changes them must re-import — otherwise the
    first test's tmp dir leaks into the rest and a passing suite proves nothing."""
    ledger = tmp_path / "cost-ledger"
    if make_dirs:
        ledger.mkdir(parents=True, exist_ok=True)
    os.environ["COST_TRACKER_LEDGER_DIR"] = str(ledger)
    os.environ["COST_TRACKER_HISTORY"] = str(tmp_path / "history.log")
    os.environ["COST_TRACKER_TODAY"] = today
    os.environ["COST_TRACKER_SAVINGS_CMD"] = savings_cmd
    # Point the learned-cap config at the throwaway dir too. Without this the module
    # falls back to the REAL ~/.claude/cost-tracker/cap.json, so a test asserting "no
    # cap is invented" inherits whatever cap this machine has learned and fails on a
    # developer box while passing in CI (measured: $40 leaked in, 1 failure on a
    # pristine tree). Clearing the two override env vars is not sufficient on its own.
    os.environ["COST_TRACKER_CONFIG_DIR"] = str(tmp_path / "config")
    os.environ["COST_TRACKER_PROJECTS_DIR"] = str(tmp_path / "projects")
    # Same trap as the cap, one axis over: an inherited markup would rescale every
    # denominator in the suite. Tests that WANT one set it after loading.
    os.environ.pop("COST_TRACKER_MARKUP", None)
    if cap is None:
        os.environ.pop("COST_TRACKER_CAP_USD", None)
        os.environ.pop("BUDGET_TALLY_CAP_USD", None)
    else:
        os.environ["COST_TRACKER_CAP_USD"] = str(cap)
    sys.modules.pop("cost_tracker", None)
    return _load(), ledger
