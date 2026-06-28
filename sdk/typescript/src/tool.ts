export const AIOS_TOOL_META = '__aios_tool__';
export const AIOS_TOOL_OPTIONS = '__aios_tool_options__';

export interface ToolOptions {
  description?: string;
  retries?: number;
  backoff?: number;   // ms between retries
  cacheTtl?: number;  // seconds; reserved for future checkpoint integration
}

/**
 * Mark a method as an Ai.os tool.
 *
 * Usage:
 *   @tool({ description: 'Search the web', retries: 3 })
 *   async search(query: string): Promise<string> { ... }
 *
 *   @tool('Fetch a URL')
 *   async fetch(url: string): Promise<string> { ... }
 *
 *   @tool()
 *   async doSomething(): Promise<void> { ... }
 */
export function tool(options?: ToolOptions | string): MethodDecorator {
  const opts: ToolOptions =
    typeof options === 'string' ? { description: options } : options ?? {};

  return function (
    _target: object,
    propertyKey: string | symbol,
    descriptor: PropertyDescriptor
  ): PropertyDescriptor {
    const originalMethod = descriptor.value as (
      ...args: unknown[]
    ) => Promise<unknown>;

    // Attach metadata flags directly on the function so Agent can discover them.
    (originalMethod as Record<string | symbol, unknown>)[AIOS_TOOL_META] = true;
    (originalMethod as Record<string | symbol, unknown>)[AIOS_TOOL_OPTIONS] =
      opts;

    // Wrap with retry logic if retries > 0.
    if (opts.retries && opts.retries > 0) {
      const retries = opts.retries;
      const backoff = opts.backoff ?? 1000;

      descriptor.value = async function (
        this: unknown,
        ...args: unknown[]
      ): Promise<unknown> {
        let lastError: unknown;
        for (let attempt = 0; attempt <= retries; attempt++) {
          try {
            return await originalMethod.apply(this, args);
          } catch (err) {
            lastError = err;
            if (attempt < retries) {
              await new Promise((res) =>
                setTimeout(res, backoff * Math.pow(2, attempt))
              );
            }
          }
        }
        throw lastError;
      };

      // Preserve metadata on the wrapper too.
      (descriptor.value as Record<string | symbol, unknown>)[AIOS_TOOL_META] =
        true;
      (descriptor.value as Record<string | symbol, unknown>)[
        AIOS_TOOL_OPTIONS
      ] = opts;
    }

    // Preserve the original method name.
    Object.defineProperty(descriptor.value, 'name', {
      value: String(propertyKey),
    });

    return descriptor;
  };
}

/**
 * Given a class instance, return all methods decorated with @tool,
 * along with their resolved ToolOptions.
 */
export function collectTools(
  instance: object
): Array<{ name: string; options: ToolOptions; fn: (...args: unknown[]) => Promise<unknown> }> {
  const proto = Object.getPrototypeOf(instance) as Record<string, unknown>;
  const results: Array<{
    name: string;
    options: ToolOptions;
    fn: (...args: unknown[]) => Promise<unknown>;
  }> = [];

  for (const key of Object.getOwnPropertyNames(proto)) {
    if (key === 'constructor') continue;
    const val = proto[key];
    if (
      typeof val === 'function' &&
      (val as Record<string | symbol, unknown>)[AIOS_TOOL_META] === true
    ) {
      results.push({
        name: key,
        options: ((val as Record<string | symbol, unknown>)[AIOS_TOOL_OPTIONS] ??
          {}) as ToolOptions,
        fn: (val as (...args: unknown[]) => Promise<unknown>).bind(instance),
      });
    }
  }

  return results;
}
