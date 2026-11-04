import { Handle, Position, type NodeProps } from "reactflow";
import { GitMerge } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { WorkflowNodeData } from "@/types";

export function NormalizeNode({ data, selected }: NodeProps<WorkflowNodeData>) {
  const isPartialOk = data.support_partial_success ?? true;
  const mappingsCount = Object.keys(data.mappings || {}).length;

  return (
    <div
      className={`min-w-[210px] rounded-lg border-2 bg-white shadow-md transition-all ${
        selected ? "border-purple-600 ring-2 ring-purple-100" : "border-purple-400"
      }`}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-3 !w-3 !bg-purple-600 !border-2 !border-white"
      />

      <div className="flex items-center justify-between border-b border-purple-100 bg-purple-50/70 px-3 py-2 rounded-t-md">
        <div className="flex items-center gap-2">
          <GitMerge className="h-4 w-4 text-purple-600 shrink-0" />
          <span className="text-xs font-bold text-purple-900 truncate">
            {data.label || "Data Harmonization"}
          </span>
        </div>
      </div>

      <div className="p-3 text-[11px] space-y-1.5 bg-white rounded-b-md">
        <div className="flex justify-between items-center text-slate-600">
          <span>Partial Success:</span>
          {isPartialOk ? (
            <Badge className="bg-purple-100 text-purple-800 text-[10px] border border-purple-300">
              Supported
            </Badge>
          ) : (
            <Badge variant="outline" className="text-[10px] text-slate-500">
              Disabled
            </Badge>
          )}
        </div>
        <div className="text-[10px] text-slate-500">
          {mappingsCount > 0 ? `${mappingsCount} Field Rules` : "Unified Property Schema"}
        </div>
      </div>

      <Handle
        type="source"
        position={Position.Right}
        className="!h-3 !w-3 !bg-purple-600 !border-2 !border-white"
      />
    </div>
  );
}
