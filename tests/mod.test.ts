import { test, expect } from 'claude-code/testing'
import { summarize, parseRecord, decide } from '../hooks/register'

// Stand-ins for what Claude Code answers beneath the plugins.
// The test engine has no $.session/$.env/$.fs, so readRecord() falls back to
// "no toggle file" here; decide() covers the record-driven branches directly.
function harness(on: any) {
  const pushed: string[] = []
  const injected: (readonly string[] | undefined)[] = []
  on('prompt.submit', ($: any, e: any) => { injected.push(e.context); return { text: e.text } })
  on('turn.start', () => ({ turnId: 't1' }))
  on('tool.call', { tool: 'PushNotification' }, ($: any, e: any) => { pushed.push(e.message); return { result: {} } })
  on('turn.complete', ($: any, e: any) => ({ text: e.answer }))
  return { pushed, injected }
}

async function turn($: any, origin: string, answer = '**Done:** 3 tests fixed\nmore', modelPushes = false) {
  await $.prompt.submit({ text: 'hi', wait: false, origin: { kind: origin } })
  await $.turn.start({ text: 'hi', turnId: 't1' })
  if (modelPushes) await $.tool.call({ tool: 'PushNotification', message: 'model pushed', status: 'proactive' })
  await $.turn.complete({ answer, durationMs: 1, isAborted: false, turnId: 't1', reason: 'answer' })
}

test('phone prompt, no toggle file: guidance injected and one push', async ($, on) => {
  const h = harness(on)
  await turn($, 'bridge')
  expect(String(h.injected[0]?.[0])).toContain('<mobile-mode>')
  expect(h.pushed).toEqual(['Done: 3 tests fixed'])
})

test('terminal prompt: nothing injected, no push', async ($, on) => {
  const h = harness(on)
  await turn($, 'composer')
  expect(h.injected[0]).toBeUndefined()
  expect(h.pushed).toEqual([])
})

test('decide(): explicit off, toggled on, terminal', async () => {
  const none = parseRecord(undefined)
  expect(decide(true, none)).toEqual({ mobileTurn: true, inject: true })
  expect(decide(false, none)).toEqual({ mobileTurn: false, inject: false })
  expect(decide(true, parseRecord('{"mode":"off"}'))).toEqual({ mobileTurn: false, inject: false })
  expect(decide(true, parseRecord('{"mode":"on"}'))).toEqual({ mobileTurn: true, inject: false })
  expect(decide(false, parseRecord('{"mode":"enforce"}'))).toEqual({ mobileTurn: true, inject: false })
  expect(parseRecord('{"mode":"off","push":"never"}').push).toBe('never')
})

test('no second push when the model already pushed', async ($, on) => {
  const h = harness(on)
  await turn($, 'bridge', 'x', true)
  expect(h.pushed).toEqual(['model pushed'])
})

test('helpers', async () => {
  expect(summarize('')).toBe('Claude finished — tap to see the reply.')
  expect(summarize('x'.repeat(300)).length).toBe(180)
  expect(parseRecord('{"mode":"on","push":"bogus","suggest":1}')).toEqual({ mode: 'on', push: 'always', suggest: false, exists: true })
  expect(parseRecord('not json').exists).toBe(false)
})
