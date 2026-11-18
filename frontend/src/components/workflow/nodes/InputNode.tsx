import { Handle, Position, type NodeProps } from "reactflow";
import { Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { WorkflowNodeData } from "@/types";

const SEARCH_PARAM_LABELS: Record<string, string> = {
  parcel_id: "Parcel ID",
  address: "Address",
  owner_name: "Owner",
};

export function InputNode({ data, selected }: NodeProps<WorkflowNodeData>) {
  const configured = [
    ...(data.required_params || []),
    ...(data.optional_params || []),
  ];
  const searchParams =
    configured.length > 0
      ? [...new Set(configured)]
      : ["parcel_id", "address", "owner_name"];

  return (
    <div
      className={`min-w-[200px] rounded-lg border-2 bg-white shadow-md transition-all ${
        selected ? "border-blue-600 ring-2 ring-blue-100" : "border-blue-400"
      }`}
    >
      <div className="flex items-center gap-2 border-b border-blue-100 bg-blue-50/70 px-3 py-2 rounded-t-md">
        <Search className="h-4 w-4 text-blue-600 shrink-0" />
        <span className="text-xs font-bold text-blue-900 truncate">
          {data.label || "Property Search Input"}
        </span>
      </div>

      <div className="p-3 text-[11px] space-y-1.5 bg-white rounded-b-md">
        <div className="text-muted-foreground font-medium">Search (any one):</div>
        <div className="flex flex-wrap gap-1">
          {searchParams.map((p) => (
            <Badge key={p} variant="outline" className="text-[10px] px-1.5 py-0 text-slate-700">
              {SEARCH_PARAM_LABELS[p] ?? p}
            </Badge>
          ))}
        </div>
      </div>

      <Handle
        type="source"
        position={Position.Right}
        className="!h-3 !w-3 !bg-blue-600 !border-2 !border-white"
      />
    </div>
  );
}
