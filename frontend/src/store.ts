/**
 * Stato globale dell'applicazione (Zustand).
 *
 * Tiene il progetto corrente, il job in corso e il registro degli eventi.
 * Le azioni incapsulano le chiamate all'API così i componenti restano
 * dichiarativi e non gestiscono direttamente la rete.
 */
import { create } from 'zustand';

import { ApiError, api } from './api/client';
import type {
  ApiOptions,
  PrinterPreset,
} from './api/client';
import type {
  GenerationSettings,
  JobStatus,
  PipelineReport,
  Project,
  ProjectSummary,
  UIMode,
} from './api/types';

/** Voce del registro di avanzamento mostrato durante l'elaborazione. */
export interface LogEntry {
  id: number;
  time: string;
  text: string;
  kind: 'info' | 'success' | 'error';
}

interface State {
  // dati
  projects: ProjectSummary[];
  project: Project | null;
  job: JobStatus | null;
  report: PipelineReport | null;
  options: ApiOptions | null;
  printers: Record<string, PrinterPreset>;
  log: LogEntry[];

  // interfaccia
  uiMode: UIMode;
  busy: boolean;
  error: string | null;

  // azioni
  init: () => Promise<void>;
  refreshProjects: () => Promise<void>;
  newProject: (name: string) => Promise<void>;
  openProject: (id: string) => Promise<void>;
  removeProject: (id: string) => Promise<void>;
  patchProject: (changes: Partial<Project>) => Promise<void>;
  updateSettings: (settings: GenerationSettings) => Promise<void>;
  addImages: (files: File[]) => Promise<void>;
  removeImage: (imageId: string) => Promise<void>;
  undo: () => Promise<void>;
  redo: () => Promise<void>;
  generate: () => Promise<void>;
  cancel: () => Promise<void>;
  setUiMode: (mode: UIMode) => void;
  clearError: () => void;
  appendLog: (text: string, kind?: LogEntry['kind']) => void;
}

let logCounter = 0;
let socket: WebSocket | null = null;

function now(): string {
  return new Date().toLocaleTimeString('it-IT', { hour12: false });
}

function describe(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return 'Errore imprevisto';
}

export const useStore = create<State>((set, get) => ({
  projects: [],
  project: null,
  job: null,
  report: null,
  options: null,
  printers: {},
  log: [],
  uiMode: 'beginner',
  busy: false,
  error: null,

  appendLog: (text, kind = 'info') =>
    set((state) => ({
      // Il registro è una finestra scorrevole: oltre 300 voci non serve a nulla.
      log: [...state.log, { id: ++logCounter, time: now(), text, kind }].slice(-300),
    })),

  clearError: () => set({ error: null }),

  setUiMode: (uiMode) => set({ uiMode }),

  init: async () => {
    try {
      const [options, printers] = await Promise.all([api.options(), api.printers()]);
      set({ options, printers });
      await get().refreshProjects();
    } catch (error) {
      set({ error: describe(error) });
    }
  },

  refreshProjects: async () => {
    try {
      set({ projects: await api.listProjects() });
    } catch (error) {
      set({ error: describe(error) });
    }
  },

  newProject: async (name) => {
    set({ busy: true });
    try {
      const project = await api.createProject(name);
      set({ project, report: null, job: null, log: [] });
      get().appendLog(`Progetto «${project.name}» creato`, 'success');
      await get().refreshProjects();
    } catch (error) {
      set({ error: describe(error) });
    } finally {
      set({ busy: false });
    }
  },

  openProject: async (id) => {
    set({ busy: true });
    try {
      const project = await api.getProject(id);
      set({ project, report: project.last_report, job: null, log: [] });
      get().appendLog(`Progetto «${project.name}» aperto`);
    } catch (error) {
      set({ error: describe(error) });
    } finally {
      set({ busy: false });
    }
  },

  removeProject: async (id) => {
    try {
      await api.deleteProject(id);
      if (get().project?.id === id) set({ project: null, report: null, job: null });
      await get().refreshProjects();
    } catch (error) {
      set({ error: describe(error) });
    }
  },

  patchProject: async (changes) => {
    const current = get().project;
    if (!current) return;
    try {
      const project = await api.updateProject(current.id, changes as never);
      set({ project });
    } catch (error) {
      set({ error: describe(error) });
    }
  },

  updateSettings: async (settings) => {
    await get().patchProject({ settings } as never);
  },

  addImages: async (files) => {
    const project = get().project;
    if (!project) return;
    set({ busy: true });
    try {
      for (const file of files) {
        await api.uploadImage(project.id, file);
        get().appendLog(`Immagine «${file.name}» caricata`, 'success');
      }
      set({ project: await api.getProject(project.id) });
    } catch (error) {
      set({ error: describe(error) });
    } finally {
      set({ busy: false });
    }
  },

  removeImage: async (imageId) => {
    const project = get().project;
    if (!project) return;
    try {
      await api.deleteImage(project.id, imageId);
      set({ project: await api.getProject(project.id) });
    } catch (error) {
      set({ error: describe(error) });
    }
  },

  undo: async () => {
    const project = get().project;
    if (!project) return;
    try {
      set({ project: await api.undo(project.id) });
      get().appendLog('Modifica annullata');
    } catch (error) {
      set({ error: describe(error) });
    }
  },

  redo: async () => {
    const project = get().project;
    if (!project) return;
    try {
      set({ project: await api.redo(project.id) });
      get().appendLog('Modifica ripetuta');
    } catch (error) {
      set({ error: describe(error) });
    }
  },

  generate: async () => {
    const project = get().project;
    if (!project) return;
    if (project.images.length === 0) {
      set({ error: 'Caricare almeno un’immagine prima di generare' });
      return;
    }

    set({ busy: true, report: null, log: [] });
    try {
      const job = await api.generate(project.id);
      set({ job });
      get().appendLog('Elaborazione avviata', 'success');
      subscribe(job.job_id, set, get);
    } catch (error) {
      set({ error: describe(error), busy: false });
    }
  },

  cancel: async () => {
    const job = get().job;
    if (!job) return;
    try {
      await api.cancelJob(job.job_id);
      get().appendLog('Annullamento richiesto');
    } catch (error) {
      set({ error: describe(error) });
    }
  },
}));

/**
 * Si aggancia al WebSocket degli eventi e aggiorna lo stato in tempo reale.
 *
 * Alla chiusura del job scarica il rapporto completo e aggiorna la cronologia.
 */
function subscribe(
  jobId: string,
  set: (partial: Partial<State>) => void,
  get: () => State,
): void {
  socket?.close();
  socket = api.openEvents(jobId);

  socket.onmessage = (message) => {
    const event = JSON.parse(message.data as string);
    const payload = event.payload ?? {};

    switch (event.type) {
      case 'step_started':
        get().appendLog(`▶ ${payload.label_it}`);
        set({
          job: {
            ...(get().job as JobStatus),
            progress: Number(payload.progress ?? 0),
            message_it: String(payload.label_it ?? ''),
          },
        });
        break;

      case 'step_progress':
        if (payload.message_it) get().appendLog(`   ${payload.message_it}`);
        break;

      case 'step_completed':
        get().appendLog(
          `✓ ${payload.label_it}: ${payload.message_it} (${Number(payload.duration_s).toFixed(1)}s)`,
          'success',
        );
        break;

      case 'step_failed':
        get().appendLog(`✕ ${payload.label_it}: ${payload.error_it}`, 'error');
        break;

      case 'job_finished':
        void finish(jobId, set, get);
        socket?.close();
        socket = null;
        break;

      default:
        break;
    }
  };

  socket.onerror = () => {
    // Il WebSocket è solo un canale di comodo: se cade, si ricade sul polling.
    get().appendLog('Canale di avanzamento interrotto: passo al controllo periodico', 'error');
    void poll(jobId, set, get);
  };
}

/** Scarica lo stato finale e il rapporto. */
async function finish(
  jobId: string,
  set: (partial: Partial<State>) => void,
  get: () => State,
): Promise<void> {
  try {
    const job = await api.jobStatus(jobId);
    set({ job, busy: false });

    if (job.state === 'completed') {
      const report = await api.jobReport(jobId);
      set({ report });
      get().appendLog(report.summary_it, 'success');
    } else if (job.error_it) {
      set({ error: job.error_it });
      get().appendLog(job.error_it, 'error');
    }
    await get().refreshProjects();
  } catch (error) {
    set({ busy: false, error: describe(error) });
  }
}

/** Controllo periodico dello stato, usato se il WebSocket non è disponibile. */
async function poll(
  jobId: string,
  set: (partial: Partial<State>) => void,
  get: () => State,
): Promise<void> {
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    try {
      const job = await api.jobStatus(jobId);
      set({ job });
      if (job.state !== 'running' && job.state !== 'pending') {
        await finish(jobId, set, get);
        return;
      }
    } catch (error) {
      set({ busy: false, error: describe(error) });
      return;
    }
  }
}
