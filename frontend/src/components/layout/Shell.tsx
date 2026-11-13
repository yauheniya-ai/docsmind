import { type ReactNode } from "react"
import { Sidebar } from "./Sidebar"
import { MobileNav } from "./MobileNav"

export function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col md:flex-row h-[100dvh] w-full bg-background text-foreground overflow-hidden">
      <div className="hidden md:flex">
        <Sidebar />
      </div>
      <main className="flex-1 flex flex-col min-w-0 relative">
        <div className="flex-1 overflow-y-auto w-full h-full">{children}</div>
      </main>
      <MobileNav />
    </div>
  )
}
