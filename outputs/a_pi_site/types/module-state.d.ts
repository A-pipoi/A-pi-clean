import type { Stock, StrategyInfo } from "./domain";
export interface ChartOptions {
  bands?: { start: string; end: string; color: string }[];
  rawValues?: number[];
  dates?: string[];
  tooltipId?: string;
}
export interface ChartState {
  width: number;
  height: number;
  padding: { top: number; bottom: number; left: number; right: number };
  plotWidth: number;
  plotHeight: number;
  values: number[];
  secondary: number[] | null;
  options: ChartOptions;
  points: {
    value: number;
    rawValue?: number;
    date: string;
    x: number;
    y: number;
  }[];
  colors: Record<string, string>;
  tooltipId?: string;
  activeIndex: number;
  pinned?: boolean;
}
export interface StockPickerState {
  selected: Stock[];
  results: Stock[];
  total: number;
  activeIndex: number;
  timer: number | undefined;
  requestId: number;
  controller: AbortController | null;
  source: string;
  offset: number;
  hasMore: boolean;
  loadingMore: boolean;
  loadMoreError: string;
  skipFocusLoad: boolean;
}
export interface StockPickerRefs {
  host: HTMLElement;
  hidden: HTMLInputElement;
  chips: HTMLElement;
  input: HTMLInputElement;
  meta: HTMLElement;
  suggestions: HTMLElement;
}
export interface SearchItem extends Stock {
  type: string;
  title: string;
  meta: string;
  section?: string;
  value?: string;
  icon: string;
}
export interface SearchState {
  results: SearchItem[];
  activeIndex: number;
  timer: number | undefined;
  requestId: number;
  controller: AbortController | null;
}
