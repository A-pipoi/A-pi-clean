/** State owned by one application instance, exposed through scoped dependency ports. */
/** @param {string} activeLocale @returns {import('../types/domain').AppState} */
export function createAppState(activeLocale) {
  return {
    latestBacktestValues: null,
    latestBacktestResult: null,
    latestBacktestRestored: false,
    restoringBacktestUrl: null,
    backtestRestoreRequests: new Map(),
    latestWorkspaceSnapshot: null,
    latestPaperState: null,
    MAX_RESEARCH_SYMBOLS: 24,
    researchBudgetState: null,
    latestResearchRows: [],
    activeFactorFilter: "all",
    latestEquityCurve: [],
    activeEquityRange: "6M",
    latestRiskLimits: null,
    latestRiskAlertDetails: [],
    latestScheduler: null,
    workspaceLoading: false,
    workspaceLoadError: null,
    workspaceBuilding: false,
    workspaceRetryTimer: null,
    workspaceRetryDelaySeconds: 0,
    workspaceRetryAttempt: 0,
    workspaceCatalogLoading: false,
    workspaceCatalog: null,
    runtimeConfig: {
      seed_universe: [],
      index_universes: [],
      data_sources: [],
      stale: true,
    },
    runtimeSeedSymbols: [],
    notificationState: {
      health: null,
      workspace: null,
      paper: null,
      backtest: null,
    },
    connectionState: { python: false, tushare: false, sqlite: false },
    activeLocale: activeLocale,
    toastTimer: null,
    riskViewRuntime: null,
  };
}

/**
 * @template {object} T
 * @template {keyof T} K
 * @param {T} state
 * @param {readonly K[]} keys
 * @returns {Pick<T, K>}
 */
export function createStatePort(state, keys) {
  const port = /** @type {Pick<T, K>} */ ({});
  for (const key of keys)
    Object.defineProperty(port, key, {
      enumerable: true,
      get: () => state[key],
      set: (value) => {
        state[key] = value;
      },
    });
  return port;
}

/**
 * @template {object} T
 * @template {keyof T} K
 * @template {object} S
 * @param {T} state
 * @param {readonly K[]} keys
 * @param {S} services
 * @returns {S & Pick<T, K>}
 */
export function connectState(state, keys, services) {
  return /** @type {S & Pick<T, K>} */ (
    Object.defineProperties(
      services,
      Object.getOwnPropertyDescriptors(createStatePort(state, keys)),
    )
  );
}
