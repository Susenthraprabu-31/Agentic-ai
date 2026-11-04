import { apiClient } from "./client";
import type { DocumentArtifact, DocumentDownloadAttempt } from "../types";

export interface ListDocumentsParams {
  workflow_run_id?: string;
  property_record_id?: string;
  document_type?: string;
  status?: string;
  skip?: number;
  limit?: number;
}

export interface DocumentOcrAnalysisResult {
  success: boolean;
  document_id: string;
  filename: string;
  pages_count: number;
  ocr_model: string;
  property_fields: {
    parcel_number?: string | null;
    account_number?: string | null;
    owner_name?: string | null;
    property_address?: string | null;
    mailing_address?: string | null;
    legal_description?: string | null;
    property_use?: string | null;
    taxing_district?: string | null;
    land_size?: string | null;
    exemptions?: string | null;
    map_number?: string | null;
    just_value?: number | null;
    assessed_value?: number | null;
    taxable_value?: number | null;
    subdivision?: string | null;
    tax_status?: string | null;
    total_amount_due?: number | null;
    tax_year?: number | string | null;
  };
  tables: Array<{
    headers: string[];
    rows: string[][];
  }>;
  sales_history: Array<Record<string, any>>;
  valuation_history: Array<Record<string, any>>;
  key_values: Record<string, string>;
  markdown: string;
  error?: string | null;
}

export const documentsApi = {
  /** Fetch paginated list of document artifacts */
  async list(params?: ListDocumentsParams): Promise<{ items: DocumentArtifact[]; total: number }> {
    const res = await apiClient.get<{ items: DocumentArtifact[]; total: number }>("/documents", {
      params,
    });
    return res.data;
  },

  /** Get document metadata by ID */
  async getById(id: string): Promise<DocumentArtifact> {
    const res = await apiClient.get<DocumentArtifact>(`/documents/${id}`);
    return res.data;
  },

  /** Download document file as blob and trigger browser save */
  async download(id: string, filename?: string): Promise<void> {
    const res = await apiClient.get(`/documents/${id}/download`, {
      responseType: "blob",
    });
    const contentType = (res.headers && typeof res.headers["content-type"] === "string")
      ? res.headers["content-type"]
      : "application/pdf";
    const blob = new Blob([res.data], { type: contentType });
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename || `document_${id}.pdf`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
  },

  /** Fetch binary blob for inline document preview */
  async viewBlob(id: string): Promise<{ blobUrl: string; contentType: string }> {
    const res = await apiClient.get(`/documents/${id}/view`, {
      responseType: "blob",
    });
    const contentType = (res.headers && typeof res.headers["content-type"] === "string")
      ? res.headers["content-type"]
      : "application/pdf";
    const blob = new Blob([res.data], { type: contentType });
    const blobUrl = window.URL.createObjectURL(blob);
    return { blobUrl, contentType };
  },

  /** Run Mistral Document AI OCR on document payload */
  async ocrAnalyze(id: string): Promise<DocumentOcrAnalysisResult> {
    const res = await apiClient.post<DocumentOcrAnalysisResult>(`/documents/${id}/ocr-analyze`);
    return res.data;
  },

  /** Get granular download attempts history */
  async getHistory(id: string): Promise<DocumentDownloadAttempt[]> {
    const res = await apiClient.get<DocumentDownloadAttempt[]>(`/documents/${id}/download-history`);
    return res.data;
  },

  /** Retry downloading failed or incomplete document */
  async retryDownload(id: string): Promise<DocumentArtifact> {
    const res = await apiClient.post<DocumentArtifact>(`/documents/${id}/retry-download`);
    return res.data;
  },
};
