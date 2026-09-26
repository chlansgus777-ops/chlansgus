export interface OppRow {
  id: number; rank: number | null; ticker: string; company: string; sector: string; sector_model: string;
  price: number | null; session: string | null; price_timestamp: string | null; price_source: string | null; price_quality: string;
  score: number; confidence: number; action: string; deterministic_action: string; committee_status: string;
  ideal_entry: number | null; max_buy: number | null; target: number | null; stop: number | null; downside: number | null; rr: number | null;
  catalyst: string | null; catalyst_date: string | null; risk: string | null; data_quality: string; mode: string; vetoes: string[]; as_of: string;
  current_status: string | null; current_status_reason: string | null; sessions_since: number | null; actionable_now: boolean | null;
  action_ko: string; valuation_price_basis: string | null; sector_known: boolean;
  revalidated_price?: number | null; status_problems?: string[]; version?: number; supersedes_id?: number | null; issued_at?: string | null;
}

export interface FreshnessCheck { data_type: string; quality: string; effective: string | null; published: string | null; age: number | null; unit: string; fresh_max: number; usable_max: number; reason_ko: string }

export interface Stage { stage: string; input_count: number; output_count: number; note: string }
export interface ScanInfo { id: number; as_of: string; mode: string; stages: Stage[]; excluded: number; scoring_model_version: string }
export interface Opportunities { scan: ScanInfo | null; rows: OppRow[]; readiness?: import("./components/Readiness").ReadinessInfo }

export interface SystemInfo {
  mode: "MOCK" | "LIVE"; mock_banner: boolean; now: string; versions: Record<string, string>;
  llm: { provider: string; available: boolean; fast_model: string; deep_model: string };
  providers: { kind: string; provider: string; mode: string; configured: boolean; reason: string | null }[];
}

export interface Reason { text: string; sign: number; refs: string[] }
export interface Component { name: string; weight: number; subscore: number; available: boolean; reasons: Reason[]; missing: string[] }
export interface Evidence { evidence_id: string; category: string; label: string; value: number | string | null; source: string; source_ts: string | null; quality: string; metric?: string; ticker?: string | null; unit?: string | null; period?: string | null }
export interface HorizonImpact { horizon: string; direction: number; impact_score: number; confidence: number; mechanism: string[] }
export interface CompanyIssueImpact { issue_id: string; ticker: string; hops: number; exposure_path: string[]; horizons: HorizonImpact[]; priced_in: number | null }

export interface RuleScoreT { subscore: number | null; coverage: number; critical_missing?: string[]; items: { metric: string; label: string; value: number | null; subscore: number | null; weight: number }[] }

export interface Analysis {
  ticker: string; as_of: string; mode: string; price: number | null; price_quality: string; price_timestamp: string | null; session: string | null; price_source: string | null;
  security: { ticker: string; company_name: string; exchange: string; sector: string; industry: string; market_cap: number | null; is_adr: boolean; country_of_incorporation: string };
  sector_model_id: string; sector_model_name: string; sector_model_reason: string;
  scorecard: { components: Component[]; model_version: string };
  decision: { action: string; confidence: number; vetoes: string[]; reasons: string[]; raw_action: string; suppressed_change: boolean; size_limit: string | null; notes: string[] };
  entry: { current_price: number; ideal_entry: number; acceptable_low: number; acceptable_high: number; max_buy: number; add_zone_low: number; add_zone_high: number; stop: number; target1: number; target2: number; rr_at_current: number | null; rr_at_ideal: number | null; downside_pct: number; upside_t1_pct: number; rationale: string[] } | null;
  features: Record<string, number | null>;
  fundamental_rules: RuleScoreT | null;
  valuation_rules: RuleScoreT | null;
  multiples: Record<string, number | null> | null;
  relative_valuation: { primary_multiple: string; primary_value: number | null; history_percentile: number | null; peer_median: number | null; premium_to_peers: number | null; growth_adjusted: number | null; equity_risk_spread: number | null; notes: string[] } | null;
  earnings: { revenue_surprise: number | null; eps_surprise: number | null; guide_rev_vs_cons: number | null; guide_eps_vs_cons: number | null; result_quality: string; expectation_bar: string; beat_streak: number; notes: string[] } | null;
  revisions: { eps_direction: number; revenue_direction: number; breadth_score: number | null; low_coverage: boolean; high_dispersion: boolean } | null;
  analyst: Record<string, unknown> | null;
  regimes: { regime: string; score: number; confidence: number; active: boolean; evidence: string[] }[];
  primary_regime: string;
  macro_impact: { net: number; contributions: [string, number, string][] } | null;
  issue_impacts: CompanyIssueImpact[];
  priced_in: Record<string, { value: number | null; confidence: number; components: [string, number][]; label: string; model?: string; confidence_level?: string; band?: string | null }>;
  horizon_view: Record<string, number>;
  event_risk: { level: string; days_until: number | null; reasons: string[]; nearest: { title: string; event_date: string } | null };
  upcoming_events: { event_id: string; event_type: string; event_date: string; title: string; importance: number }[];
  options: Record<string, number | null> | null;
  ownership: Record<string, unknown> | null;
  technicals: Record<string, unknown> | null;
  thesis_conditions: { condition_id: string; description: string }[];
  thesis_invalidated: boolean; thesis_breaches: string[];
  data_quality: { fields: [string, string][]; core_missing: string[]; conflicts: string[]; stale: string[]; checks: FreshnessCheck[] };
  sector_known: boolean; short_interest_pct: number | null; valuation_price_basis: string; fundamental_adjustments?: string[];
  changes: { kind: string; text: string; material: boolean; magnitude: number | null }[];
  scenarios: { name: string; trigger: string; mechanism: string; price_low: number; price_high: number; invalidation: string; probability: number | null }[];
  evidence: Evidence[];
  reason_evidence: Record<string, string[]>;
  portfolio_review: { size_cap: string; warnings: string[]; fit: string; candidate_sector_weight_after: number; max_correlation: [string, number] | null } | null;
  versions: Record<string, string>;
}

export interface AgentReport { agent: string; stance: string; confidence: number; key_strengths: string[]; key_weaknesses: string[]; risks: string[]; missing_data: string[]; evidence_ids: string[]; summary: string }
export interface DebateArg { side: string; round: number; thesis: string; points: { claim: string; evidence_ids: string[]; interpretation: string; rebuts: string | null }[] }
export interface CommitteeResult {
  ticker: string; status: string; reason: string | null; deterministic_action: string; final_action: string; deterministic_confidence: number; final_confidence: number;
  size_class: string | null; action_changed_by: string | null; reports: Record<string, AgentReport>; invalid_outputs: Record<string, string>; debate: DebateArg[];
  synthesis: { committee_agreement: string; strongest_bull_argument: string; strongest_bear_argument: string; unresolved_uncertainty: string[]; advisory_stance: string; confidence_adjustment: number } | null;
  risk_review: { risk_level: string; recommended_action: string | null; concerns: string[]; summary: string } | null;
  portfolio_advice: { portfolio_fit: string; suggested_size: string; concentration_warning: string | null; overlap_risk: string | null; summary: string } | null;
  consensus_pct: number | null; divergence: string | null; guard: Record<string, { rejected_claims: string[]; invalid_evidence_ids: string[]; confidence_scale?: number }>; injection_flags: string[]; prompt_version: string;
}

export interface StockDetail { recommendation: OppRow; analysis: Analysis; price_history?: { day: string; close: number }[]; committee: CommitteeResult | null; committee_recommendation_id?: number | null; history: { id: number; as_of: string; score: number; action: string }[]; versions: Record<string, string> }
