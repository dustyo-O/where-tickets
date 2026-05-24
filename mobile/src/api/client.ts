/**
 * Backend API client.
 *
 * Reads `BACKEND_URL` from the bundled `.env` (via `react-native-config`),
 * with a platform-aware runtime fallback so the app works on both iOS and
 * Android emulators out of the box.
 */

import { Platform } from 'react-native';
import Config from 'react-native-config';

const DEFAULT_BACKEND_URL =
  Platform.OS === 'android'
    ? 'http://10.0.2.2:8000'
    : 'http://localhost:8000';

export const BACKEND_URL: string = Config.BACKEND_URL ?? DEFAULT_BACKEND_URL;

export type HealthResult =
  | { kind: 'ok' }
  | {
      kind: 'degraded';
      failedLink: 'database' | 'backend';
      hint: string;
    };

interface HealthResponseBody {
  status?: string;
  database?: string;
  version?: string;
  error?: string;
}

const BACKEND_HINT = 'Is the backend running? Try `just dev`.';
const DATABASE_HINT = 'Postgres is unreachable. Check docker compose.';

export async function fetchHealth(): Promise<HealthResult> {
  let response: Response;
  try {
    response = await fetch(`${BACKEND_URL}/health`);
  } catch {
    return { kind: 'degraded', failedLink: 'backend', hint: BACKEND_HINT };
  }

  let body: HealthResponseBody | null = null;
  try {
    body = (await response.json()) as HealthResponseBody;
  } catch {
    body = null;
  }

  if (response.ok && body?.status === 'ok') {
    return { kind: 'ok' };
  }

  if (response.status === 503 && body?.database === 'down') {
    return { kind: 'degraded', failedLink: 'database', hint: DATABASE_HINT };
  }

  return { kind: 'degraded', failedLink: 'backend', hint: BACKEND_HINT };
}
