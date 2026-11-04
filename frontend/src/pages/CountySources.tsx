import { useEffect, useState } from "react";
import {
  ExternalLink,
  Loader2,
  MapPin,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  Trash2,
  X,
} from "lucide-react";
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
import type { ConfigStatus, CountySource, SourceConfiguration, SourceType } from "@/types";

const US_STATES = [
  "All States",
  "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
  "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
  "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
  "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
  "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
];

const STATUS_FILTERS = ["All Statuses", "active", "inactive", "pending"];

function configStatusBadge(status: ConfigStatus | string) {
  switch (status) {
    case "configured":
      return <Badge className="bg-emerald-600 hover:bg-emerald-700 text-white">Configured</Badge>;
    case "partial":
      return <Badge className="bg-amber-500 hover:bg-amber-600 text-white">Partial</Badge>;
    default:
      return <Badge variant="secondary">Unconfigured</Badge>;
  }
}

export default function CountySources() {
  const [sources, setSources] = useState<CountySource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Filters & Pagination
  const [search, setSearch] = useState("");
  const [selectedState, setSelectedState] = useState("All States");
  const [selectedStatus, setSelectedStatus] = useState("All Statuses");
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);

  // Modals
  const [isAddEditOpen, setIsAddEditOpen] = useState(false);
  const [editingSource, setEditingSource] = useState<CountySource | null>(null);

  const [selectedSourceForDetails, setSelectedSourceForDetails] = useState<CountySource | null>(null);
  const [activeTab, setActiveTab] = useState<"overview" | "assessor" | "tax">("overview");

  const [isConfigModalOpen, setIsConfigModalOpen] = useState(false);
  const [editingConfigType, setEditingConfigType] = useState<SourceType>("assessor");
  const [editingConfig, setEditingConfig] = useState<SourceConfiguration | null>(null);

  const [deleteCandidate, setDeleteCandidate] = useState<CountySource | null>(null);
  const [deleteConfigCandidate, setDeleteConfigCandidate] = useState<SourceConfiguration | null>(null);

  // Form states: Source Add/Edit
  const [formName, setFormName] = useState("");
  const [formState, setFormState] = useState("FL");
  const [formCounty, setFormCounty] = useState("");
  const [formCountry, setFormCountry] = useState("US");
  const [formAssessorUrl, setFormAssessorUrl] = useState("");
  const [formTaxUrl, setFormTaxUrl] = useState("");
  const [formDescription, setFormDescription] = useState("");
  const [formIsActive, setFormIsActive] = useState(true);
  const [formSubmitting, setFormSubmitting] = useState(false);

  // Form states: Configuration Add/Edit
  const [cfgWebsiteUrl, setCfgWebsiteUrl] = useState("");
  const [cfgSearchPageUrl, setCfgSearchPageUrl] = useState("");
  const [cfgSearchMethod, setCfgSearchMethod] = useState("FORM_SUBMIT");
  const [cfgSearchFieldsJson, setCfgSearchFieldsJson] = useState('{\n  "parcel_id": {"selector": "#parcel-input"}\n}');
  const [cfgFieldMappingsJson, setCfgFieldMappingsJson] = useState('{\n  "assessed_value": ".total-val"\n}');
  const [cfgStatus, setCfgStatus] = useState("draft");
  const [cfgSubmitting, setCfgSubmitting] = useState(false);

  // Fetch sources
  const fetchSources = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await countySourcesApi.list({
        page,
        size: 15,
        search: search.trim() || undefined,
        state: selectedState !== "All States" ? selectedState : undefined,
        status: selectedStatus !== "All Statuses" ? selectedStatus : undefined,
      });
      setSources(res.items);
      setTotalPages(res.pages);
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error).message ??
        "Failed to load county sources";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSources();
  }, [page, selectedState, selectedStatus]);

  // Debounced/instant search
  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    fetchSources();
  };

  // Open Create Source Modal
  const handleOpenCreate = () => {
    setEditingSource(null);
    setFormName("");
    setFormState("FL");
    setFormCounty("");
    setFormCountry("US");
    setFormAssessorUrl("");
    setFormTaxUrl("");
    setFormDescription("");
    setFormIsActive(true);
    setIsAddEditOpen(true);
  };

  // Open Edit Source Modal
  const handleOpenEdit = (src: CountySource) => {
    setEditingSource(src);
    setFormName(src.name);
    setFormState(src.state);
    setFormCounty(src.county);
    setFormCountry(src.country || "US");
    setFormAssessorUrl(src.assessor_url || "");
    setFormTaxUrl(src.tax_url || "");
    setFormDescription(src.description || "");
    setFormIsActive(src.is_active);
    setIsAddEditOpen(true);
  };

  // Save County Source
  const handleSaveSource = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormSubmitting(true);
    setError(null);

    const payload = {
      name: formName.trim() || `${formCounty} County Portal`,
      state: formState.trim().toUpperCase(),
      county: formCounty.trim(),
      country: formCountry.trim() || "US",
      assessor_url: formAssessorUrl.trim() || null,
      tax_url: formTaxUrl.trim() || null,
      description: formDescription.trim() || null,
      is_active: formIsActive,
    };

    try {
      if (editingSource) {
        await countySourcesApi.update(editingSource.id, payload);
        setSuccessMsg(`Updated ${payload.county} County successfully.`);
      } else {
        await countySourcesApi.create(payload);
        setSuccessMsg(`Added ${payload.county} County source successfully.`);
      }
      setIsAddEditOpen(false);
      fetchSources();
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error).message ??
        "Failed to save county source";
      setError(msg);
    } finally {
      setFormSubmitting(false);
    }
  };

  // Delete Source
  const handleConfirmDeleteSource = async () => {
    if (!deleteCandidate) return;
    try {
      await countySourcesApi.delete(deleteCandidate.id);
      setSuccessMsg(`Deleted ${deleteCandidate.county} County source.`);
      setDeleteCandidate(null);
      if (selectedSourceForDetails?.id === deleteCandidate.id) {
        setSelectedSourceForDetails(null);
      }
      fetchSources();
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error).message ??
        "Failed to delete county source";
      setError(msg);
    }
  };

  // View Details & Configurations
  const handleOpenDetails = async (src: CountySource) => {
    setSelectedSourceForDetails(src);
    setActiveTab("overview");
    try {
      // Refresh single source to get latest configurations
      const detailed = await countySourcesApi.getById(src.id);
      setSelectedSourceForDetails(detailed);
    } catch {
      // use initial object
    }
  };

  // Open Configuration Editor Modal
  const handleOpenConfigModal = (type: SourceType, existingConfig?: SourceConfiguration) => {
    setEditingConfigType(type);
    setEditingConfig(existingConfig || null);

    if (existingConfig) {
      setCfgWebsiteUrl(existingConfig.website_url);
      setCfgSearchPageUrl(existingConfig.search_page_url || "");
      setCfgSearchMethod(existingConfig.search_method || "FORM_SUBMIT");
      setCfgSearchFieldsJson(JSON.stringify(existingConfig.search_fields || {}, null, 2));
      setCfgFieldMappingsJson(JSON.stringify(existingConfig.field_mappings || {}, null, 2));
      setCfgStatus(existingConfig.status || "draft");
    } else {
      const defaultUrl =
        type === "assessor"
          ? selectedSourceForDetails?.assessor_url || ""
          : selectedSourceForDetails?.tax_url || "";
      setCfgWebsiteUrl(defaultUrl);
      setCfgSearchPageUrl(defaultUrl);
      setCfgSearchMethod("FORM_SUBMIT");
      setCfgSearchFieldsJson('{\n  "parcel_id": {\n    "selector": "input[name=\'parcel\']"\n  }\n}');
      setCfgFieldMappingsJson('{\n  "assessed_value": ".val-assessed"\n}');
      setCfgStatus("draft");
    }

    setIsConfigModalOpen(true);
  };

  // Save Configuration
  const handleSaveConfig = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedSourceForDetails) return;
    setCfgSubmitting(true);
    setError(null);

    let parsedSearchFields = {};
    let parsedFieldMappings = {};
    try {
      parsedSearchFields = JSON.parse(cfgSearchFieldsJson);
      parsedFieldMappings = JSON.parse(cfgFieldMappingsJson);
    } catch {
      setError("Invalid JSON format in Selectors or Field Mappings.");
      setCfgSubmitting(false);
      return;
    }

    const payload: Partial<SourceConfiguration> = {
      source_type: editingConfigType,
      website_url: cfgWebsiteUrl.trim(),
      search_page_url: cfgSearchPageUrl.trim() || null,
      search_method: cfgSearchMethod,
      search_fields: parsedSearchFields,
      field_mappings: parsedFieldMappings,
      status: cfgStatus,
      is_active: true,
    };

    try {
      if (editingConfig) {
        await countySourcesApi.updateConfiguration(
          selectedSourceForDetails.id,
          editingConfig.id,
          payload
        );
        setSuccessMsg(`Updated ${editingConfigType} configuration successfully.`);
      } else {
        await countySourcesApi.createConfiguration(selectedSourceForDetails.id, payload);
        setSuccessMsg(`Added ${editingConfigType} configuration successfully.`);
      }

      setIsConfigModalOpen(false);
      // Reload current details and sources list
      const updated = await countySourcesApi.getById(selectedSourceForDetails.id);
      setSelectedSourceForDetails(updated);
      fetchSources();
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error).message ??
        "Failed to save configuration";
      setError(msg);
    } finally {
      setCfgSubmitting(false);
    }
  };

  // Delete Configuration
  const handleConfirmDeleteConfig = async () => {
    if (!selectedSourceForDetails || !deleteConfigCandidate) return;
    try {
      await countySourcesApi.deleteConfiguration(
        selectedSourceForDetails.id,
        deleteConfigCandidate.id
      );
      setSuccessMsg(`Deleted ${deleteConfigCandidate.source_type} configuration.`);
      setDeleteConfigCandidate(null);

      const updated = await countySourcesApi.getById(selectedSourceForDetails.id);
      setSelectedSourceForDetails(updated);
      fetchSources();
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error).message ??
        "Failed to delete configuration";
      setError(msg);
    }
  };

  const assessorConfig = selectedSourceForDetails?.configurations?.find(
    (c) => c.source_type === "assessor"
  );
  const taxConfig = selectedSourceForDetails?.configurations?.find((c) => c.source_type === "tax");

  return (
    <div className="space-y-6">
      {/* Top Banner Notifications */}
      {error && (
        <div className="flex items-center justify-between p-4 bg-red-50 border border-red-200 rounded-lg text-sm text-red-800">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-red-600 hover:text-red-900">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {successMsg && (
        <div className="flex items-center justify-between p-4 bg-emerald-50 border border-emerald-200 rounded-lg text-sm text-emerald-800">
          <span>{successMsg}</span>
          <button onClick={() => setSuccessMsg(null)} className="text-emerald-600 hover:text-emerald-900">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">County Sources</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Manage public records websites, assessor URLs, tax portals, and automation selectors.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={fetchSources} disabled={loading}>
            <RefreshCw className={`w-4 h-4 mr-1.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
          <Button size="sm" onClick={handleOpenCreate}>
            <Plus className="w-4 h-4 mr-1.5" />
            Add County
          </Button>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div className="flex flex-col sm:flex-row gap-3">
        <form onSubmit={handleSearchSubmit} className="relative flex-1">
          <Search className="absolute left-2.5 top-2.5 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="Search by county name, state, or portal..."
            className="pl-8"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </form>
        <select
          aria-label="Filter by State"
          className="h-9 rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring"
          value={selectedState}
          onChange={(e) => {
            setSelectedState(e.target.value);
            setPage(1);
          }}
        >
          {US_STATES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by Status"
          className="h-9 rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring capitalize"
          value={selectedStatus}
          onChange={(e) => {
            setSelectedStatus(e.target.value);
            setPage(1);
          }}
        >
          {STATUS_FILTERS.map((st) => (
            <option key={st} value={st}>
              {st}
            </option>
          ))}
        </select>
      </div>

      {/* County Sources Table */}
      <Card>
        <CardContent className="p-0">
          {loading ? (
            <div className="flex flex-col items-center justify-center py-20 text-muted-foreground gap-3">
              <Loader2 className="w-8 h-8 text-blue-600 animate-spin" />
              <p className="text-sm">Loading county sources...</p>
            </div>
          ) : sources.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center text-muted-foreground">
              <MapPin className="w-12 h-12 mb-3 opacity-30 text-blue-600" />
              <h3 className="font-semibold text-base text-slate-800">No county sources found</h3>
              <p className="text-xs text-muted-foreground mt-1 max-w-sm">
                Get started by adding your first county source. You can configure individual Assessor
                and Tax automation locators afterwards.
              </p>
              <Button size="sm" className="mt-4" onClick={handleOpenCreate}>
                <Plus className="w-4 h-4 mr-1.5" />
                Add County Source
              </Button>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>County / State</TableHead>
                  <TableHead>Source Name</TableHead>
                  <TableHead>Assessor Portal</TableHead>
                  <TableHead>Tax Portal</TableHead>
                  <TableHead>Config Status</TableHead>
                  <TableHead>Active</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sources.map((src) => (
                  <TableRow key={src.id} className="hover:bg-slate-50/80">
                    <TableCell className="font-medium text-slate-900">
                      <div>
                        {src.county}, {src.state}
                        <span className="text-xs text-muted-foreground block">{src.country}</span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <span className="text-sm text-slate-700">{src.name}</span>
                    </TableCell>
                    <TableCell>
                      {src.assessor_url ? (
                        <a
                          href={src.assessor_url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center text-xs text-blue-600 hover:text-blue-800 underline underline-offset-2 max-w-[180px] truncate"
                          title={src.assessor_url}
                        >
                          <ExternalLink className="w-3 h-3 mr-1 shrink-0" />
                          <span className="truncate">{src.assessor_url}</span>
                        </a>
                      ) : (
                        <span className="text-xs text-slate-400">Not set</span>
                      )}
                    </TableCell>
                    <TableCell>
                      {src.tax_url ? (
                        <a
                          href={src.tax_url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center text-xs text-blue-600 hover:text-blue-800 underline underline-offset-2 max-w-[180px] truncate"
                          title={src.tax_url}
                        >
                          <ExternalLink className="w-3 h-3 mr-1 shrink-0" />
                          <span className="truncate">{src.tax_url}</span>
                        </a>
                      ) : (
                        <span className="text-xs text-slate-400">Not set</span>
                      )}
                    </TableCell>
                    <TableCell>{configStatusBadge(src.config_status)}</TableCell>
                    <TableCell>
                      {src.is_active ? (
                        <Badge variant="outline" className="text-emerald-700 border-emerald-300 bg-emerald-50 text-xs">
                          Active
                        </Badge>
                      ) : (
                        <Badge variant="secondary" className="text-xs">
                          Inactive
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleOpenDetails(src)}
                          title="View Details & Automation Configurations"
                        >
                          <Settings2 className="w-4 h-4 text-blue-600 mr-1" />
                          <span className="text-xs hidden md:inline">Configure</span>
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleOpenEdit(src)}
                          title="Edit Source"
                        >
                          <Pencil className="w-4 h-4 text-slate-600" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => setDeleteCandidate(src)}
                          title="Delete Source"
                        >
                          <Trash2 className="w-4 h-4 text-red-500" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Pagination Controls */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground px-2">
          <span>Page {page} of {totalPages}</span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(p - 1, 1))}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => Math.min(p + 1, totalPages))}
            >
              Next
            </Button>
          </div>
        </div>
      )}

      {/* ────────────────────────────────────────────────────────────────────────── */}
      {/* MODAL: ADD / EDIT COUNTY SOURCE                                            */}
      {/* ────────────────────────────────────────────────────────────────────────── */}
      {isAddEditOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-lg rounded-xl bg-white p-6 shadow-2xl space-y-5 animate-in fade-in-50 zoom-in-95">
            <div className="flex items-center justify-between border-b pb-3">
              <h3 className="text-lg font-semibold text-slate-900">
                {editingSource ? "Edit County Source" : "Add County Source"}
              </h3>
              <button
                onClick={() => setIsAddEditOpen(false)}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleSaveSource} className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor="county">County Name *</Label>
                  <Input
                    id="county"
                    placeholder="e.g. Orange"
                    value={formCounty}
                    onChange={(e) => setFormCounty(e.target.value)}
                    required
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="state">State (2-Letter) *</Label>
                  <Input
                    id="state"
                    placeholder="FL"
                    maxLength={2}
                    value={formState}
                    onChange={(e) => setFormState(e.target.value.toUpperCase())}
                    required
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="sourceName">Display Name</Label>
                <Input
                  id="sourceName"
                  placeholder="e.g. Orange County Property Portal"
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="assessorUrl">Assessor Website URL</Label>
                <Input
                  id="assessorUrl"
                  type="url"
                  placeholder="https://www.ocpafl.org"
                  value={formAssessorUrl}
                  onChange={(e) => setFormAssessorUrl(e.target.value)}
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="taxUrl">Tax Collector Website URL</Label>
                <Input
                  id="taxUrl"
                  type="url"
                  placeholder="https://www.octaxcol.com"
                  value={formTaxUrl}
                  onChange={(e) => setFormTaxUrl(e.target.value)}
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="description">Description / Notes</Label>
                <Input
                  id="description"
                  placeholder="Optional portal information"
                  value={formDescription}
                  onChange={(e) => setFormDescription(e.target.value)}
                />
              </div>

              <div className="flex items-center gap-2 pt-1">
                <input
                  type="checkbox"
                  id="is_active"
                  checked={formIsActive}
                  onChange={(e) => setFormIsActive(e.target.checked)}
                  className="rounded border-slate-300 text-blue-600 focus:ring-blue-500 w-4 h-4"
                />
                <Label htmlFor="is_active" className="cursor-pointer font-normal text-sm">
                  Active (available for workflow execution)
                </Label>
              </div>

              <div className="flex items-center justify-end gap-2 pt-3 border-t">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setIsAddEditOpen(false)}
                  disabled={formSubmitting}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={formSubmitting}>
                  {formSubmitting ? (
                    <>
                      <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                      Saving...
                    </>
                  ) : editingSource ? (
                    "Save Changes"
                  ) : (
                    "Create Source"
                  )}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ────────────────────────────────────────────────────────────────────────── */}
      {/* MODAL: DETAILS & CONFIGURATION MANAGEMENT                                  */}
      {/* ────────────────────────────────────────────────────────────────────────── */}
      {selectedSourceForDetails && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-3xl rounded-xl bg-white shadow-2xl max-h-[90vh] flex flex-col animate-in fade-in-50 zoom-in-95">
            {/* Modal Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b">
              <div>
                <h3 className="text-xl font-bold text-slate-900">
                  {selectedSourceForDetails.county} County, {selectedSourceForDetails.state}
                </h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {selectedSourceForDetails.name} • {configStatusBadge(selectedSourceForDetails.config_status)}
                </p>
              </div>
              <button
                onClick={() => setSelectedSourceForDetails(null)}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Navigation Tabs */}
            <div className="flex border-b px-6 bg-slate-50 gap-6 text-sm font-medium">
              <button
                onClick={() => setActiveTab("overview")}
                className={`py-3 border-b-2 transition-colors ${
                  activeTab === "overview"
                    ? "border-blue-600 text-blue-600"
                    : "border-transparent text-slate-500 hover:text-slate-800"
                }`}
              >
                Overview
              </button>
              <button
                onClick={() => setActiveTab("assessor")}
                className={`py-3 border-b-2 transition-colors flex items-center gap-1.5 ${
                  activeTab === "assessor"
                    ? "border-blue-600 text-blue-600"
                    : "border-transparent text-slate-500 hover:text-slate-800"
                }`}
              >
                Assessor Configuration
                {assessorConfig && (
                  <span className="w-2 h-2 rounded-full bg-emerald-500" title="Configured" />
                )}
              </button>
              <button
                onClick={() => setActiveTab("tax")}
                className={`py-3 border-b-2 transition-colors flex items-center gap-1.5 ${
                  activeTab === "tax"
                    ? "border-blue-600 text-blue-600"
                    : "border-transparent text-slate-500 hover:text-slate-800"
                }`}
              >
                Tax Configuration
                {taxConfig && (
                  <span className="w-2 h-2 rounded-full bg-emerald-500" title="Configured" />
                )}
              </button>
            </div>

            {/* Tab Contents */}
            <div className="p-6 overflow-y-auto space-y-6 flex-1">
              {/* TAB 1: OVERVIEW */}
              {activeTab === "overview" && (
                <div className="space-y-4 text-sm">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <span className="text-muted-foreground block text-xs">County</span>
                      <span className="font-medium text-slate-900">{selectedSourceForDetails.county}</span>
                    </div>
                    <div>
                      <span className="text-muted-foreground block text-xs">State / Country</span>
                      <span className="font-medium text-slate-900">
                        {selectedSourceForDetails.state}, {selectedSourceForDetails.country}
                      </span>
                    </div>
                    <div>
                      <span className="text-muted-foreground block text-xs">Assessor URL</span>
                      {selectedSourceForDetails.assessor_url ? (
                        <a
                          href={selectedSourceForDetails.assessor_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-blue-600 hover:underline flex items-center gap-1 text-xs truncate"
                        >
                          {selectedSourceForDetails.assessor_url}
                          <ExternalLink className="w-3 h-3 shrink-0" />
                        </a>
                      ) : (
                        <span className="text-slate-400 text-xs">None configured</span>
                      )}
                    </div>
                    <div>
                      <span className="text-muted-foreground block text-xs">Tax Collector URL</span>
                      {selectedSourceForDetails.tax_url ? (
                        <a
                          href={selectedSourceForDetails.tax_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-blue-600 hover:underline flex items-center gap-1 text-xs truncate"
                        >
                          {selectedSourceForDetails.tax_url}
                          <ExternalLink className="w-3 h-3 shrink-0" />
                        </a>
                      ) : (
                        <span className="text-slate-400 text-xs">None configured</span>
                      )}
                    </div>
                  </div>

                  <div className="pt-2">
                    <span className="text-muted-foreground block text-xs mb-1">Supported Search Types</span>
                    <div className="flex gap-2 flex-wrap">
                      {(selectedSourceForDetails.supported_search_types || []).map((t) => (
                        <Badge key={t} variant="outline" className="text-xs uppercase">
                          {t}
                        </Badge>
                      ))}
                    </div>
                  </div>

                  {selectedSourceForDetails.description && (
                    <div className="pt-2">
                      <span className="text-muted-foreground block text-xs">Description</span>
                      <p className="text-slate-700 text-xs mt-0.5">{selectedSourceForDetails.description}</p>
                    </div>
                  )}

                  <div className="border-t pt-4 flex justify-between items-center text-xs text-muted-foreground">
                    <span>
                      Created: {new Date(selectedSourceForDetails.created_at).toLocaleDateString()}
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleOpenEdit(selectedSourceForDetails)}
                    >
                      <Pencil className="w-3.5 h-3.5 mr-1" />
                      Edit General Details
                    </Button>
                  </div>
                </div>
              )}

              {/* TAB 2: ASSESSOR CONFIGURATION */}
              {activeTab === "assessor" && (
                <div className="space-y-4">
                  {assessorConfig ? (
                    <div className="space-y-4 text-sm">
                      <div className="flex items-center justify-between pb-3 border-b">
                        <div>
                          <Badge className="bg-blue-600 text-white">Assessor Portal Active</Badge>
                          <span className="text-xs text-muted-foreground ml-2">
                            Method: {assessorConfig.search_method}
                          </span>
                        </div>
                        <div className="flex gap-2">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleOpenConfigModal("assessor", assessorConfig)}
                          >
                            <Pencil className="w-3.5 h-3.5 mr-1" />
                            Edit
                          </Button>
                          <Button
                            variant="destructive"
                            size="sm"
                            onClick={() => setDeleteConfigCandidate(assessorConfig)}
                          >
                            <Trash2 className="w-3.5 h-3.5 mr-1" />
                            Remove
                          </Button>
                        </div>
                      </div>

                      <div className="space-y-2">
                        <div>
                          <span className="text-xs text-muted-foreground block">Search URL</span>
                          <span className="font-mono text-xs text-slate-800 break-all">
                            {assessorConfig.search_page_url || assessorConfig.website_url}
                          </span>
                        </div>

                        <div>
                          <span className="text-xs text-muted-foreground block mb-1">
                            Field Selectors & Automation Definition (JSON)
                          </span>
                          <pre className="p-3 bg-slate-900 text-slate-100 rounded-md text-xs font-mono overflow-x-auto max-h-48">
                            {JSON.stringify(assessorConfig.search_fields, null, 2)}
                          </pre>
                        </div>

                        <div>
                          <span className="text-xs text-muted-foreground block mb-1">
                            Extracted Field Mappings (JSON)
                          </span>
                          <pre className="p-3 bg-slate-900 text-slate-100 rounded-md text-xs font-mono overflow-x-auto max-h-48">
                            {JSON.stringify(assessorConfig.field_mappings, null, 2)}
                          </pre>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="text-center py-12 border-2 border-dashed rounded-lg space-y-3">
                      <Settings2 className="w-10 h-10 text-slate-300 mx-auto" />
                      <div>
                        <h4 className="font-medium text-slate-900 text-sm">No Assessor Website Configured</h4>
                        <p className="text-xs text-muted-foreground mt-1 max-w-sm mx-auto">
                          Configure search locators and output field mappings for this county's property appraiser.
                        </p>
                      </div>
                      <Button size="sm" onClick={() => handleOpenConfigModal("assessor")}>
                        <Plus className="w-4 h-4 mr-1.5" />
                        Configure Assessor Website
                      </Button>
                    </div>
                  )}
                </div>
              )}

              {/* TAB 3: TAX CONFIGURATION */}
              {activeTab === "tax" && (
                <div className="space-y-4">
                  {taxConfig ? (
                    <div className="space-y-4 text-sm">
                      <div className="flex items-center justify-between pb-3 border-b">
                        <div>
                          <Badge className="bg-emerald-600 text-white">Tax Portal Active</Badge>
                          <span className="text-xs text-muted-foreground ml-2">
                            Method: {taxConfig.search_method}
                          </span>
                        </div>
                        <div className="flex gap-2">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleOpenConfigModal("tax", taxConfig)}
                          >
                            <Pencil className="w-3.5 h-3.5 mr-1" />
                            Edit
                          </Button>
                          <Button
                            variant="destructive"
                            size="sm"
                            onClick={() => setDeleteConfigCandidate(taxConfig)}
                          >
                            <Trash2 className="w-3.5 h-3.5 mr-1" />
                            Remove
                          </Button>
                        </div>
                      </div>

                      <div className="space-y-2">
                        <div>
                          <span className="text-xs text-muted-foreground block">Search URL</span>
                          <span className="font-mono text-xs text-slate-800 break-all">
                            {taxConfig.search_page_url || taxConfig.website_url}
                          </span>
                        </div>

                        <div>
                          <span className="text-xs text-muted-foreground block mb-1">
                            Field Selectors & Automation Definition (JSON)
                          </span>
                          <pre className="p-3 bg-slate-900 text-slate-100 rounded-md text-xs font-mono overflow-x-auto max-h-48">
                            {JSON.stringify(taxConfig.search_fields, null, 2)}
                          </pre>
                        </div>

                        <div>
                          <span className="text-xs text-muted-foreground block mb-1">
                            Extracted Field Mappings (JSON)
                          </span>
                          <pre className="p-3 bg-slate-900 text-slate-100 rounded-md text-xs font-mono overflow-x-auto max-h-48">
                            {JSON.stringify(taxConfig.field_mappings, null, 2)}
                          </pre>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="text-center py-12 border-2 border-dashed rounded-lg space-y-3">
                      <Settings2 className="w-10 h-10 text-slate-300 mx-auto" />
                      <div>
                        <h4 className="font-medium text-slate-900 text-sm">No Tax Collector Configured</h4>
                        <p className="text-xs text-muted-foreground mt-1 max-w-sm mx-auto">
                          Configure search locators and amount due mappings for this county's tax collector portal.
                        </p>
                      </div>
                      <Button size="sm" onClick={() => handleOpenConfigModal("tax")}>
                        <Plus className="w-4 h-4 mr-1.5" />
                        Configure Tax Website
                      </Button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ────────────────────────────────────────────────────────────────────────── */}
      {/* MODAL: ADD / EDIT AUTOMATION CONFIGURATION (ASSESSOR / TAX)                */}
      {/* ────────────────────────────────────────────────────────────────────────── */}
      {isConfigModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-xl rounded-xl bg-white p-6 shadow-2xl space-y-5 animate-in fade-in-50 zoom-in-95">
            <div className="flex items-center justify-between border-b pb-3">
              <h3 className="text-lg font-semibold text-slate-900 capitalize">
                {editingConfig ? "Edit" : "Configure"} {editingConfigType} Automation
              </h3>
              <button
                onClick={() => setIsConfigModalOpen(false)}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleSaveConfig} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="cfgWebsiteUrl">Portal Base URL *</Label>
                <Input
                  id="cfgWebsiteUrl"
                  type="url"
                  placeholder="https://county-portal.gov"
                  value={cfgWebsiteUrl}
                  onChange={(e) => setCfgWebsiteUrl(e.target.value)}
                  required
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor="cfgSearchPageUrl">Search Page URL</Label>
                  <Input
                    id="cfgSearchPageUrl"
                    type="url"
                    placeholder="https://county-portal.gov/search"
                    value={cfgSearchPageUrl}
                    onChange={(e) => setCfgSearchPageUrl(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="cfgSearchMethod">Interaction Method</Label>
                  <select
                    id="cfgSearchMethod"
                    className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring"
                    value={cfgSearchMethod}
                    onChange={(e) => setCfgSearchMethod(e.target.value)}
                  >
                    <option value="FORM_SUBMIT">Standard Form Submit</option>
                    <option value="SPA_INTERACTION">Single Page App (React/Angular)</option>
                    <option value="GET">Direct GET Request</option>
                    <option value="POST">Direct POST Request</option>
                  </select>
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="cfgSearchFieldsJson">Search Field Selectors (JSON)</Label>
                <textarea
                  id="cfgSearchFieldsJson"
                  rows={4}
                  className="flex w-full rounded-md border border-input bg-slate-950 text-slate-100 font-mono text-xs px-3 py-2 shadow-sm focus:outline-none focus:ring-1 focus:ring-ring"
                  value={cfgSearchFieldsJson}
                  onChange={(e) => setCfgSearchFieldsJson(e.target.value)}
                  required
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="cfgFieldMappingsJson">Extracted Field Mappings (JSON)</Label>
                <textarea
                  id="cfgFieldMappingsJson"
                  rows={4}
                  className="flex w-full rounded-md border border-input bg-slate-950 text-slate-100 font-mono text-xs px-3 py-2 shadow-sm focus:outline-none focus:ring-1 focus:ring-ring"
                  value={cfgFieldMappingsJson}
                  onChange={(e) => setCfgFieldMappingsJson(e.target.value)}
                  required
                />
              </div>

              <div className="flex items-center justify-end gap-2 pt-3 border-t">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setIsConfigModalOpen(false)}
                  disabled={cfgSubmitting}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={cfgSubmitting}>
                  {cfgSubmitting ? (
                    <>
                      <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                      Saving...
                    </>
                  ) : (
                    "Save Configuration"
                  )}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ────────────────────────────────────────────────────────────────────────── */}
      {/* DIALOG: DELETE CONFIRMATION (COUNTY SOURCE)                                */}
      {/* ────────────────────────────────────────────────────────────────────────── */}
      {deleteCandidate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-2xl space-y-4">
            <h3 className="text-lg font-semibold text-slate-900">Delete County Source?</h3>
            <p className="text-sm text-slate-600">
              Are you sure you want to delete{" "}
              <strong>
                {deleteCandidate.county} County, {deleteCandidate.state}
              </strong>
              ? This will also delete all associated Assessor and Tax configurations.
            </p>
            <div className="flex items-center justify-end gap-2 pt-2 border-t">
              <Button variant="outline" onClick={() => setDeleteCandidate(null)}>
                Cancel
              </Button>
              <Button variant="destructive" onClick={handleConfirmDeleteSource}>
                Delete Source
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* ────────────────────────────────────────────────────────────────────────── */}
      {/* DIALOG: DELETE CONFIRMATION (CONFIGURATION)                                */}
      {/* ────────────────────────────────────────────────────────────────────────── */}
      {deleteConfigCandidate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-2xl space-y-4">
            <h3 className="text-lg font-semibold text-slate-900">Remove Configuration?</h3>
            <p className="text-sm text-slate-600">
              Are you sure you want to delete the{" "}
              <strong className="capitalize">{deleteConfigCandidate.source_type}</strong> automation
              configuration for this county?
            </p>
            <div className="flex items-center justify-end gap-2 pt-2 border-t">
              <Button variant="outline" onClick={() => setDeleteConfigCandidate(null)}>
                Cancel
              </Button>
              <Button variant="destructive" onClick={handleConfirmDeleteConfig}>
                Remove Configuration
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
