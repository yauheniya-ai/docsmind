import { useEffect, useState } from "react"
import { Link, useLocation } from "wouter"
import { BookOpen, Bot, BrainCircuit, Clock, Database, PanelLeftClose, Search } from "lucide-react"
import { cn } from "@/lib/utils"
import { api, type DocumentSummary } from "@/lib/api"

export function Sidebar({ onCollapse }: { onCollapse?: () => void }) {
  const [location] = useLocation()
  const [documents, setDocuments] = useState<DocumentSummary[] | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .listDocuments()
      .then((docs) => {
        if (!cancelled) setDocuments(docs)
      })
      .catch(() => {
        if (!cancelled) setDocuments([])
      })
    return () => {
      cancelled = true
    }
  }, [location])

  const navItems = [
    { href: "/", label: "Workspace", icon: Search },
    { href: "/documents", label: "Register", icon: BookOpen },
    { href: "/activity", label: "Activity", icon: Clock },
    { href: "/agents", label: "Agents", icon: Bot },
  ]
  const collectionCount = documents ? new Set(documents.map((d) => d.collection)).size : 0

  return (
    <div className="w-64 lg:w-72 flex-shrink-0 border-r border-sidebar-border bg-sidebar text-sidebar-foreground flex flex-col h-[100dvh]">
      <div className="p-6 pb-4 flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-primary text-primary-foreground flex items-center justify-center shadow-sm">
          <BrainCircuit className="w-5 h-5" strokeWidth={2.5} />
        </div>
        <div className="flex-1">
          <h2 className="font-serif font-medium text-xl tracking-tight text-sidebar-primary leading-tight">
            DocsMind
          </h2>
        </div>
        {onCollapse && (
          <button
            type="button"
            onClick={onCollapse}
            aria-label="Collapse sidebar"
            title="Collapse sidebar"
            className="flex h-8 w-8 items-center justify-center rounded-lg text-sidebar-foreground/60 transition-colors hover:bg-sidebar-accent/50 hover:text-sidebar-foreground"
          >
            <PanelLeftClose className="h-5 w-5" />
          </button>
        )}
      </div>

      <nav className="flex-1 px-4 py-4 flex flex-col gap-1.5">
        {navItems.map((item) => {
          const isActive = item.href === "/documents" ? location.startsWith("/documents") : location === item.href
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-medium transition-all duration-200 group active:scale-[0.98]",
                isActive
                  ? "bg-sidebar-accent text-sidebar-accent-foreground shadow-sm"
                  : "text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground"
              )}
            >
              <item.icon
                className={cn(
                  "w-4 h-4 transition-transform group-hover:scale-110",
                  isActive ? "text-sidebar-accent-foreground" : "text-sidebar-foreground/50"
                )}
                strokeWidth={isActive ? 2.5 : 2}
              />
              {item.label}
            </Link>
          )
        })}
      </nav>

      {documents && (
        <div className="p-4 mt-auto pb-6">
          <div className="rounded-2xl bg-sidebar-accent/30 border border-sidebar-border/50 p-5 backdrop-blur-sm">
            <h3 className="text-[10px] font-bold text-sidebar-primary mb-4 flex items-center gap-1.5 uppercase tracking-widest">
              <Database className="w-3.5 h-3.5" />
              Database
            </h3>
            <div className="space-y-2.5 text-sm text-sidebar-foreground/80">
              <div className="flex justify-between items-center">
                <span className="font-medium text-sidebar-foreground/70">Documents</span>
                <span className="font-mono text-xs font-semibold text-sidebar-primary bg-sidebar-accent/50 px-2 py-0.5 rounded">
                  {documents.length}
                </span>
              </div>
              <div className="flex justify-between items-center">
                <span className="font-medium text-sidebar-foreground/70">Collections</span>
                <span className="font-mono text-xs font-semibold text-sidebar-primary bg-sidebar-accent/50 px-2 py-0.5 rounded">
                  {collectionCount}
                </span>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
