import { Handle, Position, type NodeProps } from "reactflow";
import { Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { WorkflowNodeData } from "@/types";

export function InputNode({ data, selected }: NodeProps<WorkflowNodeData>) {
  const req = data.required_params || ["parcel_id"];
  const opt = data.optional_params || [];

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
        <div className="text-muted-foreground font-medium">Search Parameters:</div>
        <div className="flex flex-wrap gap-1">
          {req.map((p) => (
            <Badge key={p} className="bg-blue-600 text-white text-[10px] px-1.5 py-0">
              *{p}
            </Badge>
          ))}
          {opt.map((p) => (
            <Badge key={p} variant="outline" className="text-[10px] px-1.5 py-0 text-slate-600">
              {p}
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
