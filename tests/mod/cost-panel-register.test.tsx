// Behavior tests for hooks/cost-panel-register.tsx on the real engine
// (`claude plugin test .`). The engine injects $ and on; the fake-engine handlers
// below record every process.run / ui call, so these tests prove the DELIVERY
// wiring, not just the view model:
//  - /cost-panel opens the pane and reads the snapshot via the bundled read-only
//    inspector (shared_snapshot.py --json) — never an MCP server
//  - refresh goes through the bounded producer (statusline --refresh-shared),
//    single-flight: two rapid renders trigger at most ONE producer run
//  - the portable layout (scripts/ only) is probed, not just bin/
/* eslint-disable ts-patterns/no-magic-numbers, ts-patterns/require-regex-example --
   fixtures: amounts are the fixture data; /opened/ matches 'cost panel opened…', rejects '' */
import { expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

const CAP = 40
const AMOUNT = 31.37
const CLAUDE = 30.9
const CODEX = 0.47
const EXIT2 = 2
const EXIT1 = 1
const EXIT0 = 0

const SNAP = {
  schema: 'cost-tracker/shared-snapshot/1',
  snapshot_id: 'abc123',
  scope_id: 'joyia-shared',
  scope_label: 'JoyIA shared key',
  day_utc: '2026-11-21',
  estimated_usd: '31.370000',
  gateway_confirmed_usd: null,
  cap_usd: CAP,
  primary_amount_usd: AMOUNT,
  primary_basis: 'local estimate (coverage complete)',
  budget_state: 'unknown',
  headroom_usd: null,
  last_refreshed_at: '2026-11-21T10:00:00+00:00',
  coverage: { complete: true, missing_sources: [], unpriced_requests: 0,
              ambiguous_overlap: false, inferred_bindings: false, freshness: 'live-at-write' },
  by_host: { claude_code: CLAUDE, codex: CODEX, claude_desktop: null, other: null },
  diagnostics: [],
}

const result = (exitCode: number, stdout = '') => ({
  value: { exitCode, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
})

// The fake engine: answers the CLI and ui calls, records what the mod asked.
const engine = (on: On, o: { binExists?: boolean } = {}) => {
  const binExists = o.binExists ?? true
  const rec = {
    runs: [] as string[][],
    opens: [] as Record<string, unknown>[],
    panes: [] as { id: string; title: string }[],
  }
  on('process.run', (_, e) => {
    rec.runs.push(e.argv)
    const isBin = e.argv.some(a => a.includes('/bin/'))
    if (e.argv.some(a => a.endsWith('shared_snapshot.py'))) {
      if (isBin && !binExists) return result(EXIT2)
      return result(EXIT0, JSON.stringify({ scope: { id: 'joyia-shared', label: 'JoyIA shared key' }, snapshot: SNAP, stale: false }))
    }
    if (e.argv.some(a => a.endsWith('cost-tracker'))) {
      if (isBin && !binExists) return result(EXIT2)
      return result(EXIT0)
    }
    return result(EXIT1, 'unknown')
  })
  on('command.register', (_, e) => ({ value: { command: e.name } }))
  on('ui.open', (_, e) => {
    rec.opens.push({ ...e })
    if (!rec.panes.some(p => p.id === e.id)) rec.panes.push({ id: e.id, title: (e.title as string) ?? '' })
    return { value: { isPlaced: true } }
  })
  on('ui.close', (_, e) => {
    rec.panes = rec.panes.filter(p => p.id !== e.id)
    return { value: undefined }
  })
  on('ui.panes', () => ({ value: rec.panes.map(p => ({ ...p })) }) as never)
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  mock.clock(on)
  return rec
}

const runPanel = ($: { command: { run: (e: never) => Promise<{ text?: string }> } }) =>
  $.command.run({ command: 'cost-panel', args: '', origin: { kind: 'composer' } } as never)

const renderPane = ($: { ui: { mount: (t: never) => Promise<unknown> } }) =>
  $.ui.mount({
    plugin: 'cost-tracker',
    surface: 'terminal',
    component: 'Pane',
    requestId: 'cost-panel',
    props: { title: 'Cost', isFocused: true, bodyColumns: 60, placement: 'dock', scroll: { offset: 0, bodyRows: 30 }, view: {} },
  } as never)

test('/cost-panel opens the pane and reads the snapshot via the bundled inspector, never an MCP server', async ($, on) => {
  const rec = engine(on)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true } as never)
  await runPanel($ as never)
  expect(rec.opens.map(o => o.id)).toContain('cost-panel')
  const inspector = rec.runs.filter(a => a.some(x => x.endsWith('shared_snapshot.py')))
  expect(inspector.length).toBeGreaterThan(0)
  expect(inspector[0]).toContain('--json')
  // no MCP server anywhere in the exec record
  expect(rec.runs.some(a => a.some(x => x.includes('mcp')))).toBe(false)
})

test('refresh is single-flight and goes through the bounded producer', async ($, on) => {
  const rec = engine(on)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true } as never)
  await runPanel($ as never)
  const before = rec.runs.filter(a => a.some(x => x.endsWith('cost-tracker'))).length
  const first = (await renderPane($ as never)) as { unmount: () => Promise<void> }
  await first.unmount()
  await renderPane($ as never)
  const after = rec.runs.filter(a => a.some(x => x.endsWith('cost-tracker'))).length
  expect(after - before).toBeLessThanOrEqual(1)
  const producer = rec.runs.filter(a => a.some(x => x.endsWith('cost-tracker')))
  expect(producer.every(a => a.includes('--refresh-shared'))).toBe(true)
})

test('portable layout: scripts/ is probed when bin/ is absent', async ($, on) => {
  const rec = engine(on, { binExists: false })
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true } as never)
  await runPanel($ as never)
  const inspector = rec.runs.filter(a => a.some(x => x.endsWith('shared_snapshot.py')))
  expect(inspector.some(a => a.some(x => x.includes('/scripts/')))).toBe(true)
})
