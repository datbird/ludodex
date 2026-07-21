import { motionReduced } from './motion'

// Magic-wand sparkle burst: a short-lived shower of twinkling stars flung outward from
// a point (or an element's center). Pure DOM spans + one CSS keyframe (spark-fly);
// each particle removes itself on animationend. Cheap (~20-60 nodes for <1s) and no lib.

const GLYPHS = ['✦', '✧', '⋆', '✨', '✺'] // ✦ ✧ ⋆ ✨ ✺
const COLORS = ['#ffd76b', '#ffe9a8', '#c084fc', '#e9d5ff', '#ffffff']

/** Burst `count` twinkling stars outward from viewport point (x, y). `big` = a wider,
 *  larger, more dramatic shower (for headline moments like "Wave the wand"). */
export function sparkleBurst(x: number, y: number, count = 22, big = false): void {
  if (typeof document === 'undefined' || motionReduced()) return
  const frag = document.createDocumentFragment()
  const near = big ? 55 : 32
  const far = big ? 150 : 82
  const minSz = big ? 13 : 9
  const szRange = big ? 20 : 13
  for (let i = 0; i < count; i++) {
    const s = document.createElement('span')
    s.className = 'spark'
    s.textContent = GLYPHS[(Math.random() * GLYPHS.length) | 0]
    const angle = (Math.PI * 2 * i) / count + (Math.random() - 0.5) * 0.7
    const dist = near + Math.random() * (far - near)
    const tx = Math.cos(angle) * dist
    const ty = Math.sin(angle) * dist - (big ? 24 : 16) // slight upward float — magical
    s.style.left = `${x}px`
    s.style.top = `${y}px`
    s.style.color = COLORS[(Math.random() * COLORS.length) | 0]
    s.style.fontSize = `${(minSz + Math.random() * szRange) | 0}px`
    s.style.setProperty('--tx', `${tx.toFixed(1)}px`)
    s.style.setProperty('--ty', `${ty.toFixed(1)}px`)
    s.style.setProperty('--s', (0.7 + Math.random() * (big ? 1.7 : 1.25)).toFixed(2))
    s.style.setProperty('--r', `${(Math.random() * 300 - 150) | 0}deg`)
    s.style.setProperty('--dur', `${((big ? 720 : 620) + Math.random() * 560) | 0}ms`)
    s.addEventListener('animationend', () => s.remove(), { once: true })
    frag.appendChild(s)
  }
  document.body.appendChild(frag)
}

/** Burst from an element's center (e.g. the clicked wand button). */
export function sparkleFrom(el: Element | null | undefined, count?: number, big?: boolean): void {
  if (!el) return
  const r = el.getBoundingClientRect()
  if (!r.width && !r.height) return
  sparkleBurst(r.left + r.width / 2, r.top + r.height / 2, count, big)
}
