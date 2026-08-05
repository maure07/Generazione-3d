/**
 * Tipi condivisi con il backend.
 *
 * Rispecchiano i modelli Pydantic di `printready.domain.models`: quando si
 * modifica un modello lì, va aggiornato anche qui.
 */

export type JobState = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

export type JoineryType =
  | 'cylindrical_pin'
  | 'conical_pin'
  | 'square_pin'
  | 'magnet'
  | 'none';

export type ExportFormat = 'stl' | 'obj' | '3mf' | 'step' | 'glb';

export type SlicerTarget =
  | 'bambu_studio'
  | 'orca_slicer'
  | 'cura'
  | 'prusa_slicer'
  | 'anycubic_slicer';

export type UIMode = 'beginner' | 'expert';

export type Severity = 'info' | 'warning' | 'error' | 'critical';

export interface PrinterProfile {
  name: string;
  nozzle_diameter_mm: number;
  layer_height_mm: number;
  bed_size_mm: [number, number, number];
  max_overhang_deg: number;
  min_wall_mm: number;
  min_feature_mm: number;
  has_ams: boolean;
  ams_slots: number;
  purge_volume_mm3: number;
}

export interface JoinerySettings {
  enabled: boolean;
  joint_type: JoineryType;
  tolerance_mm: number;
  pin_diameter_mm: number;
  pin_length_mm: number;
  conical_taper_ratio: number;
  magnet_diameter_mm: number;
  magnet_height_mm: number;
  magnet_recess_mm: number;
  auto_scale_to_part: boolean;
  anti_rotation: boolean;
}

export interface SegmentationSettings {
  enabled: boolean;
  max_parts: number;
  min_part_volume_ratio: number;
  merge_tiny_parts: boolean;
  separate_by_color: boolean;
  use_anatomical_priors: boolean;
  forced_labels: string[];
  split_symmetric_pairs: boolean;
}

export interface OptimizationSettings {
  target_faces: number;
  preserve_detail: number;
  adaptive: boolean;
  weld_distance_mm: number;
  remove_floaters_ratio: number;
}

export interface SolidifySettings {
  enabled: boolean;
  shell_thickness_mm: number;
  hollow: boolean;
  drain_holes: number;
  drain_hole_diameter_mm: number;
}

export interface AMSSettings {
  enabled: boolean;
  max_colors: number;
  minimize_swaps: boolean;
  prefer_part_split_over_swap: boolean;
  max_acceptable_swaps: number;
}

export interface GenerationSettings {
  printer: PrinterProfile;
  joinery: JoinerySettings;
  segmentation: SegmentationSettings;
  optimization: OptimizationSettings;
  solidify: SolidifySettings;
  ams: AMSSettings;
  target_height_mm: number;
  ai_provider: string;
  export_formats: ExportFormat[];
  slicer_targets: SlicerTarget[];
  ui_mode: UIMode;
  auto_fix: boolean;
  seed: number | null;
}

export interface ImageRef {
  id: string;
  filename: string;
  path: string;
  width: number;
  height: number;
  view: string;
  is_primary: boolean;
}

export interface PartInfo {
  id: string;
  name: string;
  part_type: string;
  side: string;
  faces: number;
  vertices: number;
  volume_mm3: number;
  area_mm2: number;
  bounds: { min: [number, number, number]; max: [number, number, number] } | null;
  color_hex: string | null;
  ams_slot: number | null;
  watertight: boolean;
  confidence: number;
}

export interface ConnectorInfo {
  id: string;
  joint_type: JoineryType;
  male_part_id: string;
  female_part_id: string;
  diameter_mm: number;
  length_mm: number;
  tolerance_mm: number;
  magnet_spec: string | null;
}

export interface Issue {
  code: string;
  severity: Severity;
  message_it: string;
  part_id: string | null;
  count: number;
  auto_fixed: boolean;
}

export interface StepResult {
  step: string;
  state: JobState;
  duration_s: number;
  message_it: string;
  details: Record<string, unknown>;
}

export interface AMSPlan {
  slots: Record<string, string>;
  slot_names_it: Record<string, string>;
  part_assignment: Record<string, number>;
  color_changes: number;
  color_changes_naive: number;
  purge_waste_mm3: number;
  purge_waste_saved_mm3: number;
  estimated_time_saved_min: number;
  notes_it: string[];
}

export interface ExportedFile {
  format: ExportFormat;
  path: string;
  filename: string;
  size_bytes: number;
  part_id: string | null;
  contains_all_parts: boolean;
  slicer_hint_it: string | null;
}

export interface PipelineReport {
  job_id: string;
  project_id: string;
  state: JobState;
  steps: StepResult[];
  parts: PartInfo[];
  connectors: ConnectorInfo[];
  issues: Issue[];
  ams_plan: AMSPlan | null;
  exports: ExportedFile[];
  printability_score: number;
  summary_it: string;
  error_it: string | null;
}

export interface JobStatus {
  job_id: string;
  project_id: string;
  state: JobState;
  current_step: string | null;
  progress: number;
  message_it: string;
  report: PipelineReport | null;
  error_it: string | null;
}

export interface Project {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  prompt: string;
  negative_prompt: string;
  images: ImageRef[];
  settings: GenerationSettings;
  last_report: PipelineReport | null;
  tags: string[];
  notes: string;
  thumbnail_path: string | null;
}

export interface ProjectSummary {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  prompt: string;
  parts_count: number;
  last_state: JobState;
}

/** Evento trasmesso dal WebSocket di avanzamento. */
export interface PipelineEvent {
  type: string;
  job_id: string | null;
  project_id: string | null;
  payload: Record<string, unknown>;
  timestamp: string;
}
