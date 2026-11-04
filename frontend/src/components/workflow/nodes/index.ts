import type { NodeTypes } from "reactflow";
import { InputNode } from "./InputNode";
import { AssessorNode } from "./AssessorNode";
import { TaxNode } from "./TaxNode";
import { NormalizeNode } from "./NormalizeNode";
import { OutputNode } from "./OutputNode";

export const workflowNodeTypes: NodeTypes = {
  input: InputNode,
  assessor: AssessorNode,
  tax: TaxNode,
  normalize: NormalizeNode,
  output: OutputNode,
};

export { InputNode, AssessorNode, TaxNode, NormalizeNode, OutputNode };

