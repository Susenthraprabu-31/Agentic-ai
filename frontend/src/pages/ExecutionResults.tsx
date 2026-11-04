import { useEffect, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Building2,
  Calendar,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Code2,
  Columns,
  Copy,
  DollarSign,
  Download,
  ExternalLink,
  Eye,
  FileCheck,
  FileSearch,
  FileSpreadsheet,
  FileText,
  GitMerge,
  Globe,
  History,
  Home,
  Info,
  Landmark,
  Layers,
  Loader2,
  MinusCircle,
  MonitorPlay,
  Receipt,
  RefreshCw,
  RotateCcw,
  Sparkles,
  StopCircle,
  Table2,
  X,
  XCircle,
} from "lucide-react";
import { executionsApi } from "@/api/executions";
import { documentsApi, type DocumentOcrAnalysisResult } from "@/api/documents";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";
import type {
  BrowserActivityResponse,
  DocumentArtifact,
  ExecutionResultsResponse,
  NodeRun,
  WorkflowRun,
} from "@/types";

function runStatusBadge(status: string) {
  const s = status.toUpperCase();
  switch (s) {
    case "COMPLETED":
      return <Badge className="bg-emerald-600 text-white font-medium">Completed</Badge>;
    case "PARTIALLY_COMPLETED":
      return <Badge className="bg-purple-600 text-white font-medium">Partially Completed</Badge>;
    case "RUNNING":
      return (
        <Badge className="bg-blue-600 text-white flex items-center gap-1 font-medium">
          <Loader2 className="w-3 h-3 animate-spin" />
          Running
        </Badge>
      );
    case "QUEUED":
    case "PENDING":
      return <Badge className="bg-amber-500 text-white font-medium">Queued in Celery</Badge>;
    case "CANCEL_REQUESTED":
      return <Badge className="bg-slate-500 text-white font-medium">Cancelling...</Badge>;
    case "CANCELLED":
      return <Badge variant="secondary">Cancelled</Badge>;
    case "FAILED":
      return <Badge variant="destructive">Failed</Badge>;
    default:
      return <Badge variant="outline">{status}</Badge>;
  }
}

function nodeStatusBadge(status: string) {
  const s = status.toUpperCase();
  switch (s) {
    case "COMPLETED":
      return <Badge className="bg-emerald-600 text-white text-[10px]">Completed</Badge>;
    case "FAILED":
      return <Badge className="bg-rose-600 text-white text-[10px]">Failed</Badge>;
    case "SKIPPED":
      return <Badge variant="outline" className="text-[10px] text-slate-500">Skipped</Badge>;
    case "CANCELLED":
      return <Badge variant="secondary" className="text-[10px]">Cancelled</Badge>;
    case "RUNNING":
      return (
        <Badge className="bg-blue-600 text-white text-[10px] flex items-center gap-1">
          <Loader2 className="w-2.5 h-2.5 animate-spin" />
          Running
        </Badge>
      );
    default:
      return <Badge variant="outline" className="text-[10px]">Pending</Badge>;
  }
}

function documentStatusBadge(status: string, isDuplicate = false) {
  if (isDuplicate) {
    return <Badge className="bg-purple-100 text-purple-800 text-[10px] border-purple-200">Duplicate</Badge>;
  }
  const s = status.toLowerCase();
  switch (s) {
    case "validated":
      return <Badge className="bg-emerald-600 text-white text-[10px]">Validated PDF</Badge>;
    case "failed":
      return <Badge variant="destructive" className="text-[10px]">Failed</Badge>;
    case "downloading":
      return (
        <Badge className="bg-blue-600 text-white text-[10px] flex items-center gap-1">
          <Loader2 className="w-2.5 h-2.5 animate-spin" />
          Downloading
        </Badge>
      );
    case "discovered":
      return <Badge className="bg-amber-500 text-white text-[10px]">Discovered</Badge>;
    default:
      return <Badge variant="outline" className="text-[10px]">{status}</Badge>;
  }
}

function branchStatusDisplay(status?: string) {
  const s = (status || "NOT_CONFIGURED").toUpperCase();
  switch (s) {
    case "SUCCEEDED":
    case "COMPLETED":
      return (
        <span className="text-emerald-600 text-sm flex items-center gap-1 font-semibold">
          <CheckCircle2 className="w-3.5 h-3.5" /> Succeeded
        </span>
      );
    case "FAILED":
      return (
        <span className="text-rose-600 text-sm flex items-center gap-1 font-semibold">
          <XCircle className="w-3.5 h-3.5" /> Failed
        </span>
      );
    case "SKIPPED":
      return (
        <span className="text-amber-600 text-sm flex items-center gap-1 font-semibold">
          <MinusCircle className="w-3.5 h-3.5" /> Skipped
        </span>
      );
    case "RUNNING":
      return (
        <span className="text-blue-600 text-sm flex items-center gap-1 font-semibold">
          <Loader2 className="w-3.5 h-3.5 animate-spin" /> Running...
        </span>
      );
    case "QUEUED":
    case "PENDING":
      return (
        <span className="text-amber-600 text-sm flex items-center gap-1 font-semibold">
          <Clock className="w-3.5 h-3.5" /> Queued
        </span>
      );
    default:
      return (
        <span className="text-slate-500 text-sm flex items-center gap-1 font-medium">
          <MinusCircle className="w-3.5 h-3.5 text-slate-400" /> Not Configured
        </span>
      );
  }
}

export function formatSafeString(val: any): string {
  if (val === null || val === undefined || val === "") return "";
  if (typeof val === "string") return val;
  if (typeof val === "number") return String(val);
  if (typeof val === "boolean") return val ? "Yes" : "No";
  if (typeof val === "object") {
    // If it's a { value, unit } or { value, units } object (common in OCR / property schemas)
    if ("value" in val && "unit" in val) {
      return `${val.value ?? ""} ${val.unit ?? ""}`.trim();
    }
    if ("value" in val && "units" in val) {
      return `${val.value ?? ""} ${val.units ?? ""}`.trim();
    }
    // If it's an array
    if (Array.isArray(val)) {
      return val.map((v) => formatSafeString(v)).filter(Boolean).join(", ");
    }
    // If it's a key-value object (like exemptions: { Homestead: 0, School: 0 })
    const entries = Object.entries(val);
    if (entries.length > 0) {
      const nonZero = entries.filter(([, v]) => v !== 0 && v !== "0" && v !== null && v !== false && v !== "");
      if (nonZero.length > 0) {
        return nonZero.map(([k, v]) => `${k}: ${formatSafeString(v)}`).join(", ");
      }
      return entries.map(([k]) => k).join(", ");
    }
    return JSON.stringify(val);
  }
  return String(val);
}

export function renderSafeValue(val: any, fallback = "—"): React.ReactNode {
  const str = formatSafeString(val);
  return str && str.trim() !== "" ? str : fallback;
}

function formatCurrency(val: any): string {
  if (val === null || val === undefined || val === "") return "—";
  if (typeof val === "object") {
    if ("amount" in val) return formatCurrency(val.amount);
    if ("value" in val) return formatCurrency(val.value);
    if ("total" in val) return formatCurrency(val.total);
    return "—";
  }
  if (typeof val === "string" && val.startsWith("$")) {
    const raw = val.replace(/[^0-9.-]/g, "");
    const n = parseFloat(raw);
    if (!isNaN(n)) return `$${n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
    return val;
  }
  const num = typeof val === "number" ? val : parseFloat(String(val).replace(/[^0-9.-]/g, ""));
  if (isNaN(num)) return formatSafeString(val) || "—";
  return `$${num.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}

function getField(obj: any, ...keys: string[]): any {
  if (!obj || typeof obj !== "object") return null;
  for (const k of keys) {
    if (obj[k] !== undefined && obj[k] !== null && String(obj[k]).trim() !== "") {
      return obj[k];
    }
  }
  const extra = obj.sections || obj.raw_data;
  if (extra && typeof extra === "object") {
    for (const k of keys) {
      if (extra[k] !== undefined && extra[k] !== null && String(extra[k]).trim() !== "") {
        return extra[k];
      }
      const lowerK = k.toLowerCase();
      for (const [sk, sv] of Object.entries(extra)) {
        if (sk.toLowerCase() === lowerK && sv !== undefined && sv !== null && String(sv).trim() !== "") {
          return sv;
        }
      }
    }
  }
  return null;
}

function DynamicTable({
  title,
  icon: Icon,
  data,
}: {
  title: string;
  icon?: any;
  data?: any[];
}) {
  if (!data || !Array.isArray(data) || data.length === 0) return null;
  const validRows = data.filter((r) => r && typeof r === "object");
  if (validRows.length === 0) return null;

  const headers = Array.from(
    new Set(validRows.flatMap((row) => Object.keys(row)))
  ).filter((k) => !k.startsWith("_") && k !== "raw_json");

  if (headers.length === 0) return null;

  return (
    <div className="space-y-2 mt-4">
      <div className="flex items-center gap-2 font-semibold text-xs text-slate-800 uppercase tracking-wider">
        {Icon && <Icon className="w-3.5 h-3.5 text-blue-600" />}
        <span>{title}</span>
        <Badge variant="outline" className="text-[10px] px-1.5 py-0 font-mono bg-slate-50 text-slate-700">
          {validRows.length} {validRows.length === 1 ? "record" : "records"}
        </Badge>
      </div>
      <div className="border border-slate-200 rounded-lg overflow-x-auto bg-white shadow-2xs max-h-72 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="bg-slate-50 sticky top-0 border-b border-slate-200 text-slate-600 font-semibold text-left">
            <tr>
              {headers.map((h) => (
                <th key={h} className="px-3 py-2 whitespace-nowrap capitalize text-[11px]">
                  {h.replace(/_/g, " ")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {validRows.map((row, idx) => (
              <tr key={idx} className="hover:bg-slate-50/80 transition-colors">
                {headers.map((h) => {
                  const val = row[h];
                  const displayVal =
                    val === null || val === undefined || val === ""
                      ? "—"
                      : typeof val === "object"
                      ? formatSafeString(val)
                      : String(val);
                  return (
                    <td key={h} className="px-3 py-2 text-slate-700 whitespace-nowrap font-mono text-[11px]">
                      {displayVal}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PropertyDetailsCard({
  data,
  isNormalized = false,
}: {
  data: Record<string, any>;
  isNormalized?: boolean;
}) {
  const [showAllFields, setShowAllFields] = useState(false);

  const parcelId = getField(data, "parcel_number", "parcel_id", "parcel", "pin", "folio");
  const ownerName = getField(data, "owner_name", "owner", "primary_owner");
  const mailingAddress = getField(data, "mailing_address", "mailing", "mail_address");
  const siteAddress = getField(data, "property_address", "site_address", "physical_address", "address");
  const propertyUse = getField(data, "property_use", "use_code", "use cd", "land_use");
  const taxingDistrict = getField(data, "taxing_district", "district", "tax_dist", "millage_code");
  const landSize = getField(data, "land_size", "acres", "acreage", "lot_size");
  const exemptions = getField(data, "exemptions", "exemption", "homestead");
  const mapNumber = getField(data, "map_number", "map_no", "map", "pom");
  const legalDesc = getField(data, "legal_description", "legal", "short_legal");

  const assessedVal = getField(data, "assessed_value", "total_assessed_value", "assessed");
  const justVal = getField(data, "just_value", "market_value", "total_just");
  const landVal = getField(data, "land_value", "total_land");
  const improvementVal = getField(data, "improvement_value", "building_value", "bldg_value");
  const taxableVal = getField(data, "taxable_value", "taxable");
  const taxYear = getField(data, "tax_year", "year") || "2024";

  const yearBuilt = getField(data, "year_built", "year_blt", "actual_year_built");
  const livingArea = getField(data, "living_area", "heated_area", "total_sq_ft", "base_sf");
  const bedrooms = getField(data, "bedrooms", "beds", "bed");
  const bathrooms = getField(data, "bathrooms", "baths", "bath");

  const assessmentHistory =
    data.assessment_history || data.values_history || data.raw_data?.assessment_history || [];
  const salesHistory = data.sales_history || data.raw_data?.sales_history || [];
  const buildings = data.buildings || data.raw_data?.buildings || [];
  const landLines = data.land_lines || data.raw_data?.land_lines || [];
  const extraFeatures = data.extra_features || data.raw_data?.extra_features || [];
  const otherTables = data.raw_data?.other_tables || data.other_tables || [];

  const allKeyValues: Record<string, any> = {};
  const knownKeys = new Set([
    "parcel_number", "parcel_id", "parcel", "owner_name", "owner",
    "mailing_address", "property_address", "site_address", "address",
    "property_use", "taxing_district", "land_size", "exemptions",
    "map_number", "legal_description", "assessed_value", "total_assessed_value",
    "just_value", "market_value", "land_value", "improvement_value",
    "building_value", "taxable_value", "tax_year", "year_built",
    "living_area", "bedrooms", "bathrooms", "assessment_history",
    "values_history", "sales_history", "buildings", "land_lines",
    "extra_features", "other_tables", "raw_data", "sections", "raw_json",
    "_adapter", "_mock", "_records", "tax_status", "tax_amount_due",
    "is_delinquent", "is_partial", "missing_sources", "source_errors"
  ]);

  if (data.sections && typeof data.sections === "object") {
    Object.entries(data.sections).forEach(([k, v]) => {
      if (!knownKeys.has(k.toLowerCase()) && v !== null && v !== undefined && String(v).trim() !== "") {
        allKeyValues[k] = v;
      }
    });
  }

  if (data.raw_data && typeof data.raw_data === "object") {
    Object.entries(data.raw_data).forEach(([k, v]) => {
      if (!knownKeys.has(k.toLowerCase()) && typeof v !== "object" && v !== null && v !== undefined && String(v).trim() !== "") {
        allKeyValues[k] = v;
      }
    });
  }

  Object.entries(data).forEach(([k, v]) => {
    if (!knownKeys.has(k.toLowerCase()) && typeof v !== "object" && v !== null && v !== undefined && String(v).trim() !== "") {
      allKeyValues[k] = v;
    }
  });

  const additionalFieldCount = Object.keys(allKeyValues).length;

  return (
    <div className="space-y-4">
      {/* 2-Column Grid: Property Identification & Valuation */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
        {/* Card 1: Owner & Property Information */}
        <div className="p-4 border rounded-lg bg-white space-y-2.5 shadow-2xs">
          <div className="flex items-center gap-1.5 pb-2 border-b font-bold text-slate-800 text-xs">
            <Home className="w-3.5 h-3.5 text-blue-600" />
            <span>Owner & Property Information</span>
          </div>

          <div className="flex justify-between py-1 border-b border-slate-100 gap-2">
            <span className="text-muted-foreground whitespace-nowrap">Parcel ID:</span>
            <span className="font-mono font-bold text-slate-900 select-all">{renderSafeValue(parcelId)}</span>
          </div>

          <div className="flex justify-between py-1 border-b border-slate-100 gap-2">
            <span className="text-muted-foreground whitespace-nowrap">Owner Name:</span>
            <span className="font-semibold text-slate-900 text-right">{renderSafeValue(ownerName)}</span>
          </div>

          <div className="flex justify-between py-1 border-b border-slate-100 gap-2">
            <span className="text-muted-foreground whitespace-nowrap">Mailing Address:</span>
            <span className="text-slate-800 text-right font-medium max-w-xs">{renderSafeValue(mailingAddress)}</span>
          </div>

          <div className="flex justify-between py-1 border-b border-slate-100 gap-2">
            <span className="text-muted-foreground whitespace-nowrap">Site / Situs Address:</span>
            <span className="text-slate-900 text-right font-semibold max-w-xs">{renderSafeValue(siteAddress)}</span>
          </div>

          <div className="flex justify-between py-1 border-b border-slate-100 gap-2">
            <span className="text-muted-foreground whitespace-nowrap">Property Use:</span>
            <span className="text-slate-800 text-right font-medium">{renderSafeValue(propertyUse)}</span>
          </div>

          <div className="flex justify-between py-1 border-b border-slate-100 gap-2">
            <span className="text-muted-foreground whitespace-nowrap">Taxing District:</span>
            <span className="font-mono text-slate-800">{renderSafeValue(taxingDistrict)}</span>
          </div>

          <div className="flex justify-between py-1 border-b border-slate-100 gap-2">
            <span className="text-muted-foreground whitespace-nowrap">Land Size (Acres / Lot):</span>
            <span className="font-semibold text-slate-800">{renderSafeValue(landSize)}</span>
          </div>

          <div className="flex justify-between py-1 border-b border-slate-100 gap-2">
            <span className="text-muted-foreground whitespace-nowrap">Exemptions:</span>
            <span className="text-right font-medium text-emerald-800 bg-emerald-50 px-1.5 py-0.5 rounded text-[11px]">
              {renderSafeValue(exemptions, "None")}
            </span>
          </div>

          <div className="flex justify-between py-1 border-b border-slate-100 gap-2">
            <span className="text-muted-foreground whitespace-nowrap">Map / Plat Number:</span>
            <span className="font-mono text-slate-700">{renderSafeValue(mapNumber)}</span>
          </div>

          <div className="flex flex-col py-1 gap-1">
            <span className="text-muted-foreground">Legal Description:</span>
            <span className="text-slate-700 font-mono text-[11px] bg-slate-50 p-2 rounded border border-slate-100 leading-relaxed whitespace-pre-wrap">
              {renderSafeValue(legalDesc)}
            </span>
          </div>
        </div>

        {/* Card 2: Valuation & Financials */}
        <div className="space-y-4">
          <div className="p-4 border rounded-lg bg-white space-y-2.5 shadow-2xs">
            <div className="flex items-center justify-between pb-2 border-b">
              <div className="flex items-center gap-1.5 font-bold text-slate-800 text-xs">
                <DollarSign className="w-3.5 h-3.5 text-emerald-600" />
                <span>Valuation & Assessment</span>
              </div>
              <Badge variant="outline" className="text-[10px] font-mono">
                Tax Roll: {taxYear}
              </Badge>
            </div>

            <div className="flex justify-between py-1 border-b border-slate-100 gap-2 items-center">
              <span className="text-muted-foreground">Total Assessed Value:</span>
              <span className="font-mono font-bold text-base text-emerald-700">
                {formatCurrency(assessedVal)}
              </span>
            </div>

            <div className="flex justify-between py-1 border-b border-slate-100 gap-2">
              <span className="text-muted-foreground">Just / Market Value:</span>
              <span className="font-mono font-semibold text-slate-800">
                {formatCurrency(justVal)}
              </span>
            </div>

            <div className="flex justify-between py-1 border-b border-slate-100 gap-2">
              <span className="text-muted-foreground">Land Value:</span>
              <span className="font-mono text-slate-700">
                {formatCurrency(landVal)}
              </span>
            </div>

            <div className="flex justify-between py-1 border-b border-slate-100 gap-2">
              <span className="text-muted-foreground">Improvement / Building Value:</span>
              <span className="font-mono text-slate-700">
                {formatCurrency(improvementVal)}
              </span>
            </div>

            <div className="flex justify-between py-1 border-b border-slate-100 gap-2">
              <span className="text-muted-foreground">Taxable Value:</span>
              <span className="font-mono font-medium text-slate-800">
                {formatCurrency(taxableVal)}
              </span>
            </div>

            {isNormalized && (
              <>
                <div className="flex justify-between py-1 border-b border-slate-100 gap-2 items-center pt-2">
                  <span className="text-muted-foreground font-semibold">Tax Collector Status:</span>
                  <span>
                    {data.is_delinquent ? (
                      <Badge variant="destructive">Delinquent</Badge>
                    ) : (
                      <Badge className="bg-emerald-600 text-white">Paid / Current</Badge>
                    )}
                  </span>
                </div>

                <div className="flex justify-between py-1 border-b border-slate-100 gap-2">
                  <span className="text-muted-foreground font-semibold">Tax Amount Due:</span>
                  <span className="font-mono font-bold text-slate-900">
                    {formatCurrency(data.tax_amount_due)}
                  </span>
                </div>
              </>
            )}
          </div>

          {/* Building Characteristics Card (if building details present) */}
          {(yearBuilt || livingArea || bedrooms || bathrooms) && (
            <div className="p-4 border rounded-lg bg-white space-y-2.5 shadow-2xs">
              <div className="flex items-center gap-1.5 pb-2 border-b font-bold text-slate-800 text-xs">
                <Building2 className="w-3.5 h-3.5 text-blue-600" />
                <span>Building & Structure Characteristics</span>
              </div>

              <div className="grid grid-cols-2 gap-3 pt-1">
                <div className="p-2 bg-slate-50 rounded border border-slate-100">
                  <span className="text-[10px] text-muted-foreground block">Year Built</span>
                  <span className="font-mono font-semibold text-slate-900 text-xs">{renderSafeValue(yearBuilt)}</span>
                </div>
                <div className="p-2 bg-slate-50 rounded border border-slate-100">
                  <span className="text-[10px] text-muted-foreground block">Living / Heated Area</span>
                  <span className="font-mono font-semibold text-slate-900 text-xs">
                    {livingArea ? `${formatSafeString(livingArea)} sq ft` : "—"}
                  </span>
                </div>
                <div className="p-2 bg-slate-50 rounded border border-slate-100">
                  <span className="text-[10px] text-muted-foreground block">Bedrooms</span>
                  <span className="font-semibold text-slate-900 text-xs">{renderSafeValue(bedrooms)}</span>
                </div>
                <div className="p-2 bg-slate-50 rounded border border-slate-100">
                  <span className="text-[10px] text-muted-foreground block">Bathrooms</span>
                  <span className="font-semibold text-slate-900 text-xs">{renderSafeValue(bathrooms)}</span>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Dynamic Tables */}
      <DynamicTable
        title="Valuation & Assessment History"
        icon={Calendar}
        data={assessmentHistory}
      />

      <DynamicTable
        title="Recorded Sales & Transfers History"
        icon={History}
        data={salesHistory}
      />

      <DynamicTable
        title="Building Characteristics & Sub-Areas"
        icon={Building2}
        data={buildings}
      />

      <DynamicTable
        title="Land Lines & Acreage Breakdown"
        icon={Layers}
        data={landLines}
      />

      <DynamicTable
        title="Extra Features & Outbuildings"
        icon={Table2}
        data={extraFeatures}
      />

      {otherTables.map((ot: any, idx: number) => (
        <DynamicTable
          key={idx}
          title={`Additional Property Data Table (${idx + 1})`}
          icon={FileSpreadsheet}
          data={ot.rows}
        />
      ))}

      {/* Additional Captured Property Attributes Collapsible */}
      {additionalFieldCount > 0 && (
        <div className="border border-slate-200 rounded-lg bg-white overflow-hidden shadow-2xs mt-4">
          <button
            type="button"
            onClick={() => setShowAllFields(!showAllFields)}
            className="w-full px-4 py-2.5 bg-slate-50/80 hover:bg-slate-100 flex items-center justify-between text-xs font-semibold text-slate-700 transition-colors"
          >
            <div className="flex items-center gap-2">
              <Info className="w-3.5 h-3.5 text-blue-600" />
              <span>All Additional Captured Property Attributes</span>
              <Badge variant="outline" className="text-[10px] font-mono px-1.5 py-0 bg-white">
                {additionalFieldCount} fields
              </Badge>
            </div>
            {showAllFields ? (
              <ChevronDown className="w-4 h-4 text-slate-500" />
            ) : (
              <ChevronRight className="w-4 h-4 text-slate-500" />
            )}
          </button>

          {showAllFields && (
            <div className="p-4 border-t max-h-96 overflow-y-auto">
              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2">
                {Object.entries(allKeyValues).map(([k, v]) => (
                  <div key={k} className="p-2 rounded bg-slate-50/60 border border-slate-100 text-xs">
                    <span className="text-[10px] text-muted-foreground uppercase font-semibold block truncate" title={k}>
                      {k.replace(/_/g, " ")}
                    </span>
                    <span className="font-mono text-slate-800 text-[11px] break-words">
                      {renderSafeValue(v)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ExecutionResults() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const runId = searchParams.get("id");

  const [run, setRun] = useState<WorkflowRun | null>(null);
  const [results, setResults] = useState<ExecutionResultsResponse | null>(null);
  const [nodeRuns, setNodeRuns] = useState<NodeRun[]>([]);
  const [documents, setDocuments] = useState<DocumentArtifact[]>([]);
  const [browserActivity, setBrowserActivity] = useState<BrowserActivityResponse | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<
    "normalized" | "assessor" | "tax" | "documents" | "activity" | "json"
  >("assessor");
  const [selectedAssessorIndex, setSelectedAssessorIndex] = useState(0);

  // Document Viewer & Mistral OCR Modal State
  const [viewingDoc, setViewingDoc] = useState<DocumentArtifact | null>(null);
  const [docBlobUrl, setDocBlobUrl] = useState<string | null>(null);
  const [docBlobLoading, setDocBlobLoading] = useState(false);
  const [docViewTab, setDocViewTab] = useState<"split" | "preview" | "ocr">("split");
  const [ocrLoading, setOcrLoading] = useState(false);
  const [ocrResult, setOcrResult] = useState<DocumentOcrAnalysisResult | null>(null);
  const [ocrError, setOcrError] = useState<string | null>(null);
  const [copiedMd, setCopiedMd] = useState(false);
  const [showRawMd, setShowRawMd] = useState(false);
  const [showAllKVs, setShowAllKVs] = useState(false);

  // Fetch execution and results
  const fetchExecutionData = async (isPoll = false) => {
    if (!runId) {
      setLoading(false);
      return;
    }

    try {
      if (!isPoll) setLoading(true);
      const [runData, resultsData, nodesData, docsData, activityData] = await Promise.all([
        executionsApi.getById(runId),
        executionsApi.getResults(runId),
        executionsApi.listNodes(runId),
        executionsApi.getDocuments(runId).catch(() => []),
        executionsApi.getBrowserActivity(runId).catch(() => null),
      ]);
      setRun(runData);
      setResults(resultsData);
      setNodeRuns(nodesData);
      setDocuments(docsData || []);
      if (activityData) setBrowserActivity(activityData);
      setErrorMsg(null);
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to load execution results.");
    } finally {
      if (!isPoll) setLoading(false);
    }
  };

  useEffect(() => {
    fetchExecutionData();
  }, [runId]);

  // Controlled polling until execution reaches terminal state
  useEffect(() => {
    if (!run) return;
    const terminalStates = ["COMPLETED", "PARTIALLY_COMPLETED", "FAILED", "CANCELLED"];
    if (terminalStates.includes(run.status.toUpperCase())) {
      return; // Stop polling
    }

    const interval = setInterval(() => {
      fetchExecutionData(true);
    }, 2500);

    return () => clearInterval(interval);
  }, [run?.status, runId]);

  const handleCancel = async () => {
    if (!runId || !confirm("Request cancellation of this execution?")) return;
    try {
      await executionsApi.cancel(runId);
      fetchExecutionData(true);
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to cancel execution.");
    }
  };

  const handleDownload = async (doc: DocumentArtifact) => {
    try {
      setDownloadingId(doc.id);
      await documentsApi.download(doc.id, doc.original_filename);
    } catch (err: any) {
      alert(err.response?.data?.detail || "Failed to download document.");
    } finally {
      setDownloadingId(null);
    }
  };

  const handleRetryDownload = async (doc: DocumentArtifact) => {
    try {
      setRetryingId(doc.id);
      const updated = await documentsApi.retryDownload(doc.id);
      setDocuments((prev) => prev.map((d) => (d.id === doc.id ? updated : d)));
    } catch (err: any) {
      alert(err.response?.data?.detail || "Failed to retry document download.");
    } finally {
      setRetryingId(null);
    }
  };

  const handleView = async (doc: DocumentArtifact) => {
    setViewingDoc(doc);
    setDocBlobUrl(null);
    setDocBlobLoading(true);
    setOcrResult(null);
    setOcrError(null);
    setDocViewTab("split");

    try {
      const { blobUrl } = await documentsApi.viewBlob(doc.id);
      setDocBlobUrl(blobUrl);
    } catch (err: any) {
      console.error("Failed to load document preview:", err);
    } finally {
      setDocBlobLoading(false);
    }

    // Automatically trigger Mistral OCR analysis for instant visual data
    runOcrAnalysis(doc.id);
  };

  const runOcrAnalysis = async (docId: string) => {
    setOcrLoading(true);
    setOcrError(null);
    try {
      const res = await documentsApi.ocrAnalyze(docId);
      if (res.success) {
        setOcrResult(res);
      } else {
        setOcrError(res.error || "Mistral OCR analysis returned incomplete data.");
      }
    } catch (err: any) {
      setOcrError(err.response?.data?.detail || "Failed to analyze document with Mistral OCR.");
    } finally {
      setOcrLoading(false);
    }
  };

  const closeViewModal = () => {
    if (docBlobUrl) {
      window.URL.revokeObjectURL(docBlobUrl);
    }
    setViewingDoc(null);
    setDocBlobUrl(null);
    setOcrResult(null);
    setOcrError(null);
  };

  const handleCopyMarkdown = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedMd(true);
    setTimeout(() => setCopiedMd(false), 2000);
  };

  if (!runId) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center">
        <FileSearch className="w-12 h-12 text-slate-300 mb-3" />
        <h3 className="text-base font-semibold text-slate-900">No Execution Selected</h3>
        <p className="text-xs text-muted-foreground mt-1 max-w-sm">
          Please select a workflow execution from the Execution History page to inspect its results.
        </p>
        <Button
          size="sm"
          className="mt-4"
          onClick={() => navigate("/execution-history")}
        >
          <History className="w-4 h-4 mr-1.5" />
          Go to Execution History
        </Button>
      </div>
    );
  }

  const isTerminal = run
    ? ["COMPLETED", "PARTIALLY_COMPLETED", "FAILED", "CANCELLED"].includes(run.status.toUpperCase())
    : true;

  const rawNormalized = results?.normalized_result as Record<string, any> | null;
  const assessor = results?.assessor_result as Record<string, any> | null;
  const tax = results?.tax_result as Record<string, any> | null;

  const assessorRecords: Record<string, any>[] =
    Array.isArray(assessor?._records) && assessor._records.length > 0
      ? assessor._records
      : assessor
      ? [assessor]
      : [];

  const currentAssessor = assessorRecords[selectedAssessorIndex] || assessorRecords[0] || assessor;

  // Synthesize normalized property view if no explicit normalize node was run,
  // but assessor or tax records exist
  const normalized =
    rawNormalized ||
    (currentAssessor || tax
      ? {
          parcel_id: currentAssessor?.parcel_number || tax?.parcel_number || "",
          owner_name: currentAssessor?.owner_name || tax?.owner_name || "",
          property_address: currentAssessor?.property_address || tax?.property_address || "",
          mailing_address: currentAssessor?.mailing_address || null,
          property_use: currentAssessor?.property_use || null,
          taxing_district: currentAssessor?.taxing_district || null,
          land_size: currentAssessor?.land_size || null,
          exemptions: currentAssessor?.exemptions || null,
          map_number: currentAssessor?.map_number || null,
          legal_description: currentAssessor?.legal_description || tax?.legal_description || "",
          total_assessed_value: currentAssessor?.assessed_value ?? currentAssessor?.total_assessed_value ?? null,
          just_value: currentAssessor?.just_value ?? null,
          land_value: currentAssessor?.land_value ?? null,
          improvement_value: currentAssessor?.improvement_value ?? null,
          taxable_value: currentAssessor?.taxable_value ?? null,
          tax_year: currentAssessor?.tax_year ?? null,
          year_built: currentAssessor?.year_built ?? null,
          living_area: currentAssessor?.living_area ?? null,
          bedrooms: currentAssessor?.bedrooms ?? null,
          bathrooms: currentAssessor?.bathrooms ?? null,
          sales_history: currentAssessor?.sales_history ?? [],
          assessment_history: currentAssessor?.assessment_history ?? currentAssessor?.values_history ?? [],
          buildings: currentAssessor?.buildings ?? [],
          land_lines: currentAssessor?.land_lines ?? [],
          extra_features: currentAssessor?.extra_features ?? [],
          sections: currentAssessor?.sections ?? {},
          raw_data: currentAssessor?.raw_data ?? {},
          is_delinquent: tax?.is_delinquent ?? false,
          tax_amount_due: tax?.amount_due ?? tax?.tax_amount_due ?? null,
          is_partial: !tax && Boolean(results?.has_tax_branch),
          missing_sources: !tax && results?.has_tax_branch ? ["Tax Collector"] : [],
        }
      : null);

  const hasAssessor = results?.has_assessor_branch ?? nodeRuns.some((n) => n.node_type === "assessor");
  const hasTax = results?.has_tax_branch ?? nodeRuns.some((n) => n.node_type === "tax");
  const hasNormalize = results?.has_normalize_branch ?? nodeRuns.some((n) => n.node_type === "normalize");

  useEffect(() => {
    if (results) {
      if (!hasNormalize && hasAssessor && !normalized) {
        setActiveTab("assessor");
      }
    }
  }, [results?.workflow_run_id, hasNormalize, hasAssessor]);

  return (
    <div className="space-y-6">
      {/* Top Navigation & Status Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-white p-4 border rounded-lg shadow-sm">
        <div className="flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => navigate("/execution-history")}
            className="h-8 text-xs"
          >
            <ArrowLeft className="w-3.5 h-3.5 mr-1" />
            History
          </Button>

          <div>
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm font-bold text-slate-900">
                Run: {runId.slice(0, 8)}...
              </span>
              {run && runStatusBadge(run.status)}
              {run?.celery_task_id && (
                <span className="text-[10px] font-mono text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded">
                  Celery: {run.celery_task_id.slice(0, 8)}
                </span>
              )}
            </div>
            <div className="text-xs text-muted-foreground mt-0.5">
              Workflow: {run?.workflow_name || "Title Search"} (v{run?.version_number || 1})
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {!isTerminal && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleCancel}
              className="text-xs h-8 text-rose-600 border-rose-200 hover:bg-rose-50"
            >
              <StopCircle className="w-3.5 h-3.5 mr-1" />
              Cancel Run
            </Button>
          )}

          <Button
            variant="outline"
            size="sm"
            onClick={() => fetchExecutionData(false)}
            disabled={loading}
            className="text-xs h-8"
          >
            <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Error Banner */}
      {errorMsg && (
        <div className="p-3 text-xs bg-rose-50 border border-rose-200 text-rose-800 rounded-md flex items-center justify-between">
          <div className="flex items-center gap-2">
            <XCircle className="w-4 h-4 text-rose-600 shrink-0" />
            <span>{errorMsg}</span>
          </div>
        </div>
      )}

      {/* Execution Summary Metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Card>
          <CardContent className="p-3">
            <div className="text-[11px] text-muted-foreground font-medium">Duration</div>
            <div className="text-base font-bold font-mono text-slate-900 mt-1">
              {run?.duration_ms !== null && run?.duration_ms !== undefined
                ? `${run.duration_ms}ms`
                : "In Progress..."}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-3">
            <div className="text-[11px] text-muted-foreground font-medium">Assessor Branch</div>
            <div className="text-base font-bold text-slate-900 mt-1">
              {branchStatusDisplay(
                results?.assessor_branch_status ||
                  (hasAssessor ? (assessor ? "SUCCEEDED" : (isTerminal ? "FAILED" : (run?.status === "QUEUED" ? "QUEUED" : "RUNNING"))) : "NOT_CONFIGURED")
              )}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-3">
            <div className="text-[11px] text-muted-foreground font-medium">Tax Branch</div>
            <div className="text-base font-bold text-slate-900 mt-1">
              {branchStatusDisplay(
                results?.tax_branch_status ||
                  (hasTax ? (tax ? "SUCCEEDED" : (isTerminal ? "FAILED" : (run?.status === "QUEUED" ? "QUEUED" : "RUNNING"))) : "NOT_CONFIGURED")
              )}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-3">
            <div className="text-[11px] text-muted-foreground font-medium">Fault Mode</div>
            <div className="text-base font-bold text-slate-900 mt-1">
              {results?.is_partial_success ? (
                <span className="text-purple-600 text-sm">Partial Success</span>
              ) : (
                <span className="text-slate-600 text-sm">Strict / Complete</span>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Node Execution Timeline */}
      <Card>
        <CardContent className="p-4 space-y-3">
          <div className="text-xs font-bold uppercase tracking-wider text-slate-500">
            Node Execution Timeline
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-5 gap-3">
            {nodeRuns.map((nr) => (
              <div
                key={nr.id}
                className="p-3 border rounded-lg bg-slate-50/70 space-y-1.5 text-xs shadow-xs"
              >
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-slate-800 capitalize">
                    {nr.node_type}
                  </span>
                  {nodeStatusBadge(nr.status)}
                </div>
                <div className="font-mono text-[10px] text-muted-foreground truncate">
                  {nr.node_id}
                </div>
                <div className="text-[11px] text-slate-500 flex items-center gap-1 pt-1 border-t">
                  <Clock className="w-3 h-3" />
                  <span>{nr.duration_ms !== null && nr.duration_ms !== undefined ? `${nr.duration_ms}ms` : "—"}</span>
                </div>
                {nr.error_message && (
                  <div className="text-[10px] text-rose-600 truncate" title={nr.error_message}>
                    {nr.error_message}
                  </div>
                )}
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Structured Results Display */}
      <div className="space-y-4">
        {/* Navigation Tabs */}
        <div className="border-b flex space-x-6">
          {(hasNormalize || normalized) && (
            <button
              onClick={() => setActiveTab("normalized")}
              className={`pb-2.5 text-xs font-medium border-b-2 flex items-center gap-1.5 transition-colors ${
                activeTab === "normalized"
                  ? "border-blue-600 text-blue-600 font-semibold"
                  : "border-transparent text-slate-500 hover:text-slate-800"
              }`}
            >
              <GitMerge className="w-3.5 h-3.5" />
              Harmonized Property Data
              {normalized?.is_partial && (
                <Badge className="bg-purple-100 text-purple-800 text-[10px] px-1.5 py-0 border-purple-200">
                  Partial
                </Badge>
              )}
            </button>
          )}

          {(hasAssessor || assessor) && (
            <button
              onClick={() => setActiveTab("assessor")}
              className={`pb-2.5 text-xs font-medium border-b-2 flex items-center gap-1.5 transition-colors ${
                activeTab === "assessor"
                  ? "border-blue-600 text-blue-600 font-semibold"
                  : "border-transparent text-slate-500 hover:text-slate-800"
              }`}
            >
              <Landmark className="w-3.5 h-3.5" />
              Assessor Records
              {results?.assessor_records_count !== undefined && (
                <Badge className="bg-slate-100 text-slate-700 text-[10px] px-1.5 py-0 border-slate-200">
                  {results.assessor_records_count}
                </Badge>
              )}
            </button>
          )}

          {(hasTax || tax) && (
            <button
              onClick={() => setActiveTab("tax")}
              className={`pb-2.5 text-xs font-medium border-b-2 flex items-center gap-1.5 transition-colors ${
                activeTab === "tax"
                  ? "border-blue-600 text-blue-600 font-semibold"
                  : "border-transparent text-slate-500 hover:text-slate-800"
              }`}
            >
              <Receipt className="w-3.5 h-3.5" />
              Tax Collector Records
            </button>
          )}

          <button
            onClick={() => setActiveTab("activity")}
            className={`pb-2.5 text-xs font-medium border-b-2 flex items-center gap-1.5 transition-colors ${
              activeTab === "activity"
                ? "border-blue-600 text-blue-600 font-semibold"
                : "border-transparent text-slate-500 hover:text-slate-800"
            }`}
          >
            <Activity className="w-3.5 h-3.5" />
            Live Browser Activity
            {browserActivity?.browser_state === "BUSY" && (
              <span className="w-2 h-2 rounded-full bg-blue-600 animate-ping" />
            )}
          </button>

          <button
            onClick={() => setActiveTab("documents")}
            className={`pb-2.5 text-xs font-medium border-b-2 flex items-center gap-1.5 transition-colors ${
              activeTab === "documents"
                ? "border-blue-600 text-blue-600 font-semibold"
                : "border-transparent text-slate-500 hover:text-slate-800"
            }`}
          >
            <FileText className="w-3.5 h-3.5" />
            Retrieved Documents
            {documents.length > 0 && (
              <Badge className="bg-slate-100 text-slate-700 text-[10px] px-1.5 py-0 border-slate-200">
                {documents.length}
              </Badge>
            )}
          </button>

          <button
            onClick={() => setActiveTab("json")}
            className={`pb-2.5 text-xs font-medium border-b-2 flex items-center gap-1.5 transition-colors ${
              activeTab === "json"
                ? "border-blue-600 text-blue-600 font-semibold"
                : "border-transparent text-slate-500 hover:text-slate-800"
            }`}
          >
            <Code2 className="w-3.5 h-3.5" />
            Raw JSON Output
          </button>
        </div>

        {/* Tab 1: Harmonized Data */}
        {activeTab === "normalized" && (
          <Card>
            <CardContent className="p-5 space-y-4">
              {normalized?.is_partial && (
                <div className="p-3 bg-purple-50 border border-purple-200 text-purple-900 rounded-md text-xs flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4 text-purple-600 shrink-0" />
                  <span>
                    <strong>Partial Success Mode:</strong> Data was harmonized despite missing sources (
                    {normalized.missing_sources?.join(", ")}).
                  </span>
                </div>
              )}

              {normalized ? (
                <ErrorBoundary>
                  <PropertyDetailsCard data={normalized} isNormalized={true} />
                </ErrorBoundary>
              ) : (
                <div className="text-center py-10 text-muted-foreground text-xs space-y-1">
                  <p className="font-semibold text-slate-700">
                    {!hasNormalize ? "Normalization Not Configured" : "Normalized Data Not Available"}
                  </p>
                  <p>
                    {!hasNormalize
                      ? "This workflow does not include a data normalization node (direct branch search)."
                      : "Harmonized data has not been generated yet for this run."}
                  </p>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {/* Tab 2: Assessor Records */}
        {activeTab === "assessor" && (
          <Card>
            <CardContent className="p-5 text-xs space-y-4">
              {results?.assessor_records_count === 0 && assessorRecords.length === 0 ? (
                <div className="text-center py-12 text-slate-500 space-y-2">
                  <CheckCircle2 className="w-10 h-10 text-emerald-500 mx-auto opacity-80" />
                  <h4 className="text-sm font-semibold text-slate-800">Search Completed Successfully</h4>
                  <p className="text-xs text-muted-foreground max-w-sm mx-auto">
                    The County Assessor portal was queried successfully. 0 matching property records were returned for the provided search criteria.
                  </p>
                </div>
              ) : assessorRecords.length > 0 ? (
                <div className="space-y-4">
                  {assessorRecords.length > 1 && (
                    <div className="flex items-center gap-2 pb-2 border-b overflow-x-auto">
                      <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider whitespace-nowrap">
                        Matched Properties ({assessorRecords.length}):
                      </span>
                      {assessorRecords.map((rec, idx) => (
                        <button
                          key={idx}
                          type="button"
                          onClick={() => setSelectedAssessorIndex(idx)}
                          className={`px-2.5 py-1 rounded text-xs font-mono transition-colors ${
                            selectedAssessorIndex === idx
                              ? "bg-blue-600 text-white font-semibold"
                              : "bg-slate-100 text-slate-700 hover:bg-slate-200"
                          }`}
                        >
                          {renderSafeValue(rec.parcel_number) || `Record #${idx + 1}`}
                        </button>
                      ))}
                    </div>
                  )}
                  <ErrorBoundary>
                    <PropertyDetailsCard data={currentAssessor || assessorRecords[0]} />
                  </ErrorBoundary>
                </div>
              ) : (
                <div className="text-center py-10 text-muted-foreground">
                  No assessor records available for this run.
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {/* Tab 3: Tax Records */}
        {activeTab === "tax" && (
          <Card>
            <CardContent className="p-5 text-xs space-y-4">
              {tax ? (
                <div className="space-y-4">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3 p-3 bg-slate-50 border rounded-lg">
                    <div className="flex justify-between py-1 border-b md:border-b-0">
                      <span className="text-muted-foreground font-medium">Parcel / Account #:</span>
                      <span className="font-mono font-bold text-slate-900">
                        {renderSafeValue(tax.account_number || tax.parcel_number)}
                      </span>
                    </div>
                    <div className="flex justify-between py-1 border-b md:border-b-0">
                      <span className="text-muted-foreground font-medium">Owner:</span>
                      <span className="font-semibold text-slate-900">{renderSafeValue(tax.owner_name)}</span>
                    </div>
                    <div className="flex justify-between py-1 border-b md:border-b-0">
                      <span className="text-muted-foreground font-medium">Situs / Address:</span>
                      <span className="font-medium text-slate-800">{renderSafeValue(tax.property_address || tax.situs_address)}</span>
                    </div>
                    <div className="flex justify-between py-1 border-b md:border-b-0">
                      <span className="text-muted-foreground font-medium">Tax Year:</span>
                      <span className="font-semibold text-slate-900">{renderSafeValue(tax.tax_year, "2026")}</span>
                    </div>
                    <div className="flex justify-between py-1 border-b md:border-b-0">
                      <span className="text-muted-foreground font-medium">Tax Status:</span>
                      <span className={`font-bold px-2 py-0.5 rounded text-[11px] ${
                        String(tax.status || tax.tax_status).toUpperCase().includes("UNPAID") || String(tax.status || tax.tax_status).toUpperCase().includes("DELINQUENT")
                          ? "bg-rose-100 text-rose-800 border border-rose-200"
                          : "bg-emerald-100 text-emerald-800 border border-emerald-200"
                      }`}>
                        {renderSafeValue(tax.status || tax.tax_status, "Unknown")}
                      </span>
                    </div>
                    <div className="flex justify-between py-1 border-b md:border-b-0">
                      <span className="text-muted-foreground font-medium">Total Amount Due:</span>
                      <span className="font-mono font-bold text-rose-600 text-sm">
                        {formatCurrency(tax.amount_due ?? tax.total_amount_due ?? 0)}
                      </span>
                    </div>
                  </div>

                  {/* Account History Table if available */}
                  {Array.isArray(tax.account_history) && tax.account_history.length > 0 && (
                    <div className="space-y-2 mt-4">
                      <h4 className="font-semibold text-slate-800 text-xs flex items-center gap-1.5">
                        <Receipt className="w-3.5 h-3.5 text-blue-600" />
                        Official Account History & Prior Annual Bills ({tax.account_history.length})
                      </h4>
                      <div className="overflow-x-auto border rounded-lg">
                        <table className="w-full text-left text-xs">
                          <thead className="bg-slate-100 border-b text-slate-700 font-semibold">
                            <tr>
                              <th className="p-2">Bill / Year</th>
                              <th className="p-2">Amount Due</th>
                              <th className="p-2">Status</th>
                              <th className="p-2">Payment Date</th>
                              <th className="p-2">Receipt #</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-100">
                            {tax.account_history.map((row: any, rIdx: number) => (
                              <tr key={rIdx} className="hover:bg-slate-50 transition-colors">
                                <td className="p-2 font-medium text-slate-900">{renderSafeValue(row.bill || row.year)}</td>
                                <td className="p-2 font-mono text-slate-700">{formatCurrency(row.amount_due)}</td>
                                <td className="p-2">
                                  <span className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                                    String(row.status).toLowerCase().includes("unpaid")
                                      ? "bg-rose-100 text-rose-800"
                                      : "bg-emerald-100 text-emerald-800"
                                  }`}>
                                    {renderSafeValue(row.status)}
                                  </span>
                                </td>
                                <td className="p-2 text-slate-600">{renderSafeValue(row.payment_date)}</td>
                                <td className="p-2 font-mono text-slate-600">{renderSafeValue(row.receipt_number)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-center py-10 text-muted-foreground">
                  No tax records available for this run.
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {/* Tab 4: Retrieved Documents */}
        {activeTab === "documents" && (
          <Card>
            <CardContent className="pt-6">
              {documents.length > 0 ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b text-left text-muted-foreground font-semibold">
                        <th className="pb-2">Document Type</th>
                        <th className="pb-2">Filename / Title</th>
                        <th className="pb-2">Recording / Doc #</th>
                        <th className="pb-2">Size & Pages</th>
                        <th className="pb-2">Status</th>
                        <th className="pb-2 text-right">Action</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {documents.map((doc) => (
                        <tr key={doc.id} className="hover:bg-slate-50 transition-colors">
                          <td className="py-2.5 font-medium capitalize text-slate-800">
                            {doc.document_type.replace(/_/g, " ")}
                          </td>
                          <td className="py-2.5">
                            <div className="font-medium text-slate-900 truncate max-w-[260px]" title={doc.original_filename}>
                              {doc.original_filename}
                            </div>
                            {doc.pdf_title && (
                              <div className="text-[11px] text-muted-foreground truncate max-w-[260px]">
                                {doc.pdf_title}
                              </div>
                            )}
                          </td>
                          <td className="py-2.5 font-mono text-slate-600">
                            {doc.recording_number || "—"}
                          </td>
                          <td className="py-2.5 text-slate-600">
                            {doc.file_size ? `${(doc.file_size / 1024).toFixed(1)} KB` : "0 KB"}
                            {doc.page_count ? ` • ${doc.page_count} page${doc.page_count > 1 ? "s" : ""}` : ""}
                          </td>
                          <td className="py-2.5">
                            {documentStatusBadge(doc.status, doc.is_duplicate)}
                            {doc.error_message && (
                              <p className="text-[10px] text-rose-600 mt-0.5 max-w-[200px] truncate" title={doc.error_message}>
                                {doc.error_message}
                              </p>
                            )}
                          </td>
                          <td className="py-2.5 text-right">
                            {doc.status.toLowerCase() === "validated" || doc.status.toLowerCase() === "completed" || doc.is_duplicate ? (
                              <div className="flex items-center justify-end gap-1.5">
                                <Button
                                  size="sm"
                                  variant="default"
                                  className="h-7 text-xs gap-1 bg-indigo-600 hover:bg-indigo-700 text-white font-medium shadow-xs"
                                  onClick={() => handleView(doc)}
                                >
                                  <Eye className="w-3.5 h-3.5" />
                                  View
                                </Button>
                                <Button
                                  size="sm"
                                  variant="outline"
                                  className="h-7 text-xs gap-1 border-slate-300 hover:bg-slate-50 text-slate-700"
                                  disabled={downloadingId === doc.id}
                                  onClick={() => handleDownload(doc)}
                                >
                                  {downloadingId === doc.id ? (
                                    <Loader2 className="w-3 h-3 animate-spin" />
                                  ) : (
                                    <Download className="w-3 h-3" />
                                  )}
                                  Download
                                </Button>
                              </div>
                            ) : doc.status.toLowerCase() === "failed" || doc.status.toLowerCase() === "retry_pending" ? (
                              <Button
                                size="sm"
                                variant="outline"
                                className="h-7 text-xs gap-1 text-amber-700 border-amber-300 hover:bg-amber-50"
                                disabled={retryingId === doc.id}
                                onClick={() => handleRetryDownload(doc)}
                              >
                                {retryingId === doc.id ? (
                                  <Loader2 className="w-3 h-3 animate-spin" />
                                ) : (
                                  <RotateCcw className="w-3 h-3" />
                                )}
                                Retry
                              </Button>
                            ) : (
                              <span className="text-muted-foreground text-[11px]">—</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="text-center py-12 text-muted-foreground">
                  <FileText className="w-10 h-10 mx-auto mb-2 text-slate-300" />
                  <p className="font-medium">No document artifacts retrieved</p>
                  <p className="text-xs text-slate-400 mt-0.5">
                    No PDF documents were discovered or configured for retrieval during this search execution.
                  </p>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {/* Tab: Live Browser Activity */}
        {activeTab === "activity" && (
          <Card>
            <CardContent className="p-5 space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3.5 bg-slate-50 border rounded-lg">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <MonitorPlay className="w-4 h-4 text-blue-600" />
                    <span className="font-semibold text-xs text-slate-900">
                      Browser Session: {browserActivity?.session_id || "Active Playwright Session"}
                    </span>
                    {browserActivity?.browser_state && (
                      <Badge
                        variant="outline"
                        className={`text-[10px] font-semibold ${
                          browserActivity.browser_state === "BUSY"
                            ? "bg-blue-50 text-blue-700 border-blue-200"
                            : browserActivity.browser_state === "READY"
                            ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                            : "bg-slate-100 text-slate-700"
                        }`}
                      >
                        {browserActivity.browser_state}
                      </Badge>
                    )}
                  </div>
                  {browserActivity?.current_url && (
                    <div className="flex items-center gap-1.5 text-xs text-slate-600 font-mono">
                      <Globe className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                      <a
                        href={browserActivity.current_url}
                        target="_blank"
                        rel="noreferrer"
                        className="hover:underline text-blue-600 truncate max-w-md flex items-center gap-1"
                      >
                        {browserActivity.current_url}
                        <ExternalLink className="w-2.5 h-2.5" />
                      </a>
                    </div>
                  )}
                </div>

                <div className="text-right text-xs">
                  <div className="text-muted-foreground text-[10px]">Active Step / Action</div>
                  <div className="font-medium text-slate-800">
                    {browserActivity?.current_step || browserActivity?.current_action || (isTerminal ? "Session Completed" : "Waiting for next step...")}
                  </div>
                </div>
              </div>

              {/* Activity Timeline */}
              <div className="space-y-3 pt-2">
                <div className="text-xs font-bold uppercase tracking-wider text-slate-500">
                  Execution Actions Timeline
                </div>

                {browserActivity?.activities && browserActivity.activities.length > 0 ? (
                  <div className="relative pl-6 space-y-3 before:absolute before:left-2.5 before:top-2 before:bottom-2 before:w-0.5 before:bg-slate-200">
                    {browserActivity.activities.map((item, idx) => {
                      const isComplete = item.status === "COMPLETED";
                      const isFailed = item.status === "FAILED";

                      return (
                        <div key={item.id || idx} className="relative flex items-start gap-3 text-xs">
                          <div
                            className={`absolute -left-6 top-1 w-5 h-5 rounded-full flex items-center justify-center text-[10px] ${
                              isComplete
                                ? "bg-emerald-100 text-emerald-700 border border-emerald-300"
                                : isFailed
                                ? "bg-rose-100 text-rose-700 border border-rose-300"
                                : "bg-blue-100 text-blue-700 border border-blue-300 animate-pulse"
                            }`}
                          >
                            {isComplete ? (
                              <CheckCircle2 className="w-3 h-3" />
                            ) : isFailed ? (
                              <XCircle className="w-3 h-3" />
                            ) : (
                              <Loader2 className="w-3 h-3 animate-spin" />
                            )}
                          </div>

                          <div className="flex-1 p-2.5 bg-white border rounded-md shadow-xs space-y-1">
                            <div className="flex items-center justify-between">
                              <span className="font-semibold text-slate-900">
                                {item.description}
                              </span>
                              <div className="flex items-center gap-2">
                                {item.duration_ms !== undefined && item.duration_ms > 0 && (
                                  <span className="text-[10px] font-mono text-slate-500 flex items-center gap-0.5">
                                    <Clock className="w-2.5 h-2.5" />
                                    {item.duration_ms}ms
                                  </span>
                                )}
                                <span
                                  className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${
                                    isComplete
                                      ? "bg-emerald-50 text-emerald-700"
                                      : isFailed
                                      ? "bg-rose-50 text-rose-700"
                                      : "bg-blue-50 text-blue-700"
                                  }`}
                                >
                                  {item.status}
                                </span>
                              </div>
                            </div>

                            {item.url && (
                              <div className="text-[11px] text-muted-foreground font-mono truncate max-w-xl">
                                {item.url}
                              </div>
                            )}

                            {item.error && (
                              <div className="text-[11px] text-rose-600 bg-rose-50 p-1.5 rounded font-mono mt-1">
                                {item.error}
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="text-center py-8 text-muted-foreground text-xs space-y-1">
                    <Activity className="w-8 h-8 text-slate-300 mx-auto mb-1" />
                    <p>No browser automation activity events recorded yet.</p>
                    <p className="text-[11px] text-slate-400">
                      When browser automation executes recipe steps, live Playwright actions will appear here in real-time.
                    </p>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        )}

        {/* Tab 5: Raw JSON */}
        {activeTab === "json" && (
          <pre className="font-mono text-xs p-4 rounded-lg bg-slate-900 text-emerald-400 overflow-x-auto max-h-[500px]">
            {JSON.stringify(
              {
                execution: run,
                results: results,
                node_runs: nodeRuns,
              },
              null,
              2
            )}
          </pre>
        )}
      </div>

      {/* DOCUMENT PREVIEW & MISTRAL OCR ANALYZER MODAL */}
      {viewingDoc && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-xs p-3 sm:p-5">
          <div className="bg-white rounded-xl shadow-2xl border border-slate-200 flex flex-col w-full max-w-[95vw] xl:max-w-7xl h-[92vh] overflow-hidden animate-in fade-in zoom-in-95 duration-150">
            {/* Modal Header */}
            <div className="px-5 py-3.5 bg-slate-900 text-white flex items-center justify-between border-b border-slate-800 shrink-0">
              <div className="flex items-center gap-3 min-w-0">
                <div className="p-2 rounded-lg bg-indigo-500/20 text-indigo-400 border border-indigo-500/30">
                  <FileText className="w-5 h-5" />
                </div>
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-semibold truncate text-white" title={viewingDoc.original_filename}>
                      {viewingDoc.original_filename}
                    </h2>
                    <Badge variant="outline" className="text-[11px] bg-slate-800 text-slate-300 border-slate-700 capitalize">
                      {viewingDoc.document_type.replace(/_/g, " ")}
                    </Badge>
                  </div>
                  <div className="text-[11px] text-slate-400 flex items-center gap-2 mt-0.5">
                    <span>{viewingDoc.file_size ? `${(viewingDoc.file_size / 1024).toFixed(1)} KB` : "—"}</span>
                    <span>•</span>
                    <span>{viewingDoc.page_count || 1} page{(viewingDoc.page_count || 1) > 1 ? "s" : ""}</span>
                    <span>•</span>
                    <span className="text-emerald-400 font-medium">Ready</span>
                  </div>
                </div>
              </div>

              {/* View Switcher Tabs & Actions */}
              <div className="flex items-center gap-2">
                <div className="flex items-center bg-slate-800 p-0.5 rounded-lg border border-slate-700 text-xs">
                  <button
                    type="button"
                    onClick={() => setDocViewTab("split")}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md transition-all font-medium ${
                      docViewTab === "split"
                        ? "bg-indigo-600 text-white shadow-xs"
                        : "text-slate-400 hover:text-white"
                    }`}
                  >
                    <Columns className="w-3.5 h-3.5" />
                    Split View
                  </button>
                  <button
                    type="button"
                    onClick={() => setDocViewTab("preview")}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md transition-all font-medium ${
                      docViewTab === "preview"
                        ? "bg-indigo-600 text-white shadow-xs"
                        : "text-slate-400 hover:text-white"
                    }`}
                  >
                    <Eye className="w-3.5 h-3.5" />
                    Document Only
                  </button>
                  <button
                    type="button"
                    onClick={() => setDocViewTab("ocr")}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md transition-all font-medium ${
                      docViewTab === "ocr"
                        ? "bg-indigo-600 text-white shadow-xs"
                        : "text-slate-400 hover:text-white"
                    }`}
                  >
                    <Sparkles className="w-3.5 h-3.5 text-amber-300" />
                    Mistral OCR
                  </button>
                </div>

                <Button
                  size="sm"
                  variant="outline"
                  className="h-8 text-xs border-slate-700 text-slate-200 hover:bg-slate-800 hover:text-white"
                  onClick={() => handleDownload(viewingDoc)}
                  disabled={downloadingId === viewingDoc.id}
                >
                  {downloadingId === viewingDoc.id ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" />
                  ) : (
                    <Download className="w-3.5 h-3.5 mr-1" />
                  )}
                  Download
                </Button>

                <button
                  type="button"
                  onClick={closeViewModal}
                  className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-800 rounded-lg transition-colors ml-1"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            {/* Modal Body */}
            <div className="flex-1 flex overflow-hidden">
              {/* Document Preview Pane (Shown in 'split' or 'preview' mode) */}
              {(docViewTab === "split" || docViewTab === "preview") && (
                <div className={`${docViewTab === "split" ? "w-1/2 border-r border-slate-200" : "w-full"} flex flex-col bg-slate-100`}>
                  {docBlobLoading ? (
                    <div className="flex-1 flex flex-col items-center justify-center p-8 text-slate-400">
                      <Loader2 className="w-8 h-8 animate-spin text-indigo-600 mb-2" />
                      <p className="text-xs font-medium">Loading document preview...</p>
                    </div>
                  ) : docBlobUrl ? (
                    <iframe
                      src={docBlobUrl}
                      title={viewingDoc.original_filename}
                      className="w-full flex-1 border-0"
                    />
                  ) : (
                    <div className="flex-1 flex flex-col items-center justify-center p-8 text-slate-400 text-center">
                      <FileText className="w-10 h-10 text-slate-300 mb-2" />
                      <p className="text-xs font-medium text-slate-600">Preview not available in browser</p>
                      <Button
                        size="sm"
                        className="mt-3 text-xs"
                        onClick={() => handleDownload(viewingDoc)}
                      >
                        <Download className="w-3.5 h-3.5 mr-1.5" />
                        Download PDF to View
                      </Button>
                    </div>
                  )}
                </div>
              )}

              {/* Mistral OCR Analyzer Pane (Shown in 'split' or 'ocr' mode) */}
              {(docViewTab === "split" || docViewTab === "ocr") && (
                <div className={`${docViewTab === "split" ? "w-1/2" : "w-full max-w-5xl mx-auto"} flex flex-col overflow-y-auto bg-slate-50 p-5 space-y-4`}>
                  {/* Mistral OCR Top Banner */}
                  <div className="p-3.5 rounded-lg bg-gradient-to-r from-purple-900/90 to-indigo-900/90 text-white flex items-center justify-between shadow-xs">
                    <div className="flex items-center gap-2.5">
                      <div className="p-2 rounded-md bg-white/10 border border-white/20">
                        <Sparkles className="w-4 h-4 text-amber-300" />
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-semibold tracking-wide">Mistral Document AI OCR</span>
                          <span className="text-[10px] bg-white/20 px-2 py-0.5 rounded font-mono">
                            {ocrResult?.ocr_model || "mistral-ocr-2512"}
                          </span>
                        </div>
                        <p className="text-[11px] text-purple-200 mt-0.5">
                          Visual document understanding & layout-grounded property extraction
                        </p>
                      </div>
                    </div>

                    <Button
                      size="sm"
                      variant="outline"
                      disabled={ocrLoading}
                      onClick={() => runOcrAnalysis(viewingDoc.id)}
                      className="h-7 text-xs bg-white/10 hover:bg-white/20 text-white border-white/20 gap-1.5"
                    >
                      <RefreshCw className={`w-3 h-3 ${ocrLoading ? "animate-spin" : ""}`} />
                      {ocrLoading ? "Analyzing..." : "Re-Analyze"}
                    </Button>
                  </div>

                  {/* OCR Loading State */}
                  {ocrLoading && (
                    <div className="py-12 flex flex-col items-center justify-center text-center space-y-3 bg-white rounded-lg border border-slate-200 p-6">
                      <div className="p-3 bg-indigo-50 text-indigo-600 rounded-full animate-pulse">
                        <Sparkles className="w-6 h-6 animate-spin" />
                      </div>
                      <div>
                        <h4 className="text-xs font-semibold text-slate-800">Analyzing Document with Mistral OCR</h4>
                        <p className="text-[11px] text-muted-foreground mt-0.5 max-w-sm">
                          Deep document AI is parsing text, tabular matrices, and visual property data...
                        </p>
                      </div>
                    </div>
                  )}

                  {/* OCR Error Alert */}
                  {ocrError && !ocrLoading && (
                    <div className="p-3.5 bg-rose-50 border border-rose-200 rounded-lg text-rose-800 text-xs flex items-start gap-2.5">
                      <AlertTriangle className="w-4 h-4 text-rose-600 shrink-0 mt-0.5" />
                      <div className="flex-1">
                        <span className="font-semibold">Analysis Notice: </span>
                        {ocrError}
                      </div>
                    </div>
                  )}

                  {/* OCR Extracted Content */}
                  {ocrResult && !ocrLoading && (
                    <div className="space-y-4">
                      {/* Property Identity Grid */}
                      <div className="bg-white rounded-lg border border-slate-200 p-4 shadow-2xs space-y-3">
                        <div className="flex items-center justify-between border-b border-slate-100 pb-2">
                          <div className="flex items-center gap-2">
                            <Building2 className="w-4 h-4 text-indigo-600" />
                            <h3 className="text-xs font-semibold text-slate-900">Extracted Property Identity</h3>
                          </div>
                          <Badge variant="outline" className="text-[10px] bg-emerald-50 text-emerald-700 border-emerald-200 font-mono">
                            {ocrResult.pages_count} Page{ocrResult.pages_count > 1 ? "s" : ""} Parsed
                          </Badge>
                        </div>

                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
                          <div className="p-2.5 bg-slate-50 rounded border border-slate-100">
                            <span className="text-[11px] text-muted-foreground uppercase font-semibold">Folio / Parcel ID</span>
                            <p className="font-mono font-bold text-slate-900 text-sm mt-0.5 select-all">
                              {renderSafeValue(ocrResult.property_fields.parcel_number || ocrResult.property_fields.account_number)}
                            </p>
                          </div>

                          <div className="p-2.5 bg-slate-50 rounded border border-slate-100">
                            <span className="text-[11px] text-muted-foreground uppercase font-semibold">Owner of Record</span>
                            <p className="font-semibold text-slate-900 text-sm mt-0.5">
                              {renderSafeValue(ocrResult.property_fields.owner_name)}
                            </p>
                          </div>

                          <div className="p-2.5 bg-slate-50 rounded border border-slate-100">
                            <span className="text-[11px] text-muted-foreground uppercase font-semibold">Site / Property Address</span>
                            <p className="font-medium text-slate-900 mt-0.5">
                              {renderSafeValue(ocrResult.property_fields.property_address)}
                            </p>
                          </div>

                          <div className="p-2.5 bg-slate-50 rounded border border-slate-100">
                            <span className="text-[11px] text-muted-foreground uppercase font-semibold">Mailing Address</span>
                            <p className="font-medium text-slate-900 mt-0.5 whitespace-pre-line">
                              {renderSafeValue(ocrResult.property_fields.mailing_address)}
                            </p>
                          </div>

                          {(ocrResult.property_fields.total_amount_due != null || ocrResult.property_fields.tax_status) && (
                            <div className="p-2.5 bg-slate-50 rounded border border-slate-100">
                              <span className="text-[11px] text-muted-foreground uppercase font-semibold">Total Amount Due</span>
                              <div className="flex items-center gap-2 mt-0.5">
                                <span className="font-mono font-bold text-rose-600 text-sm">
                                  {formatCurrency(ocrResult.property_fields.total_amount_due)}
                                </span>
                                {ocrResult.property_fields.tax_status && (
                                  <Badge
                                    variant="outline"
                                    className={`text-[10px] uppercase font-semibold ${
                                      String(ocrResult.property_fields.tax_status).toLowerCase().includes("unpaid") ||
                                      String(ocrResult.property_fields.tax_status).toLowerCase().includes("delinquent")
                                        ? "bg-rose-100 text-rose-800 border-rose-300"
                                        : "bg-emerald-100 text-emerald-800 border-emerald-300"
                                    }`}
                                  >
                                    {renderSafeValue(ocrResult.property_fields.tax_status)}
                                  </Badge>
                                )}
                              </div>
                            </div>
                          )}

                          <div className="p-2.5 bg-slate-50 rounded border border-slate-100">
                            <span className="text-[11px] text-muted-foreground uppercase font-semibold">Property Use / Zoning</span>
                            <p className="font-medium text-slate-900 mt-0.5">
                              {renderSafeValue(ocrResult.property_fields.property_use)}
                            </p>
                          </div>

                          <div className="p-2.5 bg-slate-50 rounded border border-slate-100">
                            <span className="text-[11px] text-muted-foreground uppercase font-semibold">Taxable / Assessed Value</span>
                            <p className="font-semibold text-emerald-700 text-sm mt-0.5">
                              {formatCurrency(ocrResult.property_fields.taxable_value || ocrResult.property_fields.assessed_value)}
                            </p>
                          </div>

                          {ocrResult.property_fields.land_size && (
                            <div className="p-2.5 bg-slate-50 rounded border border-slate-100">
                              <span className="text-[11px] text-muted-foreground uppercase font-semibold">Land Size</span>
                              <p className="font-medium text-slate-900 mt-0.5">
                                {renderSafeValue(ocrResult.property_fields.land_size)}
                              </p>
                            </div>
                          )}

                          {ocrResult.property_fields.exemptions && (
                            <div className="p-2.5 bg-slate-50 rounded border border-slate-100">
                              <span className="text-[11px] text-muted-foreground uppercase font-semibold">Exemptions</span>
                              <p className="font-medium text-slate-900 mt-0.5">
                                {renderSafeValue(ocrResult.property_fields.exemptions)}
                              </p>
                            </div>
                          )}
                        </div>
                      </div>

                      {/* Extracted Tables */}
                      {ocrResult.tables && ocrResult.tables.length > 0 && (
                        <div className="bg-white rounded-lg border border-slate-200 p-4 shadow-2xs space-y-3">
                          <div className="flex items-center gap-2 border-b border-slate-100 pb-2">
                            <Table2 className="w-4 h-4 text-indigo-600" />
                            <h3 className="text-xs font-semibold text-slate-900">Extracted Structured Tables ({ocrResult.tables.length})</h3>
                          </div>

                          <div className="space-y-3">
                            {ocrResult.tables.map((tbl, tIdx) => (
                              <div key={tIdx} className="border border-slate-200 rounded-md overflow-hidden text-xs">
                                <div className="overflow-x-auto max-h-56">
                                  <table className="w-full text-left border-collapse">
                                    <thead className="bg-slate-100 border-b border-slate-200 text-slate-700 font-semibold sticky top-0">
                                      <tr>
                                        {tbl.headers.map((h, hIdx) => (
                                          <th key={hIdx} className="p-2 border-r border-slate-200 last:border-r-0 whitespace-nowrap text-[11px]">
                                            {h ? h.replace(/\\textcircled\{[^}]*\}/g, "").replace(/\\[\(\)]/g, "").trim() : `Col ${hIdx + 1}`}
                                          </th>
                                        ))}
                                      </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100">
                                      {tbl.rows.map((row, rIdx) => (
                                        <tr key={rIdx} className="hover:bg-slate-50 transition-colors">
                                          {row.map((cell, cIdx) => (
                                            <td key={cIdx} className="p-2 border-r border-slate-100 last:border-r-0 text-slate-800 text-[11px]">
                                              {typeof cell === "object"
                                                ? formatSafeString(cell)
                                                : cell
                                                ? String(cell).replace(/\\textcircled\{[^}]*\}/g, "").replace(/\\[\(\)]/g, "").trim() || "—"
                                                : "—"}
                                            </td>
                                          ))}
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Detected Key-Value Badges */}
                      {ocrResult.key_values && Object.keys(ocrResult.key_values).length > 0 && (
                        <div className="bg-white rounded-lg border border-slate-200 p-4 shadow-2xs space-y-2">
                          <button
                            type="button"
                            onClick={() => setShowAllKVs(!showAllKVs)}
                            className="w-full flex items-center justify-between text-xs font-semibold text-slate-900"
                          >
                            <span className="flex items-center gap-2">
                              <FileCheck className="w-4 h-4 text-indigo-600" />
                              All Detected Attributes ({Object.keys(ocrResult.key_values).length})
                            </span>
                            <span className="text-[11px] text-indigo-600 hover:underline">
                              {showAllKVs ? "Collapse" : "Expand All"}
                            </span>
                          </button>

                          {showAllKVs && (
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-2 border-t border-slate-100 text-xs">
                              {Object.entries(ocrResult.key_values).map(([k, v]) => (
                                <div key={k} className="p-2 rounded bg-slate-50 border border-slate-100 flex flex-col">
                                  <span className="text-[10px] text-muted-foreground uppercase font-semibold">{k}</span>
                                  <span className="text-slate-800 font-medium text-[11px] mt-0.5 truncate" title={formatSafeString(v)}>
                                    {renderSafeValue(v)}
                                  </span>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}

                      {/* Raw Layout Markdown Viewer */}
                      {ocrResult.markdown && (
                        <div className="bg-white rounded-lg border border-slate-200 p-4 shadow-2xs space-y-2">
                          <div className="flex items-center justify-between">
                            <button
                              type="button"
                              onClick={() => setShowRawMd(!showRawMd)}
                              className="flex items-center gap-2 text-xs font-semibold text-slate-900"
                            >
                              <Code2 className="w-4 h-4 text-indigo-600" />
                              Mistral OCR Markdown Output
                              <span className="text-[11px] text-muted-foreground font-normal">
                                ({showRawMd ? "Hide" : "Show"})
                              </span>
                            </button>

                            <Button
                              size="sm"
                              variant="outline"
                              className="h-6 text-[11px] gap-1 px-2 border-slate-200"
                              onClick={() => handleCopyMarkdown(ocrResult.markdown)}
                            >
                              {copiedMd ? <Check className="w-3 h-3 text-emerald-600" /> : <Copy className="w-3 h-3" />}
                              {copiedMd ? "Copied" : "Copy Markdown"}
                            </Button>
                          </div>

                          {showRawMd && (
                            <pre className="p-3 bg-slate-900 text-slate-200 rounded-md text-[11px] font-mono overflow-x-auto max-h-60 leading-relaxed">
                              {ocrResult.markdown}
                            </pre>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
