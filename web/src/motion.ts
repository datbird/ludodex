// Shared motion preference. `honor` = whether to respect the OS `prefers-reduced-
// motion` setting; when false, UI animations always play. Persisted per-device in
// localStorage (like theme/view). Default: always play (honor = false) so the effects
// are on out of the box; a user who wants a calm UI can enable honoring.

const KEY = 'ludodex-honor-reduced-motion'

function readHonor(): boolean {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

let honor = readHonor()

export function honorReducedMotion(): boolean {
  return honor
}

export function setHonorReducedMotion(v: boolean): void {
  honor = v
  try {
    localStorage.setItem(KEY, v ? '1' : '0')
  } catch {
    /* ignore */
  }
}

// True when animations should be SUPPRESSED: only when we're honoring the OS setting
// AND the OS actually asks for reduced motion.
export function motionReduced(): boolean {
  if (!honor) return false
  return (
    typeof window !== 'undefined' &&
    !!window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}
