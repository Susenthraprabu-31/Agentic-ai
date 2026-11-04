import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  CheckCircle2,
  GitBranch,
  Layers,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  Sliders,
  Trash2,
  X,
  XCircle,
} from "lucide-react";
import { workflowsApi } from "@/api/workflows";
import { countySourcesApi } from "@/api/countySources";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { CountySource, Workflow } from "@/types";

export default function WorkflowManagement() {
  const navigate = useNavigate();

  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [activeFilter, setActiveFilter] = useState<string>("all");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // New Workflow Modal
  const [isNewModalOpen, setIsNewModalOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newCountyId, setNewCountyId] = useState("");
  const [counties, setCounties] = useState<CountySource[]>([]);
  const [submitting, setSubmitting] = useState(false);

  const fetchWorkflows = async () => {
    try {
      setLoading(true);
      const res = await workflowsApi.list({
        search: search || undefined,
        is_active: activeFilter === "all" ? undefined : activeFilter === "active",
        limit: 100,
      });
      setWorkflows(res.items || []);
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to load workflows.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchWorkflows();
    const loadCounties = async () => {
      try {
        const cRes = await countySourcesApi.list({ size: 100 });
        setCounties(cRes.items || []);
      } catch (e) {
        console.error(e);
      }
    };
    loadCounties();
  }, []);

  useEffect(() => {
    fetchWorkflows();
  }, [search, activeFilter]);

  const handleCreateWorkflow = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName) return;
    try {
      setSubmitting(true);
      const wf = await workflowsApi.create({
        name: newName,
        description: newDesc || undefined,
        county_source_id: newCountyId || undefined,
      });
      setIsNewModalOpen(false);
      setNewName("");
      setNewDesc("");
      navigate(`/workflow-builder?id=${wf.id}`);
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to create workflow.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteWorkflow = async (id: string) => {
    if (!confirm("Are you sure you want to delete this workflow and all its versions?")) return;
    try {
      setLoading(true);
      await workflowsApi.delete(id);
      setSuccessMsg("Workflow deleted.");
      fetchWorkflows();
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to delete workflow.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-slate-900">
            Workflow Management
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            Visual Title Search workflows, DAG topologies, independent branches, and versioning.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => setIsNewModalOpen(true)}>
            <Plus className="w-4 h-4 mr-1.5" />
            New Workflow
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => navigate("/workflow-builder")}
          >
            <Sliders className="w-4 h-4 mr-1.5 text-blue-600" />
            Open Builder Canvas
          </Button>
        </div>
      </div>

      {/* Global Alerts */}
      {errorMsg && (
        <div className="p-3 text-sm bg-rose-50 border border-rose-200 text-rose-800 rounded-md flex items-center justify-between">
          <div className="flex items-center gap-2">
            <XCircle className="w-4 h-4 text-rose-600 shrink-0" />
            <span>{errorMsg}</span>
          </div>
          <button onClick={() => setErrorMsg(null)} className="text-rose-500 hover:text-rose-700">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {successMsg && (
        <div className="p-3 text-sm bg-emerald-50 border border-emerald-200 text-emerald-800 rounded-md flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
            <span>{successMsg}</span>
          </div>
          <button onClick={() => setSuccessMsg(null)} className="text-emerald-500 hover:text-emerald-700">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Filters Bar */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-3 bg-white p-3 border rounded-lg shadow-sm">
        <div className="relative w-full sm:w-80">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search workflows by name..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8"
          />
        </div>

        <div className="flex items-center gap-2 w-full sm:w-auto">
          <select
            value={activeFilter}
            onChange={(e) => setActiveFilter(e.target.value)}
            className="text-xs border rounded-md px-3 py-2 bg-white text-slate-700 border-slate-300"
          >
            <option value="all">All Statuses</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </select>

          <Button variant="outline" size="sm" onClick={fetchWorkflows} disabled={loading}>
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          </Button>
        </div>
      </div>

      {/* Workflows Table */}
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Workflow Name</TableHead>
                <TableHead>Target County</TableHead>
                <TableHead>Current Version</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Created</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center py-12">
                    <Loader2 className="w-6 h-6 animate-spin mx-auto text-blue-600" />
                  </TableCell>
                </TableRow>
              ) : workflows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center py-14 text-muted-foreground">
                    <Layers className="w-10 h-10 mb-3 mx-auto opacity-25" />
                    <p className="font-medium text-sm">No workflows found</p>
                    <p className="text-xs mt-1">Create your first visual workflow with the Workflow Builder</p>
                  </TableCell>
                </TableRow>
              ) : (
                workflows.map((wf) => {
                  const county = counties.find((c) => c.id === wf.county_source_id);
                  return (
                    <TableRow key={wf.id}>
                      <TableCell>
                        <div className="font-medium text-slate-900">{wf.name}</div>
                        {wf.description && (
                          <div className="text-xs text-muted-foreground line-clamp-1">
                            {wf.description}
                          </div>
                        )}
                      </TableCell>
                      <TableCell className="text-sm">
                        {county ? `${county.name}, ${county.state}` : "Generic (All Counties)"}
                      </TableCell>
                      <TableCell>
                        <Badge className="bg-blue-100 text-blue-800 text-xs border border-blue-200 flex items-center gap-1 w-fit">
                          <GitBranch className="w-3 h-3" />
                          v{wf.current_version_number || 1}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant={wf.is_active ? "success" : "secondary"}>
                          {wf.is_active ? "Active" : "Inactive"}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {new Date(wf.created_at).toLocaleDateString()}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-1.5">
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => navigate(`/workflow-builder?id=${wf.id}`)}
                            className="text-xs h-8"
                          >
                            <Sliders className="w-3.5 h-3.5 mr-1 text-blue-600" />
                            Open in Builder
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleDeleteWorkflow(wf.id)}
                            className="text-rose-600 hover:text-rose-700 hover:bg-rose-50 h-8"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* New Workflow Modal */}
      {isNewModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6 space-y-4">
            <div className="flex items-center justify-between border-b pb-3">
              <h3 className="text-lg font-semibold text-slate-900">Create Workflow</h3>
              <button
                onClick={() => setIsNewModalOpen(false)}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleCreateWorkflow} className="space-y-4 text-xs">
              <div>
                <Label className="text-xs">Workflow Name *</Label>
                <Input
                  required
                  placeholder="e.g. Orange County FL Full Title Search"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  className="mt-1 text-xs"
                />
              </div>

              <div>
                <Label className="text-xs">Target County (Optional)</Label>
                <select
                  value={newCountyId}
                  onChange={(e) => setNewCountyId(e.target.value)}
                  className="w-full mt-1 border rounded-md px-3 py-2 bg-white text-slate-800 border-slate-300"
                >
                  <option value="">-- Generic (Any County) --</option>
                  {counties.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}, {c.state}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <Label className="text-xs">Description</Label>
                <textarea
                  placeholder="Description of the title search automation workflow"
                  value={newDesc}
                  onChange={(e) => setNewDesc(e.target.value)}
                  className="w-full mt-1 border rounded-md p-2 text-xs border-slate-300"
                  rows={3}
                />
              </div>

              <div className="flex justify-end gap-2 pt-3 border-t">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setIsNewModalOpen(false)}
                >
                  Cancel
                </Button>
                <Button type="submit" size="sm" disabled={submitting}>
                  {submitting && <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" />}
                  Create & Launch Builder
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
