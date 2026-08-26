/**
 * Does a game page actually PAINT its art?
 *
 * The two tests beside this one prove the API answers correctly. Neither can prove a
 * user sees anything. On 2026-08-26 the detail page rendered with no hero and no
 * background while every request returned 200, because the requests were never made
 * with a key the server understood. A browser is the only witness for that.
 *
 * THE TRAP THIS AVOIDS: `isVisible()` is not proof. An element can be "visible" and
 * painted over, or present with a broken image. So every assertion here is physical:
 * `naturalWidth > 0` means the bytes decoded, and `elementFromPoint` at the element's
 * own centre means nothing is covering it.
 *
 * It also watches the network. Any /api/media request that fails is a failure of this
 * test, which is exactly the signal the hero regression never produced on its own.
 *
 * Connects to a Playwright endpoint rather than launching one, so the browser can live
 * on another machine. Read-only: it signs in, looks, and leaves.
 *
 *   LUDODEX_BROWSER_WS=ws://127.0.0.1:9223/ \
 *   LUDODEX_URL=http://host:8001 LUDODEX_USER=… LUDODEX_PASS=… \
 *     node tests/browser/detail-render.mjs
 */
import path from 'node:path'
import { pathToFileURL } from 'node:url'

// Playwright does not have to live in this repo, and on purpose: the browser may run on
// another machine entirely. Point LUDODEX_PLAYWRIGHT at any node_modules that has it.
// ESM ignores NODE_PATH, so resolve the path ourselves rather than asking people to
// install a browser stack into a project that does not need one.
const pwDir = process.env.LUDODEX_PLAYWRIGHT
const pw = pwDir
  ? await import(pathToFileURL(path.join(pwDir, 'playwright', 'index.js')).href)
  : await import('playwright')
// playwright is CommonJS, so an ESM import lands it under .default when resolved by
// path. Accept both shapes rather than caring which resolver got there.
const { chromium } = pw.default ?? pw

const WS = process.env.LUDODEX_BROWSER_WS
const URL = (process.env.LUDODEX_URL || '').replace(/\/$/, '')
const USER = process.env.LUDODEX_USER
const PASS = process.env.LUDODEX_PASS
if (!WS || !URL || !USER || !PASS) {
  console.log('SKIPPED: need LUDODEX_BROWSER_WS, LUDODEX_URL, LUDODEX_USER, LUDODEX_PASS')
  process.exit(0)
}

let failed = 0
const check = (label, ok, detail) => {
  console.log(`  ${ok ? 'ok ' : 'FAIL'}   ${label}${ok ? '' : '   <- ' + JSON.stringify(detail ?? null).slice(0, 200)}`)
  if (!ok) failed++
}

/**
 * Physical proof that an element rendered.
 *
 *  - it has real size
 *  - if it is an IMG, `naturalWidth > 0`, so the BYTES arrived and decoded. This is the
 *    assertion that would have caught the missing hero: a broken image is still
 *    "visible" to Playwright.
 *  - unless `overlaid`, the element at its own centre is itself. `isVisible()` cannot
 *    tell you that something is painted over.
 *
 * `overlaid` is for layers that are MEANT to have things on top: the hero background
 * carries the title and the tool buttons by design, so demanding a clear centre there
 * would be asserting the layout is wrong.
 */
const painted = (locator, { overlaid = false } = {}) => locator.evaluate((el, ov) => {
  const r = el.getBoundingClientRect()
  if (r.width < 4 || r.height < 4) return { ok: false, why: 'zero-sized', r }
  const nat = el.tagName === 'IMG' ? el.naturalWidth : null
  if (nat === 0) return { ok: false, why: 'image did not decode', nat }
  if (ov) return { ok: true, nat }
  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
  const covered = !(hit === el || el.contains(hit) || (hit && hit.contains(el)))
  return { ok: !covered, why: covered ? 'covered by ' + (hit && hit.className) : '', nat }
}, overlaid)

const browser = await chromium.connect(WS)
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await ctx.newPage()

const mediaFailures = []
page.on('response', (r) => {
  const u = r.url()
  if (u.includes('/api/media/') && r.status() >= 400) mediaFailures.push(`${r.status()} ${u.slice(-80)}`)
})
const consoleErrors = []
page.on('pageerror', (e) => consoleErrors.push(String(e).slice(0, 160)))

try {
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 45000 })

  // This is a React app, so nothing exists at domcontentloaded. WAIT for one of the two
  // states rather than probing: `count()` on an unrendered page returns 0, which reads
  // as "already signed in" and then times out on a grid that was never coming. That is
  // a flaky test writing itself.
  await page.waitForSelector('input[type="password"], [data-reveal-key]', { timeout: 45000 })

  // Sign in through the real form, so the session is the one a user gets. The inputs
  // carry no name or id, so they are addressed by type, and the button by its own class
  // rather than by type=submit: it is not inside a <form>.
  const pwBox = page.locator('input[type="password"]').first()
  if (await pwBox.count()) {
    // NOT input[type="text"]: the username field has NO type attribute. The DOM
    // property reports "text" (that is the spec default), but a CSS attribute selector
    // matches the attribute, which is not there. Take the first non-password input.
    await page.locator('input:not([type="password"])').first().fill(USER)
    await pwBox.fill(PASS)
    await page.locator('.auth-submit, button:has-text("Sign in")').first().click()
  }
  // The app opens on the DASHBOARD, so there is no grid until Library is selected.
  // A test that assumed the grid was the landing page would just time out and blame
  // the grid.
  await page.waitForSelector('.pt-tab', { timeout: 45000 })
  const libraryTab = page.locator('.pt-tab', { hasText: 'Library' }).first()
  if (await libraryTab.count()) await libraryTab.click()
  await page.waitForSelector('[data-reveal-key]', { timeout: 45000 })
  check('the library grid renders', true)

  // --- the grid paints real covers, not placeholders --------------------------------
  const tiles = page.locator('[data-reveal-key]')
  const n = await tiles.count()
  check('the grid has tiles', n > 0, n)

  const covers = page.locator('[data-reveal-key] img')
  const withArt = await covers.count()
  check('tiles carry cover images', withArt > 0, withArt)
  if (withArt) {
    const first = covers.first()
    await first.scrollIntoViewIfNeeded()
    const p = await painted(first)
    check('the first cover actually decoded and is not covered', p.ok, p)
  }

  // --- open a game and prove its hero paints ---------------------------------------
  const target = tiles.first()
  const key = await target.getAttribute('data-reveal-key')
  await target.click()
  await page.waitForSelector('.hero, .hero-plain, .hero-marquee-mode', { timeout: 30000 })
  check(`the detail panel opened for ${key}`, true)

  const heroImgs = page.locator('.hero img, .hero-bg-frame img, .hero-logo')
  const heroCount = await heroImgs.count()
  check('the hero holds at least one image', heroCount > 0, {
    heroCount, hint: 'zero here is exactly what the 2026-08-26 media regression looked like',
  })
  if (heroCount > 0) {
    // overlaid: the hero deliberately carries the title and tool buttons on top of it
    const p = await painted(heroImgs.first(), { overlaid: true })
    check('the hero image decoded, so its bytes really arrived', p.ok, p)
  }

  const title = await page.locator('.hero-title, .hero-sub').first().textContent()
  check('the panel shows a title', !!(title || '').trim(), title)

  // --- the network told the truth ---------------------------------------------------
  check('no /api/media request failed', mediaFailures.length === 0, mediaFailures)
  check('no uncaught page errors', consoleErrors.length === 0, consoleErrors)
} catch (e) {
  check('the run completed', false, String(e).slice(0, 300))
} finally {
  await ctx.close().catch(() => {})
  await browser.close().catch(() => {})
}

console.log(failed ? `FAILED: ${failed} check(s)` : 'RESULT: all browser checks passed')
process.exit(failed ? 1 : 0)
