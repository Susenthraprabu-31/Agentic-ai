import { useState } from "react";
import { Eye, EyeOff, Info } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";

function MaskedInput({ id, label, placeholder }: { id: string; label: string; placeholder?: string }) {
  const [show, setShow] = useState(false);
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <div className="relative">
        <Input
          id={id}
          type={show ? "text" : "password"}
          placeholder={placeholder ?? "sk-…"}
          className="pr-10"
        />
        <button
          type="button"
          onClick={() => setShow((s) => !s)}
          className="absolute right-2.5 top-2.5 text-muted-foreground hover:text-slate-700"
          aria-label={show ? "Hide key" : "Show key"}
        >
          {show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
        </button>
      </div>
    </div>
  );
}

export default function Settings() {
  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Settings</h2>
        <p className="text-sm text-muted-foreground mt-0.5">
          Configure platform preferences and integrations
        </p>
      </div>

      {/* Phase 2 Notice */}
      <div className="flex items-start gap-2 rounded-lg bg-blue-50 border border-blue-200 px-4 py-3 text-sm text-blue-800">
        <Info className="w-4 h-4 shrink-0 mt-0.5" />
        <span>Full settings management (save/update) will be implemented in Phase 2.</span>
      </div>

      {/* General */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">General</CardTitle>
          <CardDescription>Basic platform configuration</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="platform-name">Platform Name</Label>
            <Input id="platform-name" defaultValue="Title Search Platform" />
          </div>
          <div className="space-y-2">
            <Label htmlFor="env">Environment</Label>
            <select
              id="env"
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              defaultValue="development"
            >
              <option value="development">Development</option>
              <option value="staging">Staging</option>
              <option value="production">Production</option>
            </select>
          </div>
        </CardContent>
      </Card>

      <Separator />

      {/* API Keys */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">API Keys</CardTitle>
          <CardDescription>External service credentials</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <MaskedInput id="openai-key" label="OpenAI API Key" placeholder="sk-…" />
          <MaskedInput id="mistral-key" label="Mistral API Key" placeholder="mis-…" />
        </CardContent>
      </Card>

      <Separator />

      {/* Notifications */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Notifications</CardTitle>
          <CardDescription>Alert and notification preferences</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="alert-email">Alert Email</Label>
            <Input id="alert-email" type="email" placeholder="alerts@example.com" />
          </div>
          <div className="flex items-center gap-3">
            <input
              id="notify-failures"
              type="checkbox"
              className="h-4 w-4 rounded border-gray-300"
              defaultChecked
            />
            <Label htmlFor="notify-failures" className="cursor-pointer">
              Notify on workflow failures
            </Label>
          </div>
          <div className="flex items-center gap-3">
            <input
              id="notify-security"
              type="checkbox"
              className="h-4 w-4 rounded border-gray-300"
              defaultChecked
            />
            <Label htmlFor="notify-security" className="cursor-pointer">
              Notify on security events
            </Label>
          </div>
        </CardContent>
      </Card>

      <Separator />

      {/* System */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">System</CardTitle>
          <CardDescription>Performance and resource settings</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="concurrency">Max Concurrent Workflows</Label>
            <Input id="concurrency" type="number" defaultValue={5} min={1} max={50} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="timeout">Request Timeout (seconds)</Label>
            <Input id="timeout" type="number" defaultValue={30} min={5} max={300} />
          </div>
        </CardContent>
      </Card>

      <div className="flex justify-end">
        <Button disabled>Save Settings</Button>
      </div>
    </div>
  );
}
