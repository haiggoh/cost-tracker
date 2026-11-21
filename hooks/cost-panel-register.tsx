// cost-panel mod — a dedicated pane showing the shared budget snapshot, rendered
// from the SAME cached snapshot the statusline/CLI/warnings consume (0.21.3+).
// No MCP server: the mod execs the bundled read-only inspector directly.
// Works wherever the engine seats plugin panes: Claude Code terminal, the Claude
// desktop app's Code tab, and ChatGPT desktop's Codex mode where mods run.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'
import { glanceRow, panelRows, type SnapshotReply } from './cost-panel-view'

const PANE = 'cost-panel'
const REFRESH_MS = 30_000 // only while the pane is mounted; one in-flight max
const PANE_ROWS = 12
const LAYOUT_DIRS = ['bin', 'scripts'] as const // marketplace vs portable layout

const snap = atom({ plugin: 'cost-tracker', key: 'cost-panel-snapshot' } as const, null as SnapshotReply | null)
const refreshing = atom({ plugin: 'cost-tracker', key: 'cost-panel-refreshing' } as const, false)
const lastRefresh = atom({ plugin: 'cost-tracker', key: 'cost-panel-last-refresh' } as const, 0)

// The inspector lives in bin/ (marketplace layout) or scripts/ (portable layout).
const cli = async ($: EngineInterface, args: string[]) => {
  const runs = await Promise.all(LAYOUT_DIRS.map(dir =>
    $.process
      .run(['python3', `${$.plugin.root}/${dir}/shared_snapshot.py`, ...args])
      .catch(() => null)))
  return runs.find(r => r !== null && r.exitCode === 0) ?? null
}

const load = async ($: EngineInterface): Promise<SnapshotReply> => {
  const ran = await cli($, ['--json'])
  if (!ran || ran.exitCode !== 0) {
    return { snapshot: null, error: 'no shared scope configured or no cached snapshot yet — run cost-tracker report --combined' }
  }
  try {
    return JSON.parse(ran.stdout) as SnapshotReply
  } catch {
    return { snapshot: null, error: 'snapshot cache unreadable (INCOMPLETE)' }
  }
}

const refresh = async ($: EngineInterface) => {
  if (read($, refreshing)) return // one in-flight refresh max
  update($, refreshing, () => true)
  try {
    // Bounded producer path (no inference): rebuild the cached snapshot for today.
    // Probe both layouts like cli() does — in the portable layout only scripts/ exists.
    const runs = await Promise.all(LAYOUT_DIRS.map(dir =>
      $.process
        .run(['python3', `${$.plugin.root}/${dir}/cost-tracker`, 'statusline', '--refresh-shared'])
        .catch(() => null)))
    void runs
    update($, lastRefresh, () => Date.now())
    update($, snap, () => load($) as never)
  } finally {
    update($, refreshing, () => false)
  }
}

export const register: Register = (on) => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'cost-panel',
      description: 'Open/close the shared budget cost panel (same snapshot as the statusline)',
    })
    // Seat-aware launch (user 2026-11-22): the engine's session.start event carries the
    // surface. Desktop apps (Claude desktop Code tab, ChatGPT desktop Codex mode where
    // mods run) get the pane ON by default; the terminal TUI keeps it optional —
    // /cost-panel opens it on demand there.
    if (e.surface === 'desktop') {
      update($, snap, () => load($) as never)
      await $.ui.open({ id: PANE, title: 'Cost', rows: PANE_ROWS })
    }
    return next(e)
  })

  on('command.run', { command: 'cost-panel' }, async ($, e) => {
    update($, snap, () => load($) as never)
    const panes = await $.ui.panes()
    if (panes.some(p => p.id === PANE)) {
      await $.ui.close({ id: PANE })
      return 'cost panel closed'
    }
    const r = await $.ui.open({ id: PANE, title: 'Cost', rows: PANE_ROWS })
    return 'reason' in r && r.reason
      ? `No surface draws the panel here yet (${r.reason})`
      : 'cost panel opened beside the conversation'
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Button, Text } = await $.ui.resolve(e)
    const reply = read($, snap) ?? { snapshot: null, error: 'loading…' }
    // Refresh at most every REFRESH_MS and only while mounted; never a standing poller.
    if (!read($, refreshing) && Date.now() - read($, lastRefresh) > REFRESH_MS) {
      void refresh($)
    }
    // A desktop widget is a glance: clicks may not reach it, so no refresh button.
    if (e.props.placement === 'widget') {
      return <Text dimColor>{glanceRow(reply)}</Text>
    }
    const rows = panelRows(reply)
    return (
      <Box flexDirection="column">
        {rows.map((r, i) => (
          <Text key={i} bold={r.bold} dimColor={r.dim}>
            {r.text}
          </Text>
        ))}
        <Button
          onPress={() => {
            void refresh($)
          }}
        >
          refresh snapshot (bounded, no inference)
        </Button>
      </Box>
    )
  })
}
