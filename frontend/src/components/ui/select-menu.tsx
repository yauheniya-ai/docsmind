import { useEffect, useId, useRef, useState } from "react"
import { Check, ChevronDown } from "lucide-react"
import { cn } from "@/lib/utils"

export interface SelectMenuOption {
  value: string
  label: string
  description?: string
}

interface SelectMenuProps {
  value: string
  onChange: (value: string) => void
  options: SelectMenuOption[]
  /** Open above the trigger instead of below (for controls docked to the bottom of the screen). */
  placement?: "top" | "bottom"
  className?: string
  triggerClassName?: string
  "aria-label"?: string
}

/** Custom (non-native) dropdown with keyboard support: Enter/Space/Arrows to open and
 * move, Enter to select, Escape to close, click outside to dismiss. */
export function SelectMenu({
  value,
  onChange,
  options,
  placement = "bottom",
  className,
  triggerClassName,
  "aria-label": ariaLabel,
}: SelectMenuProps) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const rootRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const listId = useId()

  const selectedIndex = Math.max(
    0,
    options.findIndex((o) => o.value === value),
  )
  const selected = options[selectedIndex]

  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: MouseEvent | TouchEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", onPointerDown)
    document.addEventListener("touchstart", onPointerDown)
    return () => {
      document.removeEventListener("mousedown", onPointerDown)
      document.removeEventListener("touchstart", onPointerDown)
    }
  }, [open])

  useEffect(() => {
    if (open) listRef.current?.children[active]?.scrollIntoView({ block: "nearest" })
  }, [open, active])

  const openMenu = () => {
    setActive(selectedIndex)
    setOpen(true)
  }

  const choose = (index: number) => {
    const option = options[index]
    if (option) onChange(option.value)
    setOpen(false)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter" || e.key === " ") {
        e.preventDefault()
        openMenu()
      }
      return
    }
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault()
        setActive((i) => Math.min(i + 1, options.length - 1))
        break
      case "ArrowUp":
        e.preventDefault()
        setActive((i) => Math.max(i - 1, 0))
        break
      case "Home":
        e.preventDefault()
        setActive(0)
        break
      case "End":
        e.preventDefault()
        setActive(options.length - 1)
        break
      case "Enter":
      case " ":
        e.preventDefault()
        choose(active)
        break
      case "Escape":
        e.preventDefault()
        setOpen(false)
        break
      case "Tab":
        setOpen(false)
        break
    }
  }

  return (
    <div ref={rootRef} className={cn("relative", className)}>
      <button
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-label={ariaLabel}
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={onKeyDown}
        className={cn(
          "flex w-full items-center justify-between gap-3 px-4 py-3 text-left text-sm md:text-base font-medium text-foreground focus:outline-none",
          triggerClassName,
        )}
      >
        <span className="truncate">{selected?.label}</span>
        <ChevronDown
          className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")}
        />
      </button>

      {open && (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          className={cn(
            "absolute left-0 right-0 z-50 max-h-72 overflow-y-auto rounded-xl border border-border/80 bg-card p-1 shadow-lg animate-fade-in",
            placement === "top" ? "bottom-full mb-2" : "top-full mt-2",
          )}
        >
          {options.map((option, index) => {
            const isSelected = option.value === value
            return (
              <li
                key={option.value}
                role="option"
                aria-selected={isSelected}
                onMouseEnter={() => setActive(index)}
                onClick={() => choose(index)}
                className={cn(
                  "flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm",
                  index === active ? "bg-secondary/60" : "",
                  isSelected ? "text-foreground font-medium" : "text-foreground/80",
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{option.label}</span>
                  {option.description && (
                    <span className="block truncate text-xs text-muted-foreground">{option.description}</span>
                  )}
                </span>
                {isSelected && <Check className="h-4 w-4 shrink-0 text-primary" />}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
