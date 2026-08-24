import { useCallback, useLayoutEffect, useRef } from 'react'
import { motionReduced } from './motion'

// Container-transform reveal for overlays: a `.panel` grows out of the clicked tile's
// rect on open and collapses back into it on close, while the backdrop fades. Pure
// transform + opacity (compositor-only). The grow is HELD hidden until `ready` (the
// content has loaded) so it grows the real window in rather than an empty shell, and
// the geometry is measured at play-time (the panel's height changes once content
// fills in). CSS transitions triggered a paint later (double rAF) — a single rAF lets
// the browser coalesce start+end into one frame and skip the animation.

export type RevealOrigin =
  | { left: number; top: number; width: number; height: number }
  | null

const REVEAL_MS = 300

export function rectOf(el: Element | null | undefined): RevealOrigin {
  if (!el) return null
  const r = el.getBoundingClientRect()
  if (!r.width || !r.height) return null
  return { left: r.left, top: r.top, width: r.width, height: r.height }
}

// The originating tile located by the `data-reveal-key` stamped on grid cards.
export function byKey(key: string): RevealOrigin {
  const sel = window.CSS && window.CSS.escape ? window.CSS.escape(key) : key
  return rectOf(document.querySelector(`[data-reveal-key="${sel}"]`))
}

// Transform mapping `panel` (at its natural rect) onto `origin`'s box, so the panel
// visually starts FROM the clicked tile. Assumes transform-origin: top-left (0 0).
function invert(panel: HTMLElement, origin: RevealOrigin): string {
  if (!origin) return 'scale(0.7)' // no source tile -> a pop from ~70%
  const p = panel.getBoundingClientRect()
  if (!p.width || !p.height) return 'scale(0.7)'
  const sx = origin.width / p.width
  const sy = origin.height / p.height
  const dx = origin.left - p.left
  const dy = origin.top - p.top
  return `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})`
}

/**
 * Attach `overlayRef` to the `.overlay` backdrop and `panelRef` to the `.panel`, then
 * call `requestClose(realOnClose)` from every close path so the exit animates before
 * unmount. `getOrigin` returns the tile rect (read live). `ready` gates the grow: while
 * false the panel is held hidden; the grow fires when it flips true (or after a safety
 * cap so a slow/failed load never leaves it stuck hidden). Defaults to open-on-mount.
 */
export function useReveal(getOrigin?: () => RevealOrigin, ready: boolean = true) {
  const overlayRef = useRef<HTMLDivElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const getOriginRef = useRef(getOrigin)
  getOriginRef.current = getOrigin
  const played = useRef(false)
  const rafs = useRef<number[]>([])

  const play = useCallback(() => {
    const panel = panelRef.current
    const overlay = overlayRef.current
    if (played.current || !panel) return
    played.current = true
    if (motionReduced()) {
      if (overlay) overlay.style.opacity = ''
      panel.style.transform = ''
      return
    }
    // Measure NOW (content has loaded, so the panel's final height is correct) and set
    // the collapsed start state; the overlay is still hidden, so no flash.
    const go = getOriginRef.current
    const from = invert(panel, go ? go() : null)
    panel.style.transformOrigin = '0 0'
    panel.style.transition = 'none'
    panel.style.transform = from
    if (overlay) {
      overlay.style.transition = 'none'
      overlay.style.opacity = '0'
    }
    const r1 = requestAnimationFrame(() => {
      const r2 = requestAnimationFrame(() => {
        panel.style.transition = `transform ${REVEAL_MS}ms var(--ease-reveal)`
        panel.style.transform = 'none'
        if (overlay) {
          overlay.style.transition = `opacity ${REVEAL_MS}ms ease`
          overlay.style.opacity = '1'
        }
      })
      rafs.current.push(r2)
    })
    rafs.current.push(r1)
  }, [])

  // Hide immediately (before the first paint) so the panel never flashes at full size
  // while the content loads. Safety cap: play even if `ready` never arrives.
  useLayoutEffect(() => {
    const panel = panelRef.current
    const overlay = overlayRef.current
    if (!panel || motionReduced()) return
    if (overlay) {
      overlay.style.transition = 'none'
      overlay.style.opacity = '0'
    }
    const cap = window.setTimeout(play, 1200)
    const pending = rafs.current
    return () => {
      window.clearTimeout(cap)
      pending.forEach(cancelAnimationFrame)
    }
    // Mount-only on purpose: this hides the panel before the FIRST paint. `play` is
    // stable (useCallback with no deps) and re-running this would re-hide a panel that
    // has already grown in.
    // oxlint-disable-next-line react-hooks/exhaustive-deps -- mount-only, see above
  }, [])

  // Grow the real window in once its content is ready.
  useLayoutEffect(() => {
    if (ready) play()
  }, [ready, play])

  const requestClose = useCallback(
    (done: () => void) => {
      const panel = panelRef.current
      const overlay = overlayRef.current
      if (!panel || motionReduced()) {
        done()
        return
      }
      const go = getOriginRef.current
      const to = invert(panel, go ? go() : null)
      let called = false
      const finish = () => {
        if (!called) {
          called = true
          done()
        }
      }
      panel.addEventListener(
        'transitionend',
        (e: TransitionEvent) => {
          if (e.propertyName === 'transform') finish()
        },
        { once: true },
      )
      window.setTimeout(finish, REVEAL_MS + 120) // safety net
      requestAnimationFrame(() => {
        panel.style.transformOrigin = '0 0'
        panel.style.transition = `transform ${REVEAL_MS}ms var(--ease-reveal-in)`
        panel.style.transform = to
        if (overlay) {
          overlay.style.transition = `opacity ${REVEAL_MS}ms ease`
          overlay.style.opacity = '0'
        }
      })
    },
    [],
  )

  return { overlayRef, panelRef, requestClose }
}
