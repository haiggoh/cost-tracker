"""Every `cost-tracker <sub>` the skill and the command teach must be a real subcommand.

The P0 contradiction this pins: SKILL.md taught `cost-tracker today` — a subcommand the CLI
has never had — so a reader following the skill got argparse's invalid-choice error instead
of a figure. The same round found the menu composing a positional period the report parser
does not accept. The guard extracts the subcommand set from the CLI's OWN --help (argparse
renders it, so there is one source of truth) and rejects any `cost-tracker <word>` mention
in the taught surfaces that is not in it. It also pins the period form: `report` takes NO
positional period, so the taught surfaces must use --today/--week/--month.
"""
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLI = ROOT / "bin" / "cost-tracker"
TAUGHT = [ROOT / "skills" / "cost-tracker" / "SKILL.md", ROOT / "commands" / "cost-today.md"]

MENTION_RE = re.compile(r"`cost-tracker ([a-z][a-z0-9-]*)[\s`]")


def _subcommands():
    """The subcommand set, from the CLI's own --help (argparse renders the choices)."""
    env = dict(os.environ)
    env.update({
        "COST_TRACKER_NO_MENU": "1",
        "COST_TRACKER_LEDGER_DIR": "/nonexistent-ledger",
        "COST_TRACKER_HISTORY": "/nonexistent-history",
        "COST_TRACKER_CONFIG_DIR": "/nonexistent-config",
    })
    r = subprocess.run([sys.executable, str(CLI), "--help"], capture_output=True, text=True,
                       env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    m = re.search(r"\{([a-z][a-z,]*)\}", r.stdout)
    assert m, r.stdout
    return set(m.group(1).split(","))


def test_every_taught_subcommand_exists():
    subs = _subcommands()
    assert {"report", "menu", "cap"} <= subs   # the probe is sane before it gates
    bad = []
    for f in TAUGHT:
        for m in MENTION_RE.finditer(f.read_text()):
            if m.group(1) not in subs:
                bad.append(f"{f.name}: cost-tracker {m.group(1)}")
    assert not bad, f"taught commands that do not exist: {bad}"


def test_period_flags_taught_not_positional():
    """The report parser takes NO positional period (P0 2026-11-13): the taught surfaces
    must use --today/--week/--month, never `report today`."""
    for f in TAUGHT:
        text = f.read_text()
        assert not re.search(r"cost-tracker report\s+(today|week|month)\b", text), (
            f"{f.name}: positional period — use --today/--week/--month")
