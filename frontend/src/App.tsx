import { type ReactNode } from "react"
import { ErrorBoundary } from "@/components/error-boundary"
import NotFound from "@/pages/not-found"
import { Route, Switch, useLocation, Router as WouterRouter } from "wouter"
import { Shell } from "@/components/layout/Shell"
import { Workspace } from "@/pages/Workspace"
import { Documents } from "@/pages/Documents"
import { DocumentDetails } from "@/pages/DocumentDetails"
import { Activity } from "@/pages/Activity"
import { Agents } from "@/pages/Agents"

function Router() {
  return (
    <Shell>
      <RoutedErrorBoundary>
        <Switch>
          <Route path="/" component={Workspace} />
          <Route path="/documents/:id" component={DocumentDetails} />
          <Route path="/documents" component={Documents} />
          <Route path="/activity" component={Activity} />
          <Route path="/agents" component={Agents} />
          <Route component={NotFound} />
        </Switch>
      </RoutedErrorBoundary>
    </Shell>
  )
}

function RoutedErrorBoundary({ children }: { children: ReactNode }) {
  const [location] = useLocation()
  return <ErrorBoundary resetKey={location}>{children}</ErrorBoundary>
}

function App() {
  return (
    <WouterRouter>
      <Router />
    </WouterRouter>
  )
}

export default App
