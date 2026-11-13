import { useLocation } from "wouter"

export function NotFound() {
  const [location] = useLocation()

  return (
    <div className="flex flex-col items-center justify-center min-h-[100dvh] bg-background text-foreground p-8">
      <h1 className="text-5xl font-serif font-bold text-primary mb-4">404</h1>
      <p className="text-xl text-muted-foreground mb-8">
        The page{" "}
        <code className="bg-muted px-1.5 py-0.5 rounded text-sm text-foreground">{location}</code> could
        not be found.
      </p>
      <a
        href="/"
        className="px-6 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors font-medium"
      >
        Return to Workspace
      </a>
    </div>
  )
}
export default NotFound
