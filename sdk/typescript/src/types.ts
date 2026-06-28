export interface ToolSchema {
  type: 'object';
  properties: Record<string, { type: string; description?: string }>;
  required: string[];
}

export interface ToolDefinition {
  name: string;
  description: string;
  schema: ToolSchema;
  fn: (...args: unknown[]) => Promise<unknown>;
}

export interface RunResult {
  success: boolean;
  error?: string;
  duration_ms: number;
}

export interface MemoryEntry {
  key: string;
  value: unknown;
  updated_at: string;
}

export interface TimelineEntry {
  event: string;
  data: Record<string, unknown>;
  at: string;
}

export interface AgentOptions {
  dbPath?: string;
}
