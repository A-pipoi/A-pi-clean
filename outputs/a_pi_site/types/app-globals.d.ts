/** Minimal browser bridge for independently mounted locale-aware controls. */
interface Window {
  localeText?: (value: string) => string;
  APiStrategyCatalog?: Array<{
    key: string;
    top_n?: number;
    lookback?: number;
  }>;
}
interface Error {
  code?: string;
}
/** Created imperatively by the experiment-comparison module. */
interface APiDomElementMap {
  compareExperimentsButton: HTMLButtonElement;
  experimentComparison: HTMLDivElement;
}
