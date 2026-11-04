import React, { useEffect, useState } from "react";
import {
  BookOpen,
  CheckCircle2,
  Code2,
  Edit3,
  Layers,
  Loader2,
  Lock,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
  X,
  XCircle,
} from "lucide-react";
import { recipeTemplatesApi } from "@/api/recipeTemplates";
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
import type {
  UITemplate,
  TemplateVersion,
  Recipe,
  RecipeVersion,
  ValidationCase,
  RecipeLifecycleStatus,
  CountySource,
  SourceConfiguration,
  CompatibilityCheckResponse,
} from "@/types";

function lifecycleBadge(status: RecipeLifecycleStatus) {
  switch (status) {
    case "published":
      return <Badge className="bg-emerald-600 hover:bg-emerald-700 text-white font-medium">Published</Badge>;
    case "approved":
      return <Badge className="bg-blue-600 hover:bg-blue-700 text-white font-medium">Approved</Badge>;
    case "validated":
      return <Badge className="bg-indigo-600 hover:bg-indigo-700 text-white font-medium">Validated</Badge>;
    case "testing":
      return <Badge className="bg-amber-500 hover:bg-amber-600 text-white font-medium">Testing</Badge>;
    case "deprecated":
      return <Badge variant="secondary" className="font-medium">Deprecated</Badge>;
    default:
      return <Badge variant="outline" className="font-medium text-slate-600">Draft</Badge>;
  }
}

export default function RecipeTemplates() {
  const [activeMainTab, setActiveMainTab] = useState<"recipes" | "templates" | "editor" | "validation">("recipes");

  // Global feedback
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Common data: County Sources & Configurations
  const [countySources, setCountySources] = useState<CountySource[]>([]);

  // ──────────────────────────────────────────────────────────────────────────
  // RECIPES STATE
  // ──────────────────────────────────────────────────────────────────────────
  const [recipes, setRecipes] = useState<Recipe[]>([]);
  const [recipeSearch, setRecipeSearch] = useState("");
  const [recipeRecordTypeFilter, setRecipeRecordTypeFilter] = useState<string>("all");
  const [recipeStatusFilter, setRecipeStatusFilter] = useState<string>("all");

  // New Recipe Modal
  const [isNewRecipeModalOpen, setIsNewRecipeModalOpen] = useState(false);
  const [newRecipeName, setNewRecipeName] = useState("");
  const [newRecipeDesc, setNewRecipeDesc] = useState("");
  const [newRecipeCountyId, setNewRecipeCountyId] = useState("");
  const [newRecipeRecordType, setNewRecipeRecordType] = useState<"assessor" | "tax">("assessor");
  const [newRecipeConfigId, setNewRecipeConfigId] = useState("");
  const [newRecipeTemplateId, setNewRecipeTemplateId] = useState("");
  const [countyConfigs, setCountyConfigs] = useState<SourceConfiguration[]>([]);

  // Recipe Versions & Details Modal
  const [selectedRecipeForDetails, setSelectedRecipeForDetails] = useState<Recipe | null>(null);
  const [recipeVersions, setRecipeVersions] = useState<RecipeVersion[]>([]);
  const [loadingVersions, setLoadingVersions] = useState(false);

  // ──────────────────────────────────────────────────────────────────────────
  // UI TEMPLATES STATE
  // ──────────────────────────────────────────────────────────────────────────
  const [templates, setTemplates] = useState<UITemplate[]>([]);
  const [templateSearch, setTemplateSearch] = useState("");
  const [patternTypeFilter, setPatternTypeFilter] = useState("all");

  // New UI Template Modal
  const [isNewTemplateModalOpen, setIsNewTemplateModalOpen] = useState(false);
  const [newTplName, setNewTplName] = useState("");
  const [newTplDesc, setNewTplDesc] = useState("");
  const [newTplPattern, setNewTplPattern] = useState("standard_form");
  const [newTplSourceTypes, setNewTplSourceTypes] = useState("assessor, tax");
  const [newTplSearchMethods, setNewTplSearchMethods] = useState("FORM_SUBMIT, GET");
  const [newTplReqFields, setNewTplReqFields] = useState("parcel_id");
  const [newTplDefaultSchemaJson, setNewTplDefaultSchemaJson] = useState('{\n  "parcel_id": { "type": "string", "description": "County parcel number" }\n}');
  const [newTplInitialDefJson, setNewTplInitialDefJson] = useState('{\n  "steps": [\n    {\n      "action": "navigate",\n      "url": "{{search_url}}"\n    },\n    {\n      "action": "fill",\n      "locator": { "strategy": "css", "value": "#parcel-input" },\n      "value": "{{input.parcel_id}}"\n    },\n    {\n      "action": "click",\n      "locator": { "strategy": "css", "value": "button[type=\'submit\']" }\n    }\n  ]\n}');

  // Template Versions Modal
  const [selectedTemplateForVersions, setSelectedTemplateForVersions] = useState<UITemplate | null>(null);
  const [templateVersionsList, setTemplateVersionsList] = useState<TemplateVersion[]>([]);
  const [isNewTemplateVersionModalOpen, setIsNewTemplateVersionModalOpen] = useState(false);
  const [newTplVerChangelog, setNewTplVerChangelog] = useState("");
  const [newTplVerDefJson, setNewTplVerDefJson] = useState('{\n  "steps": []\n}');

  // ──────────────────────────────────────────────────────────────────────────
  // RECIPE EDITOR & COMPATIBILITY STATE
  // ──────────────────────────────────────────────────────────────────────────
  const [editorRecipeId, setEditorRecipeId] = useState<string>("");
  const [editorSelectedRecipe, setEditorSelectedRecipe] = useState<Recipe | null>(null);
  const [editorCurrentVersion, setEditorCurrentVersion] = useState<RecipeVersion | null>(null);
  const [editorDefinitionJson, setEditorDefinitionJson] = useState<string>('{\n  "steps": [\n    {\n      "action": "navigate",\n      "url": "https://example-county.gov/search"\n    },\n    {\n      "action": "fill",\n      "locator": { "strategy": "css", "value": "#parcel" },\n      "value": "{{input.parcel_id}}"\n    },\n    {\n      "action": "click",\n      "locator": { "strategy": "css", "value": "#search-btn" }\n    }\n  ],\n  "extract": {\n    "parcel_id": { "strategy": "css", "value": ".parcel-val" },\n    "owner_name": { "strategy": "css", "value": ".owner-val" }\n  }\n}');
  const [editorChangelog, setEditorChangelog] = useState("");
  const [editorJsonError, setEditorJsonError] = useState<string | null>(null);

  // Compatibility State
  const [compatibilityResult, setCompatibilityResult] = useState<CompatibilityCheckResponse | null>(null);
  const [isCheckingCompatibility, setIsCheckingCompatibility] = useState(false);

  // ──────────────────────────────────────────────────────────────────────────
  // VALIDATION CASES STATE
  // ──────────────────────────────────────────────────────────────────────────
  const [validationRecipeId, setValidationRecipeId] = useState<string>("");
  const [validationCases, setValidationCases] = useState<ValidationCase[]>([]);
  const [isNewCaseModalOpen, setIsNewCaseModalOpen] = useState(false);
  const [newCaseName, setNewCaseName] = useState("");
  const [newCaseDesc, setNewCaseDesc] = useState("");
  const [newCaseInputJson, setNewCaseInputJson] = useState('{\n  "parcel_id": "01-002-0034"\n}');
  const [newCaseExpectedJson, setNewCaseExpectedJson] = useState('{\n  "parcel_id": "01-002-0034",\n  "owner_name": "JOHN DOE"\n}');

  // ──────────────────────────────────────────────────────────────────────────
  // INITIAL DATA FETCHING
  // ──────────────────────────────────────────────────────────────────────────
  const fetchRecipes = async () => {
    try {
      setLoading(true);
      const res = await recipeTemplatesApi.getRecipes({
        search: recipeSearch || undefined,
        record_type: recipeRecordTypeFilter === "all" ? undefined : recipeRecordTypeFilter,
        status: recipeStatusFilter === "all" ? undefined : recipeStatusFilter,
        limit: 100,
      });
      setRecipes(res.items || []);
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to load recipes");
    } finally {
      setLoading(false);
    }
  };

  const fetchTemplates = async () => {
    try {
      const res = await recipeTemplatesApi.getTemplates({
        search: templateSearch || undefined,
        pattern_type: patternTypeFilter === "all" ? undefined : patternTypeFilter,
        limit: 100,
      });
      setTemplates(res.items || []);
    } catch (err: any) {
      console.error(err);
    }
  };

  const fetchCountySources = async () => {
    try {
      const res = await countySourcesApi.list({ size: 100 });
      setCountySources(res.items || []);
    } catch (err: any) {
      console.error(err);
    }
  };

  useEffect(() => {
    fetchCountySources();
    fetchTemplates();
    fetchRecipes();
  }, []);

  useEffect(() => {
    fetchRecipes();
  }, [recipeSearch, recipeRecordTypeFilter, recipeStatusFilter]);

  useEffect(() => {
    fetchTemplates();
  }, [templateSearch, patternTypeFilter]);

  // Load configs when county source is picked in new recipe modal
  useEffect(() => {
    if (!newRecipeCountyId) {
      setCountyConfigs([]);
      setNewRecipeConfigId("");
      return;
    }
    const loadConfigs = async () => {
      try {
        const configs = await countySourcesApi.listConfigurations(newRecipeCountyId);
        setCountyConfigs(configs);
        if (configs.length > 0) {
          const match = configs.find((c) => c.source_type === newRecipeRecordType);
          setNewRecipeConfigId(match ? match.id : configs[0].id);
        } else {
          setNewRecipeConfigId("");
        }
      } catch (e) {
        console.error(e);
      }
    };
    loadConfigs();
  }, [newRecipeCountyId, newRecipeRecordType]);

  // ──────────────────────────────────────────────────────────────────────────
  // RECIPES ACTIONS
  // ──────────────────────────────────────────────────────────────────────────
  const handleCreateRecipe = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newRecipeName || !newRecipeCountyId) {
      setErrorMsg("Please provide a name and select a County Source.");
      return;
    }

    try {
      setLoading(true);
      await recipeTemplatesApi.createRecipe({
        name: newRecipeName,
        county_source_id: newRecipeCountyId,
        source_configuration_id: newRecipeConfigId || undefined,
        ui_template_id: newRecipeTemplateId || undefined,
        record_type: newRecipeRecordType,
        description: newRecipeDesc || undefined,
      });
      setSuccessMsg("Recipe created successfully!");
      setIsNewRecipeModalOpen(false);
      setNewRecipeName("");
      setNewRecipeDesc("");
      fetchRecipes();
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to create recipe");
    } finally {
      setLoading(false);
    }
  };

  const openRecipeDetails = async (recipe: Recipe) => {
    setSelectedRecipeForDetails(recipe);
    setLoadingVersions(true);
    try {
      const versions = await recipeTemplatesApi.getRecipeVersions(recipe.id);
      setRecipeVersions(versions);
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to load recipe versions");
    } finally {
      setLoadingVersions(false);
    }
  };

  const handleLifecycleTransition = async (recipeId: string, versionId: string, nextStatus: RecipeLifecycleStatus) => {
    try {
      setLoading(true);
      await recipeTemplatesApi.updateRecipeLifecycle(recipeId, versionId, nextStatus);
      setSuccessMsg(`Recipe transitioned to ${nextStatus.toUpperCase()} successfully.`);
      fetchRecipes();
      if (selectedRecipeForDetails?.id === recipeId) {
        const versions = await recipeTemplatesApi.getRecipeVersions(recipeId);
        setRecipeVersions(versions);
        const updatedRecipe = await recipeTemplatesApi.getRecipe(recipeId);
        setSelectedRecipeForDetails(updatedRecipe);
      }
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || `Failed to transition recipe to ${nextStatus}`);
    } finally {
      setLoading(false);
    }
  };

  const handlePublishRecipe = async (recipeId: string, versionId: string) => {
    try {
      setLoading(true);
      const res = await recipeTemplatesApi.publishRecipeVersion(recipeId, versionId);
      setSuccessMsg(res.message || "Recipe version published successfully!");
      fetchRecipes();
      if (selectedRecipeForDetails?.id === recipeId) {
        const versions = await recipeTemplatesApi.getRecipeVersions(recipeId);
        setRecipeVersions(versions);
        const updatedRecipe = await recipeTemplatesApi.getRecipe(recipeId);
        setSelectedRecipeForDetails(updatedRecipe);
      }
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to publish recipe version");
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteRecipe = async (id: string) => {
    if (!confirm("Are you sure you want to delete this recipe?")) return;
    try {
      setLoading(true);
      await recipeTemplatesApi.deleteRecipe(id);
      setSuccessMsg("Recipe deleted.");
      fetchRecipes();
      if (selectedRecipeForDetails?.id === id) {
        setSelectedRecipeForDetails(null);
      }
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to delete recipe");
    } finally {
      setLoading(false);
    }
  };

  // ──────────────────────────────────────────────────────────────────────────
  // UI TEMPLATE ACTIONS
  // ──────────────────────────────────────────────────────────────────────────
  const handleCreateTemplate = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      let schemaObj = {};
      let initialDefObj = {};
      try {
        schemaObj = JSON.parse(newTplDefaultSchemaJson);
      } catch {
        setErrorMsg("Default Config Schema must be valid JSON.");
        return;
      }
      try {
        initialDefObj = JSON.parse(newTplInitialDefJson);
      } catch {
        setErrorMsg("Initial Version Definition must be valid JSON.");
        return;
      }

      const supportedSources = newTplSourceTypes.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
      const supportedMethods = newTplSearchMethods.split(",").map((m) => m.trim().toUpperCase()).filter(Boolean);
      const reqFields = newTplReqFields.split(",").map((f) => f.trim()).filter(Boolean);

      setLoading(true);
      await recipeTemplatesApi.createTemplate({
        name: newTplName,
        description: newTplDesc || undefined,
        pattern_type: newTplPattern,
        supported_source_types: supportedSources,
        supported_search_methods: supportedMethods,
        required_configuration_fields: reqFields,
        default_config_schema: schemaObj,
        initial_version_definition: initialDefObj,
      });

      setSuccessMsg("UI Template created with initial version.");
      setIsNewTemplateModalOpen(false);
      setNewTplName("");
      setNewTplDesc("");
      fetchTemplates();
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to create UI Template");
    } finally {
      setLoading(false);
    }
  };

  const openTemplateVersions = async (tpl: UITemplate) => {
    setSelectedTemplateForVersions(tpl);
    try {
      const versions = await recipeTemplatesApi.getTemplateVersions(tpl.id);
      setTemplateVersionsList(versions);
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to load template versions");
    }
  };

  const handleCreateTemplateVersion = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedTemplateForVersions) return;
    try {
      let defObj = {};
      try {
        defObj = JSON.parse(newTplVerDefJson);
      } catch {
        setErrorMsg("Definition must be valid JSON.");
        return;
      }

      setLoading(true);
      await recipeTemplatesApi.createTemplateVersion(selectedTemplateForVersions.id, {
        definition: defObj,
        changelog: newTplVerChangelog || undefined,
      });

      setSuccessMsg("Template version created.");
      setIsNewTemplateVersionModalOpen(false);
      setNewTplVerChangelog("");
      const versions = await recipeTemplatesApi.getTemplateVersions(selectedTemplateForVersions.id);
      setTemplateVersionsList(versions);
      fetchTemplates();
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to create template version");
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteTemplate = async (id: string) => {
    if (!confirm("Are you sure you want to delete this UI Template?")) return;
    try {
      setLoading(true);
      await recipeTemplatesApi.deleteTemplate(id);
      setSuccessMsg("Template deleted.");
      fetchTemplates();
      if (selectedTemplateForVersions?.id === id) {
        setSelectedTemplateForVersions(null);
      }
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to delete template");
    } finally {
      setLoading(false);
    }
  };

  // ──────────────────────────────────────────────────────────────────────────
  // RECIPE EDITOR & COMPATIBILITY ACTIONS
  // ──────────────────────────────────────────────────────────────────────────
  const loadRecipeIntoEditor = async (recipeId: string) => {
    setEditorRecipeId(recipeId);
    setCompatibilityResult(null);
    setEditorJsonError(null);
    if (!recipeId) {
      setEditorSelectedRecipe(null);
      setEditorCurrentVersion(null);
      return;
    }

    try {
      setLoading(true);
      const recipe = await recipeTemplatesApi.getRecipe(recipeId);
      setEditorSelectedRecipe(recipe);

      const versions = await recipeTemplatesApi.getRecipeVersions(recipeId);
      if (versions.length > 0) {
        // Find current or latest
        const currentVer = versions.find((v) => v.is_current) || versions[0];
        setEditorCurrentVersion(currentVer);
        setEditorDefinitionJson(JSON.stringify(currentVer.definition, null, 2));
      } else {
        setEditorCurrentVersion(null);
      }
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to load recipe into editor");
    } finally {
      setLoading(false);
    }
  };

  const runCompatibilityCheck = async () => {
    if (!editorSelectedRecipe) return;
    if (!editorSelectedRecipe.ui_template_id || !editorSelectedRecipe.source_configuration_id) {
      setErrorMsg("This recipe must be linked to both a UI Template and a Source Configuration to perform compatibility validation.");
      return;
    }

    setIsCheckingCompatibility(true);
    setCompatibilityResult(null);
    try {
      const res = await recipeTemplatesApi.checkCompatibility({
        template_id: editorSelectedRecipe.ui_template_id,
        source_configuration_id: editorSelectedRecipe.source_configuration_id,
      });
      setCompatibilityResult(res);
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Compatibility check failed");
    } finally {
      setIsCheckingCompatibility(false);
    }
  };

  const handleSaveEditorDefinition = async (createAsNewVersion: boolean) => {
    if (!editorSelectedRecipe) return;

    let parsedDef: Record<string, unknown>;
    try {
      parsedDef = JSON.parse(editorDefinitionJson);
      setEditorJsonError(null);
    } catch (err: any) {
      setEditorJsonError("Invalid JSON syntax: " + err.message);
      return;
    }

    try {
      setLoading(true);

      // Check if current version is published and user attempts direct update
      if (!createAsNewVersion && editorCurrentVersion?.is_published) {
        setErrorMsg("Published versions are strictly immutable. Please save as a new version.");
        return;
      }

      if (createAsNewVersion || !editorCurrentVersion) {
        // Create new recipe version
        const newVer = await recipeTemplatesApi.createRecipeVersion(editorSelectedRecipe.id, {
          definition: parsedDef,
          changelog: editorChangelog || "Updated definition in editor",
          template_version_id: editorCurrentVersion?.template_version_id || undefined,
        });
        setSuccessMsg(`Saved as new Version ${newVer.version_number}!`);
        setEditorCurrentVersion(newVer);
      } else {
        // Update draft version
        const updatedVer = await recipeTemplatesApi.updateRecipeVersion(
          editorSelectedRecipe.id,
          editorCurrentVersion.id,
          {
            definition: parsedDef,
            changelog: editorChangelog || undefined,
          }
        );
        setSuccessMsg(`Version ${updatedVer.version_number} definition updated!`);
        setEditorCurrentVersion(updatedVer);
      }

      setEditorChangelog("");
      fetchRecipes();
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to save recipe definition");
    } finally {
      setLoading(false);
    }
  };

  // ──────────────────────────────────────────────────────────────────────────
  // VALIDATION CASES ACTIONS
  // ──────────────────────────────────────────────────────────────────────────
  const loadValidationCases = async (recipeId: string) => {
    setValidationRecipeId(recipeId);
    if (!recipeId) {
      setValidationCases([]);
      return;
    }
    try {
      setLoading(true);
      const cases = await recipeTemplatesApi.getValidationCases(recipeId);
      setValidationCases(cases);
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to load validation cases");
    } finally {
      setLoading(false);
    }
  };

  const handleCreateValidationCase = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!validationRecipeId || !newCaseName) {
      setErrorMsg("Please select a recipe and enter a test case name.");
      return;
    }

    let inputObj: Record<string, unknown> = {};
    let expectedObj: Record<string, unknown> = {};
    try {
      inputObj = JSON.parse(newCaseInputJson);
    } catch {
      setErrorMsg("Input Data must be valid JSON.");
      return;
    }
    try {
      expectedObj = JSON.parse(newCaseExpectedJson);
    } catch {
      setErrorMsg("Expected Output must be valid JSON.");
      return;
    }

    try {
      setLoading(true);
      await recipeTemplatesApi.createValidationCase(validationRecipeId, {
        name: newCaseName,
        description: newCaseDesc || undefined,
        input_data: inputObj,
        expected_output: expectedObj,
      });
      setSuccessMsg("Validation test case created.");
      setIsNewCaseModalOpen(false);
      setNewCaseName("");
      setNewCaseDesc("");
      loadValidationCases(validationRecipeId);
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to create validation case");
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteValidationCase = async (caseId: string) => {
    if (!confirm("Delete this validation test case?")) return;
    try {
      setLoading(true);
      await recipeTemplatesApi.deleteValidationCase(validationRecipeId, caseId);
      setSuccessMsg("Validation test case deleted.");
      loadValidationCases(validationRecipeId);
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to delete test case");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-slate-900">
            Recipe & Template Management
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            Reusable UI automation templates, deterministic county recipes, version immutability, and validation test suites.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {activeMainTab === "recipes" && (
            <Button size="sm" onClick={() => setIsNewRecipeModalOpen(true)}>
              <Plus className="w-4 h-4 mr-1.5" />
              New Recipe
            </Button>
          )}
          {activeMainTab === "templates" && (
            <Button size="sm" onClick={() => setIsNewTemplateModalOpen(true)}>
              <Plus className="w-4 h-4 mr-1.5" />
              New UI Template
            </Button>
          )}
          {activeMainTab === "validation" && validationRecipeId && (
            <Button size="sm" onClick={() => setIsNewCaseModalOpen(true)}>
              <Plus className="w-4 h-4 mr-1.5" />
              New Test Case
            </Button>
          )}
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

      {/* Top Tabs */}
      <div className="border-b border-slate-200 flex space-x-6">
        <button
          onClick={() => setActiveMainTab("recipes")}
          className={`pb-3 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${
            activeMainTab === "recipes"
              ? "border-blue-600 text-blue-600"
              : "border-transparent text-slate-500 hover:text-slate-800"
          }`}
        >
          <BookOpen className="w-4 h-4" />
          Recipes
          <span className="text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-600">
            {recipes.length}
          </span>
        </button>

        <button
          onClick={() => setActiveMainTab("templates")}
          className={`pb-3 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${
            activeMainTab === "templates"
              ? "border-blue-600 text-blue-600"
              : "border-transparent text-slate-500 hover:text-slate-800"
          }`}
        >
          <Layers className="w-4 h-4" />
          UI Templates
          <span className="text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-600">
            {templates.length}
          </span>
        </button>

        <button
          onClick={() => setActiveMainTab("editor")}
          className={`pb-3 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${
            activeMainTab === "editor"
              ? "border-blue-600 text-blue-600"
              : "border-transparent text-slate-500 hover:text-slate-800"
          }`}
        >
          <Code2 className="w-4 h-4" />
          Recipe Editor & Compatibility
        </button>

        <button
          onClick={() => setActiveMainTab("validation")}
          className={`pb-3 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${
            activeMainTab === "validation"
              ? "border-blue-600 text-blue-600"
              : "border-transparent text-slate-500 hover:text-slate-800"
          }`}
        >
          <ShieldCheck className="w-4 h-4" />
          Validation Test Cases
        </button>
      </div>

      {/* ─────────────────────────────────────────────────────────────────── */}
      {/* TAB 1: RECIPES */}
      {/* ─────────────────────────────────────────────────────────────────── */}
      {activeMainTab === "recipes" && (
        <div className="space-y-4">
          {/* Filters Bar */}
          <div className="flex flex-col sm:flex-row items-center justify-between gap-3 bg-white p-3 border rounded-lg">
            <div className="relative w-full sm:w-80">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Search recipe name..."
                value={recipeSearch}
                onChange={(e) => setRecipeSearch(e.target.value)}
                className="pl-8"
              />
            </div>
            <div className="flex items-center gap-2 w-full sm:w-auto">
              <select
                value={recipeRecordTypeFilter}
                onChange={(e) => setRecipeRecordTypeFilter(e.target.value)}
                className="text-xs border rounded-md px-3 py-2 bg-white text-slate-700 border-slate-300"
              >
                <option value="all">All Record Types</option>
                <option value="assessor">Assessor</option>
                <option value="tax">Tax</option>
              </select>

              <select
                value={recipeStatusFilter}
                onChange={(e) => setRecipeStatusFilter(e.target.value)}
                className="text-xs border rounded-md px-3 py-2 bg-white text-slate-700 border-slate-300"
              >
                <option value="all">All Lifecycle States</option>
                <option value="draft">Draft</option>
                <option value="testing">Testing</option>
                <option value="validated">Validated</option>
                <option value="approved">Approved</option>
                <option value="published">Published</option>
                <option value="deprecated">Deprecated</option>
              </select>

              <Button variant="outline" size="sm" onClick={fetchRecipes} disabled={loading}>
                <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
              </Button>
            </div>
          </div>

          {/* Recipes Table */}
          <Card>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Recipe Name</TableHead>
                    <TableHead>County Source</TableHead>
                    <TableHead>Record Type</TableHead>
                    <TableHead>Lifecycle Status</TableHead>
                    <TableHead>Active Version</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {recipes.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={6} className="text-center py-12 text-muted-foreground">
                        <BookOpen className="w-10 h-10 mx-auto mb-2 opacity-20" />
                        <p className="text-sm font-medium">No recipes found</p>
                        <p className="text-xs mt-1">Create your first county automation recipe</p>
                      </TableCell>
                    </TableRow>
                  ) : (
                    recipes.map((r) => {
                      const county = countySources.find((c) => c.id === r.county_source_id);
                      return (
                        <TableRow key={r.id}>
                          <TableCell>
                            <div className="font-medium text-slate-900">{r.name}</div>
                            {r.description && (
                              <div className="text-xs text-muted-foreground line-clamp-1">
                                {r.description}
                              </div>
                            )}
                          </TableCell>
                          <TableCell className="text-sm">
                            {county ? `${county.name}, ${county.state}` : r.county_source_id}
                          </TableCell>
                          <TableCell>
                            <Badge variant="outline" className="capitalize text-xs">
                              {r.record_type}
                            </Badge>
                          </TableCell>
                          <TableCell>{lifecycleBadge(r.status)}</TableCell>
                          <TableCell>
                            {r.versions && r.versions.length > 0 ? (
                              <div className="flex items-center gap-1.5">
                                <span className="font-mono text-xs font-semibold text-slate-700">
                                  v{r.versions[0].version_number}
                                </span>
                                {r.versions[0].is_published && (
                                  <Badge className="bg-emerald-100 text-emerald-800 text-[10px] px-1.5 py-0 border-emerald-300">
                                    Locked
                                  </Badge>
                                )}
                              </div>
                            ) : (
                              <span className="text-xs text-muted-foreground">v1 (Draft)</span>
                            )}
                          </TableCell>
                          <TableCell className="text-right">
                            <div className="flex items-center justify-end gap-1">
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => openRecipeDetails(r)}
                                title="Manage Versions & Lifecycle"
                              >
                                Manage
                              </Button>
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => {
                                  setActiveMainTab("editor");
                                  loadRecipeIntoEditor(r.id);
                                }}
                                title="Open in Recipe Editor"
                              >
                                <Edit3 className="w-3.5 h-3.5" />
                              </Button>
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => handleDeleteRecipe(r.id)}
                                className="text-rose-600 hover:text-rose-700 hover:bg-rose-50"
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
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────────────── */}
      {/* TAB 2: UI TEMPLATES */}
      {/* ─────────────────────────────────────────────────────────────────── */}
      {activeMainTab === "templates" && (
        <div className="space-y-4">
          {/* Filters Bar */}
          <div className="flex flex-col sm:flex-row items-center justify-between gap-3 bg-white p-3 border rounded-lg">
            <div className="relative w-full sm:w-80">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Search template name..."
                value={templateSearch}
                onChange={(e) => setTemplateSearch(e.target.value)}
                className="pl-8"
              />
            </div>
            <div className="flex items-center gap-2 w-full sm:w-auto">
              <select
                value={patternTypeFilter}
                onChange={(e) => setPatternTypeFilter(e.target.value)}
                className="text-xs border rounded-md px-3 py-2 bg-white text-slate-700 border-slate-300"
              >
                <option value="all">All Interaction Patterns</option>
                <option value="standard_form">standard_form</option>
                <option value="spa_search">spa_search</option>
                <option value="paginated_table">paginated_table</option>
                <option value="dropdown_drilldown">dropdown_drilldown</option>
              </select>

              <Button variant="outline" size="sm" onClick={fetchTemplates}>
                <RefreshCw className="w-4 h-4" />
              </Button>
            </div>
          </div>

          {/* Templates Table */}
          <Card>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Template Name</TableHead>
                    <TableHead>Pattern Type</TableHead>
                    <TableHead>Supported Sources</TableHead>
                    <TableHead>Search Methods</TableHead>
                    <TableHead>Required Fields</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {templates.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={6} className="text-center py-12 text-muted-foreground">
                        <Layers className="w-10 h-10 mx-auto mb-2 opacity-20" />
                        <p className="text-sm font-medium">No UI templates found</p>
                        <p className="text-xs mt-1">Create a reusable interaction pattern template</p>
                      </TableCell>
                    </TableRow>
                  ) : (
                    templates.map((tpl) => (
                      <TableRow key={tpl.id}>
                        <TableCell>
                          <div className="font-medium text-slate-900">{tpl.name}</div>
                          {tpl.description && (
                            <div className="text-xs text-muted-foreground line-clamp-1">
                              {tpl.description}
                            </div>
                          )}
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline" className="font-mono text-xs bg-slate-50">
                            {tpl.pattern_type}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-wrap gap-1">
                            {tpl.supported_source_types.map((st) => (
                              <Badge key={st} variant="secondary" className="text-[11px] uppercase">
                                {st}
                              </Badge>
                            ))}
                          </div>
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-wrap gap-1">
                            {tpl.supported_search_methods.map((sm) => (
                              <Badge key={sm} variant="outline" className="text-[10px]">
                                {sm}
                              </Badge>
                            ))}
                          </div>
                        </TableCell>
                        <TableCell>
                          <span className="text-xs font-mono text-slate-600">
                            {tpl.required_configuration_fields.join(", ") || "None"}
                          </span>
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-1">
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => openTemplateVersions(tpl)}
                              title="View & Add Versions"
                            >
                              Versions
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => handleDeleteTemplate(tpl.id)}
                              className="text-rose-600 hover:text-rose-700 hover:bg-rose-50"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────────────── */}
      {/* TAB 3: RECIPE EDITOR & COMPATIBILITY */}
      {/* ─────────────────────────────────────────────────────────────────── */}
      {activeMainTab === "editor" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left Column: Recipe Selector & Controls */}
          <div className="space-y-4">
            <Card>
              <CardContent className="p-4 space-y-4">
                <div>
                  <Label className="text-xs font-semibold text-slate-700">Select Recipe</Label>
                  <select
                    value={editorRecipeId}
                    onChange={(e) => loadRecipeIntoEditor(e.target.value)}
                    className="w-full mt-1.5 text-sm border rounded-md px-3 py-2 bg-white text-slate-800 border-slate-300"
                  >
                    <option value="">-- Choose a Recipe to Edit --</option>
                    {recipes.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.name} ({r.record_type})
                      </option>
                    ))}
                  </select>
                </div>

                {editorSelectedRecipe && (
                  <div className="p-3 bg-slate-50 border rounded-md space-y-2 text-xs">
                    <div className="flex justify-between items-center">
                      <span className="text-muted-foreground">Lifecycle State:</span>
                      {lifecycleBadge(editorSelectedRecipe.status)}
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="text-muted-foreground">Record Type:</span>
                      <span className="font-medium uppercase">{editorSelectedRecipe.record_type}</span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="text-muted-foreground">County Source ID:</span>
                      <span className="font-mono text-[11px] truncate max-w-[150px]">
                        {editorSelectedRecipe.county_source_id}
                      </span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="text-muted-foreground">Version:</span>
                      <span className="font-semibold">
                        v{editorCurrentVersion?.version_number ?? 1}
                        {editorCurrentVersion?.is_published && " (Locked/Published)"}
                      </span>
                    </div>
                  </div>
                )}

                {/* Compatibility Button */}
                <div className="pt-2 border-t">
                  <Button
                    variant="outline"
                    size="sm"
                    className="w-full text-xs flex items-center justify-center gap-1.5"
                    disabled={!editorSelectedRecipe || isCheckingCompatibility}
                    onClick={runCompatibilityCheck}
                  >
                    {isCheckingCompatibility ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <ShieldCheck className="w-3.5 h-3.5 text-blue-600" />
                    )}
                    Check Template Compatibility
                  </Button>
                </div>

                {/* Compatibility Outcome Alert */}
                {compatibilityResult && (
                  <div
                    className={`p-3 rounded-md text-xs border ${
                      compatibilityResult.compatible
                        ? "bg-emerald-50 border-emerald-200 text-emerald-800"
                        : "bg-rose-50 border-rose-200 text-rose-800"
                    }`}
                  >
                    <div className="flex items-center gap-1.5 font-semibold">
                      {compatibilityResult.compatible ? (
                        <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
                      ) : (
                        <XCircle className="w-4 h-4 text-rose-600 shrink-0" />
                      )}
                      <span>
                        {compatibilityResult.compatible
                          ? "Compatible with Source Configuration"
                          : "Configuration Incompatible"}
                      </span>
                    </div>

                    {compatibilityResult.missing_fields.length > 0 && (
                      <div className="mt-2 text-rose-700">
                        <span className="font-medium">Missing required fields: </span>
                        {compatibilityResult.missing_fields.join(", ")}
                      </div>
                    )}

                    {compatibilityResult.warnings.length > 0 && (
                      <div className="mt-1 text-amber-700">
                        <span className="font-medium">Warnings: </span>
                        {compatibilityResult.warnings.join("; ")}
                      </div>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Version Changelog Input */}
            {editorSelectedRecipe && (
              <Card>
                <CardContent className="p-4 space-y-3">
                  <Label className="text-xs font-semibold text-slate-700">Changelog Note</Label>
                  <Input
                    placeholder="e.g. Updated parcel selector to #parcel-id"
                    value={editorChangelog}
                    onChange={(e) => setEditorChangelog(e.target.value)}
                    className="text-xs"
                  />

                  {editorCurrentVersion?.is_published && (
                    <div className="flex items-start gap-2 p-2 bg-amber-50 border border-amber-200 rounded text-amber-800 text-[11px]">
                      <Lock className="w-3.5 h-3.5 text-amber-600 shrink-0 mt-0.5" />
                      <span>
                        Current version is published and immutable. Saving changes will automatically generate a new version.
                      </span>
                    </div>
                  )}

                  <div className="flex flex-col gap-2 pt-2">
                    <Button
                      size="sm"
                      onClick={() => handleSaveEditorDefinition(false)}
                      disabled={loading || !editorSelectedRecipe || editorCurrentVersion?.is_published}
                      className="w-full text-xs"
                    >
                      Update Current Version
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleSaveEditorDefinition(true)}
                      disabled={loading || !editorSelectedRecipe}
                      className="w-full text-xs"
                    >
                      Save as New Version
                    </Button>
                  </div>
                </CardContent>
              </Card>
            )}
          </div>

          {/* Right Column: Declarative JSON Editor */}
          <div className="lg:col-span-2 space-y-2">
            <div className="flex items-center justify-between">
              <Label className="text-xs font-semibold text-slate-700">
                Recipe Automation Definition (JSON)
              </Label>
              <span className="text-[11px] text-muted-foreground">
                Deterministic steps & extraction rules (No arbitrary JS)
              </span>
            </div>

            {editorJsonError && (
              <div className="p-2 text-xs bg-rose-50 border border-rose-200 text-rose-700 rounded">
                {editorJsonError}
              </div>
            )}

            <textarea
              value={editorDefinitionJson}
              onChange={(e) => {
                setEditorDefinitionJson(e.target.value);
                setEditorJsonError(null);
              }}
              rows={24}
              className="w-full font-mono text-xs p-3 border rounded-lg bg-slate-900 text-emerald-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
              spellCheck={false}
            />
          </div>
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────────────── */}
      {/* TAB 4: VALIDATION TEST CASES */}
      {/* ─────────────────────────────────────────────────────────────────── */}
      {activeMainTab === "validation" && (
        <div className="space-y-4">
          <div className="flex flex-col sm:flex-row items-center justify-between gap-3 bg-white p-3 border rounded-lg">
            <div className="flex items-center gap-3 w-full sm:w-auto">
              <Label className="text-xs font-semibold text-slate-700 whitespace-nowrap">
                Select Recipe:
              </Label>
              <select
                value={validationRecipeId}
                onChange={(e) => loadValidationCases(e.target.value)}
                className="text-xs border rounded-md px-3 py-2 bg-white text-slate-800 border-slate-300 min-w-[240px]"
              >
                <option value="">-- Choose a Recipe --</option>
                {recipes.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name} ({r.record_type})
                  </option>
                ))}
              </select>
            </div>
          </div>

          <Card>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Test Case Name</TableHead>
                    <TableHead>Input Data</TableHead>
                    <TableHead>Expected Output</TableHead>
                    <TableHead>Last Run Status</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {!validationRecipeId ? (
                    <TableRow>
                      <TableCell colSpan={5} className="text-center py-12 text-muted-foreground">
                        <ShieldCheck className="w-10 h-10 mx-auto mb-2 opacity-20" />
                        <p className="text-sm font-medium">Select a recipe above to view validation test cases</p>
                      </TableCell>
                    </TableRow>
                  ) : validationCases.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={5} className="text-center py-12 text-muted-foreground">
                        <ShieldCheck className="w-10 h-10 mx-auto mb-2 opacity-20" />
                        <p className="text-sm font-medium">No test cases configured for this recipe</p>
                        <p className="text-xs mt-1">Create a test case to verify parcel lookups</p>
                      </TableCell>
                    </TableRow>
                  ) : (
                    validationCases.map((tc) => (
                      <TableRow key={tc.id}>
                        <TableCell>
                          <div className="font-medium text-slate-900">{tc.name}</div>
                          {tc.description && (
                            <div className="text-xs text-muted-foreground">{tc.description}</div>
                          )}
                        </TableCell>
                        <TableCell>
                          <pre className="font-mono text-[11px] bg-slate-50 p-2 rounded border max-w-xs overflow-x-auto">
                            {JSON.stringify(tc.input_data, null, 2)}
                          </pre>
                        </TableCell>
                        <TableCell>
                          <pre className="font-mono text-[11px] bg-slate-50 p-2 rounded border max-w-xs overflow-x-auto">
                            {JSON.stringify(tc.expected_output, null, 2)}
                          </pre>
                        </TableCell>
                        <TableCell>
                          {tc.last_run_status === "passed" ? (
                            <Badge className="bg-emerald-600 text-white text-xs">Passed</Badge>
                          ) : tc.last_run_status === "failed" ? (
                            <Badge className="bg-rose-600 text-white text-xs">Failed</Badge>
                          ) : (
                            <Badge variant="outline" className="text-xs">Pending</Badge>
                          )}
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleDeleteValidationCase(tc.id)}
                            className="text-rose-600 hover:text-rose-700 hover:bg-rose-50"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────────────── */}
      {/* MODAL: NEW RECIPE */}
      {/* ─────────────────────────────────────────────────────────────────── */}
      {isNewRecipeModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-lg w-full p-6 space-y-4">
            <div className="flex items-center justify-between border-b pb-3">
              <h3 className="text-lg font-semibold text-slate-900">Create New Recipe</h3>
              <button
                onClick={() => setIsNewRecipeModalOpen(false)}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleCreateRecipe} className="space-y-4 text-xs">
              <div>
                <Label className="text-xs">Recipe Name *</Label>
                <Input
                  required
                  placeholder="e.g. Orange County FL Assessor Automation"
                  value={newRecipeName}
                  onChange={(e) => setNewRecipeName(e.target.value)}
                  className="mt-1 text-xs"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label className="text-xs">Record Type *</Label>
                  <select
                    value={newRecipeRecordType}
                    onChange={(e) => setNewRecipeRecordType(e.target.value as "assessor" | "tax")}
                    className="w-full mt-1 border rounded-md px-3 py-2 bg-white text-slate-800 border-slate-300"
                  >
                    <option value="assessor">Assessor</option>
                    <option value="tax">Tax</option>
                  </select>
                </div>

                <div>
                  <Label className="text-xs">County Source *</Label>
                  <select
                    required
                    value={newRecipeCountyId}
                    onChange={(e) => setNewRecipeCountyId(e.target.value)}
                    className="w-full mt-1 border rounded-md px-3 py-2 bg-white text-slate-800 border-slate-300"
                  >
                    <option value="">-- Select County --</option>
                    {countySources.map((cs) => (
                      <option key={cs.id} value={cs.id}>
                        {cs.name}, {cs.state}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label className="text-xs">Source Configuration</Label>
                  <select
                    value={newRecipeConfigId}
                    onChange={(e) => setNewRecipeConfigId(e.target.value)}
                    className="w-full mt-1 border rounded-md px-3 py-2 bg-white text-slate-800 border-slate-300"
                    disabled={countyConfigs.length === 0}
                  >
                    <option value="">
                      {countyConfigs.length === 0 ? "-- No configs found --" : "-- Select Config --"}
                    </option>
                    {countyConfigs.map((cfg) => (
                      <option key={cfg.id} value={cfg.id}>
                        {cfg.source_type} ({cfg.search_method || "Standard"})
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <Label className="text-xs">UI Automation Template</Label>
                  <select
                    value={newRecipeTemplateId}
                    onChange={(e) => setNewRecipeTemplateId(e.target.value)}
                    className="w-full mt-1 border rounded-md px-3 py-2 bg-white text-slate-800 border-slate-300"
                  >
                    <option value="">-- None (Custom) --</option>
                    {templates.map((tpl) => (
                      <option key={tpl.id} value={tpl.id}>
                        {tpl.name} ({tpl.pattern_type})
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div>
                <Label className="text-xs">Description</Label>
                <textarea
                  placeholder="Optional description of this county automation recipe"
                  value={newRecipeDesc}
                  onChange={(e) => setNewRecipeDesc(e.target.value)}
                  className="w-full mt-1 border rounded-md p-2 text-xs border-slate-300"
                  rows={3}
                />
              </div>

              <div className="flex justify-end gap-2 pt-3 border-t">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setIsNewRecipeModalOpen(false)}
                >
                  Cancel
                </Button>
                <Button type="submit" size="sm" disabled={loading}>
                  {loading && <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" />}
                  Create Recipe
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────────────── */}
      {/* MODAL: RECIPE DETAILS & LIFECYCLE MANAGEMENT */}
      {/* ─────────────────────────────────────────────────────────────────── */}
      {selectedRecipeForDetails && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full p-6 space-y-4 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between border-b pb-3">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">
                  {selectedRecipeForDetails.name}
                </h3>
                <div className="flex items-center gap-2 mt-1">
                  {lifecycleBadge(selectedRecipeForDetails.status)}
                  <span className="text-xs text-muted-foreground capitalize">
                    {selectedRecipeForDetails.record_type} Record
                  </span>
                </div>
              </div>
              <button
                onClick={() => setSelectedRecipeForDetails(null)}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Versions List */}
            <div className="space-y-3">
              <h4 className="text-xs font-bold uppercase tracking-wider text-slate-500">
                Recipe Versions & Immutability History
              </h4>

              {loadingVersions ? (
                <div className="py-6 text-center text-muted-foreground">
                  <Loader2 className="w-6 h-6 animate-spin mx-auto mb-1 text-blue-600" />
                  <span className="text-xs">Loading versions...</span>
                </div>
              ) : recipeVersions.length === 0 ? (
                <div className="text-xs text-muted-foreground py-4 text-center">
                  No versions recorded yet.
                </div>
              ) : (
                <div className="space-y-2">
                  {recipeVersions.map((v) => (
                    <div
                      key={v.id}
                      className="p-3 border rounded-lg bg-slate-50/50 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs"
                    >
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <span className="font-mono font-bold text-slate-900">
                            Version {v.version_number}
                          </span>
                          {lifecycleBadge(v.status)}
                          {v.is_published && (
                            <Badge className="bg-emerald-600 text-white text-[10px] flex items-center gap-1">
                              <Lock className="w-2.5 h-2.5" />
                              Immutable / Published
                            </Badge>
                          )}
                          {v.is_current && (
                            <Badge variant="outline" className="text-[10px]">
                              Active Target
                            </Badge>
                          )}
                        </div>
                        {v.changelog && (
                          <div className="text-muted-foreground text-[11px]">{v.changelog}</div>
                        )}
                        <div className="text-[10px] text-slate-400">
                          Created {new Date(v.created_at).toLocaleString()}
                        </div>
                      </div>

                      {/* Lifecycle Transition Buttons */}
                      <div className="flex items-center gap-1.5 flex-wrap">
                        {v.status === "draft" && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() =>
                              handleLifecycleTransition(selectedRecipeForDetails.id, v.id, "testing")
                            }
                            className="text-[11px] h-7"
                          >
                            Advance to Testing
                          </Button>
                        )}
                        {v.status === "testing" && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() =>
                              handleLifecycleTransition(selectedRecipeForDetails.id, v.id, "validated")
                            }
                            className="text-[11px] h-7 text-indigo-700 border-indigo-200 hover:bg-indigo-50"
                          >
                            Mark Validated
                          </Button>
                        )}
                        {v.status === "validated" && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() =>
                              handleLifecycleTransition(selectedRecipeForDetails.id, v.id, "approved")
                            }
                            className="text-[11px] h-7 text-blue-700 border-blue-200 hover:bg-blue-50"
                          >
                            Approve
                          </Button>
                        )}
                        {(v.status === "validated" || v.status === "approved") && !v.is_published && (
                          <Button
                            size="sm"
                            onClick={() =>
                              handlePublishRecipe(selectedRecipeForDetails.id, v.id)
                            }
                            className="text-[11px] h-7 bg-emerald-600 hover:bg-emerald-700 text-white"
                          >
                            Publish (Lock)
                          </Button>
                        )}
                        {v.is_published && v.status !== "deprecated" && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() =>
                              handleLifecycleTransition(selectedRecipeForDetails.id, v.id, "deprecated")
                            }
                            className="text-[11px] h-7 text-slate-600 hover:bg-slate-100"
                          >
                            Deprecate
                          </Button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="flex justify-end pt-3 border-t">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setSelectedRecipeForDetails(null)}
              >
                Close
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────────────── */}
      {/* MODAL: NEW UI TEMPLATE */}
      {/* ─────────────────────────────────────────────────────────────────── */}
      {isNewTemplateModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-xl w-full p-6 space-y-4 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between border-b pb-3">
              <h3 className="text-lg font-semibold text-slate-900">Create UI Template</h3>
              <button
                onClick={() => setIsNewTemplateModalOpen(false)}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleCreateTemplate} className="space-y-4 text-xs">
              <div>
                <Label className="text-xs">Template Name *</Label>
                <Input
                  required
                  placeholder="e.g. Standard Single-Form Search"
                  value={newTplName}
                  onChange={(e) => setNewTplName(e.target.value)}
                  className="mt-1 text-xs"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label className="text-xs">Interaction Pattern Type</Label>
                  <select
                    value={newTplPattern}
                    onChange={(e) => setNewTplPattern(e.target.value)}
                    className="w-full mt-1 border rounded-md px-3 py-2 bg-white text-slate-800 border-slate-300"
                  >
                    <option value="standard_form">standard_form</option>
                    <option value="spa_search">spa_search</option>
                    <option value="paginated_table">paginated_table</option>
                    <option value="dropdown_drilldown">dropdown_drilldown</option>
                  </select>
                </div>

                <div>
                  <Label className="text-xs">Required Config Fields</Label>
                  <Input
                    placeholder="parcel_id, search_button"
                    value={newTplReqFields}
                    onChange={(e) => setNewTplReqFields(e.target.value)}
                    className="mt-1 text-xs"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label className="text-xs">Supported Sources (comma-sep)</Label>
                  <Input
                    placeholder="assessor, tax"
                    value={newTplSourceTypes}
                    onChange={(e) => setNewTplSourceTypes(e.target.value)}
                    className="mt-1 text-xs"
                  />
                </div>

                <div>
                  <Label className="text-xs">Search Methods (comma-sep)</Label>
                  <Input
                    placeholder="FORM_SUBMIT, GET, POST"
                    value={newTplSearchMethods}
                    onChange={(e) => setNewTplSearchMethods(e.target.value)}
                    className="mt-1 text-xs"
                  />
                </div>
              </div>

              <div>
                <Label className="text-xs">Default Config Schema (JSON)</Label>
                <textarea
                  value={newTplDefaultSchemaJson}
                  onChange={(e) => setNewTplDefaultSchemaJson(e.target.value)}
                  className="w-full mt-1 font-mono text-xs border rounded-md p-2 bg-slate-50 border-slate-300"
                  rows={4}
                />
              </div>

              <div>
                <Label className="text-xs">Initial Version Definition (JSON)</Label>
                <textarea
                  value={newTplInitialDefJson}
                  onChange={(e) => setNewTplInitialDefJson(e.target.value)}
                  className="w-full mt-1 font-mono text-xs border rounded-md p-2 bg-slate-900 text-emerald-400 border-slate-700"
                  rows={6}
                />
              </div>

              <div className="flex justify-end gap-2 pt-3 border-t">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setIsNewTemplateModalOpen(false)}
                >
                  Cancel
                </Button>
                <Button type="submit" size="sm" disabled={loading}>
                  {loading && <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" />}
                  Create UI Template
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────────────── */}
      {/* MODAL: TEMPLATE VERSIONS */}
      {/* ─────────────────────────────────────────────────────────────────── */}
      {selectedTemplateForVersions && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full p-6 space-y-4 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between border-b pb-3">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">
                  {selectedTemplateForVersions.name}
                </h3>
                <span className="text-xs font-mono text-muted-foreground">
                  Pattern: {selectedTemplateForVersions.pattern_type}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  onClick={() => setIsNewTemplateVersionModalOpen(true)}
                  className="text-xs h-8"
                >
                  <Plus className="w-3.5 h-3.5 mr-1" />
                  New Version
                </Button>
                <button
                  onClick={() => setSelectedTemplateForVersions(null)}
                  className="text-slate-400 hover:text-slate-600"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            <div className="space-y-3">
              {templateVersionsList.length === 0 ? (
                <div className="text-xs text-muted-foreground py-6 text-center">
                  No versions recorded for this template.
                </div>
              ) : (
                templateVersionsList.map((tv) => (
                  <div key={tv.id} className="p-3 border rounded-lg bg-slate-50 space-y-2 text-xs">
                    <div className="flex items-center justify-between">
                      <span className="font-mono font-bold text-slate-800">
                        Version {tv.version_number}
                      </span>
                      {tv.is_current && (
                        <Badge className="bg-emerald-600 text-white text-[10px]">Current Target</Badge>
                      )}
                    </div>
                    {tv.changelog && (
                      <div className="text-muted-foreground text-[11px]">{tv.changelog}</div>
                    )}
                    <pre className="font-mono text-[10px] bg-slate-900 text-emerald-400 p-2 rounded max-h-32 overflow-y-auto">
                      {JSON.stringify(tv.definition, null, 2)}
                    </pre>
                  </div>
                ))
              )}
            </div>

            <div className="flex justify-end pt-3 border-t">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setSelectedTemplateForVersions(null)}
              >
                Close
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────────────── */}
      {/* MODAL: NEW TEMPLATE VERSION */}
      {/* ─────────────────────────────────────────────────────────────────── */}
      {isNewTemplateVersionModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-lg w-full p-6 space-y-4">
            <div className="flex items-center justify-between border-b pb-3">
              <h3 className="text-base font-semibold text-slate-900">
                New Template Version for {selectedTemplateForVersions?.name}
              </h3>
              <button
                onClick={() => setIsNewTemplateVersionModalOpen(false)}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleCreateTemplateVersion} className="space-y-4 text-xs">
              <div>
                <Label className="text-xs">Changelog Note</Label>
                <Input
                  placeholder="e.g. Added wait step for dynamic React rendering"
                  value={newTplVerChangelog}
                  onChange={(e) => setNewTplVerChangelog(e.target.value)}
                  className="mt-1 text-xs"
                />
              </div>

              <div>
                <Label className="text-xs">Template Definition (JSON)</Label>
                <textarea
                  value={newTplVerDefJson}
                  onChange={(e) => setNewTplVerDefJson(e.target.value)}
                  rows={8}
                  className="w-full mt-1 font-mono text-xs border rounded-md p-2 bg-slate-900 text-emerald-400 border-slate-700"
                />
              </div>

              <div className="flex justify-end gap-2 pt-3 border-t">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setIsNewTemplateVersionModalOpen(false)}
                >
                  Cancel
                </Button>
                <Button type="submit" size="sm" disabled={loading}>
                  {loading && <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" />}
                  Create Version
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────────────── */}
      {/* MODAL: NEW VALIDATION CASE */}
      {/* ─────────────────────────────────────────────────────────────────── */}
      {isNewCaseModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-lg w-full p-6 space-y-4">
            <div className="flex items-center justify-between border-b pb-3">
              <h3 className="text-base font-semibold text-slate-900">Add Validation Test Case</h3>
              <button
                onClick={() => setIsNewCaseModalOpen(false)}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleCreateValidationCase} className="space-y-4 text-xs">
              <div>
                <Label className="text-xs">Test Case Name *</Label>
                <Input
                  required
                  placeholder="e.g. Known Parcel 01-002-0034 Standard Lookup"
                  value={newCaseName}
                  onChange={(e) => setNewCaseName(e.target.value)}
                  className="mt-1 text-xs"
                />
              </div>

              <div>
                <Label className="text-xs">Description</Label>
                <Input
                  placeholder="Verify parcel owner extraction matches expected"
                  value={newCaseDesc}
                  onChange={(e) => setNewCaseDesc(e.target.value)}
                  className="mt-1 text-xs"
                />
              </div>

              <div>
                <Label className="text-xs">Input Data (JSON) *</Label>
                <textarea
                  value={newCaseInputJson}
                  onChange={(e) => setNewCaseInputJson(e.target.value)}
                  rows={3}
                  className="w-full mt-1 font-mono text-xs border rounded-md p-2 bg-slate-50 border-slate-300"
                />
              </div>

              <div>
                <Label className="text-xs">Expected Output (JSON) *</Label>
                <textarea
                  value={newCaseExpectedJson}
                  onChange={(e) => setNewCaseExpectedJson(e.target.value)}
                  rows={3}
                  className="w-full mt-1 font-mono text-xs border rounded-md p-2 bg-slate-50 border-slate-300"
                />
              </div>

              <div className="flex justify-end gap-2 pt-3 border-t">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setIsNewCaseModalOpen(false)}
                >
                  Cancel
                </Button>
                <Button type="submit" size="sm" disabled={loading}>
                  {loading && <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" />}
                  Save Test Case
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
