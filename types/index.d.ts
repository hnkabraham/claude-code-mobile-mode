// mobile-mode's $.state contract: what the quick-action row under the latest
// reply reads while drawing on the mobile surface.
export type MobileAction = { label: string; prompt: string }

export type MobileLast = {
  /** The final visible text of the latest finished phone-driven turn. */
  answer: string
  /** Next-prompt suggestions for that turn (from a small model), once ready. */
  suggestions?: MobileAction[]
}

declare module 'claude-code' {
  interface PluginState {
    'mobile-mode': { last: MobileLast | null }
  }
}
