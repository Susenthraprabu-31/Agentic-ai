import { useState } from "react";
import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  GitBranch,
  Layers,
  MapPin,
  BookOpen,
  History,
  FileSearch,
  Shield,
  Settings,
  Activity,
  FileText,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface NavItem {
  label: string;
  to: string;
  icon: React.ElementType;
}

interface NavSection {
  title: string;
  items: NavItem[];
}

const navSections: NavSection[] = [
  {
    title: "Main",
    items: [
      { label: "Dashboard", to: "/dashboard", icon: LayoutDashboard },
      { label: "Workflow Builder", to: "/workflow-builder", icon: GitBranch },
      { label: "Workflow Management", to: "/workflows", icon: Layers },
    ],
  },
  {
    title: "Data",
    items: [
      { label: "County Sources", to: "/county-sources", icon: MapPin },
      { label: "Recipe Templates", to: "/recipe-templates", icon: BookOpen },
    ],
  },
  {
    title: "History",
    items: [
      { label: "Execution History", to: "/execution-history", icon: History },
      { label: "Execution Results", to: "/execution-results", icon: FileSearch },
      { label: "Security Events", to: "/security-events", icon: Shield },
    ],
  },
  {
    title: "System",
    items: [
      { label: "Worker & System Health", to: "/monitoring", icon: Activity },
      { label: "Settings", to: "/settings", icon: Settings },
    ],
  },
];

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <aside
      className={cn(
        "relative flex flex-col bg-slate-900 text-slate-100 transition-all duration-300 ease-in-out shrink-0",
        collapsed ? "w-16" : "w-64"
      )}
    >
      {/* Logo */}
      <div className="flex items-center gap-3 px-4 py-5 border-b border-slate-700">
        <div className="flex items-center justify-center w-8 h-8 rounded-md bg-blue-600 shrink-0">
          <FileText className="w-5 h-5 text-white" />
        </div>
        {!collapsed && (
          <span className="font-semibold text-base tracking-tight whitespace-nowrap">
            Title Search
          </span>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-4 px-2 space-y-6">
        {navSections.map((section) => (
          <div key={section.title}>
            {!collapsed && (
              <p className="px-2 mb-1 text-xs font-semibold uppercase tracking-wider text-slate-400">
                {section.title}
              </p>
            )}
            <ul className="space-y-0.5">
              {section.items.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-3 rounded-md px-2 py-2 text-sm font-medium transition-colors",
                        isActive
                          ? "bg-blue-600 text-white"
                          : "text-slate-300 hover:bg-slate-800 hover:text-white"
                      )
                    }
                    title={collapsed ? item.label : undefined}
                  >
                    <item.icon className="w-4 h-4 shrink-0" />
                    {!collapsed && <span>{item.label}</span>}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      {/* Collapse toggle */}
      <button
        onClick={() => setCollapsed((c) => !c)}
        className="absolute -right-3 top-20 flex items-center justify-center w-6 h-6 rounded-full bg-slate-700 border border-slate-600 hover:bg-slate-600 transition-colors z-10"
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
      >
        {collapsed ? (
          <ChevronRight className="w-3 h-3 text-slate-300" />
        ) : (
          <ChevronLeft className="w-3 h-3 text-slate-300" />
        )}
      </button>
    </aside>
  );
}
