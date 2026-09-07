/** IST calendar-day boundaries for dashboard period filters. */
export function istDayBoundsMs(when = new Date()): { start: number; end: number } {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Kolkata',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    })
      .formatToParts(when)
      .map((p) => [p.type, p.value]),
  )
  const start = Date.parse(`${parts.year}-${parts.month}-${parts.day}T00:00:00+05:30`)
  return { start, end: start + 24 * 60 * 60 * 1000 }
}

export function istWeekStartMs(when = new Date()): number {
  const { start: dayStart } = istDayBoundsMs(when)
  const weekday = new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Kolkata', weekday: 'short' })
    .format(when)
  const map: Record<string, number> = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 }
  const offset = map[weekday] ?? 0
  return dayStart - offset * 24 * 60 * 60 * 1000
}

/** IST bounds for a custom [from, to] range of YYYY-MM-DD dates, end-inclusive. */
export function istRangeBoundsMs(from: string, to: string): { start: number; end: number } {
  const start = Date.parse(`${from}T00:00:00+05:30`)
  const end = Date.parse(`${to}T00:00:00+05:30`) + 24 * 60 * 60 * 1000
  return { start, end }
}

/** Today's IST date as YYYY-MM-DD — the max selectable date. */
export function istTodayDate(when = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Kolkata',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(when)
}

export function istMonthStartMs(when = new Date()): number {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Kolkata',
      year: 'numeric',
      month: '2-digit',
    })
      .formatToParts(when)
      .map((p) => [p.type, p.value]),
  )
  return Date.parse(`${parts.year}-${parts.month}-01T00:00:00+05:30`)
}
