import type {
  AppState,
  BacktestResult,
  Budget,
  DataQuality,
  EquityPoint,
  FactorRow,
  Market,
  NotificationState,
  PaperState,
  Portfolio,
  Probe,
  RiskAlert,
  RiskLimits,
  Scheduler,
  Stock,
  Workspace,
  WorkspaceCatalog,
} from "./domain";

export type FactorRegistrationServices = Pick<AppState, never> &
  Pick<
    SharedServices,
    | "openDialog"
    | "closeDialog"
    | "showGovernance"
    | "toast"
    | "reloadWorkspace"
    | "registered"
  >;
export type GovernanceActionsServices = Pick<AppState, never> &
  Pick<
    SharedServices,
    "localeText" | "loadGovernance" | "toast" | "formatNumber"
  >;
export type ExperimentComparisonServices = Pick<AppState, never> &
  Pick<
    SharedServices,
    "toast" | "escapeHtml" | "formatPercent" | "formatNumber"
  >;
export type BacktestRunnerServices = Pick<AppState, never> &
  Pick<
    SharedServices,
    | "localeText"
    | "setText"
    | "backtestPreset"
    | "applyResult"
    | "loadGovernance"
    | "syncBudgetControls"
    | "formatNumber"
    | "dataSourceLabel"
    | "toast"
  >;
export type NotificationsServices = Pick<AppState, never> &
  Pick<
    SharedServices,
    | "notificationState"
    | "escapeHtml"
    | "getLocale"
    | "applyLocale"
    | "paperStatusLabel"
    | "localeText"
    | "showToast"
    | "showSection"
    | "checkServiceHealth"
    | "loadWorkspace"
    | "fetchPaperState"
    | "closestHTMLElement"
  >;
export type PaperPageServices = Pick<AppState, never> &
  Pick<
    SharedServices,
    | "setPaperState"
    | "getWorkspace"
    | "getRiskLimits"
    | "getLocale"
    | "dataSourceLabel"
    | "setText"
    | "formatNumber"
    | "formatPercent"
    | "escapeHtml"
    | "beijingDateTime"
    | "renderAllocationDonut"
    | "applySignalSnapshot"
    | "applyRiskSnapshot"
    | "applyLocale"
    | "localeText"
    | "showToast"
    | "backtestPreset"
  >;
export type ChartsServices = Pick<AppState, never> & {
  getBacktestResult(): BacktestResult | null;
} & Pick<
    SharedServices,
    "getCurve" | "updateAxis" | "escapeHtml" | "formatNumber"
  >;
export type PortfolioOptimizerServices = Pick<AppState, never> &
  Pick<SharedServices, "translate">;
export type StockPickerServices = Pick<AppState, never> &
  Pick<
    SharedServices,
    | "getMaxSymbols"
    | "getLocale"
    | "escapeHtml"
    | "localeText"
    | "applyLocale"
    | "showToast"
    | "closestHTMLElement"
  >;
export type StrategySearchServices = Pick<AppState, never> &
  Pick<
    SharedServices,
    | "sectionNames"
    | "escapeHtml"
    | "getLocale"
    | "localeText"
    | "applyLocale"
    | "initBacktestUniverseSelector"
    | "showSection"
    | "showToast"
    | "addStockToPicker"
    | "loadGovernance"
    | "closestHTMLElement"
  >;
export type FinancialStatementsServices = Pick<AppState, never> &
  Pick<
    SharedServices,
    | "escapeHtml"
    | "getLocale"
    | "getLatestTradeDate"
    | "localeText"
    | "applyLocale"
    | "showToast"
  >;
export type FactorMiningPanelServices = Pick<AppState, never> &
  Pick<
    SharedServices,
    | "escapeHtml"
    | "setText"
    | "showToast"
    | "loadFactorDefinitions"
    | "closestHTMLElement"
    | "localeText"
  >;

export type MarketViewServices = Pick<AppState, "activeLocale"> &
  Pick<
    SharedServices,
    | "applyLocale"
    | "dataSourceClass"
    | "dataSourceLabel"
    | "escapeHtml"
    | "formatNumber"
    | "localeText"
    | "setText"
    | "showToast"
  >;
export type PortfolioViewServices = Pick<
  AppState,
  | "latestBacktestValues"
  | "latestEquityCurve"
  | "latestRiskAlertDetails"
  | "latestRiskLimits"
  | "latestWorkspaceSnapshot"
  | "riskViewRuntime"
  | "runtimeConfig"
> &
  Pick<
    SharedServices,
    | "notificationState"
    | "escapeHtml"
    | "formatNumber"
    | "formatPercent"
    | "localeText"
    | "setText"
    | "updateEquityAxis"
    | "visibleEquityCurve"
    | "getCurrentUsername"
  >;
export type DataHealthServices = Pick<AppState, "latestWorkspaceSnapshot"> &
  Pick<SharedServices, "notificationState" | "localeText" | "setText">;
export type WorkspacePresenterServices = Pick<
  AppState,
  | "connectionState"
  | "latestBacktestRestored"
  | "latestBacktestResult"
  | "latestPaperState"
  | "latestRiskLimits"
  | "latestScheduler"
  | "latestWorkspaceSnapshot"
  | "workspaceBuilding"
  | "workspaceLoadError"
> &
  Pick<
    SharedServices,
    | "notificationState"
    | "applyBacktestResult"
    | "applyMarketSnapshot"
    | "applyPortfolioSnapshot"
    | "applyResearchSnapshot"
    | "applyRiskSnapshot"
    | "applySignalSnapshot"
    | "beijingDateTime"
    | "dataSourceLabel"
    | "escapeHtml"
    | "formatPercent"
    | "localeText"
    | "renderDataHealth"
    | "setText"
    | "updateConnectionStatus"
  >;
export type FactorRegistryViewServices = Pick<
  AppState,
  "activeFactorFilter" | "activeLocale" | "latestResearchRows"
> &
  Pick<
    SharedServices,
    "applyLocale" | "escapeHtml" | "formatPercent" | "setText"
  >;
export type ResearchGovernanceServices = Pick<
  AppState,
  "latestBacktestResult" | "researchBudgetState" | "restoringBacktestUrl"
> &
  Pick<
    SharedServices,
    | "escapeHtml"
    | "formatPercent"
    | "localeText"
    | "restoreBacktestResult"
    | "setText"
    | "showToast"
    | "syncResearchBudgetControls"
  >;
export type WorkspaceControllerServices = Pick<
  AppState,
  | "activeLocale"
  | "latestWorkspaceSnapshot"
  | "workspaceBuilding"
  | "workspaceCatalog"
  | "workspaceCatalogLoading"
  | "workspaceLoadError"
  | "workspaceLoading"
  | "workspaceRetryAttempt"
  | "workspaceRetryDelaySeconds"
  | "workspaceRetryTimer"
> &
  Pick<
    SharedServices,
    | "notificationState"
    | "applyLocale"
    | "applyWorkspaceSnapshot"
    | "closestHTMLElement"
    | "escapeHtml"
    | "loadFactorDefinitions"
    | "loadGovernance"
    | "loadOperationsStatus"
    | "localeText"
    | "renderDataHealth"
    | "renderDataProbeStatus"
    | "setText"
    | "showToast"
  >;
export type BacktestResultsServices = Pick<
  AppState,
  | "backtestRestoreRequests"
  | "latestBacktestRestored"
  | "latestBacktestResult"
  | "latestBacktestValues"
  | "latestEquityCurve"
  | "latestPaperState"
  | "latestRiskLimits"
  | "latestWorkspaceSnapshot"
  | "restoringBacktestUrl"
> &
  Pick<
    SharedServices,
    | "notificationState"
    | "applyPortfolioSnapshot"
    | "applyRiskSnapshot"
    | "dataSourceLabel"
    | "escapeHtml"
    | "formatPercent"
    | "formatNumber"
    | "loadAdjustmentHealth"
    | "loadOperationsStatus"
    | "localeText"
    | "renderGuardrails"
    | "setText"
    | "updateEquityAxis"
    | "visibleEquityCurve"
  >;
export type AppShellServices = Pick<
  AppState,
  | "activeLocale"
  | "connectionState"
  | "latestRiskAlertDetails"
  | "latestScheduler"
  | "latestWorkspaceSnapshot"
  | "toastTimer"
> &
  Pick<
    SharedServices,
    | "notificationState"
    | "applyLocale"
    | "appShell"
    | "authView"
    | "breadcrumb"
    | "escapeHtml"
    | "fetchPaperState"
    | "formatNumber"
    | "formatPercent"
    | "localeText"
    | "navItems"
    | "sectionNames"
    | "sections"
    | "sessionKey"
    | "dismissRiskAlert"
    | "setText"
  >;
export type RuntimeConfigServices = Pick<
  AppState,
  | "activeLocale"
  | "latestPaperState"
  | "latestWorkspaceSnapshot"
  | "MAX_RESEARCH_SYMBOLS"
  | "researchBudgetState"
  | "runtimeConfig"
  | "runtimeSeedSymbols"
> &
  Pick<
    SharedServices,
    "applyLocale" | "applySignalSnapshot" | "escapeHtml" | "showToast"
  >;
export type ServiceHealthServices = Pick<
  AppState,
  "connectionState" | "latestWorkspaceSnapshot" | "MAX_RESEARCH_SYMBOLS"
> &
  Pick<
    SharedServices,
    | "notificationState"
    | "renderDataHealth"
    | "renderDataProbeStatus"
    | "setText"
    | "showToast"
    | "updateConnectionStatus"
  >;
export type RiskViewServices = Pick<AppState, never> &
  Pick<
    SharedServices,
    | "setText"
    | "localeText"
    | "escapeHtml"
    | "getRiskLimits"
    | "setRiskLimits"
    | "getWorkspaceSnapshot"
    | "getHealth"
    | "getCurrentUsername"
    | "setAlertDetails"
  >;

export interface SharedServices {
  localeText(value: unknown): string;
  escapeHtml(value: unknown): string;
  setText(id: string, value: unknown): void;
  formatNumber(value: unknown, digits?: number): string;
  formatPercent(value: unknown, digits?: number): string;
  beijingDateTime(value: unknown): string;
  applyLocale(locale?: string): void;
  showToast(message: unknown): void;
  toast(message: unknown): void;
  dataSourceClass(source: unknown, stale?: boolean): string;
  dataSourceLabel(source: unknown, stale?: boolean): string;
  applyBacktestResult(
    data: BacktestResult,
    options?: { restored?: boolean },
  ): void;
  applyMarketSnapshot(market: Market | null | undefined): void;
  applyPortfolioSnapshot(portfolio: Portfolio | null | undefined): void;
  applyResearchSnapshot(research: import("./domain").ResearchSummary): void;
  applyRiskSnapshot(
    paper: PaperState | null,
    portfolio: Portfolio | null,
    limits?: RiskLimits | null,
  ): void;
  applySignalSnapshot(
    signals: Stock[] | undefined,
    paper?: PaperState | null,
  ): void;
  applyWorkspaceSnapshot(data: Workspace): void;
  closestHTMLElement(event: Event, selector: string): HTMLElement | null;
  loadWorkspace(showFailure?: boolean): Promise<boolean>;
  reloadWorkspace(): Promise<boolean>;
  loadFactorDefinitions(): Promise<void>;
  loadGovernance(showFailure?: boolean): Promise<boolean>;
  loadOperationsStatus(): Promise<void>;
  loadAdjustmentHealth(symbols?: string[]): Promise<void>;
  checkServiceHealth(showFailure?: boolean): Promise<boolean>;
  fetchPaperState(showFailure?: boolean): Promise<boolean>;
  renderDataHealth(
    snapshot?: Workspace | null,
    health?: import("./domain").HealthState | null,
  ): void;
  renderDataProbeStatus(): void;
  updateConnectionStatus(): void;
  visibleEquityCurve(): EquityPoint[];
  updateEquityAxis(curve: EquityPoint[]): void;
  renderGuardrails(audit: Workspace): void;
  restoreBacktestResult(url: string): Promise<boolean>;
  syncResearchBudgetControls(): void;
  syncBudgetControls(): void;
  authView: HTMLElement;
  appShell: HTMLElement;
  breadcrumb: HTMLElement | null;
  navItems: HTMLElement[];
  sections: HTMLElement[];
  sectionNames: Record<string, string>;
  sessionKey: string;
  openDialog(title: string, body: string): void;
  closeDialog(): void;
  showGovernance(): void;
  registered(factor?: FactorRow): void;
  openUtilityDialog(title: string, body: string): void;
  closeUtilityDialog(): void;
  getLocale(): string;
  getLatestTradeDate(): string;
  getMaxSymbols(): number;
  showSection(id: string): void;
  initBacktestUniverseSelector(): void;
  addStockToPicker(item: Stock): boolean;
  getWorkspace(): Workspace | null;
  getRiskLimits(): RiskLimits | null;
  getWorkspaceSnapshot(): Workspace | null;
  getHealth(): import("./domain").HealthState | null;
  getCurrentUsername(): string;
  dismissRiskAlert(id: string): void;
  setRiskLimits(value: RiskLimits | null): void;
  setAlertDetails(value: RiskAlert[]): void;
  setPaperState(
    paper: PaperState,
    scheduler: Scheduler | null,
    limits?: RiskLimits | null,
  ): void;
  renderAllocationDonut(
    positions: Stock[],
    equity: number,
    marketValue: number,
  ): void;
  backtestPreset(options?: { researchOnly?: boolean }): ResearchPreset;
  applyResult(data: BacktestResult): void;
  translate(value: unknown): string;
  paperStatusLabel(status: unknown): string;
  getCurve(): EquityPoint[];
  updateAxis(curve: EquityPoint[]): void;
  setStatus(message: string, state?: string): void;
  notificationState: NotificationState;
}
export interface ResearchPreset {
  recipe_version?: string;
  strategy: string;
  symbols?: string[];
  rebalance: string;
  top_n: number;
  lookback: number;
  data_source: string;
  start_date: string;
  end_date: string;
  universe_mode: string;
  universe_index_code?: string;
  initial_cash?: number;
  adjust?: string;
  commission_bps?: number;
  slippage_bps?: number;
  [key: string]: unknown;
}
