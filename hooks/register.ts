import type { EngineInterface, Register } from 'claude-code'

// Function-hook half of mobile-mode (v0.6.0). Claude Code builds with the
// function-hook (mod) engine stamp every submitted prompt with its origin, and a
// Remote Control prompt arrives as `origin.kind === 'bridge'`. That removes the
// two weaknesses of the shell hooks alone:
//
//   1. Detection. Shell hooks get no origin field, so mobile mode needed a manual
//      per-session toggle. Here a phone prompt turns it on for that turn by
//      itself; terminal prompts, cron jobs and chat bridges are never 'bridge',
//      so unattended sessions stay untouched without any switch.
//   2. The push. The shell hooks can only ask the model to call PushNotification
//      (and, under `enforce`, block once if it didn't). Here the push is sent by
//      the plugin when the turn ends whenever the cadence is `always` and the
//      model didn't already push. The tool still suppresses itself when the user
//      is at the terminal.
//
// The per-session record (~/.claude/mobile-mode/sessions/<id>.json, written by
// /mobile-mode:toggle) still rules: an explicit `off` disables the automatic
// mode for that session, and its push cadence / suggest preference apply. When
// the toggle is on, the shell hook already injects the guidance, so this module
// only adds the guaranteed push. On builds without the mod engine this file is
// simply not loaded and the shell hooks behave exactly as before.

type Push = 'always' | 'needed' | 'never'
type Rec = { mode: 'on' | 'enforce' | 'off'; push: Push; suggest: boolean; exists: boolean }

const MAX = 180
const SESSION_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$/

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
export function decide(bridge: boolean, rec: Rec): { mobileTurn: boolean; inject: boolean } {
  const toggledOn = rec.mode === 'on' || rec.mode === 'enforce'
  const explicitlyOff = rec.exists && rec.mode === 'off'
  return { mobileTurn: toggledOn || (bridge && !explicitlyOff), inject: bridge && !toggledOn && !explicitlyOff }
}

export function summarize(answer: string): string {
  const line = answer
    .split('\n')
    .map(l => l.replace(/[`*_#>|]/g, '').replace(/\[([^\]]*)\]\([^)]*\)/g, '$1').trim())
    .find(l => l.length > 0 && !/^-{3,}$/.test(l))
  if (!line) return 'Claude finished — tap to see the reply.'
  return line.length > MAX ? line.slice(0, MAX - 1).trimEnd() + '…' : line
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

export const register: Register = on => {
  let mobileTurn = false // the current main-loop turn is one mobile mode covers
  let cadence: Push = 'always'
  let pushedThisTurn = false
  let selfPushing = false

  on('prompt.submit', async ($, e, next) => {
    // A prompt delivered into a running turn doesn't change who that turn is for.
    if (e.turnId !== undefined) return next(e)
    if (e.text.trimStart().startsWith('/mobile-mode:toggle')) {
      mobileTurn = false
      return next(e)
    }
    const rec = await readRecord($)
    const d = decide(e.origin.kind === 'bridge', rec)
    cadence = rec.push
    mobileTurn = d.mobileTurn
    if (d.inject) return next({ ...e, context: [...(e.context ?? []), guidance(rec.push, rec.suggest)] })
    return next(e)
  })

  on('turn.start', ($, e, next) => {
    pushedThisTurn = false
    return next(e)
  })

  on('tool.call', { tool: 'PushNotification' }, async ($, e, next) => {
    const r = await next(e)
    if (!selfPushing && r.deny === undefined) pushedThisTurn = true
    return r
  })

  on('turn.complete', async ($, e, next) => {
    const r = await next(e)
    if (e.agentId === undefined && mobileTurn && cadence === 'always' && !pushedThisTurn && !e.isAborted) {
      pushedThisTurn = true
      selfPushing = true
      try {
        await $.tool.call({ tool: 'PushNotification', message: summarize(e.answer), status: 'proactive' })
      } catch {
        // A failed push must never disturb the turn's answer.
      } finally {
        selfPushing = false
      }
    }
    return r
  })
}
