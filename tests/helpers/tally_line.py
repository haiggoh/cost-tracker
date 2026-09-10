"""Render budget-tally's SessionStart line under the current environment.

A helper FILE rather than a heredoc inside the shell test: budget-tally.py has no .py
extension problem, but nesting a python heredoc inside the bash heredoc that writes the
test is how a `PY` terminator silently closes the wrong block.
"""
import importlib.util
import os

spec = importlib.util.spec_from_file_location("bt", os.environ["TALLY"])
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

total, pct, remaining, unknown, priced, cur, ledger_total, recon_total = bt.compute()
print(bt.format_line(total, pct, remaining, unknown, "test scope",
                     cur, ledger_total, recon_total))
