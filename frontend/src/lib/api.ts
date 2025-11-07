import { browser } from '$app/environment';

// Default to same-origin to avoid CORS when served behind the backend
const DEFAULT_BASE = '';

const baseUrl = (() => {
  if (browser) {
    return import.meta.env.PUBLIC_API_BASE ?? DEFAULT_BASE;
  }
  return DEFAULT_BASE;
})();

async function request<T>(endpoint: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${baseUrl}${endpoint}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {})
    }
  });

  if (!res.ok) {
    const message = await res.text();
    throw new Error(message || `Request failed with ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export type Provider = {
  id: number;
  name: string;
  base_url: string;
  api_key: string;
  enabled: boolean;
  download_types: string;
  created_at: string;
  updated_at: string;
};

export type Magazine = {
  id: number;
  title: string;
  regex: string | null;
  status: string;
  language: string;
  interval_months: number | null;
  interval_reference_issue: number | null;
  interval_reference_year: number | null;
  interval_reference_month: number | null;
  auto_download_since_year: number | null;
  auto_download_since_issue: number | null;
  created_at: string;
  updated_at: string;
};

export type SearchResult = {
  provider: string;
  title: string;
  link: string;
  published: string | null;
  size: number | null;
  categories: string[];
  magazine_title?: string | null;
  issue_code?: string | null;
  issue_label?: string | null;
  issue_year?: number | null;
  issue_month?: number | null;
  issue_day?: number | null;
  issue_number?: number | null;
  issue_volume?: number | null;
};

export type SabnzbdStatus = {
  enabled: boolean;
  base_url: string | null;
  category: string | null;
};

export type SabnzbdTestResponse = {
  ok: boolean;
  message: string;
};

export type SabnzbdEnqueuePayload = {
  link: string;
  title?: string | null;
  metadata?: {
    magazine_title?: string | null;
    issue_code?: string | null;
    issue_label?: string | null;
    issue_year?: number | null;
    issue_month?: number | null;
    issue_number?: number | null;
  } | null;
};

export type SabnzbdEnqueueResponse = {
  queued: boolean;
  nzo_ids: string[];
  message: string;
};

export type SabnzbdConfig = {
  id: number;
  base_url: string | null;
  api_key: string | null;
  category: string | null;
  priority: number | null;
  timeout: number | null;
  created_at: string;
  updated_at: string;
};

export type SabnzbdConfigPayload = {
  base_url?: string | null;
  api_key?: string | null;
  category?: string | null;
  priority?: number | null;
  timeout?: number | null;
};

export type DownloadQueueEntry = {
  name: string;
  type: 'file' | 'directory';
  size: number;
  modified: string;
  ready: boolean;
};

export type TrackedDownload = {
  id: number;
  sabnzbd_id: string | null;
  title: string | null;
  magazine_title: string | null;
  content_name: string | null;
  status: string;
  sab_status: string | null;
  progress: number | null;
  time_remaining: string | null;
  message: string | null;
  last_seen: string | null;
  clean_name: string | null;
  thumbnail_path: string | null;
  staging_path: string | null;
  issue_code: string | null;
  issue_label: string | null;
  issue_year: number | null;
  issue_month: number | null;
  issue_number: number | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  moved_at: string | null;
};

export type DownloadQueueResponse = {
  enabled: boolean;
  entries: DownloadQueueEntry[];
  jobs: TrackedDownload[];
};

export type DownloadClearResponse = {
  cleared: number;
};

export type AutoDownloadScanResponse = {
  started: boolean;
  enqueued: number;
  message: string;
};

export type AppConfig = {
  id: number;
  auto_download_enabled: boolean;
  auto_download_interval: number;
  auto_download_max_results: number;
  auto_fail_enabled: boolean;
  auto_fail_minutes: number;
  created_at: string;
  updated_at: string;
};

export type AppConfigPayload = {
  auto_download_enabled?: boolean;
  auto_download_interval?: number | null;
  auto_download_max_results?: number | null;
  auto_fail_enabled?: boolean;
  auto_fail_minutes?: number | null;
};

export const api = {
  getProviders: () => request<Provider[]>('/providers'),
  createProvider: (payload: Partial<Provider>) =>
    request<Provider>('/providers', { method: 'POST', body: JSON.stringify(payload) }),
  updateProvider: (id: number, payload: Partial<Provider>) =>
    request<Provider>(`/providers/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteProvider: (id: number) => request<void>(`/providers/${id}`, { method: 'DELETE' }),

  getMagazines: () => request<Magazine[]>('/magazines'),
  createMagazine: (payload: Partial<Magazine>) =>
    request<Magazine>('/magazines', { method: 'POST', body: JSON.stringify(payload) }),
  updateMagazine: (id: number, payload: Partial<Magazine>) =>
    request<Magazine>(`/magazines/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteMagazine: (id: number) => request<void>(`/magazines/${id}`, { method: 'DELETE' }),

  runSearch: (titles?: string[]) =>
    request<SearchResult[]>('/magazines/search', {
      method: 'POST',
      body: JSON.stringify({ titles: titles?.length ? titles : undefined })
    }),

  getSabnzbdStatus: () => request<SabnzbdStatus>('/sabnzbd/status'),
  testSabnzbd: () =>
    request<SabnzbdTestResponse>('/sabnzbd/test', {
      method: 'POST'
    }),
  enqueueSabnzbdDownload: (payload: SabnzbdEnqueuePayload) =>
    request<SabnzbdEnqueueResponse>('/sabnzbd/download', {
      method: 'POST',
      body: JSON.stringify(payload)
    }),
  getSabnzbdConfig: () => request<SabnzbdConfig>('/sabnzbd/config'),
  updateSabnzbdConfig: (payload: SabnzbdConfigPayload) =>
    request<SabnzbdConfig>('/sabnzbd/config', {
      method: 'PATCH',
      body: JSON.stringify(payload)
    }),
  triggerAutoDownloadScan: () =>
    request<AutoDownloadScanResponse>('/auto-download/scan', {
      method: 'POST'
    }),
  getAppConfig: () => request<AppConfig>('/app/config'),
  updateAppConfig: (payload: AppConfigPayload) =>
    request<AppConfig>('/app/config', {
      method: 'PATCH',
      body: JSON.stringify(payload)
    }),
  getDownloadQueue: () => request<DownloadQueueResponse>('/downloads'),
  deleteDownloadJob: (id: number) => request<void>(`/downloads/${id}`, { method: 'DELETE' }),
  clearDownloadJobs: () =>
    request<DownloadClearResponse>('/downloads', {
      method: 'DELETE'
    })
};
