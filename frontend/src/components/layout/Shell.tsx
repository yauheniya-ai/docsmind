import { useEffect, useState, type ReactNode } from "react"
import { PanelLeftOpen } from "lucide-react"
import { cn } from "@/lib/utils"
import { Sidebar } from "./Sidebar"
import { MobileNav } from "./MobileNav"

const STORAGE_KEY = "docsmind.sidebarCollapsed"

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1"
  } catch {
    return false
  }
}

export function Shell({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(readCollapsed)

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0")
    } catch {
      // storage unavailable: the preference just isn't remembered
    }
  }, [collapsed])

  return (
    <div className="flex flex-col md:flex-row h-[100dvh] w-full bg-background text-foreground overflow-hidden">
      <div
        aria-hidden={collapsed}
        inert={collapsed}
        className={cn(
          "hidden md:block flex-shrink-0 overflow-hidden transition-[width] duration-200 ease-in-out",
          collapsed ? "w-0" : "w-64 lg:w-72"
        )}
      >
        <Sidebar onCollapse={() => setCollapsed(true)} />
      </div>
      <main className="flex-1 flex flex-col min-w-0 relative">
        {collapsed && (
          <button
            type="button"
            onClick={() => setCollapsed(false)}
            aria-label="Expand sidebar"
            title="Expand sidebar"
            className="absolute left-3 top-3 z-20 hidden h-9 w-9 items-center justify-center rounded-lg border border-border/60 bg-background/90 text-muted-foreground shadow-sm backdrop-blur transition-colors hover:text-foreground md:flex"
          >
            <PanelLeftOpen className="h-5 w-5" />
          </button>
        )}
        <div className="flex-1 overflow-y-auto w-full h-full">{children}</div>
      </main>
      <MobileNav />
    </div>
  )
}
