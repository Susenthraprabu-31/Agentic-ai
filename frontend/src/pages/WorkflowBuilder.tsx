import { useCallback, useEffect, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  addEdge,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
} from "reactflow";
import "reactflow/dist/style.css";
import {
  CheckCircle2,
  Database,
  GitMerge,
  Landmark,
  Loader2,
  Play,
  Plus,
  Receipt,
  RotateCcw,
  Save,
  Search,
  ShieldCheck,
  Sliders,
  Sparkles,
  Trash2,
  X,
  XCircle,
} from "lucide-react";
import { workflowsApi } from "@/api/workflows";
import { executionsApi } from "@/api/executions";
import { countySourcesApi } from "@/api/countySources";
import { recipeTemplatesApi } from "@/api/recipeTemplates";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { workflowNodeTypes } from "@/components/workflow/nodes";
import type {
  CountySource,
  Recipe,
  Workflow,
  WorkflowDefinition,
  WorkflowNodeData,
  WorkflowNodeType,
  WorkflowValidationResponse,
  WorkflowVersion,
} from "@/types";

// Default preset definitions
const DUAL_BRANCH_PRESET: WorkflowDefinition = {
  nodes: [
    {
      id: "node-input",
      type: "input",
      position: { x: 80, y: 180 },
      data: {
        label: "Property Search Input",
        required_params: ["parcel_id"],
        optional_params: ["address", "owner_name"],
      },
    },
    {
      id: "node-assessor",
      type: "assessor",
      position: { x: 380, y: 80 },
      data: {
        label: "County Assessor Search",
        allow_failure: true,
      },
    },
    {
      id: "node-tax",
      type: "tax",
      position: { x: 380, y: 280 },
      data: {
        label: "County Tax Search",
        allow_failure: true,
      },
    },
    {
      id: "node-normalize",
      type: "normalize",
      position: { x: 680, y: 180 },
      data: {
        label: "Data Harmonization",
        mappings: {
          parcel_id: "assessor.parcel_id || tax.parcel_id || input.parcel_id",
          property_address: "assessor.address",
          total_assessed_value: "assessor.assessed_value",
          tax_delinquent: "tax.is_delinquent",
        },
        support_partial_success: true,
      },
    },
    {
      id: "node-output",
      type: "output",
      position: { x: 960, y: 180 },
      data: {
        label: "Title Search Output",
        output_format: "json",
      },
    },
  ],
  edges: [
    { id: "e1", source: "node-input", target: "node-assessor" },
    { id: "e2", source: "node-input", target: "node-tax" },
    { id: "e3", source: "node-assessor", target: "node-normalize" },
    { id: "e4", source: "node-tax", target: "node-normalize" },
    { id: "e5", source: "node-normalize", target: "node-output" },
  ],
  execution_config: {
    mode: "parallel_branches",
    allow_partial_success: true,
    timeout_seconds: 120,
  },
};

const ASSESSOR_ONLY_PRESET: WorkflowDefinition = {
  nodes: [
    {
      id: "node-input",
      type: "input",
      position: { x: 100, y: 180 },
      data: {
        label: "Property Search Input",
        required_params: ["parcel_id"],
        optional_params: ["address"],
      },
    },
    {
      id: "node-assessor",
      type: "assessor",
      position: { x: 420, y: 180 },
      data: {
        label: "County Assessor Search",
        allow_failure: false,
      },
    },
    {
      id: "node-output",
      type: "output",
      position: { x: 740, y: 180 },
      data: {
        label: "Assessor Search Output",
        output_format: "json",
      },
    },
  ],
  edges: [
    { id: "e1", source: "node-input", target: "node-assessor" },
    { id: "e2", source: "node-assessor", target: "node-output" },
  ],
};

const TAX_ONLY_PRESET: WorkflowDefinition = {
  nodes: [
    {
      id: "node-input",
      type: "input",
      position: { x: 100, y: 180 },
      data: {
        label: "Property Search Input",
        required_params: ["parcel_id"],
        optional_params: ["owner_name"],
      },
    },
    {
      id: "node-tax",
      type: "tax",
      position: { x: 420, y: 180 },
      data: {
        label: "Property Tax Search",
        allow_failure: false,
      },
    },
    {
      id: "node-output",
      type: "output",
      position: { x: 740, y: 180 },
      data: {
        label: "Tax Search Output",
        output_format: "json",
      },
    },
  ],
  edges: [
    { id: "e1", source: "node-input", target: "node-tax" },
    { id: "e2", source: "node-tax", target: "node-output" },
  ],
};

export default function WorkflowBuilder() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const workflowIdParam = searchParams.get("id");

  // Workflow Metadata State
  const [currentWorkflow, setCurrentWorkflow] = useState<Workflow | null>(null);
  const [workflowName, setWorkflowName] = useState("Standard Title Search Workflow");
  const [workflowDesc, setWorkflowDesc] = useState("Automated Assessor and Tax search workflow");
  const [countySourceId, setCountySourceId] = useState("");
  const [activeVersionNumber, setActiveVersionNumber] = useState<number>(1);
  const [workflowVersions, setWorkflowVersions] = useState<WorkflowVersion[]>([]);

  // React Flow Graph State
  const [nodes, setNodes, onNodesChange] = useNodesState<WorkflowNodeData>(
    DUAL_BRANCH_PRESET.nodes as Node<WorkflowNodeData>[]
  );
  const [edges, setEdges, onEdgesChange] = useEdgesState(
    DUAL_BRANCH_PRESET.edges as Edge[]
  );

  // Selected Node for Inspector
  const [selectedNode, setSelectedNode] = useState<Node<WorkflowNodeData> | null>(null);

  // Validation & Feedback State
  const [validationResult, setValidationResult] = useState<WorkflowValidationResponse | null>(null);
  const [isValidating, setIsValidating] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Save New Version Modal
  const [isNewVersionModalOpen, setIsNewVersionModalOpen] = useState(false);
  const [versionChangelog, setVersionChangelog] = useState("");
  const [markAsCurrentOnSave, setMarkAsCurrentOnSave] = useState(true);

  // Execution Modal State
  const [isExecuteModalOpen, setIsExecuteModalOpen] = useState(false);
  const [execParcelId, setExecParcelId] = useState("");
  const [execAddress, setExecAddress] = useState("");
  const [execOwnerName, setExecOwnerName] = useState("");
  const [execSimulateDelinquent, setExecSimulateDelinquent] = useState(false);
  const [execFailTax, setExecFailTax] = useState(false);
  const [isSubmittingExec, setIsSubmittingExec] = useState(false);

  // External reference data
  const [counties, setCounties] = useState<CountySource[]>([]);
  const [approvedRecipes, setApprovedRecipes] = useState<Recipe[]>([]);

  // Load Reference Data
  useEffect(() => {
    const loadReferences = async () => {
      try {
        const [cRes, rRes] = await Promise.all([
          countySourcesApi.list({ size: 100 }),
          recipeTemplatesApi.getRecipes({ limit: 100 }),
        ]);
        setCounties(cRes.items || []);
        setApprovedRecipes(rRes.items || []);
      } catch (e) {
        console.error(e);
      }
    };
    loadReferences();
  }, []);

  // Load Workflow if ID param exists
  useEffect(() => {
    if (!workflowIdParam) return;
    const fetchWf = async () => {
      try {
        const wf = await workflowsApi.getById(workflowIdParam);
        setCurrentWorkflow(wf);
        setWorkflowName(wf.name);
        setWorkflowDesc(wf.description || "");
        setCountySourceId(wf.county_source_id || "");
        setActiveVersionNumber(wf.current_version_number || 1);
        if (wf.versions && wf.versions.length > 0) {
          setWorkflowVersions(wf.versions);
          const currentVer = wf.versions.find((v) => v.is_current) || wf.versions[0];
          if (currentVer && currentVer.definition) {
            setNodes((currentVer.definition.nodes as Node<WorkflowNodeData>[]) || []);
            setEdges((currentVer.definition.edges as Edge[]) || []);
          }
        }
      } catch (err: any) {
        setErrorMsg(err.response?.data?.detail || "Failed to load workflow.");
      }
    };
    fetchWf();
  }, [workflowIdParam]);

  // Handle Connections
  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((eds) => addEdge({ ...connection, animated: true }, eds));
      setValidationResult(null);
    },
    [setEdges]
  );

  // Handle Node Selection
  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    setSelectedNode(node as Node<WorkflowNodeData>);
  }, []);

  const onPaneClick = useCallback(() => {
    setSelectedNode(null);
  }, []);

  // Palette: Add New Node
  const addNode = (type: WorkflowNodeType) => {
    const id = `node-${type}-${Date.now().toString().slice(-4)}`;
    let initialData: WorkflowNodeData = { label: `${type.toUpperCase()} Node` };

    if (type === "input") {
      initialData = {
        label: "Property Search Input",
        required_params: ["parcel_id"],
        optional_params: ["address"],
      };
    } else if (type === "assessor") {
      initialData = {
        label: "County Assessor Search",
        allow_failure: true,
      };
    } else if (type === "tax") {
      initialData = {
        label: "Property Tax Search",
        allow_failure: true,
      };
    } else if (type === "normalize") {
      initialData = {
        label: "Data Harmonization",
        support_partial_success: true,
        mappings: {
          parcel_id: "assessor.parcel_id || tax.parcel_id || input.parcel_id",
        },
      };
    } else if (type === "output") {
      initialData = {
        label: "Title Search Output",
        output_format: "json",
      };
    }

    const newNode: Node<WorkflowNodeData> = {
      id,
      type,
      position: { x: 250 + Math.random() * 100, y: 150 + Math.random() * 100 },
      data: initialData,
    };

    setNodes((nds) => [...nds, newNode]);
    setSelectedNode(newNode);
    setValidationResult(null);
  };

  // Update Selected Node Data
  const updateSelectedNodeData = (updates: Partial<WorkflowNodeData>) => {
    if (!selectedNode) return;
    setNodes((nds) =>
      nds.map((n) => {
        if (n.id === selectedNode.id) {
          const updated = {
            ...n,
            data: { ...n.data, ...updates },
          };
          setSelectedNode(updated);
          return updated;
        }
        return n;
      })
    );
    setValidationResult(null);
  };

  // Delete Selected Node
  const deleteSelectedNode = () => {
    if (!selectedNode) return;
    setNodes((nds) => nds.filter((n) => n.id !== selectedNode.id));
    setEdges((eds) => eds.filter((e) => e.source !== selectedNode.id && e.target !== selectedNode.id));
    setSelectedNode(null);
    setValidationResult(null);
  };

  // Preset Applicator
  const applyPreset = (preset: WorkflowDefinition, name: string) => {
    if (!confirm(`Apply the '${name}' preset? Current unsaved canvas changes will be replaced.`)) return;
    setNodes(preset.nodes as Node<WorkflowNodeData>[]);
    setEdges(preset.edges as Edge[]);
    setSelectedNode(null);
    setValidationResult(null);
  };

  // Build Graph Definition Object
  const getCurrentDefinition = (): WorkflowDefinition => {
    return {
      nodes: nodes.map((n) => ({
        id: n.id,
        type: n.type as WorkflowNodeType,
        position: n.position,
        data: n.data,
      })),
      edges: edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        label: typeof e.label === "string" ? e.label : undefined,
      })),
      execution_config: {
        mode: "parallel_branches",
        allow_partial_success: true,
        timeout_seconds: 120,
      },
    };
  };

  // Validate Graph
  const validateCurrentGraph = async () => {
    setIsValidating(true);
    setValidationResult(null);
    setErrorMsg(null);
    try {
      const def = getCurrentDefinition();
      const res = await workflowsApi.validate(def);
      setValidationResult(res);
      if (res.is_valid) {
        setSuccessMsg(`Workflow valid! Topological Execution Order: ${res.execution_order.join(" ➔ ")}`);
      } else {
        setErrorMsg(`Workflow validation issues: ${res.errors.join("; ")}`);
      }
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Validation request failed.");
    } finally {
      setIsValidating(false);
    }
  };

  // Save / Update Workflow
  const handleSaveWorkflow = async () => {
    setIsSaving(true);
    setErrorMsg(null);
    try {
      const def = getCurrentDefinition();
      if (!currentWorkflow) {
        // Create new workflow
        const newWf = await workflowsApi.create({
          name: workflowName,
          description: workflowDesc || undefined,
          county_source_id: countySourceId || undefined,
          initial_definition: def,
        });
        setCurrentWorkflow(newWf);
        setSearchParams({ id: newWf.id });
        setSuccessMsg(`Workflow '${newWf.name}' created successfully with Version 1.`);
      } else {
        // Update workflow metadata and create new version
        await workflowsApi.update(currentWorkflow.id, {
          name: workflowName,
          description: workflowDesc || undefined,
          county_source_id: countySourceId || undefined,
        });
        const newVer = await workflowsApi.createVersion(currentWorkflow.id, {
          definition: def,
          changelog: "Canvas updates saved from builder",
          is_current: true,
        });
        setActiveVersionNumber(newVer.version_number);
        setSuccessMsg(`Saved updates as Version ${newVer.version_number}.`);
      }
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to save workflow.");
    } finally {
      setIsSaving(false);
    }
  };

  // Save as New Version with Custom Changelog
  const handleSaveNewVersion = async () => {
    if (!currentWorkflow) return;
    setIsSaving(true);
    setErrorMsg(null);
    try {
      const def = getCurrentDefinition();
      const newVer = await workflowsApi.createVersion(currentWorkflow.id, {
        definition: def,
        changelog: versionChangelog || undefined,
        is_current: markAsCurrentOnSave,
      });
      setIsNewVersionModalOpen(false);
      setVersionChangelog("");
      if (markAsCurrentOnSave) {
        setActiveVersionNumber(newVer.version_number);
      }
      setSuccessMsg(`Created new Version ${newVer.version_number}!`);
      const updatedVersions = await workflowsApi.listVersions(currentWorkflow.id);
      setWorkflowVersions(updatedVersions);
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to create new version.");
    } finally {
      setIsSaving(false);
    }
  };

  // Switch Active Version
  const handleSwitchVersion = async (versionId: string) => {
    if (!currentWorkflow) return;
    try {
      const ver = await workflowsApi.getVersion(currentWorkflow.id, versionId);
      if (ver.definition) {
        setNodes((ver.definition.nodes as Node<WorkflowNodeData>[]) || []);
        setEdges((ver.definition.edges as Edge[]) || []);
        setActiveVersionNumber(ver.version_number);
        setSuccessMsg(`Loaded Version ${ver.version_number}.`);
      }
    } catch (e: any) {
      setErrorMsg("Failed to load version.");
    }
  };

  // AI Page Analyzer — inspects county portal, discovers tabs and selectors, and saves recipe
  const handleAnalyzePageWithAI = async () => {
    let targetCountyId = selectedNode?.data?.county_source_id || countySourceId;
    if (!targetCountyId) {
      const assessorNode = nodes.find((n) => n.type === "assessor");
      if (assessorNode?.data?.county_source_id) {
        targetCountyId = assessorNode.data.county_source_id;
      } else if (counties.length > 0) {
        const collier = counties.find((c) => c.name.toLowerCase().includes("collier"));
        targetCountyId = collier ? collier.id : counties[0].id;
      }
    }

    if (!targetCountyId) {
      setErrorMsg("Please select a target county first.");
      return;
    }

    setIsAnalyzing(true);
    setErrorMsg(null);
    setSuccessMsg(null);

    try {
      const res = await countySourcesApi.analyzeRecipe(targetCountyId, {
        record_type: selectedNode?.type === "tax" ? "tax" : "assessor",
      });

      // Refresh approved recipes list so dropdown has the new recipe version
      const rRes = await recipeTemplatesApi.getRecipes({ limit: 100 });
      setApprovedRecipes(rRes.items || []);

      // Automatically bind recipe to selected node or assessor node
      setNodes((nds) =>
        nds.map((n) => {
          if (
            (selectedNode && n.id === selectedNode.id) ||
            (!selectedNode && n.type === "assessor")
          ) {
            return {
              ...n,
              data: {
                ...n.data,
                county_source_id: targetCountyId,
                recipe_id: res.recipe_id,
                recipe_version_id: res.recipe_version_id,
              },
            };
          }
          return n;
        })
      );

      if (selectedNode) {
        setSelectedNode((prev) =>
          prev
            ? {
                ...prev,
                data: {
                  ...prev.data,
                  county_source_id: targetCountyId,
                  recipe_id: res.recipe_id,
                  recipe_version_id: res.recipe_version_id,
                },
              }
            : null
        );
      }

      setSuccessMsg(
        `AI Analysis complete for ${res.county_name} County! Generated Recipe (v${res.version_number}) with ${res.discovered_tabs?.length || 0} tabs (${res.discovered_tabs?.map((t: any) => t.text).join(", ")}). Actions saved to database and bound to Assessor node.`
      );
    } catch (err: any) {
      setErrorMsg(
        err.response?.data?.detail || "AI Page Analysis failed. Check county portal connection."
      );
    } finally {
      setIsAnalyzing(false);
    }
  };

  // Trigger Workflow Execution
  const handleExecuteWorkflow = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!currentWorkflow) return;
    setIsSubmittingExec(true);
    setErrorMsg(null);
    try {
      const run = await executionsApi.executeWorkflow(currentWorkflow.id, {
        input_data: {
          parcel_id: execParcelId.trim(),
          address: execAddress.trim(),
          owner_name: execOwnerName.trim(),
          simulate_delinquent: execSimulateDelinquent,
          fail_tax: execFailTax,
        },
      });
      setIsExecuteModalOpen(false);
      navigate(`/execution-results?id=${run.id}`);
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to trigger workflow execution.");
    } finally {
      setIsSubmittingExec(false);
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-7.5rem)] gap-3">
      {/* Top Controls Toolbar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between bg-white rounded-lg border p-3 gap-3 shadow-sm">
        <div className="flex items-center gap-3 flex-wrap">
          <Input
            value={workflowName}
            onChange={(e) => setWorkflowName(e.target.value)}
            className="font-bold text-sm h-8 w-64 text-slate-900 border-slate-300"
            placeholder="Workflow Name"
          />

          <select
            value={countySourceId}
            onChange={(e) => setCountySourceId(e.target.value)}
            className="text-xs border rounded-md px-2.5 py-1.5 bg-white text-slate-700 border-slate-300 h-8"
          >
            <option value="">-- Generic (All Counties) --</option>
            {counties.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}, {c.state}
              </option>
            ))}
          </select>

          {currentWorkflow && (
            <div className="flex items-center gap-1.5">
              <Badge className="bg-blue-100 text-blue-800 text-xs border border-blue-200">
                v{activeVersionNumber} Active
              </Badge>
              {workflowVersions.length > 1 && (
                <select
                  onChange={(e) => handleSwitchVersion(e.target.value)}
                  className="text-xs border rounded px-1.5 py-1 text-slate-600 bg-slate-50"
                  defaultValue=""
                >
                  <option value="" disabled>
                    Switch Version...
                  </option>
                  {workflowVersions.map((v) => (
                    <option key={v.id} value={v.id}>
                      v{v.version_number} {v.is_current ? "(Current)" : ""}
                    </option>
                  ))}
                </select>
              )}
            </div>
          )}
        </div>

        {/* Toolbar Buttons */}
        <div className="flex items-center gap-2 flex-wrap">
          {/* Preset templates selector */}
          <select
            onChange={(e) => {
              if (e.target.value === "dual") applyPreset(DUAL_BRANCH_PRESET, "Dual Branch");
              if (e.target.value === "assessor") applyPreset(ASSESSOR_ONLY_PRESET, "Assessor Only");
              if (e.target.value === "tax") applyPreset(TAX_ONLY_PRESET, "Tax Only");
              e.target.value = "";
            }}
            defaultValue=""
            className="text-xs border rounded-md px-2 py-1.5 text-slate-700 bg-slate-50 border-slate-300 h-8"
          >
            <option value="" disabled>
              Preset Templates...
            </option>
            <option value="dual">Dual Branch (Assessor + Tax)</option>
            <option value="assessor">Assessor Only</option>
            <option value="tax">Tax Only</option>
          </select>

          <Button
            variant="outline"
            size="sm"
            onClick={validateCurrentGraph}
            disabled={isValidating}
            className="h-8 text-xs flex items-center gap-1 text-blue-700 border-blue-200 hover:bg-blue-50"
          >
            {isValidating ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <ShieldCheck className="w-3.5 h-3.5" />
            )}
            Validate Graph
          </Button>

          <Button
            variant="outline"
            size="sm"
            onClick={handleAnalyzePageWithAI}
            disabled={isAnalyzing}
            className="h-8 text-xs border-purple-200 text-purple-700 hover:bg-purple-50 flex items-center gap-1.5 font-medium"
          >
            {isAnalyzing ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin text-purple-600" />
            ) : (
              <Sparkles className="w-3.5 h-3.5 text-purple-600" />
            )}
            {isAnalyzing ? "Analyzing Page..." : "Analyze Page with AI"}
          </Button>

          {currentWorkflow && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIsNewVersionModalOpen(true)}
              className="h-8 text-xs"
            >
              <Plus className="w-3.5 h-3.5 mr-1" />
              New Version
            </Button>
          )}

          <Button
            size="sm"
            onClick={handleSaveWorkflow}
            disabled={isSaving}
            className="h-8 text-xs bg-blue-600 hover:bg-blue-700 text-white flex items-center gap-1.5"
          >
            {isSaving ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Save className="w-3.5 h-3.5" />
            )}
            {currentWorkflow ? "Save Updates" : "Create Workflow"}
          </Button>

          {currentWorkflow && (
            <Button
              size="sm"
              onClick={() => setIsExecuteModalOpen(true)}
              className="h-8 text-xs bg-emerald-600 hover:bg-emerald-700 text-white flex items-center gap-1.5 font-semibold"
            >
              <Play className="w-3.5 h-3.5 fill-current" />
              Execute Workflow
            </Button>
          )}
        </div>
      </div>

      {/* Global Alerts */}
      {errorMsg && (
        <div className="p-2.5 text-xs bg-rose-50 border border-rose-200 text-rose-800 rounded-md flex items-center justify-between">
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
        <div className="p-2.5 text-xs bg-emerald-50 border border-emerald-200 text-emerald-800 rounded-md flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
            <span>{successMsg}</span>
          </div>
          <button onClick={() => setSuccessMsg(null)} className="text-emerald-500 hover:text-emerald-700">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {validationResult && (
        <div
          className={`p-2.5 text-xs rounded-md border flex flex-col gap-1.5 ${
            validationResult.is_valid
              ? "bg-slate-50 border-slate-200 text-slate-800"
              : "bg-rose-50 border-rose-200 text-rose-800"
          }`}
        >
          <div className="flex items-center justify-between font-semibold">
            <div className="flex items-center gap-2">
              <ShieldCheck
                className={`w-4 h-4 ${
                  validationResult.is_valid ? "text-emerald-600" : "text-rose-600"
                }`}
              />
              <span>
                Topology Analysis: {validationResult.node_count} Nodes, {validationResult.edge_count} Connections
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              {validationResult.supports_assessor && (
                <Badge className="bg-indigo-100 text-indigo-800 text-[10px]">Assessor Branch</Badge>
              )}
              {validationResult.supports_tax && (
                <Badge className="bg-emerald-100 text-emerald-800 text-[10px]">Tax Branch</Badge>
              )}
              {validationResult.supports_partial_success && (
                <Badge className="bg-purple-100 text-purple-800 text-[10px]">Partial Fault Tolerance</Badge>
              )}
            </div>
          </div>

          {validationResult.warnings.length > 0 && (
            <div className="text-[11px] text-amber-700">
              <span className="font-semibold">Notice: </span>
              {validationResult.warnings.join("; ")}
            </div>
          )}
        </div>
      )}

      {/* Main Canvas + Sidebars Layout */}
      <div className="flex flex-1 gap-3 overflow-hidden">
        {/* Left Sidebar: Node Palette */}
        <div className="w-48 bg-white border rounded-lg p-3 flex flex-col gap-2 shrink-0 shadow-sm">
          <div className="text-xs font-bold uppercase tracking-wider text-slate-500 pb-1 border-b">
            Add Node
          </div>

          <button
            onClick={() => addNode("input")}
            className="flex items-center gap-2 p-2 rounded border border-blue-200 bg-blue-50/60 hover:bg-blue-100 text-blue-900 text-xs font-medium transition-colors text-left"
          >
            <Search className="w-3.5 h-3.5 text-blue-600 shrink-0" />
            <div>
              <div>Input</div>
              <div className="text-[10px] text-blue-600 font-normal">Search parameters</div>
            </div>
          </button>

          <button
            onClick={() => addNode("assessor")}
            className="flex items-center gap-2 p-2 rounded border border-indigo-200 bg-indigo-50/60 hover:bg-indigo-100 text-indigo-900 text-xs font-medium transition-colors text-left"
          >
            <Landmark className="w-3.5 h-3.5 text-indigo-600 shrink-0" />
            <div>
              <div>Assessor</div>
              <div className="text-[10px] text-indigo-600 font-normal">Property records</div>
            </div>
          </button>

          <button
            onClick={() => addNode("tax")}
            className="flex items-center gap-2 p-2 rounded border border-emerald-200 bg-emerald-50/60 hover:bg-emerald-100 text-emerald-900 text-xs font-medium transition-colors text-left"
          >
            <Receipt className="w-3.5 h-3.5 text-emerald-600 shrink-0" />
            <div>
              <div>Tax</div>
              <div className="text-[10px] text-emerald-600 font-normal">Taxes & liens</div>
            </div>
          </button>

          <button
            onClick={() => addNode("normalize")}
            className="flex items-center gap-2 p-2 rounded border border-purple-200 bg-purple-50/60 hover:bg-purple-100 text-purple-900 text-xs font-medium transition-colors text-left"
          >
            <GitMerge className="w-3.5 h-3.5 text-purple-600 shrink-0" />
            <div>
              <div>Normalize</div>
              <div className="text-[10px] text-purple-600 font-normal">Harmonize data</div>
            </div>
          </button>

          <button
            onClick={() => addNode("output")}
            className="flex items-center gap-2 p-2 rounded border border-amber-200 bg-amber-50/60 hover:bg-amber-100 text-amber-900 text-xs font-medium transition-colors text-left"
          >
            <Database className="w-3.5 h-3.5 text-amber-600 shrink-0" />
            <div>
              <div>Output</div>
              <div className="text-[10px] text-amber-600 font-normal">Delivery result</div>
            </div>
          </button>

          {/* Canvas Actions */}
          <div className="pt-3 mt-auto border-t space-y-1.5">
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                if (confirm("Reset canvas to empty?")) {
                  setNodes([]);
                  setEdges([]);
                  setSelectedNode(null);
                  setValidationResult(null);
                }
              }}
              className="w-full text-xs h-7 text-slate-600 hover:text-rose-600"
            >
              <RotateCcw className="w-3 h-3 mr-1" />
              Clear Canvas
            </Button>
          </div>
        </div>

        {/* Center: React Flow Canvas */}
        <div className="flex-1 rounded-lg border overflow-hidden bg-slate-50 relative shadow-inner">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={workflowNodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={onNodeClick}
            onPaneClick={onPaneClick}
            fitView
          >
            <Background color="#cbd5e1" gap={16} size={1} />
            <Controls className="!bg-white !border !border-slate-200 !shadow-sm !rounded-md" />
            <MiniMap
              className="!border !border-slate-200 !rounded-md !bg-white/80"
              nodeColor={(n) => {
                if (n.type === "input") return "#3b82f6";
                if (n.type === "assessor") return "#6366f1";
                if (n.type === "tax") return "#10b981";
                if (n.type === "normalize") return "#a855f7";
                return "#f59e0b";
              }}
            />
          </ReactFlow>
        </div>

        {/* Right Drawer: Selected Node Inspector */}
        {selectedNode && (
          <div className="w-72 bg-white border rounded-lg p-3 flex flex-col gap-3 shrink-0 shadow-sm overflow-y-auto">
            <div className="flex items-center justify-between pb-2 border-b">
              <div className="flex items-center gap-1.5">
                <Sliders className="w-4 h-4 text-blue-600" />
                <span className="text-xs font-bold text-slate-800">Node Configuration</span>
              </div>
              <button
                onClick={() => setSelectedNode(null)}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="space-y-3 text-xs">
              <div>
                <Label className="text-[11px] font-semibold text-slate-600">Label</Label>
                <Input
                  value={selectedNode.data.label || ""}
                  onChange={(e) => updateSelectedNodeData({ label: e.target.value })}
                  className="mt-1 h-7 text-xs"
                />
              </div>

              {/* Type specific configurations */}
              {selectedNode.type === "input" && (
                <div className="space-y-2">
                  <Label className="text-[11px] font-semibold text-slate-600">
                    Required Search Parameters
                  </Label>
                  <Input
                    value={(selectedNode.data.required_params || []).join(", ")}
                    onChange={(e) =>
                      updateSelectedNodeData({
                        required_params: e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                      })
                    }
                    placeholder="parcel_id, address"
                    className="h-7 text-xs"
                  />
                  <span className="text-[10px] text-muted-foreground">
                    Comma separated parameters required from API clients.
                  </span>
                </div>
              )}

              {(selectedNode.type === "assessor" || selectedNode.type === "tax") && (
                <div className="space-y-2.5">
                  <div>
                    <Label className="text-[11px] font-semibold text-slate-600">Target County</Label>
                    <select
                      value={selectedNode.data.county_source_id || ""}
                      onChange={(e) => updateSelectedNodeData({ county_source_id: e.target.value })}
                      className="w-full mt-1 border rounded px-2 py-1 bg-white text-slate-800 text-xs h-7"
                    >
                      <option value="">-- Inherit from Workflow --</option>
                      {counties.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}, {c.state}
                        </option>
                      ))}
                    </select>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={handleAnalyzePageWithAI}
                      disabled={isAnalyzing}
                      className="w-full text-xs border-purple-200 text-purple-700 hover:bg-purple-50 flex items-center justify-center gap-1.5 h-7 mt-1 font-medium"
                    >
                      {isAnalyzing ? (
                        <Loader2 className="w-3 h-3 animate-spin text-purple-600" />
                      ) : (
                        <Sparkles className="w-3 h-3 text-purple-600" />
                      )}
                      {isAnalyzing ? "Analyzing County Portal..." : "Analyze Page with AI & Save Recipe"}
                    </Button>
                  </div>

                  <div>
                    <Label className="text-[11px] font-semibold text-slate-600">Bind Approved Recipe</Label>
                    <select
                      value={selectedNode.data.recipe_id || ""}
                      onChange={(e) => updateSelectedNodeData({ recipe_id: e.target.value })}
                      className="w-full mt-1 border rounded px-2 py-1 bg-white text-slate-800 text-xs h-7"
                    >
                      <option value="">-- Auto-resolve at Runtime --</option>
                      {approvedRecipes
                        .filter((r) => r.record_type === selectedNode.type)
                        .map((r) => (
                          <option key={r.id} value={r.id}>
                            {r.name} ({r.status})
                          </option>
                        ))}
                    </select>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={handleAnalyzePageWithAI}
                      disabled={isAnalyzing}
                      className="w-full text-xs border-purple-200 text-purple-700 hover:bg-purple-50 flex items-center justify-center gap-1.5 h-7 mt-1 font-medium"
                    >
                      {isAnalyzing ? (
                        <Loader2 className="w-3 h-3 animate-spin text-purple-600" />
                      ) : (
                        <Sparkles className="w-3 h-3 text-purple-600" />
                      )}
                      {isAnalyzing ? "Analyzing County Portal..." : "Analyze Page with AI & Save Recipe"}
                    </Button>
                  </div>

                  <div className="flex items-center justify-between pt-1">
                    <Label className="text-[11px] font-semibold text-slate-600">
                      Fault Tolerant (Allow Failure)
                    </Label>
                    <input
                      type="checkbox"
                      checked={selectedNode.data.allow_failure ?? true}
                      onChange={(e) => updateSelectedNodeData({ allow_failure: e.target.checked })}
                      className="rounded border-slate-300"
                    />
                  </div>
                  <span className="text-[10px] text-muted-foreground block">
                    If enabled, search continues and yields partial records if this branch encounters missing data or blocks.
                  </span>
                </div>
              )}

              {selectedNode.type === "normalize" && (
                <div className="space-y-2.5">
                  <div className="flex items-center justify-between">
                    <Label className="text-[11px] font-semibold text-slate-600">
                      Support Partial Success
                    </Label>
                    <input
                      type="checkbox"
                      checked={selectedNode.data.support_partial_success ?? true}
                      onChange={(e) => updateSelectedNodeData({ support_partial_success: e.target.checked })}
                      className="rounded border-slate-300"
                    />
                  </div>
                  <span className="text-[10px] text-muted-foreground block">
                    Harmonizes available fields even if one branch failed.
                  </span>

                  <div>
                    <Label className="text-[11px] font-semibold text-slate-600">
                      Harmonization Mappings (JSON)
                    </Label>
                    <textarea
                      value={JSON.stringify(selectedNode.data.mappings || {}, null, 2)}
                      onChange={(e) => {
                        try {
                          const parsed = JSON.parse(e.target.value);
                          updateSelectedNodeData({ mappings: parsed });
                        } catch {
                          // ignore json syntax error during typing
                        }
                      }}
                      rows={5}
                      className="w-full mt-1 font-mono text-[10px] p-2 border rounded bg-slate-50 text-slate-800"
                    />
                  </div>
                </div>
              )}

              {selectedNode.type === "output" && (
                <div className="space-y-2">
                  <Label className="text-[11px] font-semibold text-slate-600">Output Format</Label>
                  <select
                    value={selectedNode.data.output_format || "json"}
                    onChange={(e) => updateSelectedNodeData({ output_format: e.target.value })}
                    className="w-full mt-1 border rounded px-2 py-1 bg-white text-slate-800 text-xs h-7"
                  >
                    <option value="json">Structured JSON</option>
                    <option value="tabular">Tabular Summary</option>
                    <option value="pdf_ready">PDF Report Format</option>
                  </select>
                </div>
              )}

              <div className="pt-3 border-t">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={deleteSelectedNode}
                  className="w-full text-xs text-rose-600 border-rose-200 hover:bg-rose-50 h-7"
                >
                  <Trash2 className="w-3.5 h-3.5 mr-1" />
                  Delete Node
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Save New Version Modal */}
      {isNewVersionModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6 space-y-4">
            <div className="flex items-center justify-between border-b pb-3">
              <h3 className="text-base font-semibold text-slate-900">
                Save as New Workflow Version
              </h3>
              <button
                onClick={() => setIsNewVersionModalOpen(false)}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-3 text-xs">
              <div>
                <Label className="text-xs">Version Changelog Note</Label>
                <Input
                  placeholder="e.g. Added tax branch and partial fault tolerance"
                  value={versionChangelog}
                  onChange={(e) => setVersionChangelog(e.target.value)}
                  className="mt-1 text-xs"
                />
              </div>

              <div className="flex items-center gap-2 pt-1">
                <input
                  type="checkbox"
                  id="mark-current"
                  checked={markAsCurrentOnSave}
                  onChange={(e) => setMarkAsCurrentOnSave(e.target.checked)}
                  className="rounded border-slate-300"
                />
                <Label htmlFor="mark-current" className="text-xs text-slate-700 cursor-pointer">
                  Set this new version as the active execution target
                </Label>
              </div>

              <div className="flex justify-end gap-2 pt-3 border-t">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setIsNewVersionModalOpen(false)}
                >
                  Cancel
                </Button>
                <Button size="sm" onClick={handleSaveNewVersion} disabled={isSaving}>
                  {isSaving && <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" />}
                  Save Version
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Execute Workflow Modal */}
      {isExecuteModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6 space-y-4">
            <div className="flex items-center justify-between border-b pb-3">
              <div>
                <h3 className="text-base font-semibold text-slate-900">
                  Execute Workflow: {workflowName}
                </h3>
                <span className="text-xs text-muted-foreground">
                  Triggers background execution via Celery worker
                </span>
              </div>
              <button
                onClick={() => setIsExecuteModalOpen(false)}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleExecuteWorkflow} className="space-y-3.5 text-xs">
              <div>
                <Label className="text-xs font-semibold text-slate-700">Parcel ID *</Label>
                <Input
                  required
                  placeholder="e.g. 01-002-0034"
                  value={execParcelId}
                  onChange={(e) => setExecParcelId(e.target.value)}
                  className="mt-1 text-xs"
                />
              </div>

              <div>
                <Label className="text-xs font-semibold text-slate-700">Property Address</Label>
                <Input
                  placeholder="e.g. 123 Main St, Orlando, FL"
                  value={execAddress}
                  onChange={(e) => setExecAddress(e.target.value)}
                  className="mt-1 text-xs"
                />
              </div>

              <div>
                <Label className="text-xs font-semibold text-slate-700">Owner Name</Label>
                <Input
                  placeholder="e.g. DOE JOHN"
                  value={execOwnerName}
                  onChange={(e) => setExecOwnerName(e.target.value)}
                  className="mt-1 text-xs"
                />
              </div>

              <div className="p-3 bg-slate-50 border rounded-md space-y-2">
                <div className="text-[11px] font-bold text-slate-600 uppercase tracking-wider">
                  Test Simulation Options
                </div>
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    id="sim-delinquent"
                    checked={execSimulateDelinquent}
                    onChange={(e) => setExecSimulateDelinquent(e.target.checked)}
                    className="rounded border-slate-300"
                  />
                  <Label htmlFor="sim-delinquent" className="text-xs text-slate-700 cursor-pointer">
                    Simulate Delinquent Tax Bill (Amount Due &gt; $0)
                  </Label>
                </div>
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    id="sim-fail-tax"
                    checked={execFailTax}
                    onChange={(e) => setExecFailTax(e.target.checked)}
                    className="rounded border-slate-300"
                  />
                  <Label htmlFor="sim-fail-tax" className="text-xs text-slate-700 cursor-pointer">
                    Simulate Tax Branch Failure (Verify Partial Success)
                  </Label>
                </div>
              </div>

              <div className="flex justify-end gap-2 pt-3 border-t">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setIsExecuteModalOpen(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  size="sm"
                  disabled={isSubmittingExec}
                  className="bg-emerald-600 hover:bg-emerald-700 text-white"
                >
                  {isSubmittingExec ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" />
                  ) : (
                    <Play className="w-3.5 h-3.5 mr-1.5 fill-current" />
                  )}
                  Submit Background Execution
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
