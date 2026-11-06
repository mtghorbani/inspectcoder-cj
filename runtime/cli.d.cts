export interface ResolveCliOptions {
  executable?: string;
  env?: NodeJS.ProcessEnv;
  homeDir?: string;
  platform?: string;
}
export function resolveCli(options?: ResolveCliOptions): string;
