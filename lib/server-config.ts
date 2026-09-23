import { readFileSync } from "node:fs";
import path from "node:path";

type RuntimeConfig = {
  auth?: {
    enabled?: unknown;
  };
};

function configuredAuthentication(): boolean {
  if (process.env.MIA_LOCAL_MODE !== "1" || process.env.VERCEL_ENV) return true;

  try {
    const configPath = process.env.MIA_CONFIG_PATH ?? path.join(process.cwd(), "config.json");
    const config = JSON.parse(readFileSync(configPath, "utf8")) as RuntimeConfig;
    return config.auth?.enabled !== false;
  } catch {
    return true;
  }
}

export const AUTHENTICATION_ENABLED = configuredAuthentication();
