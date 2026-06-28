import Anthropic from '@anthropic-ai/sdk';
import path from 'path';
import os from 'os';
import fs from 'fs';
import * as dotenv from 'dotenv';

import { MemoryStore } from './memory.js';
import { CheckpointEngine } from './checkpoint.js';
import { collectTools } from './tool.js';
import { getSchedule } from './schedule.js';
import { RunResult } from './types.js';

dotenv.config();

// ── Helpers ──────────────────────────────────────────────────────────────────

function defaultDbPath(agentName: string): string {
  const dir = path.join(os.homedir(), '.aios', 'data');
  fs.mkdirSync(dir, { recursive: true });
  return path.join(dir, `${agentName}.db`);
}

function toSnakeCase(name: string): string {
  return name
    .replace(/([A-Z])/g, '_$1')
    .toLowerCase()
    .replace(/^_/, '');
}

// ── Agent base class ──────────────────────────────────────────────────────────

export abstract class Agent {
  // Override these in subclasses ──────────────────────────────────────────────
  /** Human-readable agent name (also used as the SQLite filename). */
  static agentName: string;
  static model: string = 'claude-sonnet-4-6';
  static systemPrompt: string = 'You are a helpful AI agent.';
  static temperature: number = 0.7;
  static maxTokens: number = 4096;

  // Instance state (set during setup()) ───────────────────────────────────────
  memory!: MemoryStore;
  checkpoint!: CheckpointEngine;
  logger: Console = console;

  protected _client!: Anthropic;
  protected _agentId!: string;
  protected _dbPath!: string;

  // ── Lifecycle ───────────────────────────────────────────────────────────────

  /** Called once before run(). Override for additional setup. */
  async onStart(): Promise<void> {}

  /** Called once after run() completes (even on error). Override for cleanup. */
  async onStop(): Promise<void> {}

  /** Implement agent logic here. */
  abstract run(): Promise<void>;

  // ── Setup ───────────────────────────────────────────────────────────────────

  async setup(): Promise<void> {
    const ctor = this.constructor as typeof Agent;
    const name =
      ctor.agentName ?? toSnakeCase(ctor.name ?? 'agent');

    this._agentId = name;
    this._dbPath = defaultDbPath(name);

    this.memory = new MemoryStore(this._agentId, this._dbPath);
    this.checkpoint = new CheckpointEngine(this._agentId, this._dbPath);

    this._client = new Anthropic({
      apiKey: process.env.ANTHROPIC_API_KEY,
    });
  }

  // ── LLM methods ─────────────────────────────────────────────────────────────

  /**
   * Single-shot completion. Returns the assistant's text response.
   */
  async think(prompt: string): Promise<string> {
    const ctor = this.constructor as typeof Agent;

    const response = await this._client.messages.create({
      model: ctor.model,
      max_tokens: ctor.maxTokens,
      temperature: ctor.temperature,
      system: ctor.systemPrompt,
      messages: [{ role: 'user', content: prompt }],
    });

    const block = response.content.find((b) => b.type === 'text');
    return block?.type === 'text' ? block.text : '';
  }

  /**
   * Agentic loop: calls the LLM repeatedly, executing @tool methods until
   * the model produces a final text response with no more tool calls.
   */
  async thinkWithTools(
    prompt: string,
    maxIterations = 10
  ): Promise<string> {
    const ctor = this.constructor as typeof Agent;
    const toolMethods = collectTools(this);

    // Build Anthropic tool definitions from @tool-decorated methods.
    const tools: Anthropic.Tool[] = toolMethods.map(({ name, options }) => ({
      name,
      description: options.description ?? name,
      input_schema: {
        type: 'object' as const,
        properties: {},
        required: [],
      },
    }));

    const messages: Anthropic.MessageParam[] = [
      { role: 'user', content: prompt },
    ];

    let iterations = 0;

    while (iterations < maxIterations) {
      iterations++;

      const response = await this._client.messages.create({
        model: ctor.model,
        max_tokens: ctor.maxTokens,
        temperature: ctor.temperature,
        system: ctor.systemPrompt,
        tools: tools.length > 0 ? tools : undefined,
        messages,
      });

      // Add assistant turn to history.
      messages.push({ role: 'assistant', content: response.content });

      // If the model is done, return its text.
      if (response.stop_reason === 'end_turn') {
        const block = response.content.find((b) => b.type === 'text');
        return block?.type === 'text' ? block.text : '';
      }

      // Process tool calls.
      const toolUseBlocks = response.content.filter(
        (b): b is Anthropic.ToolUseBlock => b.type === 'tool_use'
      );

      if (toolUseBlocks.length === 0) {
        // No tool calls and stop_reason is not end_turn — return what we have.
        const block = response.content.find((b) => b.type === 'text');
        return block?.type === 'text' ? block.text : '';
      }

      // Execute each tool call and build the tool result turn.
      const toolResults: Anthropic.ToolResultBlockParam[] = [];

      for (const block of toolUseBlocks) {
        const method = toolMethods.find((t) => t.name === block.name);

        if (!method) {
          toolResults.push({
            type: 'tool_result',
            tool_use_id: block.id,
            content: `Error: unknown tool "${block.name}"`,
            is_error: true,
          });
          continue;
        }

        try {
          // Pass the input object's values as positional args when possible,
          // otherwise pass the whole input object as the first argument.
          const input = block.input as Record<string, unknown>;
          const args = Object.values(input);
          const result = await method.fn(...args);
          toolResults.push({
            type: 'tool_result',
            tool_use_id: block.id,
            content:
              typeof result === 'string' ? result : JSON.stringify(result),
          });
        } catch (err) {
          toolResults.push({
            type: 'tool_result',
            tool_use_id: block.id,
            content: `Error: ${err instanceof Error ? err.message : String(err)}`,
            is_error: true,
          });
        }
      }

      messages.push({ role: 'user', content: toolResults });
    }

    return `[Agent reached max iterations (${maxIterations})]`;
  }

  // ── Internal execution ───────────────────────────────────────────────────────

  private async _execute(): Promise<RunResult> {
    const start = Date.now();

    await this.setup();
    await this.onStart();

    this.memory.logEvent('agent_start', {
      model: (this.constructor as typeof Agent).model,
    });

    try {
      await this.run();
      this.memory.logEvent('agent_stop', { status: 'success' });
      await this.onStop();
      return { success: true, duration_ms: Date.now() - start };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.memory.logEvent('agent_error', { error: message });
      await this.onStop();
      return { success: false, error: message, duration_ms: Date.now() - start };
    } finally {
      this.memory.close();
      this.checkpoint.close();
    }
  }

  // ── Static entry point ───────────────────────────────────────────────────────

  /**
   * Call `MyAgent.launch()` as the entry point.
   *
   * If the `run()` method (or any method) is decorated with @schedule,
   * the agent loops at that interval. Otherwise it runs once and exits.
   */
  static launch<T extends Agent>(this: new () => T): void {
    const AgentClass = this as unknown as typeof Agent & (new () => T);
    const proto = AgentClass.prototype as Record<string, unknown>;

    // Check if run() has a @schedule decorator.
    const runFn = proto['run'];
    const scheduleOpts =
      typeof runFn === 'function' ? getSchedule(runFn as Function) : undefined;

    if (scheduleOpts) {
      const { intervalMs, raw } = scheduleOpts;

      const loop = async (): Promise<void> => {
        const instance = new AgentClass();
        const result = await instance._execute();

        const name =
          AgentClass.agentName ?? toSnakeCase(AgentClass.name ?? 'agent');
        if (result.success) {
          console.log(
            `[${name}] run complete in ${result.duration_ms}ms. ` +
              `Next run in ${raw}.`
          );
        } else {
          console.error(
            `[${name}] run failed: ${result.error}. ` +
              `Retrying in ${raw}.`
          );
        }

        setTimeout(loop, intervalMs);
      };

      void loop();
    } else {
      // Single run.
      const instance = new AgentClass();
      instance._execute().then((result) => {
        const name =
          AgentClass.agentName ?? toSnakeCase(AgentClass.name ?? 'agent');
        if (result.success) {
          console.log(`[${name}] done in ${result.duration_ms}ms.`);
        } else {
          console.error(`[${name}] failed: ${result.error}`);
          process.exitCode = 1;
        }
      });
    }
  }
}
