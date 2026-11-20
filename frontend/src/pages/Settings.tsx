import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Eye, EyeOff, Info, Globe, Upload, RefreshCw, CheckCircle2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { browserToolsApi, type MiamiDadeAuthStatus } from "@/api/browserTools";

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
  const [authStatus, setAuthStatus] = useState<MiamiDadeAuthStatus | null>(null);
  const [authLoading, setAuthLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState<string | null>(null);

  const refreshAuthStatus = useCallback(async () => {
    setAuthLoading(true);
    try {
      setAuthStatus(await browserToolsApi.getMiamiDadeAuthStatus());
    } catch {
      setAuthStatus(null);
    } finally {
      setAuthLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshAuthStatus();
  }, [refreshAuthStatus]);

  const onAuthFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setUploading(true);
    setUploadMsg(null);
    try {
      const res = await browserToolsApi.uploadMiamiDadeAuth(file);
      setUploadMsg(res.message);
      await refreshAuthStatus();
    } catch {
      setUploadMsg("Upload failed. Use auth.json from the local Chrome helper script.");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Settings</h2>
        <p className="text-sm text-muted-foreground mt-0.5">
          Configure platform preferences and integrations
        </p>
      </div>

      <div className="flex items-start gap-2 rounded-lg bg-blue-50 border border-blue-200 px-4 py-3 text-sm text-blue-800">
        <Info className="w-4 h-4 shrink-0 mt-0.5" />
        <span>Full settings management (save/update) will be implemented in Phase 2.</span>
      </div>

      <Card id="cloudflare-browser">
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Globe className="w-4 h-4 text-slate-700" />
            Custom tax browser profile (Miami-Dade / Cloudflare)
          </CardTitle>
          <CardDescription>
            Creates a persistent Chrome profile with customizable policies; workers import auth.json (EC2 cannot
            open an interactive browser).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          <ol className="list-decimal list-inside space-y-2 text-slate-700">
            <li>
              On your <strong>Windows PC</strong> (with Google Chrome installed), run:
              <pre className="mt-1.5 p-3 rounded-md bg-slate-900 text-slate-100 text-xs overflow-x-auto">
                {`cd backend\n.\\open_miami_dade_tax_browser.ps1`}
              </pre>
            </li>
            <li>Complete Cloudflare in the Chrome window that opens.</li>
            <li>
              When the tax search page loads, the script saves a profile under{" "}
              <code className="text-xs">storage/browser_profiles/miami_dade_tax</code> and exports{" "}
              <code className="text-xs">backend/auth.json</code>.
            </li>
            <li>
              Upload <code className="text-xs">auth.json</code> below (or scp to EC2 and{" "}
              <code className="text-xs">docker compose restart celery_worker</code>).
            </li>
            <li>
              Tune launch policies in <code className="text-xs">backend/config/tax_browser_policy.json</code> (args,
              user agent, viewport).
            </li>
          </ol>

          <div className="flex items-center gap-2 flex-wrap">
            <Button type="button" variant="outline" size="sm" onClick={() => refreshAuthStatus()} disabled={authLoading}>
              <RefreshCw className={`w-4 h-4 mr-1.5 ${authLoading ? "animate-spin" : ""}`} />
              Refresh status
            </Button>
            <label className="inline-flex">
              <input type="file" accept=".json,application/json" className="sr-only" onChange={onAuthFile} disabled={uploading} />
              <Button type="button" variant="default" size="sm" disabled={uploading} asChild>
                <span>
                  <Upload className="w-4 h-4 mr-1.5" />
                  {uploading ? "Uploading…" : "Upload auth.json"}
                </span>
              </Button>
            </label>
          </div>

          {authStatus && (
            <div
              className={`rounded-lg border px-3 py-2.5 ${
                authStatus.configured ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"
              }`}
            >
              <p className="font-medium flex items-center gap-1.5">
                {authStatus.configured ? (
                  <CheckCircle2 className="w-4 h-4 text-emerald-700" />
                ) : null}
                {authStatus.message}
              </p>
              {authStatus.configured && (
                <p className="text-xs text-muted-foreground mt-1">
                  {authStatus.file_name}: {authStatus.cookie_count} cookies, {authStatus.origin_count} origins
                  {authStatus.auth_updated_at ? ` · auth ${authStatus.auth_updated_at}` : ""}
                </p>
              )}
              {authStatus?.profile_configured && (
                <p className="text-xs text-muted-foreground mt-1">
                  Worker profile dir: {authStatus.profile_path_hint || "—"}
                  {authStatus.profile_updated_at ? ` · ${authStatus.profile_updated_at}` : ""}
                </p>
              )}
              {authStatus?.policy_file && (
                <p className="text-xs text-muted-foreground">Policy file on worker: {authStatus.policy_file}</p>
              )}
            </div>
          )}
          {uploadMsg && <p className="text-xs text-slate-600">{uploadMsg}</p>}
        </CardContent>
      </Card>

      <Separator />

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
          <p className="text-xs text-muted-foreground">
            Stuck on Cloudflare during a run?{" "}
            <Link to="/settings#cloudflare-browser" className="text-blue-600 underline">
              Create a browser session
            </Link>
            .
          </p>
        </CardContent>
      </Card>

      <div className="flex justify-end">
        <Button disabled>Save Settings</Button>
      </div>
    </div>
  );
}