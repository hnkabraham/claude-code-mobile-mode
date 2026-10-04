// mobile-mode's $.state contract: what the quick-action row under the latest
// reply reads while drawing on the mobile surface.
export type MobileLast = {
  /** The final visible text of the latest finished phone-driven turn. */
  answer: string
}

declare module 'claude-code' {
  interface PluginState {
    'mobile-mode': { last: MobileLast | null }
  }
}
