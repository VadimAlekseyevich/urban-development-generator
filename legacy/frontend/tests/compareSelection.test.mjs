import test from 'node:test'
import assert from 'node:assert/strict'

import {
  MAX_COMPARE_RUNS,
  moveBaselineFirst,
  resolveMapRunId,
  toggleComparedRun,
} from '../src/compareSelection.ts'

test('comparison retains explicit baseline/order and forbids duplicates and >10 runs', () => {
  let selected = ['baseline', 'second']
  selected = toggleComparedRun(selected, 'second', true)
  assert.deepEqual(selected, ['baseline', 'second'])
  for (let n = 3; n <= MAX_COMPARE_RUNS; n += 1) {
    selected = toggleComparedRun(selected, `r${n}`, true)
  }
  assert.equal(selected.length, MAX_COMPARE_RUNS)
  assert.deepEqual(toggleComparedRun(selected, 'overflow', true), selected)
  assert.deepEqual(moveBaselineFirst(selected, 'r7').slice(0, 4), [
    'r7', 'baseline', 'second', 'r3',
  ])
  assert.deepEqual(moveBaselineFirst(selected, 'not-selected'), selected)
  selected = toggleComparedRun(selected, 'r7', false)
  assert.equal(selected.includes('r7'), false)
  assert.deepEqual(toggleComparedRun(selected, 'new', true).at(-1), 'new')
})

test('pin never silently substitutes another generated run', () => {
  const available = [{ id: 'first' }, { id: 'second' }]
  assert.equal(resolveMapRunId(available, 'first', null), 'first')
  assert.equal(resolveMapRunId(available, 'first', 'second'), 'second')
  assert.equal(resolveMapRunId(available, 'first', 'missing'), '')
  assert.equal(resolveMapRunId([], 'first', 'first'), '')
})
