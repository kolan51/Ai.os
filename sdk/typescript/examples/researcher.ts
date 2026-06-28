import { Agent, tool, schedule } from '../src/index.js';

class ResearchAgent extends Agent {
  static agentName = 'researcher';
  static model = 'claude-sonnet-4-6';
  static systemPrompt = 'You are a precise research assistant. When asked to research a topic, use the available tools to save your findings, then summarize what you found.';

  @tool({ description: 'Save a research finding to long-term memory', retries: 2 })
  async saveFinding(topic: string, summary: string): Promise<string> {
    this.memory.save(`finding:${topic}`, {
      summary,
      savedAt: new Date().toISOString(),
    });
    this.memory.logEvent('finding_saved', { topic });
    return `Saved finding for topic: ${topic}`;
  }

  @tool({ description: 'Load all previously saved research findings' })
  async loadFindings(): Promise<string> {
    const all = this.memory.all();
    const findings = Object.entries(all)
      .filter(([k]) => k.startsWith('finding:'))
      .map(([k, v]) => `${k.slice(8)}: ${JSON.stringify(v)}`);

    if (findings.length === 0) return 'No findings saved yet.';
    return findings.join('\n');
  }

  async onStart(): Promise<void> {
    console.log(`[${ResearchAgent.agentName}] Starting up...`);
  }

  async run(): Promise<void> {
    // Check for context from a previous run.
    const lastTopic = this.memory.load('last_topic') as string | undefined;
    if (lastTopic) {
      console.log(`[${ResearchAgent.agentName}] Last researched topic: ${lastTopic}`);
    }

    const result = await this.thinkWithTools(
      'Research the latest developments in AI agents. Save 3 key findings using the saveFinding tool, then summarize what you found.'
    );

    this.memory.save('last_topic', 'ai-agents');
    console.log(`[${ResearchAgent.agentName}] Done:\n${result}`);
  }
}

// Run once immediately, then repeat every hour.
// Remove @schedule to run just once.
// schedule('every 1h')(ResearchAgent.prototype, 'run', Object.getOwnPropertyDescriptor(ResearchAgent.prototype, 'run')!);

if (require.main === module) {
  ResearchAgent.launch();
}
