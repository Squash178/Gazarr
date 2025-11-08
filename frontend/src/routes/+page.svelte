<script lang="ts">
  import { onMount } from 'svelte';
  import {
    api,
    type Provider,
    type ProviderCategory,
    type ProviderCategoryOption,
    type Magazine,
    type SearchResult,
    type SabnzbdStatus,
    type SabnzbdConfig,
    type DownloadQueueEntry,
    type TrackedDownload,
    type AutoDownloadScanResponse,
    type AppConfig,
    type AppConfigPayload
  } from '$lib/api';

  const LANGUAGES = [
    { code: 'en', label: 'English' },
    { code: 'de', label: 'Deutsch' }
  ];

  const TABS = [
    { id: 'overview', label: 'Overview' },
    { id: 'downloads', label: 'Downloads' },
    { id: 'settings', label: 'Settings' }
  ] as const;

  type TabId = (typeof TABS)[number]['id'];
  let activeTab: TabId = 'overview';

  let providers: Provider[] = [];
  let magazines: Magazine[] = [];
  let searchResults: SearchResult[] = [];
  let visibleResults: SearchResult[] = [];
  let activeFilters: string[] = [];
  let sabnzbdStatus: SabnzbdStatus | null = null;
  let sabnzbdConfig: SabnzbdConfig | null = null;
  let sabnzbdEnabled = false;
  let sabnzbdTesting = false;
  let sabnzbdSaving = false;
  let loadingSabnzbd = false;
  let appConfig: AppConfig | null = null;
  let loadingAppConfig = false;
  let savingAppConfig = false;
  let downloadQueue: DownloadQueueEntry[] = [];
  let trackedDownloads: TrackedDownload[] = [];
  let downloadsEnabled = false;
  let loadingDownloads = false;
  let clearingDownloads = false;
  let downloadsTimer: ReturnType<typeof setInterval> | null = null;
  let providerCategories: Record<number, ProviderCategory[]> = {};
  let providerCategoryForms: Record<number, { code: string; name: string }> = {};
  let providerCategoriesOpen: number | null = null;
  let providerCategoriesLoading = new Set<number>();
  let categoryEditor: {
    magazine: Magazine;
    options: ProviderCategoryOption[];
    loading: boolean;
    saving: boolean;
  } | null = null;

  let loadingProviders = false;
  let loadingMagazines = false;
  let isSearching = false;

  let toast: { type: 'success' | 'error'; message: string } | null = null;
  let queueingDownloads = new Set<string>();
  let autoScanPending = false;
  let removingJobs = new Set<number>();
  $: autoScanAvailable = appConfig?.auto_download_enabled ?? appConfigForm.auto_download_enabled;

  $: visibleResults = activeFilters.length ? searchResults.filter(matchesFilters) : [...searchResults];

  const providerForm = {
    name: '',
    base_url: '',
    api_key: '',
    download_types: 'M',
    enabled: true
  };

  const magazineForm = {
    title: '',
    regex: '',
    language: 'en',
    interval_months: '',
    interval_reference_issue: '',
    interval_reference_year: '',
    interval_reference_month: '',
    auto_download_since_year: '',
    auto_download_since_issue: ''
  };

  const sabnzbdForm = {
    base_url: '',
    api_key: '',
    category: '',
    priority: '',
    timeout: ''
  };

  const appConfigForm = {
    auto_download_enabled: false,
    auto_download_interval: '',
    auto_download_max_results: '',
    auto_fail_enabled: false,
    auto_fail_minutes: '',
    debug_logging: false
  };

  const parseOptionalInt = (value: string) => {
    const trimmed = value.trim();
    if (trimmed === '') {
      return null;
    }
    const parsed = Number(trimmed);
    return Number.isNaN(parsed) ? null : parsed;
  };

  const resetProviderForm = () => {
    providerForm.name = '';
    providerForm.base_url = '';
    providerForm.api_key = '';
    providerForm.download_types = 'M';
    providerForm.enabled = true;
  };

  const ensureProviderCategoryForm = (providerId: number) => {
    if (!providerCategoryForms[providerId]) {
      providerCategoryForms[providerId] = { code: '', name: '' };
    }
    return providerCategoryForms[providerId];
  };

  const resetMagazineForm = () => {
    magazineForm.title = '';
    magazineForm.regex = '';
    magazineForm.language = 'en';
    magazineForm.interval_months = '';
    magazineForm.interval_reference_issue = '';
    magazineForm.interval_reference_year = '';
    magazineForm.interval_reference_month = '';
    magazineForm.auto_download_since_year = '';
    magazineForm.auto_download_since_issue = '';
  };

  async function withToast<T>(fn: () => Promise<T>, successMessage: string) {
    try {
      await fn();
      toast = { type: 'success', message: successMessage };
    } catch (err) {
      toast = {
        type: 'error',
        message: err instanceof Error ? err.message : 'Unexpected error'
      };
      throw err;
    } finally {
      setTimeout(() => {
        toast = null;
      }, 4200);
    }
  }

  async function loadProviders() {
    loadingProviders = true;
    try {
      providers = await api.getProviders();
      providers.forEach((provider) => ensureProviderCategoryForm(provider.id));
    } finally {
      loadingProviders = false;
    }
  }

  async function loadMagazines() {
    loadingMagazines = true;
    try {
      magazines = await api.getMagazines();
    } finally {
      loadingMagazines = false;
    }
  }

  async function loadSabnzbdConfig() {
    loadingSabnzbd = true;
    try {
      sabnzbdConfig = await api.getSabnzbdConfig();
      sabnzbdForm.base_url = sabnzbdConfig.base_url ?? '';
      sabnzbdForm.api_key = sabnzbdConfig.api_key ?? '';
      sabnzbdForm.category = sabnzbdConfig.category ?? '';
      sabnzbdForm.priority = sabnzbdConfig.priority === null || sabnzbdConfig.priority === undefined ? '' : String(sabnzbdConfig.priority);
      sabnzbdForm.timeout = sabnzbdConfig.timeout === null || sabnzbdConfig.timeout === undefined ? '' : String(sabnzbdConfig.timeout);
    } catch (err) {
      console.error('Failed to load SABnzbd config', err);
      sabnzbdConfig = null;
    } finally {
      loadingSabnzbd = false;
    }
  }

  async function loadSabnzbdStatus() {
    try {
      sabnzbdStatus = await api.getSabnzbdStatus();
    } catch (err) {
      console.error('Failed to load SABnzbd status', err);
      sabnzbdStatus = { enabled: false, base_url: null, category: null };
    }
  }

  async function loadAppConfig() {
    loadingAppConfig = true;
    try {
      appConfig = await api.getAppConfig();
      appConfigForm.auto_download_enabled = appConfig.auto_download_enabled;
      appConfigForm.auto_download_interval = String(appConfig.auto_download_interval ?? '');
      appConfigForm.auto_download_max_results = String(appConfig.auto_download_max_results ?? '');
      appConfigForm.auto_fail_enabled = appConfig.auto_fail_enabled;
      appConfigForm.auto_fail_minutes = String(appConfig.auto_fail_minutes ?? '');
      appConfigForm.debug_logging = appConfig.debug_logging;
    } catch (err) {
      console.error('Failed to load app config', err);
      appConfig = null;
    } finally {
      loadingAppConfig = false;
    }
  }

  async function loadDownloads() {
    loadingDownloads = true;
    try {
      const response = await api.getDownloadQueue();
      downloadsEnabled = response.enabled;
      downloadQueue = response.entries;
      trackedDownloads = response.jobs ?? [];
    } catch (err) {
      console.error('Failed to load download queue', err);
      downloadsEnabled = false;
      downloadQueue = [];
      trackedDownloads = [];
    } finally {
      loadingDownloads = false;
    }
  }

  async function loadAll() {
    await Promise.all([loadProviders(), loadMagazines(), loadSabnzbdConfig(), loadSabnzbdStatus(), loadAppConfig()]);
  }

  onMount(() => {
    loadAll();
    loadDownloads();
    downloadsTimer = setInterval(() => {
      if (!loadingDownloads) {
        loadDownloads();
      }
    }, 10000);
    return () => {
      if (downloadsTimer) {
        clearInterval(downloadsTimer);
        downloadsTimer = null;
      }
    };
  });

  $: sabnzbdEnabled =
    sabnzbdStatus?.enabled ?? Boolean((sabnzbdConfig?.base_url ?? '').length && (sabnzbdConfig?.api_key ?? '').length);

  function formatDate(value: string | null) {
    if (!value) return '—';
    const date = new Date(value);
    return isNaN(date.getTime()) ? '—' : date.toLocaleString();
  }

  function formatSize(size: number | null) {
    if (!size) return '—';
    const mb = size / 1_048_576;
    return `${mb.toFixed(mb > 100 ? 0 : 1)} MB`;
  }

  function formatJobStatus(status: string | null) {
    if (!status) return '—';
    const text = status.replace(/_/g, ' ').trim();
    return text ? text[0].toUpperCase() + text.slice(1) : '—';
  }

  function formatProgress(value: number | null) {
    if (value === null || value === undefined || Number.isNaN(value)) {
      return '—';
    }
    return `${Math.round(value)}%`;
  }

  function matchesFilters(result: SearchResult) {
    if (!activeFilters.length) {
      return true;
    }
    const title = normalizeString(result.title);
    const label = result.issue_label ? result.issue_label.toLowerCase() : '';
    return activeFilters.some((filter) => {
      const check = normalizeString(filter);
      return title.includes(check) || label.includes(check);
    });
  }

  function normalizeString(value: string) {
    return value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function groupCategoryOptions(options: ProviderCategoryOption[]) {
    const groups = new Map<number, { provider_id: number; provider_name: string; categories: ProviderCategoryOption[] }>();
    for (const option of options) {
      if (!groups.has(option.provider_id)) {
        groups.set(option.provider_id, {
          provider_id: option.provider_id,
          provider_name: option.provider_name,
          categories: []
        });
      }
      groups.get(option.provider_id)?.categories.push(option);
    }
    return Array.from(groups.values());
  }

  function setQueueing(link: string, active: boolean) {
    const next = new Set(queueingDownloads);
    if (active) {
      next.add(link);
    } else {
      next.delete(link);
    }
    queueingDownloads = next;
  }

  function isQueueing(link: string) {
    return queueingDownloads.has(link);
  }

  async function handleCreateProvider() {
    await withToast(
      async () => {
        await api.createProvider({ ...providerForm });
        await loadProviders();
        resetProviderForm();
      },
      'Provider saved'
    );
  }

  async function handleToggleProvider(provider: Provider) {
    await withToast(
      async () => {
        await api.updateProvider(provider.id, { enabled: !provider.enabled });
        await loadProviders();
      },
      `Provider ${provider.enabled ? 'disabled' : 'enabled'}`
    );
  }

  async function handleDeleteProvider(provider: Provider) {
    if (!confirm(`Remove provider "${provider.name}"?`)) return;
    await withToast(
      async () => {
        await api.deleteProvider(provider.id);
        await loadProviders();
      },
      'Provider removed'
    );
  }

  async function toggleProviderCategories(provider: Provider) {
    if (providerCategoriesOpen === provider.id) {
      providerCategoriesOpen = null;
      return;
    }
    providerCategoriesOpen = provider.id;
    ensureProviderCategoryForm(provider.id);
    if (!providerCategories[provider.id]) {
      await loadProviderCategories(provider.id);
    }
  }

  async function loadProviderCategories(providerId: number) {
    const loading = new Set(providerCategoriesLoading);
    loading.add(providerId);
    providerCategoriesLoading = loading;
    try {
      const categories = await api.getProviderCategories(providerId);
      providerCategories = { ...providerCategories, [providerId]: categories };
    } finally {
      const next = new Set(providerCategoriesLoading);
      next.delete(providerId);
      providerCategoriesLoading = next;
    }
  }

  async function handleCreateProviderCategory(provider: Provider) {
    const form = ensureProviderCategoryForm(provider.id);
    if (!form.code.trim() || !form.name.trim()) {
      return;
    }
    await withToast(
      async () => {
        await api.createProviderCategory(provider.id, {
          code: form.code.trim(),
          name: form.name.trim()
        });
        form.code = '';
        form.name = '';
        await loadProviderCategories(provider.id);
      },
      'Category saved'
    );
  }

  async function handleDeleteProviderCategory(provider: Provider, categoryId: number) {
    if (!confirm('Remove this category?')) {
      return;
    }
    await withToast(
      async () => {
        await api.deleteProviderCategory(provider.id, categoryId);
        await loadProviderCategories(provider.id);
      },
      'Category removed'
    );
  }

  async function handleCreateMagazine() {
    await withToast(
      async () => {
        const payload = {
          title: magazineForm.title.trim(),
          regex: magazineForm.regex.trim() ? magazineForm.regex.trim() : null,
          language: magazineForm.language || 'en',
          interval_months: parseOptionalInt(magazineForm.interval_months),
          interval_reference_issue: parseOptionalInt(magazineForm.interval_reference_issue),
          interval_reference_year: parseOptionalInt(magazineForm.interval_reference_year),
          interval_reference_month: parseOptionalInt(magazineForm.interval_reference_month),
          auto_download_since_year: parseOptionalInt(magazineForm.auto_download_since_year),
          auto_download_since_issue: parseOptionalInt(magazineForm.auto_download_since_issue)
        };
        await api.createMagazine(payload);
        await loadMagazines();
        resetMagazineForm();
      },
      'Magazine saved'
    );
  }

  async function handleToggleMagazineStatus(magazine: Magazine) {
    const nextStatus = magazine.status === 'active' ? 'paused' : 'active';
    await withToast(
      async () => {
        await api.updateMagazine(magazine.id, { status: nextStatus });
        await loadMagazines();
      },
      `Magazine ${nextStatus === 'active' ? 'activated' : 'paused'}`
    );
  }

  async function handleDeleteMagazine(magazine: Magazine) {
    if (!confirm(`Delete "${magazine.title}"? This cannot be undone.`)) return;
    await withToast(
      async () => {
        await api.deleteMagazine(magazine.id);
        await loadMagazines();
      },
      'Magazine removed'
    );
  }

  type AutoStartField = 'auto_download_since_year' | 'auto_download_since_issue';

  async function handleLanguageChange(magazine: Magazine, language: string) {
    if (language === magazine.language) {
      return;
    }
    await withToast(
      async () => {
        await api.updateMagazine(magazine.id, { language });
        magazine.language = language;
        await loadMagazines();
      },
      'Magazine language updated'
    );
  }

  async function openCategoryEditor(magazine: Magazine) {
    categoryEditor = {
      magazine,
      options: [],
      loading: true,
      saving: false
    };
    try {
      const options = await api.getMagazineCategories(magazine.id);
      categoryEditor = {
        magazine,
        options,
        loading: false,
        saving: false
      };
    } catch (err) {
      categoryEditor = null;
      toast = {
        type: 'error',
        message: err instanceof Error ? err.message : 'Failed to load categories'
      };
      setTimeout(() => (toast = null), 4200);
    }
  }

  function toggleMagazineCategory(categoryId: number) {
    if (!categoryEditor || categoryEditor.loading) {
      return;
    }
    categoryEditor = {
      ...categoryEditor,
      options: categoryEditor.options.map((option) =>
        option.id === categoryId ? { ...option, selected: !option.selected } : option
      )
    };
  }

  async function handleSaveMagazineCategories() {
    if (!categoryEditor || categoryEditor.loading || categoryEditor.saving) {
      return;
    }
    categoryEditor = { ...categoryEditor, saving: true };
    try {
      const selectedIds = categoryEditor.options.filter((option) => option.selected).map((option) => option.id);
      const updated = await api.updateMagazineCategories(categoryEditor.magazine.id, {
        provider_category_ids: selectedIds
      });
      categoryEditor = {
        ...categoryEditor,
        options: updated,
        saving: false
      };
      toast = { type: 'success', message: 'Categories updated' };
      setTimeout(() => (toast = null), 4200);
    } catch (err) {
      categoryEditor = categoryEditor ? { ...categoryEditor, saving: false } : null;
      toast = {
        type: 'error',
        message: err instanceof Error ? err.message : 'Failed to save categories'
      };
      setTimeout(() => (toast = null), 4200);
    }
  }

  function closeCategoryEditor() {
    categoryEditor = null;
  }

  async function handleAutoStartChange(magazine: Magazine, field: AutoStartField, value: string) {
    const parsed = parseOptionalInt(value);
    await withToast(
      async () => {
        await api.updateMagazine(magazine.id, { [field]: parsed });
        magazine[field] = parsed as Magazine[AutoStartField];
      },
      'Auto-download guard updated'
    );
  }

  async function handleSearch(titles?: string[]) {
    activeFilters = titles ?? [];
    isSearching = true;
    try {
      searchResults = await api.runSearch(titles);
      toast = { type: 'success', message: `Search returned ${searchResults.length} results` };
      setTimeout(() => (toast = null), 4200);
    } catch (err) {
      toast = { type: 'error', message: err instanceof Error ? err.message : 'Search failed' };
    } finally {
      isSearching = false;
    }
  }

  async function handleRefreshDownloads() {
    if (loadingDownloads) {
      return;
    }
    await loadDownloads();
  }

  async function handleClearDownloads() {
    if (clearingDownloads) {
      return;
    }
    clearingDownloads = true;
    try {
      await withToast(
        async () => {
          await api.clearDownloadJobs();
          downloadQueue = [];
          trackedDownloads = [];
        },
        'Cleared tracked downloads'
      );
      await loadDownloads();
    } catch (err) {
      console.error('Failed to clear downloads', err);
    } finally {
      clearingDownloads = false;
    }
  }

  async function handleDeleteDownloadJob(job: TrackedDownload) {
    if (!job?.id || removingJobs.has(job.id)) {
      return;
    }
    if (!confirm(`Remove "${job.clean_name ?? job.title ?? job.content_name ?? 'this download'}" from the log?`)) {
      return;
    }
    const next = new Set(removingJobs);
    next.add(job.id);
    removingJobs = next;
    try {
      await api.deleteDownloadJob(job.id);
      trackedDownloads = trackedDownloads.filter((item) => item.id !== job.id);
    } catch (err) {
      toast = {
        type: 'error',
        message: err instanceof Error ? err.message : 'Failed to remove download entry'
      };
      setTimeout(() => (toast = null), 4200);
    } finally {
      const updated = new Set(removingJobs);
      updated.delete(job.id);
      removingJobs = updated;
    }
  }

  async function handleDownload(result: SearchResult) {
    if (!sabnzbdEnabled) {
      toast = {
        type: 'error',
        message: 'Configure SABnzbd to enable downloads.'
      };
      setTimeout(() => {
        toast = null;
      }, 4200);
      return;
    }

    const link = result.link;
    setQueueing(link, true);
    const title = result.title?.trim();
    const metadata = {
      magazine_title: result.magazine_title ?? title ?? null,
      issue_code: result.issue_code ?? null,
      issue_label: result.issue_label ?? null,
      issue_year: result.issue_year ?? null,
      issue_month: result.issue_month ?? null,
      issue_number: result.issue_number ?? null
    };
    const hasMetadata = Object.values(metadata).some((value) => value !== null && value !== undefined);

    const payload = {
      link,
      title: title ? title : undefined,
      metadata: hasMetadata ? metadata : undefined
    };

    try {
      const response = await api.enqueueSabnzbdDownload(payload);
      toast = {
        type: 'success',
        message: response.message ?? 'Request queued in SABnzbd'
      };
    } catch (err) {
      toast = {
        type: 'error',
        message: err instanceof Error ? err.message : 'Failed to send download to SABnzbd'
      };
    } finally {
      setQueueing(link, false);
      setTimeout(() => {
        toast = null;
      }, 4200);
    }
  }

  async function handleSaveSabnzbd() {
    sabnzbdSaving = true;
    const priorityInput = sabnzbdForm.priority.trim();
    const timeoutInput = sabnzbdForm.timeout.trim();
    const priorityValue = priorityInput === '' ? null : Number(priorityInput);
    const timeoutValue = timeoutInput === '' ? null : Number(timeoutInput);

    const payload = {
      base_url: sabnzbdForm.base_url.trim() ? sabnzbdForm.base_url.trim() : null,
      api_key: sabnzbdForm.api_key.trim() ? sabnzbdForm.api_key.trim() : null,
      category: sabnzbdForm.category.trim() ? sabnzbdForm.category.trim() : null,
      priority: priorityValue !== null && Number.isNaN(priorityValue) ? null : priorityValue,
      timeout: timeoutValue !== null && Number.isNaN(timeoutValue) ? null : timeoutValue
    };

    try {
      await withToast(
        async () => {
          await api.updateSabnzbdConfig(payload);
          await Promise.all([loadSabnzbdConfig(), loadSabnzbdStatus()]);
        },
        'SABnzbd settings saved'
      );
    } finally {
      sabnzbdSaving = false;
    }
  }

  async function handleTestSabnzbd() {
    sabnzbdTesting = true;
    try {
      await withToast(
        async () => {
          await api.testSabnzbd();
          await loadSabnzbdStatus();
        },
        'SABnzbd connection looks good'
      );
    } catch (err) {
      // toast helper already surfaced the error message
    } finally {
      sabnzbdTesting = false;
    }
  }

  async function handleAutoDownloadScan() {
    if (autoScanPending) {
      return;
    }
    autoScanPending = true;
    try {
      const response: AutoDownloadScanResponse = await api.triggerAutoDownloadScan();
      toast = {
        type: 'success',
        message: response.message
      };
    } catch (err) {
      toast = {
        type: 'error',
        message: err instanceof Error ? err.message : 'Auto download scan failed'
      };
    } finally {
      autoScanPending = false;
      setTimeout(() => {
        toast = null;
      }, 4200);
    }
  }

  async function handleSaveAppConfig() {
    savingAppConfig = true;
    try {
      const intervalInput = String(appConfigForm.auto_download_interval ?? '').trim();
      const maxInput = String(appConfigForm.auto_download_max_results ?? '').trim();
      const failMinutesInput = String(appConfigForm.auto_fail_minutes ?? '').trim();
      const intervalValue = intervalInput === '' ? undefined : Number(intervalInput);
      const maxValue = maxInput === '' ? undefined : Number(maxInput);
      const failMinutesValue = failMinutesInput === '' ? undefined : Number(failMinutesInput);
      const payload: AppConfigPayload = {
        auto_download_enabled: appConfigForm.auto_download_enabled,
        auto_download_interval: intervalValue !== undefined && Number.isFinite(intervalValue) ? intervalValue : undefined,
        auto_download_max_results: maxValue !== undefined && Number.isFinite(maxValue) ? maxValue : undefined,
        auto_fail_enabled: appConfigForm.auto_fail_enabled,
        auto_fail_minutes: failMinutesValue !== undefined && Number.isFinite(failMinutesValue) ? failMinutesValue : undefined,
        debug_logging: appConfigForm.debug_logging
      };
      await withToast(
        async () => {
          const updated = await api.updateAppConfig(payload);
          appConfig = updated;
          appConfigForm.auto_download_enabled = updated.auto_download_enabled;
          appConfigForm.auto_download_interval = String(updated.auto_download_interval ?? '');
          appConfigForm.auto_download_max_results = String(updated.auto_download_max_results ?? '');
          appConfigForm.auto_fail_enabled = updated.auto_fail_enabled;
          appConfigForm.auto_fail_minutes = String(updated.auto_fail_minutes ?? '');
          appConfigForm.debug_logging = updated.debug_logging;
        },
        'Auto-download settings saved'
      );
    } finally {
      savingAppConfig = false;
    }
  }
</script>

<main style="max-width: 1180px; margin: 0 auto; padding: 2.5rem 1.5rem 4rem;">
  <header class="surface" style="margin-bottom: 2rem;">
    <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 1.5rem;">
      <div>
        <div class="badge">alpha preview</div>
        <h1 style="margin-top: 0.35rem; font-size: clamp(2.4rem, 4vw, 3.2rem); font-weight: 700;">
          Gazarr Dashboard
        </h1>
        <p style="max-width: 640px; color: rgba(226, 232, 240, 0.72); line-height: 1.55;">
          Manage your Torznab providers, curate active magazines, and trigger quick searches — all in one glassy
          interface built just for magazines.
        </p>
      </div>
      <div style="display: flex; flex-direction: column; gap: 0.6rem; min-width: 180px; align-items: flex-end;">
        <button type="button" class="btn-primary" on:click={() => handleSearch()} disabled={isSearching}>
          {#if isSearching}
            <span
              style="width: 0.85rem; height: 0.85rem; border: 2px solid rgba(255,255,255,0.3); border-top-color: #fff; border-radius: 50%; display: inline-block; animation: spin 0.9s linear infinite;"
            ></span>
            Scanning...
          {:else}
            🔍 Run global search
          {/if}
        </button>
        <button
          type="button"
          class="btn-primary"
          on:click={handleAutoDownloadScan}
          disabled={autoScanPending || !autoScanAvailable}
          title={autoScanAvailable ? 'Run the auto downloader immediately' : 'Enable auto downloader in settings'}
        >
          {#if autoScanPending}
            <span
              style="width: 0.85rem; height: 0.85rem; border: 2px solid rgba(255,255,255,0.3); border-top-color: #fff; border-radius: 50%; display: inline-block; animation: spin 0.9s linear infinite;"
            ></span>
            Syncing auto downloads...
          {:else}
            ⚡ Scan auto downloads
          {/if}
        </button>
        <small style="color: rgba(226, 232, 240, 0.52); letter-spacing: 0.08em; text-transform: uppercase;">
          API base: {import.meta.env.PUBLIC_API_BASE ?? 'http://localhost:8000'}
        </small>
        <small style="color: rgba(226, 232, 240, 0.52); letter-spacing: 0.08em; text-transform: uppercase;">
          SABnzbd: {sabnzbdEnabled ? 'Connected' : 'Not configured'}
        </small>
      </div>
    </div>
  </header>

  {#if toast}
    <div
      class="card"
      style="
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 1.5rem;
        border-color: rgba(74, 222, 128, 0.35);
        background: rgba(34, 197, 94, 0.15);
        color: rgba(240, 253, 244, 0.92);
      "
    >
      <strong>{toast.type === 'success' ? 'Success' : 'Heads up'}</strong>
      <span>{toast.message}</span>
    </div>
  {/if}

  <nav class="tab-nav">
    {#each TABS as tab}
      <button
        type="button"
        class="tab-button {tab.id === activeTab ? 'is-active' : ''}"
        on:click={() => (activeTab = tab.id)}
      >
        {tab.label}
      </button>
    {/each}
  </nav>

  {#if activeTab === 'overview'}
    <section class="surface" style="margin-bottom: 2rem;">
      <div class="section-header">
        <div>
          <h2 style="margin: 0; font-size: 1.25rem;">Magazines</h2>
          <p style="margin: 0.4rem 0 0; color: rgba(226, 232, 240, 0.6);">
            {magazines.filter((m) => m.status === 'active').length} active · {magazines.length} total
          </p>
        </div>
        <button type="button" class="btn-primary" on:click={handleCreateMagazine} disabled={loadingMagazines}>
          + Add magazine
        </button>
      </div>

      <div style="display: grid; gap: 1rem; margin-bottom: 1.5rem;">
        <div class="input-field">
          <label for="magazine-title">Title</label>
          <input id="magazine-title" bind:value={magazineForm.title} placeholder="eg. Linux Format" />
        </div>
        <div class="input-field">
          <label for="magazine-regex">Custom search term (optional)</label>
          <input id="magazine-regex" bind:value={magazineForm.regex} placeholder="Override search keyword" />
        </div>
        <div class="input-field">
          <label for="magazine-language">Language</label>
          <select id="magazine-language" bind:value={magazineForm.language}>
            {#each LANGUAGES as option}
              <option value={option.code}>{option.label}</option>
            {/each}
          </select>
        </div>
        <div class="input-field">
          <label for="magazine-interval">Publication interval (months, optional)</label>
          <input
            id="magazine-interval"
            bind:value={magazineForm.interval_months}
            placeholder="e.g. 1"
            inputmode="numeric"
          />
          <small>Used when releases only include an issue number.</small>
        </div>
        <div class="input-field">
          <label for="magazine-ref-issue">Reference issue number</label>
          <input
            id="magazine-ref-issue"
            bind:value={magazineForm.interval_reference_issue}
            placeholder="e.g. 250"
            inputmode="numeric"
          />
        </div>
        <div class="input-field">
          <label for="magazine-ref-month">Reference month (1-12)</label>
          <input
            id="magazine-ref-month"
            bind:value={magazineForm.interval_reference_month}
            placeholder="e.g. 4"
            inputmode="numeric"
          />
        </div>
        <div class="input-field">
          <label for="magazine-ref-year">Reference year</label>
          <input
            id="magazine-ref-year"
            bind:value={magazineForm.interval_reference_year}
            placeholder="e.g. 2024"
            inputmode="numeric"
          />
        </div>
        <div class="input-field">
          <label for="magazine-auto-year">Auto-download start year</label>
          <input
            id="magazine-auto-year"
            bind:value={magazineForm.auto_download_since_year}
            placeholder="e.g. 2023"
            inputmode="numeric"
          />
          <small>Only issues newer than this year will be auto-downloaded.</small>
        </div>
        <div class="input-field">
          <label for="magazine-auto-issue">Auto-download start issue number</label>
          <input
            id="magazine-auto-issue"
            bind:value={magazineForm.auto_download_since_issue}
            placeholder="e.g. 350"
            inputmode="numeric"
          />
          <small>When the issue year matches, Gazarr only grabs numbers above this.</small>
        </div>
      </div>

      <div style="overflow-x: auto;">
        <table class="table">
          <thead>
            <tr>
              <th>Title</th>
              <th>Search</th>
              <th>Language</th>
              <th>Auto download start</th>
              <th>Status</th>
              <th style="text-align: right;">Actions</th>
            </tr>
          </thead>
          <tbody>
            {#if loadingMagazines}
              <tr>
                <td colspan="6">Loading magazines…</td>
              </tr>
            {:else if magazines.length === 0}
              <tr>
                <td colspan="6">Add a magazine title to kick off your library.</td>
              </tr>
            {:else}
              {#each magazines as magazine}
                <tr>
                  <td style="font-weight: 600; overflow-wrap: anywhere; word-break: break-word;">{magazine.title}</td>
                  <td style="font-size: 0.85rem; color: rgba(226, 232, 240, 0.7); overflow-wrap: anywhere; word-break: break-word;">
                    {magazine.regex ?? '—'}
                  </td>
                  <td>
                    <select
                      bind:value={magazine.language}
                      on:change={(event) =>
                        handleLanguageChange(magazine, (event.target as HTMLSelectElement).value)
                      }
                    >
                      {#each LANGUAGES as option}
                        <option value={option.code}>{option.label}</option>
                      {/each}
                    </select>
                  </td>
                  <td>
                    <div class="auto-guard">
                      <div class="auto-guard-inputs">
                        <input
                          type="number"
                          min="1900"
                          max="2200"
                          placeholder="Year"
                          value={magazine.auto_download_since_year ?? ''}
                          on:change={(event) =>
                            handleAutoStartChange(
                              magazine,
                              'auto_download_since_year',
                              (event.target as HTMLInputElement).value
                            )
                          }
                        />
                        <input
                          type="number"
                          min="1"
                          placeholder="Issue #"
                          value={magazine.auto_download_since_issue ?? ''}
                          on:change={(event) =>
                            handleAutoStartChange(
                              magazine,
                              'auto_download_since_issue',
                              (event.target as HTMLInputElement).value
                            )
                          }
                        />
                      </div>
                      <small>Auto downloads only if newer.</small>
                    </div>
                  </td>
                  <td>
                    <span class="chip {magazine.status === 'active' ? 'success' : 'warning'}">
                      {magazine.status}
                    </span>
                  </td>
                  <td style="text-align: right;">
                    <button
                      type="button"
                      class="btn-primary"
                      style="padding: 0.45rem 1.1rem;"
                      on:click={() => handleToggleMagazineStatus(magazine)}
                    >
                      {magazine.status === 'active' ? 'Pause' : 'Activate'}
                    </button>
                    <button
                      type="button"
                      class="btn-primary"
                      style="margin-left: 0.5rem; padding: 0.45rem 1.1rem;"
                      on:click={() => handleSearch([magazine.title])}
                    >
                      Search
                    </button>
                    <button
                      type="button"
                      class="btn-primary"
                      style="margin-left: 0.5rem; padding: 0.45rem 1.1rem;"
                      on:click={() => openCategoryEditor(magazine)}
                    >
                      Categories
                    </button>
                    <button
                      type="button"
                      class="btn-primary"
                      style="margin-left: 0.5rem; padding: 0.45rem 1.1rem; background: rgba(248, 113, 113, 0.22); color: rgba(255, 255, 255, 0.85);"
                      on:click={() => handleDeleteMagazine(magazine)}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              {/each}
            {/if}
          </tbody>
        </table>
      </div>
    </section>

    {#if categoryEditor}
      <section class="surface" style="margin-bottom: 2rem;">
        <div class="section-header">
          <div>
            <h2 style="margin: 0; font-size: 1.25rem;">Categories for {categoryEditor.magazine.title}</h2>
            <p style="margin: 0.4rem 0 0; color: rgba(226, 232, 240, 0.6);">
              Choose which provider categories Gazarr should search for this magazine.
            </p>
          </div>
          <div style="display: flex; gap: 0.5rem;">
            <button
              type="button"
              class="btn-primary"
              on:click={handleSaveMagazineCategories}
              disabled={categoryEditor.loading || categoryEditor.saving}
            >
              {categoryEditor.saving ? 'Saving…' : 'Save' }
            </button>
            <button type="button" class="btn-primary" on:click={closeCategoryEditor}>
              Close
            </button>
          </div>
        </div>

        {#if categoryEditor.loading}
          <p>Loading categories…</p>
        {:else}
          {#if categoryEditor.options.length}
            {#each groupCategoryOptions(categoryEditor.options) as group}
              <div class="category-group">
                <strong>{group.provider_name}</strong>
                <div class="category-options">
                  {#each group.categories as option}
                    <label class="category-option">
                      <input
                        type="checkbox"
                        checked={option.selected}
                        on:change={() => toggleMagazineCategory(option.id)}
                      />
                      <span>{option.code} – {option.name}</span>
                    </label>
                  {/each}
                </div>
              </div>
            {/each}
          {:else}
            <p style="margin: 0; color: rgba(226, 232, 240, 0.7);">
              No provider categories defined yet. Use the Providers section to add categories first.
            </p>
          {/if}
        {/if}
      </section>
    {/if}

    <section class="surface" style="margin-bottom: 2rem;">
      <div class="section-header">
        <div>
          <h2 style="margin: 0; font-size: 1.25rem;">Auto downloader</h2>
          <p style="margin: 0.4rem 0 0; color: rgba(226, 232, 240, 0.6);">
            Toggle the background search loop and tune how often it runs.
          </p>
        </div>
        <button
          type="button"
          class="btn-primary"
          on:click={handleSaveAppConfig}
          disabled={savingAppConfig || loadingAppConfig}
        >
          {savingAppConfig ? 'Saving…' : 'Save auto-download'}
        </button>
      </div>

      {#if loadingAppConfig}
        <p style="margin: 0; color: rgba(226, 232, 240, 0.7);">Loading auto-download settings…</p>
      {:else}
        <div class="input-field">
          <label style="display: flex; align-items: center; gap: 0.55rem;">
            <input type="checkbox" bind:checked={appConfigForm.auto_download_enabled} />
            Auto downloader enabled
          </label>
        </div>
        <div class="grid" style="gap: 1rem; margin-top: 1rem;">
          <div class="input-field">
            <label for="auto-interval">Scan interval (hours)</label>
            <input
              id="auto-interval"
              type="number"
              min="0.25"
              max="24"
              step="0.25"
              bind:value={appConfigForm.auto_download_interval}
              placeholder="12"
              disabled={!appConfigForm.auto_download_enabled}
            />
            <small>How long to wait between automatic scans.</small>
          </div>
          <div class="input-field">
            <label for="auto-max">Max results per scan</label>
            <input
              id="auto-max"
              type="number"
              min="1"
              max="5"
              bind:value={appConfigForm.auto_download_max_results}
              placeholder="1"
              disabled={!appConfigForm.auto_download_enabled}
            />
            <small>Stops the auto downloader after enqueuing this many issues.</small>
          </div>
        </div>
        <hr style="border: 0; border-top: 1px solid rgba(148, 163, 184, 0.2); margin: 1.5rem 0;" />
        <div class="input-field">
          <label style="display: flex; align-items: center; gap: 0.55rem;">
            <input type="checkbox" bind:checked={appConfigForm.auto_fail_enabled} />
            Auto fail stuck downloads
          </label>
        </div>
        <div class="grid" style="gap: 1rem; margin-top: 1rem;">
          <div class="input-field">
            <label for="auto-fail-minutes">Fail after (minutes)</label>
            <input
              id="auto-fail-minutes"
              type="number"
              min="1"
              max="10080"
              step="1"
              bind:value={appConfigForm.auto_fail_minutes}
              placeholder="720"
              disabled={!appConfigForm.auto_fail_enabled}
            />
            <small>Jobs older than this (without progress) are marked failed automatically.</small>
          </div>
        </div>
        <hr style="border: 0; border-top: 1px solid rgba(148, 163, 184, 0.2); margin: 1.5rem 0;" />
        <div class="input-field">
          <label style="display: flex; align-items: center; gap: 0.55rem;">
            <input type="checkbox" bind:checked={appConfigForm.debug_logging} />
            Verbose SAB debug logging
          </label>
          <small>Writes full queue/history snapshots and job updates into the backend logs for troubleshooting.</small>
        </div>
      {/if}
    </section>

    <section class="surface">
      <div class="section-header">
        <div>
          <h2 style="margin: 0; font-size: 1.25rem;">Latest search results</h2>
          <p style="margin: 0.4rem 0 0; color: rgba(226, 232, 240, 0.6);">
            {#if visibleResults.length}
              Displaying {visibleResults.length} release{visibleResults.length === 1 ? '' : 's'}
              {#if activeFilters.length}
                filtered to <strong>{activeFilters.join(', ')}</strong>
                {#if visibleResults.length !== searchResults.length}
                  (from {searchResults.length} total)
                {/if}
              {/if}
            {:else if searchResults.length}
              No releases matched the current filter.
            {:else}
              Run a search to populate this table.
            {/if}
          </p>
        </div>
        <button type="button" class="btn-primary" on:click={() => handleSearch()} disabled={isSearching}>
          {#if activeFilters.length}
            {isSearching ? 'Refreshing…' : 'Clear filter'}
          {:else}
            {isSearching ? 'Searching…' : 'Refresh results'}
          {/if}
        </button>
      </div>

      <div style="overflow-x: auto;">
        <table class="table">
          <thead>
            <tr>
              <th>Release</th>
              <th>Issue</th>
              <th>Provider</th>
              <th>Size</th>
              <th>Published</th>
              <th>Categories</th>
              <th style="text-align: right;">Download</th>
            </tr>
          </thead>
          <tbody>
            {#if isSearching}
              <tr>
                <td colspan="7">Searching providers…</td>
              </tr>
            {:else if searchResults.length === 0}
              <tr>
                <td colspan="7">No results yet.</td>
              </tr>
            {:else if visibleResults.length === 0}
              <tr>
                <td colspan="7">No results matched the current filter.</td>
              </tr>
            {:else}
              {#each visibleResults as result}
                <tr>
                  <td style="font-weight: 600; max-width: 320px; overflow-wrap: anywhere; word-break: break-word;">
                    {result.title}
                  </td>
                  <td style="white-space: nowrap; color: rgba(226, 232, 240, 0.8);">
                    {result.issue_label ?? '—'}
                  </td>
                  <td>{result.provider}</td>
                  <td>{formatSize(result.size)}</td>
                  <td>{formatDate(result.published)}</td>
                  <td>
                    <div class="pill-group">
                      {#each result.categories as cat}
                        <span class="chip">{cat}</span>
                      {/each}
                    </div>
                  </td>
                  <td style="text-align: right;">
                    <button
                      type="button"
                      class="btn-primary"
                      style="padding: 0.45rem 1.1rem; display: inline-flex; align-items: center; gap: 0.35rem;"
                      on:click={() => handleDownload(result)}
                      disabled={isQueueing(result.link)}
                      title={sabnzbdEnabled ? 'Send to SABnzbd' : 'Configure SABnzbd to enable downloads'}
                    >
                      {#if isQueueing(result.link)}
                        <span
                          style="width: 0.75rem; height: 0.75rem; border: 2px solid rgba(255,255,255,0.25); border-top-color: #fff; border-radius: 50%; display: inline-block; animation: spin 0.9s linear infinite;"
                        ></span>
                        Sending…
                      {:else}
                        Download
                      {/if}
                    </button>
                  </td>
                </tr>
              {/each}
            {/if}
          </tbody>
        </table>
      </div>
    </section>

  {:else if activeTab === 'downloads'}

    <section class="surface" style="margin-bottom: 2rem;">
      <div class="section-header">
        <div>
          <h2 style="margin: 0; font-size: 1.25rem;">Download queue</h2>
          <p style="margin: 0.4rem 0 0; color: rgba(226, 232, 240, 0.6);">
            {#if downloadsEnabled}
              {#if downloadQueue.length}
                Watching {downloadQueue.length} item{downloadQueue.length === 1 ? '' : 's'} for completion.
              {:else}
                Monitoring the downloads folder for new items.
              {/if}
            {:else}
              Configure download and library directories to enable monitoring.
            {/if}
          </p>
        </div>
        <div style="display: flex; gap: 0.6rem; flex-wrap: wrap; justify-content: flex-end;">
          <button type="button" class="btn-primary" on:click={handleRefreshDownloads} disabled={loadingDownloads}>
            {loadingDownloads ? 'Refreshing…' : 'Refresh'}
          </button>
          <button
            type="button"
            class="btn-primary"
            style="background: rgba(248, 113, 113, 0.28); color: rgba(255, 255, 255, 0.9);"
            on:click={handleClearDownloads}
            disabled={
              clearingDownloads || !downloadsEnabled || (!trackedDownloads.length && !downloadQueue.length)
            }
          >
            {clearingDownloads ? 'Clearing…' : 'Clear downloads'}
          </button>
        </div>
      </div>

      {#if !downloadsEnabled}
        <p style="margin: 0; color: rgba(226, 232, 240, 0.7);">
          Set <code>GAZARR_DOWNLOADS_DIR</code> and <code>GAZARR_LIBRARY_DIR</code> (or update via settings) to enable the queue.
        </p>
      {:else}
        {#if trackedDownloads.length}
          <h3 style="margin: 1.5rem 0 0.75rem; font-size: 1rem; color: rgba(226, 232, 240, 0.8);">
            Tracked downloads
          </h3>
          <div style="overflow-x: auto; margin-bottom: 1.25rem;">
            <table class="table">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Stage</th>
                  <th>SAB status</th>
                  <th>Progress</th>
                  <th>Remaining</th>
                  <th>Updated</th>
                  <th>Message</th>
                  <th style="text-align: right;">Actions</th>
                </tr>
              </thead>
              <tbody>
                {#each trackedDownloads as job}
                  <tr>
                    <td style="font-weight: 600; max-width: 320px; overflow-wrap: anywhere; word-break: break-word;">
                      {job.clean_name ?? job.title ?? job.content_name ?? job.sabnzbd_id ?? '—'}
                    </td>
                    <td>{formatJobStatus(job.status)}</td>
                    <td>{job.sab_status ?? '—'}</td>
                    <td>{formatProgress(job.progress)}</td>
                    <td>{job.time_remaining ?? '—'}</td>
                    <td>{formatDate(job.updated_at)}</td>
                    <td style="max-width: 280px; color: rgba(226, 232, 240, 0.78);">
                      {job.message ?? '—'}
                    </td>
                    <td style="text-align: right;">
                      <button
                        type="button"
                        class="btn-primary"
                        style="padding: 0.3rem 0.9rem; background: rgba(248, 113, 113, 0.25); color: #fee2e2;"
                        on:click={() => handleDeleteDownloadJob(job)}
                        disabled={removingJobs.has(job.id)}
                      >
                        {removingJobs.has(job.id) ? 'Removing…' : 'Remove'}
                      </button>
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}

        {#if loadingDownloads && downloadQueue.length === 0 && trackedDownloads.length === 0}
          <p style="margin: 0; color: rgba(226, 232, 240, 0.7);">Loading current downloads…</p>
        {:else if downloadQueue.length === 0}
          <p style="margin: 0; color: rgba(226, 232, 240, 0.7);">
            {trackedDownloads.length ? 'Downloads are still processing in SABnzbd.' : 'No active downloads detected.'}
          </p>
        {:else}
          <div style="overflow-x: auto;">
            <table class="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Type</th>
                  <th>Size</th>
                  <th>Last modified</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {#each downloadQueue as entry}
                  <tr>
                    <td style="font-weight: 600; max-width: 320px; overflow-wrap: anywhere; word-break: break-word;">
                      {entry.name}
                    </td>
                    <td style="text-transform: capitalize;">{entry.type === 'directory' ? 'Folder' : 'File'}</td>
                    <td>{formatSize(entry.size)}</td>
                    <td>{formatDate(entry.modified)}</td>
                    <td>
                      <span class="chip {entry.ready ? 'success' : 'warning'}">
                        {entry.ready ? 'Ready to move' : 'Waiting for files'}
                      </span>
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      {/if}
    </section>

  {:else}

    <section class="surface" style="margin-bottom: 2rem;">
      <div class="section-header">
        <div>
          <h2 style="margin: 0; font-size: 1.25rem;">SABnzbd connection</h2>
          <p style="margin: 0.4rem 0 0; color: rgba(226, 232, 240, 0.6);">
            {sabnzbdEnabled
              ? `Connected to ${sabnzbdStatus?.base_url ?? sabnzbdConfig?.base_url ?? '—'}`
              : 'Provide your SABnzbd base URL and API key to enable one-click downloads.'}
          </p>
        </div>
        <div style="display: flex; gap: 0.6rem;">
          <button
            type="button"
            class="btn-primary"
            on:click={handleTestSabnzbd}
            disabled={sabnzbdTesting || loadingSabnzbd}
          >
            {sabnzbdTesting ? 'Testing…' : 'Test connection'}
          </button>
          <button
            type="button"
            class="btn-primary"
            on:click={handleSaveSabnzbd}
            disabled={sabnzbdSaving || loadingSabnzbd}
          >
            {sabnzbdSaving ? 'Saving…' : 'Save settings'}
          </button>
        </div>
      </div>

      {#if loadingSabnzbd}
        <p style="margin: 0; color: rgba(226, 232, 240, 0.7);">Loading SABnzbd settings…</p>
      {:else}
        <div class="grid" style="gap: 1rem; margin-top: 1rem;">
          <div class="input-field">
            <label for="sab-base">Base URL</label>
            <input
              id="sab-base"
              bind:value={sabnzbdForm.base_url}
              placeholder="http://localhost:8080/sabnzbd"
              disabled={sabnzbdSaving || loadingSabnzbd}
            />
          </div>
          <div class="input-field">
            <label for="sab-key">API key</label>
            <input
              id="sab-key"
              bind:value={sabnzbdForm.api_key}
              placeholder="Paste SABnzbd API key"
              disabled={sabnzbdSaving || loadingSabnzbd}
            />
          </div>
          <div class="input-field">
            <label for="sab-category">Category (optional)</label>
            <input
              id="sab-category"
              bind:value={sabnzbdForm.category}
              placeholder="Magazines"
              disabled={sabnzbdSaving || loadingSabnzbd}
            />
          </div>
          <div class="input-field">
            <label for="sab-priority">Priority (optional)</label>
            <input
              id="sab-priority"
              type="number"
              bind:value={sabnzbdForm.priority}
              min="-1"
              max="2"
              disabled={sabnzbdSaving || loadingSabnzbd}
            />
          </div>
          <div class="input-field">
            <label for="sab-timeout">Timeout (seconds)</label>
            <input
              id="sab-timeout"
              type="number"
              bind:value={sabnzbdForm.timeout}
              min="1"
              max="180"
              disabled={sabnzbdSaving || loadingSabnzbd}
            />
          </div>
        </div>
        <small style="display: block; margin-top: 0.75rem; color: rgba(226, 232, 240, 0.52);">
          Leave fields blank to clear them. Environment defaults (if provided) seed the initial values.
        </small>
      {/if}
    </section>

    <section class="surface" style="margin-bottom: 2rem;">
      <div class="section-header">
        <div>
          <h2 style="margin: 0; font-size: 1.25rem;">Providers</h2>
          <p style="margin: 0.4rem 0 0; color: rgba(226, 232, 240, 0.6);">
            Currently tracking {providers.length} provider{providers.length === 1 ? '' : 's'}.
          </p>
        </div>
        <button type="button" class="btn-primary" on:click={handleCreateProvider} disabled={loadingProviders}>
          + Add provider
        </button>
      </div>

      <div style="display: grid; gap: 1rem; margin-bottom: 1.5rem;">
        <div class="grid" style="gap: 1rem;">
          <div class="input-field">
            <label for="provider-name">Name</label>
            <input id="provider-name" bind:value={providerForm.name} placeholder="eg. Prowlarr" />
          </div>
          <div class="input-field">
            <label for="provider-url">Base URL</label>
            <input id="provider-url" bind:value={providerForm.base_url} placeholder="https://prow.example/api" />
          </div>
          <div class="input-field">
            <label for="provider-key">API key</label>
            <input id="provider-key" bind:value={providerForm.api_key} placeholder="Paste API key" />
          </div>
          <div class="input-field">
            <label for="provider-types">Download types</label>
            <input id="provider-types" bind:value={providerForm.download_types} placeholder="M,E" />
          </div>
        </div>
        <label style="display: flex; align-items: center; gap: 0.6rem; cursor: pointer;">
          <input type="checkbox" bind:checked={providerForm.enabled} />
          Enabled by default
        </label>
      </div>

      <div style="overflow-x: auto;">
        <table class="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Base URL</th>
              <th>Status</th>
              <th>Download types</th>
              <th style="text-align: right;">Actions</th>
            </tr>
          </thead>
          <tbody>
            {#if loadingProviders}
              <tr>
                <td colspan="5">Loading providers…</td>
              </tr>
            {:else if providers.length === 0}
              <tr>
                <td colspan="5">Add your first provider to get started.</td>
              </tr>
            {:else}
              {#each providers as provider}
                <tr>
                  <td style="font-weight: 600; overflow-wrap: anywhere; word-break: break-word;">{provider.name}</td>
                  <td style="font-size: 0.85rem; color: rgba(226, 232, 240, 0.7); overflow-wrap: anywhere; word-break: break-word;">
                    {provider.base_url}
                  </td>
                  <td>
                    <span class="chip {provider.enabled ? 'success' : 'warning'}">
                      {provider.enabled ? 'Enabled' : 'Paused'}
                    </span>
                  </td>
                  <td>
                    <div class="pill-group">
                      {#each provider.download_types.split(',') as type}
                        <span class="chip">{type.trim()}</span>
                      {/each}
                    </div>
                  </td>
                  <td style="text-align: right;">
                    <button
                      type="button"
                      class="btn-primary"
                      style="padding: 0.45rem 1.1rem;"
                      on:click={() => handleToggleProvider(provider)}
                    >
                      {provider.enabled ? 'Disable' : 'Enable'}
                    </button>
                    <button
                      type="button"
                      class="btn-primary"
                      style="margin-left: 0.5rem; padding: 0.45rem 1.1rem;"
                      on:click={() => toggleProviderCategories(provider)}
                    >
                      {providerCategoriesOpen === provider.id ? 'Hide categories' : 'Categories'}
                    </button>
                    <button
                      type="button"
                      class="btn-primary"
                      style="margin-left: 0.5rem; padding: 0.45rem 1.1rem; background: rgba(248, 113, 113, 0.22); color: rgba(255, 255, 255, 0.85);"
                      on:click={() => handleDeleteProvider(provider)}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
                {#if providerCategoriesOpen === provider.id}
                  <tr>
                    <td colspan="5">
                      <div class="provider-categories-panel">
                        {#if providerCategoriesLoading.has(provider.id)}
                          <p>Loading categories…</p>
                        {:else}
                          {#if providerCategories[provider.id]?.length}
                            <div class="pill-group" style="flex-wrap: wrap; gap: 0.4rem;">
                              {#each providerCategories[provider.id] as category}
                                <span class="chip" style="display: inline-flex; align-items: center; gap: 0.35rem;">
                                  {category.code}: {category.name}
                                  <button
                                    type="button"
                                    class="chip-btn"
                                    on:click={() => handleDeleteProviderCategory(provider, category.id)}
                                  >
                                    ×
                                  </button>
                                </span>
                              {/each}
                            </div>
                          {:else}
                            <p style="margin: 0; color: rgba(226, 232, 240, 0.7);">No categories defined yet.</p>
                          {/if}
                        {/if}
                        <div class="grid" style="margin-top: 1rem; gap: 0.75rem;">
                          <div class="input-field">
                            <label>Category code</label>
                            <input
                              bind:value={providerCategoryForms[provider.id].code}
                              placeholder="7110"
                              on:input={() => ensureProviderCategoryForm(provider.id)}
                            />
                          </div>
                          <div class="input-field">
                            <label>Label</label>
                            <input
                              bind:value={providerCategoryForms[provider.id].name}
                              placeholder="e.g. Magazines"
                              on:input={() => ensureProviderCategoryForm(provider.id)}
                            />
                          </div>
                        </div>
                        <button
                          type="button"
                          class="btn-primary"
                          style="margin-top: 0.75rem;"
                          on:click={() => handleCreateProviderCategory(provider)}
                        >
                          + Add category
                        </button>
                      </div>
                    </td>
                  </tr>
                {/if}
              {/each}
            {/if}
          </tbody>
        </table>
      </div>
    </section>

  {/if}
</main>

<style>
  @keyframes spin {
    from {
      transform: rotate(0deg);
    }
    to {
      transform: rotate(360deg);
    }
  }

  .tab-nav {
    display: inline-flex;
    gap: 0.5rem;
    margin-bottom: 1.5rem;
    background: rgba(15, 23, 42, 0.35);
    padding: 0.45rem;
    border-radius: 9999px;
    border: 1px solid rgba(148, 163, 184, 0.18);
  }

  .tab-button {
    border: 1px solid transparent;
    border-radius: 9999px;
    background: rgba(148, 163, 184, 0.14);
    color: rgba(226, 232, 240, 0.78);
    font-weight: 600;
    letter-spacing: 0.02em;
    padding: 0.45rem 1.2rem;
    cursor: pointer;
    transition: background 0.2s ease, border-color 0.2s ease, color 0.2s ease;
  }

  .tab-button:hover {
    background: rgba(148, 163, 184, 0.22);
    border-color: rgba(148, 163, 184, 0.42);
  }

  .tab-button.is-active {
    background: rgba(59, 130, 246, 0.28);
    border-color: rgba(96, 165, 250, 0.6);
    color: rgba(255, 255, 255, 0.95);
  }

  .provider-categories-panel {
    margin-top: 0.75rem;
  }

  .chip-btn {
    background: transparent;
    border: none;
    color: rgba(248, 250, 252, 0.85);
    cursor: pointer;
    font-weight: 700;
  }

  .category-group {
    border: 1px solid rgba(148, 163, 184, 0.2);
    border-radius: 0.75rem;
    padding: 0.9rem;
    margin-bottom: 0.9rem;
  }

  .category-options {
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;
    margin-top: 0.6rem;
  }

  .category-option {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    background: rgba(15, 23, 42, 0.35);
    border-radius: 0.5rem;
    padding: 0.35rem 0.6rem;
  }

  .auto-guard {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
  }

  .auto-guard-inputs {
    display: flex;
    gap: 0.4rem;
  }

  .auto-guard input {
    width: 100%;
    padding: 0.4rem 0.55rem;
    border-radius: 0.5rem;
    border: 1px solid rgba(148, 163, 184, 0.35);
    background: rgba(15, 23, 42, 0.45);
    color: rgba(226, 232, 240, 0.95);
  }

  .auto-guard small {
    color: rgba(226, 232, 240, 0.55);
  }
</style>
