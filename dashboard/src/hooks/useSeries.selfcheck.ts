// Run: npx tsx src/hooks/useSeries.selfcheck.ts
import { getSeries, pushPoint } from './useSeries'

function eq(a: unknown, b: unknown, msg: string) {
  if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${msg}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`)
}

// dedupe identical trailing values
pushPoint('k', 5)
pushPoint('k', 5)
pushPoint('k', 6)
eq(getSeries('k'), [5, 6], 'trailing dupes collapse')

// nulls / NaN ignored
pushPoint('k', null)
pushPoint('k', NaN)
eq(getSeries('k'), [5, 6], 'null/NaN skipped')

// cap at 120 points, keeps newest
for (let i = 0; i < 300; i++) pushPoint('cap', i)
const capped = getSeries('cap')
eq(capped.length, 120, 'ring buffer capped')
eq(capped[capped.length - 1], 299, 'keeps newest')

console.log('useSeries ok')
