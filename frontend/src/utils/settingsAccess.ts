import { getOrCreateClientId } from '@/api/client';

const SETTINGS_ACCESS_OVERRIDE_KEY = 'banana_settings_access_override';

function parseAllowedClientIds(rawValue: string | undefined): Set<string> {
  if (!rawValue) {
    return new Set();
  }
  return new Set(
    rawValue
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean),
  );
}

const ALLOWED_SETTINGS_CLIENT_IDS = parseAllowedClientIds(
  import.meta.env.VITE_SETTINGS_ALLOWED_CLIENT_IDS,
);

export function canAccessSettings(): boolean {
  if (typeof window === 'undefined') {
    return false;
  }

  const override = localStorage.getItem(SETTINGS_ACCESS_OVERRIDE_KEY);
  if (override === '1' || override === 'true') {
    return true;
  }

  if (ALLOWED_SETTINGS_CLIENT_IDS.size === 0) {
    return false;
  }

  const clientId = getOrCreateClientId();
  return ALLOWED_SETTINGS_CLIENT_IDS.has(clientId);
}
