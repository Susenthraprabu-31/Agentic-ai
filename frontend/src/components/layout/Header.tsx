import { Bell, LogOut } from "lucide-react";
import { useAuthStore } from "@/store/authStore";
import { Button } from "@/components/ui/button";

interface HeaderProps {
  title: string;
}

export default function Header({ title }: HeaderProps) {
  const { user, logout } = useAuthStore();

  const handleLogout = async () => {
    await logout();
    window.location.href = "/login";
  };

  const initials = user?.full_name
    ? user.full_name
        .split(" ")
        .map((n) => n[0])
        .join("")
        .toUpperCase()
        .slice(0, 2)
    : user?.email?.slice(0, 2).toUpperCase() ?? "US";

  return (
    <header className="flex items-center justify-between h-14 px-6 border-b bg-white shrink-0">
      <h1 className="text-lg font-semibold text-slate-900">{title}</h1>

      <div className="flex items-center gap-2">
        {/* Notification bell */}
        <Button variant="ghost" size="icon" aria-label="Notifications">
          <Bell className="w-4 h-4 text-slate-500" />
        </Button>

        {/* User avatar */}
        <div className="flex items-center gap-2 ml-2">
          <div className="flex items-center justify-center w-8 h-8 rounded-full bg-blue-600 text-white text-xs font-semibold">
            {initials}
          </div>
          <div className="hidden sm:block text-right">
            <p className="text-sm font-medium text-slate-900 leading-none">
              {user?.full_name ?? user?.email ?? "User"}
            </p>
            {user?.email && user.full_name && (
              <p className="text-xs text-slate-500 mt-0.5">{user.email}</p>
            )}
          </div>
          <Button
            variant="ghost"
            size="icon"
            onClick={handleLogout}
            aria-label="Sign out"
            title="Sign out"
          >
            <LogOut className="w-4 h-4 text-slate-400" />
          </Button>
        </div>
      </div>
    </header>
  );
}
