import { useState } from "react";
import { Shield } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { SecurityEvent, SecurityEventType } from "@/types";

const mockEvents: SecurityEvent[] = [];

const EVENT_TYPES: SecurityEventType[] = [
  "cloudflare",
  "captcha",
  "turnstile",
  "rate_limit",
  "blocked",
  "other",
];

type BadgeVariant = "orange" | "destructive" | "purple" | "warning" | "gray" | "secondary";

function eventVariant(type: SecurityEventType): BadgeVariant {
  switch (type) {
    case "cloudflare": return "orange";
    case "captcha": return "destructive";
    case "turnstile": return "purple";
    case "rate_limit": return "warning";
    case "blocked": return "gray";
    default: return "secondary";
  }
}

export default function SecurityEvents() {
  const [typeFilter, setTypeFilter] = useState<"all" | SecurityEventType>("all");

  const filtered =
    typeFilter === "all" ? mockEvents : mockEvents.filter((e) => e.event_type === typeFilter);

  return (
    <div className="space-y-5">
      {/* Header */}
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Security Events</h2>
        <p className="text-sm text-muted-foreground mt-0.5">
          Detected bot challenges, CAPTCHAs, and access blocks
        </p>
      </div>

      {/* Filter by Type */}
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => setTypeFilter("all")}
          className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
            typeFilter === "all"
              ? "bg-slate-900 text-white border-slate-900"
              : "border-slate-200 text-slate-600 hover:border-slate-400"
          }`}
        >
          All
        </button>
        {EVENT_TYPES.map((t) => (
          <button
            key={t}
            onClick={() => setTypeFilter(t)}
            className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors capitalize ${
              typeFilter === t
                ? "bg-slate-900 text-white border-slate-900"
                : "border-slate-200 text-slate-600 hover:border-slate-400"
            }`}
          >
            {t.replace("_", " ")}
          </button>
        ))}
      </div>

      {/* Table */}
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Event Type</TableHead>
                <TableHead>URL</TableHead>
                <TableHead>County Source</TableHead>
                <TableHead>Detected At</TableHead>
                <TableHead>Details</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5}>
                    <div className="flex flex-col items-center justify-center py-14 text-center text-muted-foreground">
                      <Shield className="w-10 h-10 mb-3 opacity-25" />
                      <p className="font-medium text-sm">No security events</p>
                      <p className="text-xs mt-1">Events will appear when bot challenges are detected</p>
                    </div>
                  </TableCell>
                </TableRow>
              ) : (
                filtered.map((event) => (
                  <TableRow key={event.id}>
                    <TableCell>
                      <Badge variant={eventVariant(event.event_type)} className="capitalize text-xs">
                        {event.event_type.replace("_", " ")}
                      </Badge>
                    </TableCell>
                    <TableCell
                      className="text-xs text-muted-foreground max-w-[200px] truncate"
                      title={event.url}
                    >
                      {event.url}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {event.county_source_id ?? "—"}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {new Date(event.detected_at).toLocaleString()}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {JSON.stringify(event.details).slice(0, 50)}…
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
