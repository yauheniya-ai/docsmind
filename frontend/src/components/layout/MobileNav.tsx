import { Link, useLocation } from "wouter"
import { BookOpen, Bot, Clock, Search } from "lucide-react"
import { cn } from "@/lib/utils"

export function MobileNav() {
  const [location] = useLocation()
  const navItems = [
    { href: "/", label: "Workspace", icon: Search },
    { href: "/documents", label: "Register", icon: BookOpen },
    { href: "/activity", label: "Activity", icon: Clock },
    { href: "/agents", label: "Agents", icon: Bot },
  ]

  return (
    <div className="md:hidden border-t border-border bg-background/95 backdrop-blur-md pb-safe">
      <nav className="flex items-center justify-around px-2 py-2">
        {navItems.map((item) => {
          const isActive = item.href === "/documents" ? location.startsWith("/documents") : location === item.href
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex flex-col items-center justify-center w-16 h-12 gap-1 rounded-xl transition-colors",
                isActive ? "text-primary" : "text-muted-foreground hover:text-foreground"
              )}
            >
              <item.icon className="w-5 h-5" strokeWidth={isActive ? 2.5 : 2} />
              <span className="text-[10px] font-medium">{item.label}</span>
            </Link>
          )
        })}
      </nav>
    </div>
  )
}
