/**
 * Client dell'API di PrintReady AI.
 *
 * Tutte le chiamate passano da qui: un solo punto in cui gestire l'indirizzo
 * del backend, gli errori e la traduzione dei messaggi per l'utente.
 */
import type {
  ExportFormat,
  GenerationSettings,
  ImageRef,
  JobStatus,
  PipelineReport,
  Project,
  ProjectSummary,
} from './types';

/** Errore dell'API con messaggio già pronto per l'interfaccia. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

let baseUrl = 'http://127.0.0.1:8765';

/** Imposta l'indirizzo del backend (fornito da Electron all'avvio). */
export function setBaseUrl(url: string): void {
  baseUrl = url.replace(/\/$/, '');
}

export function getBaseUrl(): string {
  return baseUrl;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, {
      ...init,
      headers: {
        ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
        ...init?.headers,
      },
    });
  } catch {
    throw new ApiError(
      'Impossibile contattare il motore di elaborazione. Riavviare l’applicazione.',
      0,
    );
  }

  if (!response.ok) {
    let detail = `Errore ${response.status}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === 'string') detail = body.detail;
    } catch {
      // Risposta senza corpo JSON: si tiene il messaggio generico.
    }
    throw new ApiError(detail, response.status);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  // -- stato -----------------------------------------------------------
  health: () => request<{ stato: string; versione: string }>('/api/health'),

  // -- progetti --------------------------------------------------------
  listProjects: () => request<ProjectSummary[]>('/api/projects'),

  createProject: (name: string, prompt = '') =>
    request<Project>('/api/projects', {
      method: 'POST',
      body: JSON.stringify({ name, prompt }),
    }),

  getProject: (id: string) => request<Project>(`/api/projects/${id}`),

  updateProject: (
    id: string,
    changes: Partial<{
      name: string;
      prompt: string;
      negative_prompt: string;
      settings: GenerationSettings;
      notes: string;
      tags: string[];
    }>,
  ) =>
    request<Project>(`/api/projects/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(changes),
    }),

  deleteProject: (id: string) =>
    request<void>(`/api/projects/${id}`, { method: 'DELETE' }),

  uploadImage: (projectId: string, file: File, view = 'auto') => {
    const form = new FormData();
    form.append('file', file);
    return request<ImageRef>(`/api/projects/${projectId}/images?view=${view}`, {
      method: 'POST',
      body: form,
    });
  },

  deleteImage: (projectId: string, imageId: string) =>
    request<void>(`/api/projects/${projectId}/images/${imageId}`, { method: 'DELETE' }),

  imageUrl: (projectId: string, imageId: string) =>
    `${baseUrl}/api/projects/${projectId}/images/${imageId}/file`,

  // -- cronologia ------------------------------------------------------
  history: (projectId: string) =>
    request<{ index: number; label_it: string; created_at: string; current: boolean }[]>(
      `/api/projects/${projectId}/history`,
    ),

  undo: (projectId: string) =>
    request<Project>(`/api/projects/${projectId}/undo`, { method: 'POST' }),

  redo: (projectId: string) =>
    request<Project>(`/api/projects/${projectId}/redo`, { method: 'POST' }),

  // -- generazione -----------------------------------------------------
  generate: (projectId: string, settings?: GenerationSettings) =>
    request<JobStatus>('/api/generate', {
      method: 'POST',
      body: JSON.stringify({ project_id: projectId, settings: settings ?? null }),
    }),

  generateBatch: (projectIds: string[], maxParallel = 2) =>
    request<JobStatus[]>('/api/generate/batch', {
      method: 'POST',
      body: JSON.stringify({ project_ids: projectIds, max_parallel: maxParallel }),
    }),

  jobStatus: (jobId: string) => request<JobStatus>(`/api/generate/${jobId}`),

  jobReport: (jobId: string) => request<PipelineReport>(`/api/generate/${jobId}/report`),

  cancelJob: (jobId: string) =>
    request<{ annullato: boolean }>(`/api/generate/${jobId}/cancel`, { method: 'POST' }),

  // -- risultati -------------------------------------------------------
  previewUrl: (jobId: string) => `${baseUrl}/api/mesh/${jobId}/preview`,

  partFileUrl: (jobId: string, partId: string, format: ExportFormat) =>
    `${baseUrl}/api/mesh/${jobId}/parts/${partId}/file?fmt=${format}`,

  files: (jobId: string) =>
    request<
      {
        formato: string;
        nome: string;
        percorso: string;
        dimensione_byte: number;
        part_id: string | null;
        completo: boolean;
        suggerimento_it: string | null;
      }[]
    >(`/api/mesh/${jobId}/files`),

  instructionsUrl: (jobId: string) => `${baseUrl}/api/mesh/${jobId}/instructions`,

  // -- impostazioni ----------------------------------------------------
  defaults: () => request<GenerationSettings>('/api/settings/defaults'),

  printers: () => request<Record<string, PrinterPreset>>('/api/settings/printers'),

  options: () => request<ApiOptions>('/api/settings/options'),

  providers: () =>
    request<
      { name: string; label_it: string; available: boolean; offline: boolean }[]
    >('/api/settings/providers'),

  checkUpdates: () =>
    request<{
      disponibile: boolean;
      versione_attuale: string;
      versione_disponibile: string;
      messaggio_it: string;
      note_it: string;
    }>('/api/settings/updates/check'),

  /** Apre il WebSocket degli eventi di avanzamento. */
  openEvents: (jobId?: string): WebSocket => {
    const wsBase = baseUrl.replace(/^http/, 'ws');
    const query = jobId ? `?job_id=${encodeURIComponent(jobId)}` : '';
    return new WebSocket(`${wsBase}/ws/jobs${query}`);
  },
};

export interface PrinterPreset {
  name: string;
  nozzle_diameter_mm: number;
  layer_height_mm: number;
  bed_size_mm: [number, number, number];
  has_ams: boolean;
  ams_slots: number;
  parete_minima_effettiva_mm: number;
}

export interface ApiOptions {
  tipi_incastro: { valore: string; etichetta_it: string }[];
  formati_export: { valore: string; estensione: string; supporta_colore: boolean }[];
  slicer: {
    valore: string;
    etichetta_it: string;
    formato_consigliato: string;
    supporta_ams: boolean;
    note_it: string;
  }[];
  parti: { valore: string; etichetta_it: string }[];
  passi_pipeline: { valore: string; etichetta_it: string }[];
  classi_tolleranza: {
    nome: string;
    minimo_mm: number;
    massimo_mm: number;
    descrizione_it: string;
  }[];
  controlli_stampa: {
    codice: string;
    nome_it: string;
    descrizione_it: string;
    correzione_automatica: boolean;
  }[];
}
