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
  stampMotionAttr()
}

// Publish the SAME decision motionReduced() makes as a root attribute, so CSS can gate
// on it too. A bare `@media (prefers-reduced-motion: reduce)` rule would ignore this
// app's opt-in (`honor`, default off) and suppress animations for a user who explicitly
// asked to always play them — the attribute keeps CSS and JS answering the same way.
export function stampMotionAttr(): void {
  if (typeof document === 'undefined') return
  document.documentElement.setAttribute('data-motion', motionReduced() ? 'reduced' : 'full')
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

stampMotionAttr()

// The OS setting can change while the app is open.
if (typeof window !== 'undefined' && window.matchMedia) {
  const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
  if (mq.addEventListener) mq.addEventListener('change', stampMotionAttr)
}
