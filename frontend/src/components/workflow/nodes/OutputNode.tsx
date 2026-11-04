import { Handle, Position, type NodeProps } from "reactflow";
import { Database } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { WorkflowNodeData } from "@/types";

export function OutputNode({ data, selected }: NodeProps<WorkflowNodeData>) {
  return (
    <div
      className={`min-w-[190px] rounded-lg border-2 bg-white shadow-md transition-all ${
        selected ? "border-amber-600 ring-2 ring-amber-100" : "border-amber-400"
      }`}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-3 !w-3 !bg-amber-600 !border-2 !border-white"
      />

      <div className="flex items-center gap-2 border-b border-amber-100 bg-amber-50/70 px-3 py-2 rounded-t-md">
        <Database className="h-4 w-4 text-amber-600 shrink-0" />
        <span className="text-xs font-bold text-amber-900 truncate">
          {data.label || "Title Search Output"}
        </span>
      </div>

      <div className="p-3 text-[11px] space-y-1 bg-white rounded-b-md">
        <div className="flex justify-between items-center text-slate-600">
          <span>Format:</span>
          <Badge variant="outline" className="text-[10px] uppercase font-mono">
            {data.output_format || "JSON"}
          </Badge>
        </div>
        <div className="text-[10px] text-slate-400">Terminal delivery node</div>
      </div>
    </div>
  );
}
