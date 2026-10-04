import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

// Function-hook half of mobile-mode. Claude Code builds with the function-hook
// (mod) engine stamp every submitted prompt with its origin, and a Remote
// Control prompt arrives as `origin.kind === 'bridge'`. That lets this module do
// what the shell hooks alone can't:
//
//   * Detection (0.6.0). A phone prompt turns mobile mode on for that turn by
//     itself; terminal prompts, cron jobs and chat bridges are never 'bridge'.
//   * The push (0.6.0). Sent by the plugin when the turn ends (cadence `always`)
//     unless the model already pushed. The tool suppresses itself at the desk.
//   * Approval pushes (0.7.0). When a permission dialog opens during a phone
//     turn, push "Needs your OK: …" right away instead of at the end.
//   * Quick actions (0.7.0). Tappable buttons under the latest reply in the
//     Claude mobile app; a press submits that prompt as the user.
//   * Background pushes (0.7.0). A turn started by a finished background task
//     while the phone was recently in use pushes when it ends.
//   * Next-prompt suggestions (0.8.0). After a phone turn a small model (Haiku
//     by default, through the session's own credentials) reads the last
//     exchange and proposes 2-3 next prompts; they replace the fixed quick
//     actions under the reply until the next turn. Happy (slopus/happy) got
//     options by having the main model emit an <options> block; a separate
//     small call keeps the reply clean and costs the main turn nothing.
//   * Long turns + quiet hours (0.7.0). One "still working" push when a phone
//     turn runs long; no routine pushes during quiet hours (approval requests
//     and failed turns still push).
//
// The per-session record (~/.claude/mobile-mode/sessions/<id>.json, written by
// /mobile-mode:toggle) still rules: an explicit `off` keeps this module out of
// the session; the push cadence and suggest preference apply. When the toggle
// is on, the shell hook injects the guidance, so this module doesn't double it.
// On builds without function hooks this file isn't loaded and the shell hooks
// behave exactly as before.

type Push = 'always' | 'needed' | 'never'
type Rec = { mode: 'on' | 'enforce' | 'off'; push: Push; suggest: boolean; exists: boolean }
type Options = { quietHours?: string; quickActions?: string; longTurnMinutes?: string; suggestions?: string; suggestModel?: string }
type Action = { label: string; prompt: string }

const MAX = 180
const SESSION_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$/
const PHONE_RECENT_MS = 3 * 3600_000 // a background turn pushes if the phone was used this recently
const last = atom({ plugin: 'mobile-mode', key: 'last' } as const, null)

const PUSH_ITEMS: Record<Push, string> = {
  always: `2. A ONE-LINE PUSH IS SENT AUTOMATICALLY when this turn ends, built from the
   first line of your answer -- so lead with the thing itself ("auth tests
   failing, 2 of 14" beats "task complete"). Do not call PushNotification
   yourself.`,
  needed: `2. PUSH ONE LINE WHEN THEY NEED TO LOOK. If the PushNotification tool is
   available, call it once: right before you wait on a question, or when the
   turn ends with something they would act on. Lead with the thing itself.`,
  never: `2. DO NOT PUSH. Never call PushNotification in this session: the user has
   turned pushes off for mobile mode.`,
}

const END_CLAUSES = {
  plain: `A turn that finishes the work should
   just end. Never manufacture a question: one whose answer would not
   change what you do next is worse than none.`,
  suggest: `Never manufacture a decision that isn't
   real. But when the work is finished and nothing needs the user's input,
   still end by calling AskUserQuestion with 2-4 concrete next steps,
   phrased as prompts they could pick up next, so they tap instead of
   type. Make them distinct and genuinely useful.`,
}

export function guidance(push: Push, suggest: boolean): string {
  return `<mobile-mode>
This prompt came from the user's phone (Remote Control): they are away from the
terminal. Reading is cheap for them, typing is expensive, tapping is free.
1. ASK ONLY WHEN THE NEXT ACTION NEEDS THEIR DECISION -- and when it does, ask
   with AskUserQuestion so the choices render as taps instead of prose they
   would have to answer by thumb-typing. Keep each option to a few words and
   make the options genuinely different. ${suggest ? END_CLAUSES.suggest : END_CLAUSES.plain}
${PUSH_ITEMS[push]}
3. WRITE FOR A PHONE SCREEN. Lead with the answer. Cut the preamble. Long
   tables and wide code blocks do not survive the trip.
</mobile-mode>`
}

export function parseRecord(text: string | undefined): Rec {
  const off: Rec = { mode: 'off', push: 'always', suggest: false, exists: false }
  if (text === undefined) return off
  try {
    const d = JSON.parse(text)
    if (!d || typeof d !== 'object' || !['on', 'enforce', 'off'].includes(d.mode)) return off
    return {
      mode: d.mode,
      push: ['always', 'needed', 'never'].includes(d.push) ? d.push : 'always',
      suggest: d.suggest === true,
      exists: true,
    }
  } catch {
    return off
  }
}

// Whether a turn is covered by mobile mode, and whether this module (rather than
// the shell hook) must inject the guidance. Toggled on: the shell hook already
// injects it, so don't double it. Explicit `off` in this session: stay out.
// A background-task turn is covered (push only, no guidance) when the phone was
// used recently.
export function decide(
  origin: string,
  rec: Rec,
  phoneRecent = false,
): { mobileTurn: boolean; inject: boolean } {
  const toggledOn = rec.mode === 'on' || rec.mode === 'enforce'
  const explicitlyOff = rec.exists && rec.mode === 'off'
  if (explicitlyOff) return { mobileTurn: false, inject: false }
  const bridge = origin === 'bridge' || origin === 'quick-action'
  const background = origin === 'task-notification' && phoneRecent
  return { mobileTurn: toggledOn || bridge || background, inject: bridge && !toggledOn }
}

// "23-7" -> true between 23:00 and 06:59 local; "off" / malformed -> never quiet.
export function isQuiet(spec: string | undefined, hour: number): boolean {
  const m = /^\s*(\d{1,2})\s*-\s*(\d{1,2})\s*$/.exec(spec ?? '')
  if (!m) return false
  const a = Number(m[1]) % 24
  const b = Number(m[2]) % 24
  if (a === b) return false
  return a < b ? hour >= a && hour < b : hour >= a || hour < b
}

export function parseActions(spec: string | undefined): { label: string; prompt: string }[] {
  return (spec ?? '')
    .split('|')
    .map(p => {
      const i = p.indexOf('=')
      return i > 0 ? { label: p.slice(0, i).trim(), prompt: p.slice(i + 1).trim() } : null
    })
    .filter((a): a is { label: string; prompt: string } => !!a && !!a.label && !!a.prompt)
    .slice(0, 4)
}

export function summarize(answer: string): string {
  const line = answer
    .split('\n')
    .map(l => l.replace(/[`*_#>|]/g, '').replace(/\[([^\]]*)\]\([^)]*\)/g, '$1').trim())
    .find(l => l.length > 0 && !/^-{3,}$/.test(l))
  if (!line) return 'Claude finished — tap to see the reply.'
  return clip(line)
}

function clip(s: string): string {
  return s.length > MAX ? s.slice(0, MAX - 1).trimEnd() + '…' : s
}

// One line describing what a tool is about to do, for the approval push.
export function describeCall(tool: string, input: unknown): string {
  const i = (input ?? {}) as Record<string, unknown>
  const detail =
    typeof i.command === 'string' ? i.command
    : typeof i.file_path === 'string' ? i.file_path
    : typeof i.url === 'string' ? i.url
    : typeof i.description === 'string' ? i.description
    : ''
  return clip(`Needs your OK: ${tool}${detail ? ` — ${detail.replace(/\s+/g, ' ')}` : ''}`)
}

const SUGGEST_SYSTEM = `You suggest what a user might send next to their AI coding/assistant agent.
Given their last message and the agent's reply, propose 2 or 3 likely next messages, written as
the user would type them: specific to this conversation, short, and genuinely different from each
other (e.g. a follow-up question, the obvious next action, a check or variation). Never suggest
something the reply already did, and never just "thanks" or "continue" unless the reply clearly
paused mid-task. Output ONLY a JSON array: [{"label": "<2-4 words>", "prompt": "<the full message>"}]`

export function parseSuggestions(text: string): Action[] {
  try {
    const i = text.indexOf('[')
    const j = text.lastIndexOf(']')
    if (i < 0 || j <= i) return []
    const arr = JSON.parse(text.slice(i, j + 1))
    if (!Array.isArray(arr)) return []
    return arr
      .filter(a => a && typeof a.label === 'string' && typeof a.prompt === 'string')
      .map(a => ({ label: String(a.label).trim().slice(0, 24), prompt: String(a.prompt).trim().slice(0, 500) }))
      .filter(a => a.label && a.prompt)
      .slice(0, 3)
  } catch {
    return []
  }
}

// One small-model call over the last exchange; [] on any failure.
async function suggestNext($: EngineInterface, model: string, userText: string, answer: string): Promise<Action[]> {
  try {
    const r = await $.model.complete({
      model,
      system: SUGGEST_SYSTEM,
      prompt: `User's last message:\n${userText.slice(0, 2000)}\n\nAgent's reply:\n${answer.slice(-6000)}`,
      maxTokens: 400,
      timeoutMs: 20_000,
    })
    return r.isAnswered ? parseSuggestions(r.text) : []
  } catch {
    return []
  }
}

async function readRecord($: EngineInterface): Promise<Rec> {
  try {
    const id = await $.session.id()
    const home = await $.env.get('HOME')
    if (!home || !SESSION_ID.test(id)) return parseRecord(undefined)
    const text = await $.fs.read(`${home}/.claude/mobile-mode/sessions/${id}.json`).catch(() => undefined)
    return parseRecord(typeof text === 'string' ? text : undefined)
  } catch {
    return parseRecord(undefined)
  }
}

async function sendPush($: EngineInterface, message: string): Promise<void> {
  try {
    await $.tool.call({ tool: 'PushNotification', message, status: 'proactive' })
  } catch {
    // A failed push must never disturb the turn.
  }
}

// True while this module's own PushNotification call runs, so the tool.call hook
// doesn't count it as the model having pushed.
let selfPushing = false

async function selfPush($: EngineInterface, message: string): Promise<void> {
  selfPushing = true
  try { await sendPush($, message) } finally { selfPushing = false }
}

export const register: Register = (on, options) => {
  const opts = (options ?? {}) as Options
  const actions = parseActions(opts.quickActions)
  const longMs = Math.max(0, Number(opts.longTurnMinutes ?? '10') || 0) * 60_000
  const suggestMode = ['phone', 'always', 'off'].includes(opts.suggestions ?? '') ? opts.suggestions : 'phone'
  const suggestModel = (opts.suggestModel ?? '').trim() || 'haiku'

  let mobileTurn = false
  let cadence: Push = 'always'
  let pushedThisTurn = false
  let lastPhoneAt = 0
  let phoneSeen = false // a phone-driven prompt has arrived in this session
  let quickActionPending = false
  let lastTool = ''
  let longTimer: { cancel: () => void } | undefined
  let lastUserText = ''

  const quietNow = () => isQuiet(opts.quietHours ?? '23-7', new Date().getHours())

  on('prompt.submit', async ($, e, next) => {
    // A prompt delivered into a running turn doesn't change who that turn is for.
    if (e.turnId !== undefined) return next(e)
    if (e.text.trimStart().startsWith('/mobile-mode:toggle')) {
      mobileTurn = false
      return next(e)
    }
    lastUserText = e.text
    let origin: string = e.origin.kind
    if (origin === 'plugin' && quickActionPending) origin = 'quick-action'
    quickActionPending = false
    const rec = await readRecord($)
    const now = await $.clock.now()
    const d = decide(origin, rec, phoneSeen && now - lastPhoneAt < PHONE_RECENT_MS)
    if (origin === 'bridge' || origin === 'quick-action') { lastPhoneAt = now; phoneSeen = true }
    cadence = rec.push
    mobileTurn = d.mobileTurn
    if (d.inject) return next({ ...e, context: [...(e.context ?? []), guidance(rec.push, rec.suggest)] })
    return next(e)
  })

  on('turn.start', ($, e, next) => {
    pushedThisTurn = false
    lastTool = ''
    longTimer?.cancel()
    longTimer = undefined
    if (mobileTurn && cadence !== 'never' && longMs > 0) {
      longTimer = $.clock.after(longMs, () => {
        if (!mobileTurn || quietNow()) return
        const what = lastTool ? `: ${lastTool}` : ''
        void selfPush($, clip(`Still working (${Math.round(longMs / 60_000)} min)${what}`))
      })
    }
    return next(e)
  })

  on('tool.call', async ($, e, next) => {
    if (e.tool !== 'PushNotification') {
      const i = e as unknown as Record<string, unknown>
      const d = typeof i.description === 'string' ? i.description : typeof i.command === 'string' ? i.command : ''
      lastTool = clip(`${e.tool}${d ? ` ${String(d).replace(/\s+/g, ' ')}` : ''}`).slice(0, 80)
      return next(e)
    }
    const r = await next(e)
    if (!selfPushing && r.deny === undefined) pushedThisTurn = true
    return r
  })

  // A permission dialog is about to open: the user must act, so push now even in
  // quiet hours (unless pushes are off for the session).
  on('classic.PermissionRequest', async ($, e, next) => {
    if (mobileTurn && cadence !== 'never') await selfPush($, describeCall(e.tool_name, e.tool_input))
    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    const r = await next(e)
    if (e.agentId !== undefined) return r
    longTimer?.cancel()
    longTimer = undefined
    const wantSuggest = !e.isAborted && e.answer.trim() !== '' &&
      (suggestMode === 'always' || (suggestMode === 'phone' && mobileTurn))
    if (mobileTurn) {
      if (actions.length > 0 || wantSuggest) await update($, last, () => ({ answer: e.answer }))
      const failed = e.reason === 'error' || e.reason === 'refusal'
      const due = (cadence === 'always' && !pushedThisTurn) || (failed && cadence !== 'never')
      if (due && !e.isAborted && (!quietNow() || failed)) {
        pushedThisTurn = true
        await selfPush($, failed ? clip(`Turn failed (${e.reason}): ${summarize(e.answer)}`) : summarize(e.answer))
      }
    }
    if (wantSuggest && e.reason === 'answer') {
      // After the push, so the suggestion call never delays it.
      const answer = e.answer
      const suggestions = await suggestNext($, suggestModel, lastUserText, answer)
      if (suggestions.length > 0) {
        await update($, last, cur => (cur && cur.answer === answer ? { ...cur, suggestions } : cur))
        if (suggestMode === 'always') void $.prompt.suggest({ text: suggestions[0]!.prompt })
      }
    }
    return r
  })

  // Quick-action buttons under the latest reply, in the Claude mobile app only.
  on('ui.render', { component: 'AssistantMessage' }, async ($, e, next) => {
    const drawn = await next(e)
    if (e.surface !== 'mobile') return drawn
    const latest = await read($, last)
    const text = e.props.text.trim()
    if (!latest || !text || !latest.answer.trimEnd().endsWith(text)) return drawn
    const buttons: Action[] = latest.suggestions?.length ? latest.suggestions : actions
    if (buttons.length === 0) return drawn
    const { Box, Button } = $.ui.resolve(e)
    return (
      <Box flexDirection="column">
        {drawn}
        <Box flexDirection="row" gap={1}>
          {buttons.map((a, i) => (
            <Button
              key={`qa-${i}`}
              label={a.label}
              onPress={async () => {
                await update($, last, () => null) // one tap; the row returns with the next reply
                quickActionPending = true
                await $.prompt.submit({ text: a.prompt, asUser: true })
              }}
            />
          ))}
        </Box>
      </Box>
    )
  })
}
