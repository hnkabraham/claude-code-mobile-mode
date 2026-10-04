import { test, expect, mock } from 'claude-code/testing'
import { summarize, parseRecord, decide, isQuiet, parseActions, describeCall, parseSuggestions, endsWithQuestion } from '../hooks/register'

// Quiet hours off so these tests don't depend on the time of day they run.
const OPTS = { options: { quietHours: 'off', longTurnMinutes: '10', suggestions: 'off' } }
const SUGGEST = { options: { quietHours: 'off', longTurnMinutes: '10', suggestions: 'phone' } }

// Stand-ins for what Claude Code answers beneath the plugins. The test engine
// has no $.session/$.env/$.fs, so the toggle record reads as "no file" here;
// decide() covers the record-driven branches directly.
function harness(on: any, clock?: ReturnType<typeof mock.clock>) {
  clock ?? mock.clock(on)
  on('classic.PermissionRequest', () => ({}))
  on('ui.render', { component: 'AssistantMessage' }, ($2: any, e: any) => {
    const { Text } = $2.ui.resolve(e)
    return h(Text, null, e.props.text)
  })
  const pushed: string[] = []
  const injected: (readonly string[] | undefined)[] = []
  const submitted: { text: string; origin: string }[] = []
  on('prompt.submit', ($: any, e: any) => {
    injected.push(e.context)
    submitted.push({ text: e.text, origin: e.origin.kind })
    return { text: e.text }
  })
  on('turn.start', () => ({ turnId: 't1' }))
  on('tool.call', { tool: 'PushNotification' }, ($: any, e: any) => { pushed.push(e.message); return { result: {} } })
  on('turn.complete', ($: any, e: any) => ({ text: e.answer }))
  return { pushed, injected, submitted }
}

async function submit($: any, origin: string) {
  await $.prompt.submit({ text: 'hi', wait: false, origin: { kind: origin } })
  await $.turn.start({ text: 'hi', turnId: 't1' })
}
async function complete($: any, answer = '**Done:** 3 tests fixed\nmore', reason = 'answer') {
  await $.turn.complete({ answer, durationMs: 1, isAborted: false, turnId: 't1', reason })
}

test('phone prompt: guidance injected and one push', OPTS, async ($, on) => {
  const h = harness(on)
  await submit($, 'bridge'); await complete($)
  expect(String(h.injected[0]?.[0])).toContain('<mobile-mode>')
  expect(h.pushed).toEqual(['Done: 3 tests fixed'])
})

test('terminal prompt: nothing injected, no push', OPTS, async ($, on) => {
  const h = harness(on)
  await submit($, 'composer'); await complete($)
  expect(h.injected[0]).toBeUndefined()
  expect(h.pushed).toEqual([])
})

test('no second push when the model already pushed', OPTS, async ($, on) => {
  const h = harness(on)
  await submit($, 'bridge')
  await $.tool.call({ tool: 'PushNotification', message: 'model pushed', status: 'proactive' })
  await complete($, 'x')
  expect(h.pushed).toEqual(['model pushed'])
})

test('approval dialog during a phone turn pushes right away', OPTS, async ($, on) => {
  const h = harness(on)
  await submit($, 'bridge')
  await $.classic.PermissionRequest({ tool_name: 'Bash', tool_input: { command: 'rm -rf build/' } } as never)
  expect(h.pushed).toEqual(['Needs your OK: Bash — rm -rf build/'])
})

test('background-task turn pushes after recent phone use, not otherwise', OPTS, async ($, on) => {
  const h = harness(on)
  await submit($, 'task-notification'); await complete($, 'job done')
  expect(h.pushed).toEqual([]) // phone never used this session
  await submit($, 'bridge'); await complete($, 'started the job')
  await submit($, 'task-notification'); await complete($, 'job done')
  expect(h.pushed).toEqual(['started the job', 'job done'])
})

test('a long phone turn sends one "still working" push', OPTS, async ($, on) => {
  const clock = mock.clock(on)
  const h = harness(on, clock)
  await submit($, 'bridge')
  await clock.advance(11 * 60_000)
  expect(h.pushed.length).toBe(1)
  expect(h.pushed[0]).toContain('Still working (10 min)')
  await complete($, 'finished')
  expect(h.pushed[1]).toBe('finished')
})

test('quick-action buttons draw under the latest reply on mobile and submit as a phone turn', OPTS, async ($, on) => {
  const h = harness(on)
  await submit($, 'bridge'); await complete($, 'All set.')
  const ui = await $.ui.mount({ plugin: 'mobile-mode', surface: 'mobile', component: 'AssistantMessage', props: { text: 'All set.', isFirstOfReply: true } })
  expect(await ui.find({ type: 'Button', text: 'Continue' })).toBeDefined()
  await ui.press({ key: 'qa-0' })
  expect(h.submitted.at(-1)).toEqual({ text: 'Continue.', origin: 'plugin' })
  const term = await $.ui.mount({ plugin: 'mobile-mode', surface: 'terminal', component: 'AssistantMessage', props: { text: 'All set.', isFirstOfReply: true } })
  expect(await term.find({ type: 'Button' })).toBeUndefined()
})

test('Haiku suggestions replace the fixed buttons under a phone reply', SUGGEST, async ($, on) => {
  const asked: any[] = []
  on('model.complete', ($2: any, e: any) => {
    asked.push(e)
    return { value: { isAnswered: true, text: '[{"label":"Run tests","prompt":"Run the test suite"},{"label":"Open PR","prompt":"Open a PR for this"}]', usage: {} } } as never
  })
  const h = harness(on)
  await submit($, 'bridge'); await complete($, 'Refactor done.')
  expect(asked[0]?.model).toBe('haiku')
  expect(String(asked[0]?.prompt)).toContain('Refactor done.')
  const ui = await $.ui.mount({ plugin: 'mobile-mode', surface: 'mobile', component: 'AssistantMessage', props: { text: 'Refactor done.', isFirstOfReply: true } })
  expect(await ui.find({ type: 'Button', text: 'Run tests' })).toBeDefined()
  expect(await ui.find({ type: 'Button', text: 'Continue' })).toBeUndefined()
  await ui.press({ key: 'qa-1' })
  expect(h.submitted.at(-1)).toEqual({ text: 'Open a PR for this', origin: 'plugin' })
  expect(h.pushed).toEqual(['Refactor done.'])
})

test('a failed suggestion call falls back to the fixed buttons; terminal turns ask nothing', SUGGEST, async ($, on) => {
  let calls = 0
  on('model.complete', () => { calls++; return { value: { isAnswered: false, reason: 'api-error', status: 529, error: 'overloaded', usage: {} } } as never })
  harness(on)
  await submit($, 'composer'); await complete($, 'desk answer')
  expect(calls).toBe(0)
  await submit($, 'bridge'); await complete($, 'All set.')
  expect(calls).toBe(1)
  const ui = await $.ui.mount({ plugin: 'mobile-mode', surface: 'mobile', component: 'AssistantMessage', props: { text: 'All set.', isFirstOfReply: true } })
  expect(await ui.find({ type: 'Button', text: 'Continue' })).toBeDefined()
})

test('no suggestions or buttons when Claude ends with a question', SUGGEST, async ($, on) => {
  let calls = 0
  on('model.complete', () => { calls++; return { value: { isAnswered: true, text: '[{"label":"X","prompt":"x"}]', usage: {} } } as never })
  on('tool.call', { tool: 'AskUserQuestion' }, () => ({ result: {} }))
  harness(on)
  await submit($, 'bridge'); await complete($, 'Built it. Want me to push it to main?')
  const ui = await $.ui.mount({ plugin: 'mobile-mode', surface: 'mobile', component: 'AssistantMessage', props: { text: 'Built it. Want me to push it to main?', isFirstOfReply: true } })
  expect(await ui.find({ type: 'Button' })).toBeUndefined()
  await submit($, 'bridge')
  await $.tool.call({ tool: 'AskUserQuestion', questions: [] } as never)
  await complete($, 'Pick one above.')
  const ui2 = await $.ui.mount({ plugin: 'mobile-mode', surface: 'mobile', component: 'AssistantMessage', props: { text: 'Pick one above.', isFirstOfReply: true } })
  expect(await ui2.find({ type: 'Button' })).toBeUndefined()
  expect(calls).toBe(0)
})

test('old buttons disappear when the next phone turn starts', OPTS, async ($, on) => {
  harness(on)
  await submit($, 'bridge'); await complete($, 'All set.')
  await submit($, 'bridge')
  const ui = await $.ui.mount({ plugin: 'mobile-mode', surface: 'mobile', component: 'AssistantMessage', props: { text: 'All set.', isFirstOfReply: true } })
  expect(await ui.find({ type: 'Button' })).toBeUndefined()
})

test('decide()', async () => {
  const none = parseRecord(undefined)
  expect(decide('bridge', none)).toEqual({ mobileTurn: true, inject: true })
  expect(decide('composer', none)).toEqual({ mobileTurn: false, inject: false })
  expect(decide('bridge', parseRecord('{"mode":"off"}'))).toEqual({ mobileTurn: false, inject: false })
  expect(decide('bridge', parseRecord('{"mode":"on"}'))).toEqual({ mobileTurn: true, inject: false })
  expect(decide('task-notification', none, true)).toEqual({ mobileTurn: true, inject: false })
  expect(decide('task-notification', none, false)).toEqual({ mobileTurn: false, inject: false })
  expect(decide('quick-action', none)).toEqual({ mobileTurn: true, inject: true })
})

test('helpers', async () => {
  expect(isQuiet('23-7', 23)).toBe(true)
  expect(isQuiet('23-7', 3)).toBe(true)
  expect(isQuiet('23-7', 7)).toBe(false)
  expect(isQuiet('9-17', 12)).toBe(true)
  expect(isQuiet('off', 3)).toBe(false)
  expect(parseActions('A=do a|bad|B=do b')).toEqual([{ label: 'A', prompt: 'do a' }, { label: 'B', prompt: 'do b' }])
  expect(describeCall('Edit', { file_path: '/x/y.ts' })).toBe('Needs your OK: Edit — /x/y.ts')
  expect(parseSuggestions('Sure! [{"label":"A","prompt":"do a"},{"label":"","prompt":"x"},{"nope":1}]')).toEqual([{ label: 'A', prompt: 'do a' }])
  expect(parseSuggestions('no json here')).toEqual([])
  expect(endsWithQuestion('Done.\n\n**Want me to push it?**')).toBe(true)
  expect(endsWithQuestion('Is it fixed? Yes — all 9 tests pass.')).toBe(false)
  expect(endsWithQuestion('Shall I continue? (yes/no)')).toBe(true)
  expect(endsWithQuestion('Done (see above).')).toBe(false)
  expect(summarize('')).toBe('Claude finished — tap to see the reply.')
  expect(summarize('x'.repeat(300)).length).toBe(180)
})
