import { createBrowserRouter, Navigate } from "react-router-dom";
import AppLayout from "@/components/layout/AppLayout";
import { ProtectedRoute } from "@/routes/ProtectedRoute";
import { GuestRoute } from "@/routes/GuestRoute";
import { RouteErrorBoundary } from "@/components/ui/ErrorBoundary";
import Login from "@/pages/Login";
import Register from "@/pages/Register";
import Dashboard from "@/pages/Dashboard";
import WorkflowBuilder from "@/pages/WorkflowBuilder";
import WorkflowManagement from "@/pages/WorkflowManagement";
import CountySources from "@/pages/CountySources";
import RecipeTemplates from "@/pages/RecipeTemplates";
import ExecutionHistory from "@/pages/ExecutionHistory";
import ExecutionResults from "@/pages/ExecutionResults";
import SecurityEvents from "@/pages/SecurityEvents";
import MonitoringPage from "@/pages/MonitoringPage";
import Settings from "@/pages/Settings";

export const router = createBrowserRouter([
  // Public/Guest only routes (redirect to /dashboard if logged in)
  {
    element: <GuestRoute />,
    errorElement: <RouteErrorBoundary />,
    children: [
      { path: "/login", element: <Login /> },
      { path: "/register", element: <Register /> },
    ],
  },

  // Authenticated/Protected routes (redirect to /login if not authenticated)
  {
    element: <ProtectedRoute />,
    errorElement: <RouteErrorBoundary />,
    children: [
      {
        path: "/",
        element: <AppLayout />,
        errorElement: <RouteErrorBoundary />,
        children: [
          { index: true, element: <Navigate to="/dashboard" replace /> },
          { path: "dashboard", element: <Dashboard /> },
          { path: "workflow-builder", element: <WorkflowBuilder /> },
          { path: "workflows", element: <WorkflowManagement /> },
          { path: "county-sources", element: <CountySources /> },
          { path: "recipe-templates", element: <RecipeTemplates /> },
          { path: "execution-history", element: <ExecutionHistory /> },
          { path: "execution-results", element: <ExecutionResults />, errorElement: <RouteErrorBoundary /> },
          { path: "security-events", element: <SecurityEvents /> },
          { path: "monitoring", element: <MonitoringPage /> },
          { path: "settings", element: <Settings /> },
        ],
      },
    ],
  },

  // Catch-all fallback
  { path: "*", element: <Navigate to="/dashboard" replace /> },
]);
