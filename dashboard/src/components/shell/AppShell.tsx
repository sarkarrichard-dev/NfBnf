import { useState, type ReactNode } from 'react'
import { Logo } from '../ui/Logo'
import { IconMenu } from '../ui/Icons'
import { Sidebar, type NavGroup } from './Sidebar'
import { TickerStrip } from './TickerStrip'

/** The application frame: a sticky top bar with the brand + live status pills,
 *  a grouped sidebar, and the page in the remaining space. */
export function AppShell({
  nav,
  active,
  onNavigate,
  topRight,
  children,
}: {
  nav: NavGroup[]
  active: string
  onNavigate: (id: string) => void
  topRight?: ReactNode
  children: ReactNode
}) {
  const [menu, setMenu] = useState(false)

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-[var(--hair)] bg-[var(--ground)]/85 backdrop-blur">
        <div className="mx-auto flex h-[2.85rem] max-w-[92rem] items-center gap-3 px-4 md:px-6">
          <button
            type="button"
            onClick={() => setMenu((v) => !v)}
            aria-label="Menu"
            className="grid size-8 place-items-center rounded border border-[var(--hair)] text-slate-300 md:hidden"
          >
            <span className="size-4">
              <IconMenu />
            </span>
          </button>
          <Logo />
          <div className="ml-auto flex items-center gap-1.5">{topRight}</div>
        </div>
      </header>

      <TickerStrip />

      <div className="mx-auto flex max-w-[92rem]">
        <Sidebar
          groups={nav}
          active={active}
          onSelect={(id) => {
            onNavigate(id)
            setMenu(false)
          }}
          open={menu}
          onClose={() => setMenu(false)}
        />
        <main className="min-w-0 flex-1 px-4 py-5 md:px-6 md:py-6">{children}</main>
      </div>
    </div>
  )
}
