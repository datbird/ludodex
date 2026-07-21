// AST sweep for React rules-of-hooks violations.
// Finds hook calls that are (a) after an early return in a component body,
// (b) inside a conditional/loop/logical-shortcut, or (c) inside a nested callback
// that isn't itself a hook/component.
//
// Why this exists: oxlint has no react-hooks/rules-of-hooks rule and the repo has no
// eslint, so nothing caught the LibraryPrefs hook-after-early-return that white-screened
// the whole app (React #310, fixed in 17be9ce). This is the guard. Runs in `pnpm build`.
//
// Usage: node scripts/hooksweep.mjs [file ...]   (defaults to every .tsx under src/)
// Exits non-zero when anything is found, so a violation fails the build.
import ts from 'typescript'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

function tsxFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name)
    if (e.isDirectory()) return e.name === 'node_modules' ? [] : tsxFiles(p)
    return e.name.endsWith('.tsx') ? [p] : []
  })
}

const files = process.argv.slice(2)
const targets = files.length ? files : tsxFiles('src')

let total = 0
let scannedFns = 0
for (const file of targets) total += scanFile(file)

console.log(total
  ? `\n${total} rules-of-hooks violation(s) across ${targets.length} file(s)`
  : `CLEAN — no rules-of-hooks violations (${scannedFns} components/hooks in ${targets.length} file(s))`)
process.exit(total ? 1 : 0)

function scanFile(file) {
const src = ts.createSourceFile(file, readFileSync(file, 'utf8'), ts.ScriptTarget.ESNext, true, ts.ScriptKind.TSX)

const HOOK = /^use[A-Z]/
const isHookName = n => HOOK.test(n) || n === 'use'
const cap = n => !!n && /^[A-Z]/.test(n)

const findings = []
const line = node => src.getLineAndCharacterOfPosition(node.getStart(src)).line + 1

// name of a function-ish node, if determinable
function fnName(node) {
  if (ts.isFunctionDeclaration(node) && node.name) return node.name.text
  const p = node.parent
  if (p && ts.isVariableDeclaration(p) && ts.isIdentifier(p.name)) return p.name.text
  if (p && ts.isPropertyAssignment(p) && ts.isIdentifier(p.name)) return p.name.text
  return null
}

function isFnLike(n) {
  return ts.isFunctionDeclaration(n) || ts.isFunctionExpression(n) || ts.isArrowFunction(n) || ts.isMethodDeclaration(n)
}

// Does this call expression look like a hook call? (useX(...) or ns.useX(...))
function hookCallName(node) {
  if (!ts.isCallExpression(node)) return null
  const e = node.expression
  if (ts.isIdentifier(e) && isHookName(e.text)) return e.text
  if (ts.isPropertyAccessExpression(e) && ts.isIdentifier(e.name) && isHookName(e.name.text)) return e.name.text
  return null
}

// Walk a component/hook body. Track: have we passed a top-level return?
function scanBody(fn, ownerName) {
  const body = fn.body
  if (!body || !ts.isBlock(body)) return

  // statements at the top level of the body, in order
  let sawReturn = null

  const visitStmt = (stmt, depthCtx) => {
    // Record hooks found anywhere under this statement, with context flags.
    const walk = (node, ctx) => {
      // Don't descend into nested function bodies here — handled separately.
      if (node !== stmt && isFnLike(node)) {
        const nm = fnName(node)
        // A nested function that is itself a component or custom hook gets its own scan.
        if (cap(nm) || (nm && isHookName(nm))) scanFn(node, nm)
        else {
          // plain callback: hooks inside it are violations
          const inner = []
          collectHooks(node, inner)
          for (const h of inner) {
            findings.push({
              kind: 'hook-in-callback',
              owner: ownerName, hook: h.name, line: h.line,
              detail: `called inside a non-component callback${nm ? ` (${nm})` : ''}`,
            })
          }
        }
        return
      }
      const hn = hookCallName(node)
      if (hn) {
        if (ctx.cond) {
          findings.push({ kind: 'conditional-hook', owner: ownerName, hook: hn, line: line(node), detail: ctx.cond })
        } else if (sawReturn != null) {
          findings.push({
            kind: 'hook-after-return', owner: ownerName, hook: hn, line: line(node),
            detail: `after early return on line ${sawReturn}`,
          })
        }
      }
      let next = ctx
      if (ts.isIfStatement(node)) next = { cond: 'inside an if statement' }
      else if (ts.isConditionalExpression(node)) next = { cond: 'inside a ternary' }
      else if (ts.isForStatement(node) || ts.isForOfStatement(node) || ts.isForInStatement(node) || ts.isWhileStatement(node) || ts.isDoStatement(node)) next = { cond: 'inside a loop' }
      else if (ts.isBinaryExpression(node) && (node.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken || node.operatorToken.kind === ts.SyntaxKind.BarBarToken || node.operatorToken.kind === ts.SyntaxKind.QuestionQuestionToken)) next = { cond: 'inside a logical short-circuit' }
      else if (ts.isSwitchStatement(node)) next = { cond: 'inside a switch' }
      else if (ts.isTryStatement(node)) next = { cond: 'inside try/catch' }
      ts.forEachChild(node, c => walk(c, next))
    }
    walk(stmt, { cond: null })

    // after processing, note a top-level return
    if (ts.isReturnStatement(stmt) && sawReturn == null) sawReturn = line(stmt)
    // a top-level if that ALWAYS returns also acts as an early return guard
    if (ts.isIfStatement(stmt) && sawReturn == null) {
      const t = stmt.thenStatement
      const returns = ts.isReturnStatement(t) || (ts.isBlock(t) && t.statements.some(s => ts.isReturnStatement(s) || ts.isThrowStatement(s)))
      if (returns && !stmt.elseStatement) sawReturn = line(stmt)
    }
  }

  for (const stmt of body.statements) visitStmt(stmt)
}

function collectHooks(node, out) {
  const hn = hookCallName(node)
  if (hn) out.push({ name: hn, line: line(node) })
  ts.forEachChild(node, c => collectHooks(c, out))
}

const scanned = new Set()
function scanFn(fn, name) {
  if (scanned.has(fn)) return
  scanned.add(fn)
  scanBody(fn, name)
}

// Find every component (Capitalized) or custom hook (useX) function in the file.
function findComponents(node) {
  if (isFnLike(node)) {
    const nm = fnName(node)
    if (cap(nm) || (nm && isHookName(nm))) scanFn(node, nm)
  }
  ts.forEachChild(node, findComponents)
}
findComponents(src)

// de-dupe
const seen = new Set()
const out = findings.filter(f => {
  const k = `${f.kind}:${f.owner}:${f.hook}:${f.line}`
  if (seen.has(k)) return false
  seen.add(k); return true
}).sort((a, b) => a.line - b.line)

for (const f of out) console.log(`${file}:${f.line}  [${f.kind}]  ${f.owner || '<anon>'} → ${f.hook}()  — ${f.detail}`)
scannedFns += scanned.size
return out.length
}
