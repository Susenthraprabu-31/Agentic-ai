import { Outlet, useLocation } from "react-router-dom";
import Sidebar from "./Sidebar";
import Header from "./Header";

const PAGE_TITLES: Record<string, string> = {
  "/dashboard": "Dashboard",
  "/workflow-builder": "Workflow Builder",
  "/workflows": "Workflow Management",
  "/county-sources": "County Sources",
  "/recipe-templates": "Recipe Templates",
  "/execution-history": "Execution History",
  "/execution-results": "Execution Results",
  "/security-events": "Security Events",
  "/monitoring": "Worker & Resilience Monitoring",
  "/settings": "Settings",
};

export default function AppLayout() {
  const { pathname } = useLocation();
  const title = PAGE_TITLES[pathname] ?? "Title Search Platform";

  return (
    <div className="flex h-screen overflow-hidden bg-slate-50">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0">
        <Header title={title} />
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
