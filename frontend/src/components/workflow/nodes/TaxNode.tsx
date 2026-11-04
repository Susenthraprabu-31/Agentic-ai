import { Handle, Position, type NodeProps } from "reactflow";
import { Receipt, ShieldAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { WorkflowNodeData } from "@/types";

export function TaxNode({ data, selected }: NodeProps<WorkflowNodeData>) {
  return (
    <div
      className={`min-w-[210px] rounded-lg border-2 bg-white shadow-md transition-all ${
        selected ? "border-emerald-600 ring-2 ring-emerald-100" : "border-emerald-400"
      }`}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-3 !w-3 !bg-emerald-600 !border-2 !border-white"
      />

      <div className="flex items-center justify-between border-b border-emerald-100 bg-emerald-50/70 px-3 py-2 rounded-t-md">
        <div className="flex items-center gap-2">
          <Receipt className="h-4 w-4 text-emerald-600 shrink-0" />
          <span className="text-xs font-bold text-emerald-900 truncate">
            {data.label || "Property Tax Search"}
          </span>
        </div>
        <Badge variant="outline" className="text-[9px] uppercase px-1 border-emerald-200 text-emerald-700">
          Tax
        </Badge>
      </div>

      <div className="p-3 text-[11px] space-y-1.5 bg-white rounded-b-md">
        <div className="flex justify-between items-center text-slate-600">
          <span>Fault Mode:</span>
          {data.allow_failure ? (
            <span className="text-[10px] text-emerald-700 font-medium">Tolerant</span>
          ) : (
            <span className="text-[10px] text-rose-700 font-medium flex items-center gap-0.5">
              <ShieldAlert className="w-2.5 h-2.5" /> Strict
            </span>
          )}
        </div>
        <div className="text-[10px] text-slate-500 truncate">
          {data.recipe_id ? `Bound: ${data.recipe_id.slice(0, 8)}...` : "Recipe: Approved Target"}
        </div>
      </div>

      <Handle
        type="source"
        position={Position.Right}
        className="!h-3 !w-3 !bg-emerald-600 !border-2 !border-white"
      />
    </div>
  );
}
