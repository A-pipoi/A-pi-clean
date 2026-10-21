/** UI contracts for persisted and live research data. Optional fields represent unavailable data. */
export interface Stock {
  symbol?: string;
  ts_code?: string;
  name?: string;
  industry?: string;
  market?: string;
  signal?: string;
  trade_date?: string;
  quote_date?: string;
  source?: string;
  stale?: boolean;
  close?: number;
  change_pct?: number;
  market_value?: number;
  quantity?: number;
  weight?: number;
}
export interface EquityPoint {
  date: string;
  equity: number;
}
export interface Metrics {
  annualized_return?: number;
  max_drawdown?: number;
  turnover?: number;
  sharpe?: number;
  total_return?: number;
  volatility?: number;
  [key: string]: number | undefined;
}
export interface DataQuality {
  limits_available?: boolean;
  warnings?: string[];
  errors?: string[];
  status?: string;
}
export interface Freshness {
  fresh?: boolean;
  latest_trade_date?: string;
  expected_trade_date?: string;
  reason?: string;
  source?: string;
  warnings?: string[];
}
export interface Probe {
  status?: string;
  data_reachable?: boolean;
  data_fresh?: boolean | null;
  checked_at?: string;
  latest_trade_date?: string;
  expected_trade_date?: string;
  error?: string | null;
}
export interface PaperState {
  as_of?: string;
  updated_at?: string;
  quote_date?: string;
  data_source?: string;
  return_pct?: number;
  warnings?: string[];
  pre_trade_risk?: {
    status?: string;
    annualized_volatility?: number;
    var_95?: number;
    es_95?: number;
    daily_variance?: number;
    max_pairwise_correlation?: number;
  };
  scheduler?: Scheduler;
  latest_trade_date?: string;
  skipped?: boolean;
  skip_reason?: string;
  generated_orders?: number;
  outcome?: string | null;
  no_trade_reason?: string | null;
  maintenance_stages?: Record<
    string,
    { status?: string; reason?: string; count?: number }
  > | null;
  rebalance_schedule?: {
    due?: boolean;
    interval_trading_days?: number;
    elapsed_trading_days?: number | null;
    trading_days_since_previous?: number | null;
    previous_rebalance_date?: string | null;
    last_rebalance_date?: string | null;
    next_rebalance_date?: string | null;
    next_rebalance_in_trading_days?: number | null;
  };
  next_rebalance?: {
    status?: string;
    remaining_trading_days?: number | null;
    anchor_date?: string | null;
    reason?: string;
  };
  positions?: Stock[];
  equity?: number;
  cash?: number;
  market_value?: number;
  orders?: PaperOrder[];
  equity_curve?: EquityPoint[];
  source?: string;
  industry_metadata_stale?: boolean;
  data_quality?: DataQuality;
  data_freshness?: Freshness;
  paper_trading_allowed?: boolean;
  risk_limits?: RiskLimits;
  industry_exposure?: {
    industry: string;
    market_value: number;
    weight: number;
  }[];
}
export interface PaperOrder {
  id?: string;
  order_id?: string;
  reject_reason?: string;
  filled_quantity?: number;
  symbol?: string;
  name?: string;
  side?: string;
  quantity?: number;
  status?: string;
  created_at?: string;
  source?: string;
}
export interface Scheduler {
  interval_seconds?: number;
  last_cycle_date?: string;
  enabled?: boolean;
  running?: boolean;
  scheduler_running?: boolean;
  execution_in_progress?: boolean;
  scheduled_window?: string | Record<string, unknown>;
  authority?: string | Record<string, unknown>;
  maintenance?: {
    manual_catchups?: PaperRunAudit[];
    latest?: PaperRunAudit | null;
    history?: PaperRunAudit[];
    missed_window?: boolean | Record<string, unknown> | null;
    [key: string]: unknown;
  };
  last_result?: PaperState;
  last_error?: string;
  last_run_at?: string;
  strategy?: string;
  symbols?: string[];
  rebalance?: string;
}
export interface PaperRunAudit {
  status?: string;
  attempts?: number;
  reason_code?: string | null;
  result?: PaperState | null;
  started_at?: string | null;
  finished_at?: string | null;
  next_attempt_at?: string | null;
  run_date?: string;
}
export interface RiskLimits {
  max_single_weight: number;
  max_industry_weight: number;
  max_gross_leverage: number;
  max_drawdown: number;
  max_var_95?: number | null;
  [key: string]: number | null | undefined;
}
export interface Portfolio {
  dataset?: string;
  validation_gate?: ValidationGate;
  audit?: AuditCheck[];
  strategy_validation?: Validation;
  dataset_metadata?: {
    data_source?: string;
    latest_trade_date?: string;
    [key: string]: unknown;
  };
  metrics?: Metrics;
  final_equity?: number;
  equity_curve?: EquityPoint[];
  recovery_days?: number;
  data_quality?: DataQuality;
  data_freshness?: Freshness;
  paper_trading_allowed?: boolean;
}
export interface Market {
  index?: Stock;
  indices?: Stock[];
  breadth?: {
    rising?: number;
    falling?: number;
    turnover_billion?: number;
    up?: number;
    down?: number;
    flat?: number;
    trade_date?: string;
  };
  turnover?: number;
  source?: string;
  stale?: boolean;
}
export interface FactorRow {
  announcement_date?: string;
  report_date?: string;
  factor?: string;
  name?: string;
  key?: string;
  symbol?: string;
  value?: number | null;
  sample_days?: number | null;
  trade_date?: string | null;
  status?: string;
  source?: string;
  factor_type?: string;
  enabled?: boolean;
  expression?: string;
}
export interface AuditCheck {
  name?: string;
  status?: string;
  evidence?: string;
  passed?: boolean;
  detail?: string;
}
export interface Validation {
  fold_ranges?: ValidationFold[];
  passed?: boolean;
  reason?: string;
  notes?: string[];
  folds?: number;
  embargo?: number;
  checks?: AuditCheck[];
}
export interface ValidationGate {
  conclusion_ready?: boolean;
  status?: string;
}
export interface ValidationFold {
  train_start: string;
  train_end: string;
  embargo_start?: string;
  embargo_end?: string;
  test_start: string;
  test_end: string;
}
export interface ResearchSummary {
  rows?: FactorRow[];
  factor_count?: number;
  pipeline_version?: string;
  status?: string;
  valid_count?: number;
  average_momentum?: number;
  latest_trade_date?: string;
  factor_pipeline?: FactorPipeline;
  validation_gate?: ValidationGate;
  run_count?: number;
  runs?: number;
  run_budget?: number;
  budget?: number;
  experiment_id?: number;
}
export interface BacktestJob {
  completed_at?: string;
  created_at?: string;
  id?: string;
  job_id?: string;
  status?: string;
  progress?: number;
  message?: string;
  error?: string;
  result?: BacktestResult;
}
export interface Notification {
  id: string;
  title: string;
  type?: string;
  message?: string;
  body?: string;
  kind?: string;
  tone?: string;
  time?: string;
  section?: string;
  target?: string;
  timestamp?: string;
  unread?: boolean;
}
export interface FactorEvaluation {
  portfolio?: Record<string, PortfolioReport>;
  registry_correlation_matrix?: FactorCorrelationMatrix;
  layered_diagnostics?: Record<
    string,
    {
      status?: string;
      observations?: number;
      skipped_blocks?: number;
      layer_annualized_returns?: (number | null)[];
      monotonicity_spearman?: number | null;
      curve?: {
        long_short_gross_nav?: number;
        long_short_net_nav?: Record<string, number>;
      }[];
    }
  >;
  ic_decay_curve?: {
    curve?: { horizon: number; mean_ic?: number | null }[];
    status?: string;
    half_life?: number | null;
    peak_horizon?: number;
  };
  registry_diagnostics?: {
    max_corr_with_registry?: number | null;
    incremental_rank_ic?: number | null;
    incremental_observations?: number;
    correlations?: {
      factor_key: string;
      pearson?: number | null;
      spearman?: number | null;
      observations?: number;
    }[];
  };
  horizons?: Record<string, FactorHorizon>;
  annual_distribution_drift?: {
    years?: Record<
      string,
      {
        observations?: number;
        quantiles?: Record<string, number>;
        drift_vs_previous_year?: Record<string, number>;
        compared_to?: string;
      }
    >;
  };
  coverage?: number;
  top_quintile_turnover?: number;
  factor_autocorrelation?: number;
  n_tested?: number;
  note?: string;
}
export interface PortfolioMetrics {
  observations?: number;
  annual_sharpe?: number | null;
  annual_return?: number | null;
  annual_volatility?: number | null;
  max_drawdown?: number | null;
  calmar?: number | null;
}
export interface PortfolioReport {
  status?: string;
  gross?: PortfolioMetrics;
  net?: { daily_one_way_turnover: number; metrics: PortfolioMetrics }[];
  psr?: { probability?: number | null };
  dsr?: { probability?: number | null; n_tested?: number };
  skipped_dates?: string[];
  policy?: {
    neutral_turnover?: number;
    commission_rate?: number;
    slippage_bps?: number;
    stamp_tax_rate?: number | null;
  };
  curve?: {
    signal_date: string;
    entry_date: string;
    date: string;
    return: number;
    gross_nav?: number | null;
    net_nav?: Record<string, number | null>;
  }[];
}
export interface ArenaReport {
  arena_id: string;
  status: string;
  global_n_tested: number;
  ranking_basis: string;
  brief?: {
    training_start?: string;
    training_end?: string;
    evaluation_budget?: number;
    top_n?: number;
  };
  participants?: {
    proposer_tag: string;
    evaluations_used: number;
    budget: number;
    locked: boolean;
    n_tested: number;
    best_icir?: number | null;
    top10_mean_icir?: number | null;
    effective_hypothesis_rate?: number | null;
    sealed_pass_rate?: number | null;
    sealed_mean_ic?: number | null;
    registry_correlations?: number[];
  }[];
  manifest?: {
    peer_correlations?: {
      registry_pairs?: {
        proposer_tag: string;
        expression: string;
        registry_key: string;
        correlation?: number | null;
      }[];
      pairs?: {
        left_proposer: string;
        right_proposer: string;
        left_expression: string;
        right_expression: string;
        correlation?: number | null;
      }[];
    };
  };
}
export interface FactorCorrelationMatrix {
  keys: string[];
  matrix: ({
    pearson?: number | null;
    spearman?: number | null;
    observations?: number;
    paired_samples?: number;
  } | null)[][];
  sealed_dates_used?: boolean;
  scope?: string;
  start?: string | null;
  end?: string | null;
}
export interface FactorHorizon {
  decay_monitor?: {
    status?: string;
    as_of?: string;
    observations?: number;
    required?: number;
    alerts?: { message?: string; id?: string }[];
    series?: { date: string; rolling_ic: number; reference_ic: number }[];
  };
  mean_ic?: number;
  mean_rank_ic?: number;
  normal_ic?: { mean_ic?: number };
  icir?: number;
  observations?: number;
  monthly_hit_rate?: number;
  long_short_return?: number;
  raw_p_value?: number;
  dsr_p_value?: number;
  environment_slices?: { market_cap?: FactorSlice; industry?: FactorSlice };
}
export interface FactorSlice {
  groups?: Record<string, { mean_rank_ic?: number; observations?: number }>;
  reason?: string;
  status?: string;
}
export interface FactorCandidate {
  ic_passed?: boolean;
  portfolio_passed?: boolean;
  metric_conflict?: boolean;
  decision_note?: string;
  factor_key?: string;
  factor_keys?: string[];
  mean_rank_ic?: number;
  fitness?: number;
  nodes?: number;
  depth?: number;
  dsr_p_value?: number;
  cross_universe_reference?: {
    horizons?: Record<string, FactorHorizon>;
    status?: string;
    period_start?: string;
    period_end?: string;
    reason?: string;
  };
  key?: string;
  name?: string;
  expression?: string;
  status?: string;
  evaluation?: FactorEvaluation;
  bh_adjusted_p_value?: number;
  score?: number;
  [key: string]: unknown;
}
export interface MiningJob {
  job_id: string;
  status: string;
  stage: string;
  progress_current?: number;
  progress_total?: number | null;
  requested_start?: string;
  requested_end?: string;
  current_symbol?: string;
  error?: string;
  run_id?: string;
  created_at?: string;
}
export interface MiningCensus {
  n_tested: number;
  templates: {
    name?: string;
    expression?: string;
    category?: string;
    status: string;
    n_tested: number;
    mean_ic?: number | null;
    icir?: number | null;
    coverage?: number | null;
    turnover?: number | null;
    error?: string;
  }[];
  family_ranking: {
    family: string;
    evaluated: number;
    failed: number;
    mean_icir?: number | null;
  }[];
}
export interface FactorMiningSnapshot {
  composition_factors?: { key: string; name?: string }[];
  active_job?: MiningJob | null;
  latest_job?: MiningJob | null;
  dataset_status?: {
    sealed_ready: boolean;
    requested_start?: string | null;
    requested_end?: string | null;
  };
  candidates?: FactorCandidate[];
  registered?: FactorRow[];
  expression_templates?: {
    name: string;
    category: string;
    expression?: string;
  }[];
  sealed_test?: {
    sealed?: boolean;
    id?: number;
    sealed_start?: string;
    sealed_end?: string;
  };
  sealed_factor_acceptance?: {
    status: string;
    run_id?: string;
    result?: {
      included_runs?: { run_id: string }[];
      candidates?: FactorCandidate[];
    };
  };
  research_universe?: ResearchUniverse;
  sealed_tested_individuals?: number;
  promotion_block_reason?: string;
  pit_quality?: {
    financial?: string;
    size?: string;
    size_coverage?: number;
    industry?: string;
  };
  runs?: {
    run_id: string;
    sealed_test_id?: number;
    status: string;
    n_tested?: number;
    result?: {
      census?: MiningCensus;
      composition?: { weighting?: string; weights?: Record<string, number> };
      multi_seed?: { seeds: number[]; n_tested: number; run_ids: string[] };
      candidates?: FactorCandidate[];
      seed?: number;
      training_end?: string;
    };
  }[];
  factor_library?: {
    key?: string;
    factor_key?: string;
    name?: string;
    sealed_test_id?: number;
    data_version?: string;
    expression?: string;
    status?: string;
    metadata?: {
      acceptance?: FactorCandidate;
      training?: { dependencies?: string[]; mean_rank_ic?: number };
    };
  }[];
}
export interface ResearchUniverse {
  index_name?: string;
  index_code?: string;
  pit_membership?: boolean;
  snapshot_count?: number;
  snapshot_start?: string;
  snapshot_end?: string;
  min_listing_days?: number;
  sample_symbol_count?: number;
}
export interface FactorPipeline {
  coverage_policy?: string;
  retention_rank?: number;
  rebalance_deadband?: number;
  cash_reserve?: number;
  participation?: {
    status: string;
    evaluations: number;
    latest: {
      day?: string;
      eligible_symbols: number;
      requested_symbols: number;
    };
    factors: {
      weight: number;
      key: string;
      name: string;
      covered: number;
      requested: number;
      used: number;
      overall_coverage: number;
      missing_reason?: string | null;
    }[];
    correlations: {
      left: string;
      right: string;
      mean_rank_correlation: number;
      mean_absolute_correlation: number;
      evaluations: number;
      redundancy_warning: boolean;
    }[];
    correlation_basis: string;
  };
  version?: string;
  factor_names?: string[];
  factor_definitions?: FactorRow[];
  latest_fit?: {
    status?: string;
    training_samples?: number;
    data_version?: string;
  };
}
export interface StrategyInfo {
  key: string;
  label?: string;
  description?: string;
  available?: boolean;
  unavailable_reason?: string;
  lookback?: number;
  top_n?: number;
  [key: string]: unknown;
}
export interface BacktestResult {
  training_weight_comparison?: {
    training_start: string;
    training_end: string;
    note: string;
    rows: {
      profile: string;
      annualized_return: number | null;
      sharpe: number | null;
      max_drawdown: number | null;
      turnover: number | null;
      total_cost: number;
      status: string;
    }[];
  } | null;
  job_id?: string;
  completed_at?: string;
  strategy_version?: string;
  security_contribution?: {
    status?: string;
    net_pnl?: number;
    fees?: number;
    reconciliation_error?: number;
    security_pnl?: Record<string, number>;
  };
  benchmark_curve?: { date: string; equity: number }[];
  dataset?: string;
  final_equity?: number;
  research?: ResearchSummary;
  validation_gate?: ValidationGate;
  dataset_metadata?: {
    sealed_test_start?: string;
    sealed_test_end?: string;
    data_source?: string;
    research_universe?: ResearchUniverse;
  };
  parameters?: {
    symbols?: string[];
    research_universe?: ResearchUniverse;
    start_date?: string;
    end_date?: string;
    initial_cash?: number;
  };
  monthly_returns?: { month: string; return: number }[];
  capacity_report?: { max_participation?: number };
  fills?: number;
  fills_detail?: {
    symbol: string;
    side: string;
    date: string;
    quantity: number;
  }[];
  industry_trade_report?: {
    rows?: { industry?: string; fills?: number; share?: number }[];
    classified_fills?: number;
    unclassified_fills?: number;
  };
  brinson_report?: {
    status?: string;
    reason?: string;
    linked?: { allocation?: number; selection?: number; interaction?: number };
  };
  factor_pipeline?: FactorPipeline;
  strategy?: string;
  strategy_label?: string;
  experiment_id?: number;
  risk_analytics?: RiskAnalytics;
  portfolio?: Portfolio;
  metrics?: Metrics;
  equity_curve?: EquityPoint[];
  warnings?: string[];
  data_quality?: DataQuality;
  strategy_validation?: Validation;
  audit?: AuditCheck[];
  source?: string;
  data_freshness?: Freshness;
  paper_trading_allowed?: boolean;
}
export interface Workspace {
  validation_gate?: ValidationGate;
  snapshot_cached?: boolean;
  snapshot_age_seconds?: number;
  strategy_validation?: Validation;
  audit?: AuditCheck[];
  data_quality?: DataQuality;
  source?: string;
  portfolio?: Portfolio;
  paper?: PaperState;
  scheduler?: Scheduler;
  risk_limits?: RiskLimits;
  market?: Market;
  signals?: Stock[];
  research?: ResearchSummary;
  latest_trade_date?: string;
  data_source?: string;
  warnings?: string[];
  backtest?: BacktestResult;
}
export interface HealthState {
  online?: boolean;
  providerReady?: boolean;
  dataReachable?: boolean;
  dataFresh?: boolean | null;
  probe?: Probe;
  sqlite?: boolean;
  error?: string;
  checked_at?: string;
}
export interface RiskAnalytics {
  status?: string;
  observations?: number;
  annualized_volatility?: number;
  historical_var_95?: number;
  historical_expected_shortfall_95?: number;
  max_drawdown?: number;
  var_95?: number;
  es_95?: number;
  max_pairwise_correlation?: number;
  daily_variance?: number;
  stress_horizons?: Record<
    string,
    { return?: number; start?: string; end?: string } | null
  >;
  historical_periods?: Record<
    string,
    { status?: string; max_drawdown?: number }
  >;
  correlation?: {
    symbols?: string[];
    matrix?: Record<string, Record<string, number | null>>;
  };
  basis?: string;
  limits?: { correlation?: string };
}
export interface NotificationState {
  health: HealthState | null;
  workspace: Workspace | null;
  paper: PaperState | null;
  backtest: BacktestResult | null;
}
export interface RuntimeConfig {
  universe_note?: string;
  seed_universe: Stock[];
  index_universes: { code: string; name?: string; ts_code?: string }[];
  data_sources: { key: string; label: string }[];
  stale: boolean;
}
export interface Budget {
  used: number;
  budget: number;
  remaining: number;
  strategy: string;
}
export interface LedgerEntry {
  id?: number;
  kind?: string;
  status?: string;
  reason?: string;
  result_url?: string;
}
export interface WorkspaceCatalog {
  current_id?: string;
  can_switch?: boolean;
  message?: string;
  workspaces?: {
    id: string;
    name: string;
    description?: string;
    status?: string;
    active?: boolean;
  }[];
}
export interface RiskAlert {
  id?: string;
  title: string;
  kind?: string;
  description?: string;
  rows: [string, unknown, string?][];
}
export interface OptimizationResult {
  as_of?: string;
  candidate_count?: number;
  industry_coverage_count?: number;
  benchmark_constituent_count?: number;
  weights?: Record<string, number>;
  pre_trade_risk?: RiskAnalytics;
  covariance_basis?: string;
  pit_panel_sha256?: string;
  data_version?: string;
}
export interface FinancialStatements {
  metadata?: {
    status?: string;
    errors?: string[];
    source?: string;
    as_of?: string;
  };
  statements?: Record<
    string,
    Record<
      string,
      {
        raw?: Record<string, number | string | null>;
        announcement_date?: string;
        report_date?: string;
      }
    >
  >;
}
export interface AppState {
  latestBacktestValues: number[] | null;
  latestBacktestResult: BacktestResult | null;
  latestBacktestRestored: boolean;
  restoringBacktestUrl: string | null;
  backtestRestoreRequests: Map<string, Promise<boolean>>;
  latestWorkspaceSnapshot: Workspace | null;
  latestPaperState: PaperState | null;
  MAX_RESEARCH_SYMBOLS: number;
  researchBudgetState: Budget | null;
  latestResearchRows: FactorRow[];
  activeFactorFilter: string;
  latestEquityCurve: EquityPoint[];
  activeEquityRange: string;
  latestRiskLimits: RiskLimits | null;
  latestRiskAlertDetails: RiskAlert[];
  latestScheduler: Scheduler | null;
  workspaceLoading: boolean;
  workspaceLoadError: string | null;
  workspaceBuilding: boolean;
  workspaceRetryTimer: number | null;
  workspaceRetryDelaySeconds: number;
  workspaceRetryAttempt: number;
  workspaceCatalogLoading: boolean;
  workspaceCatalog: WorkspaceCatalog | null;
  runtimeConfig: RuntimeConfig;
  runtimeSeedSymbols: string[];
  notificationState: NotificationState;
  connectionState: { python: boolean; tushare: boolean; sqlite: boolean };
  activeLocale: string;
  toastTimer: number | null;
  riskViewRuntime: {
    apply(
      paper: PaperState | null,
      portfolio: Portfolio | null,
      limits?: RiskLimits | null,
    ): void;
    dismissAlert(id: string): void;
  } | null;
}
