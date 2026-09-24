# Recovering the status line after another tool rewrites it

## The recipe

```sh
bash install.sh --dry-run   # what is wrong
bash install.sh             # fix it (backups first)
```

That's all. Open sessions pick it up on their next render; no restart needed.

`install.sh` resolves the plugin path automatically: it prefers the plugin cache
(highest-versioned entry, no hardcoded version), then falls back to the directory
containing the script (the source checkout). If the cache hasn't installed anything
yet, run it from the repo directory — any directory works, not just
`~/ClaudeWorkspace/cost-tracker`.

## When you need it

`joyia agent --setup` (and any tool like it) does two things:

1. It writes `statusLine = {"type":"command","command":"sh ~/.claude/joyia-statusline.sh","padding":0}`
   into `~/.claude/settings.json`.
2. It installs its own `~/.claude/joyia-statusline.sh` renderer.

After that the status line still shows a model and a `$` figure, so nothing *looks*
broken. Capture has stopped, though: `~/.claude/cost-ledger/` stops updating, and every
`today:` / `saved` figure reads `$0`. The quick tell is that the newest file in
`~/.claude/cost-ledger/` is older than your current session.

`install.sh` doesn't edit `joyia-statusline.sh`. It only changes the pointer to it.

## Why doing it by hand went wrong on 2026-09-23

It took two sessions and still ended on the wrong target:

- **The target.** `statusLine` has to call `bin/cost-ledger-capture.sh`, which records the
  cost and then renders. The manual fix pointed it at `statusline-render.sh`, which renders
  and nothing else, so the display came back but capture didn't.
- **The sandbox, not a locked file.** Every write to `settings.json` failed with
  `Operation not permitted`, even as its owner, with no flags, xattrs or open handles.
  The most likely cause is the Bash sandbox's write boundary, not a filesystem lock:
  after the user ran `chmod` in their own terminal the mode changed, but the session's
  writes still failed. `chmod`, `sudo` and
  Privileges don't help, and `chmod 644` needlessly widens a `600` file (it did). Run
  `install.sh` from a session whose sandbox allows writes to `~/.claude`, or type
  `! bash install.sh` in the prompt so it runs outside the tool sandbox.
- **Checking the wrong thing.** The `~/.claude/scripts` symlinks were correct the whole
  time. The old `wire-statusline.sh` checked only those and said "already wired". Since
  0.7.8 it checks `settings.json` first.
- **`/statusline` doesn't help.** Its setup agent builds a status line from your shell
  `PS1`, which would overwrite the cost-tracker line rather than restore it.
