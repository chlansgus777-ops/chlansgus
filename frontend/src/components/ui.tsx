import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { actionClass, ago, price, stamp } from "../format";
import { DIR_KO, GLOSSARY, tip } from "../glossary";
import { ACTION_INFO, BULLISH, DATA_TYPE_KO, QUALITY_INFO, STANCE_KO, STATUS_INFO, VETO_KO, actionLabel } from "../i18n";
import type { Evidence, FreshnessCheck } from "../types";
import { IAlert, IInfo } from "./icons";

export type Tone = "ok" | "warn" | "danger" | "info" | "neutral";

/** A bullish action that is no longer current (or was based on stale data) is an expired signal. */
export function isExpired(a: string | null | undefined, status?: string | null, quality?: string | null): boolean {
  return !!a && BULLISH.has(a) && (!!(status && status !== "CURRENT") || !!(quality && !["FRESH", "DELAYED"].includes(quality)));
}

/** Action badge. An expired bullish action must never look like a normal, actionable BUY. */
export function Action({ a, status, quality, lg }: { a: string | null | undefined; status?: string | null; quality?: string | null; lg?: boolean }) {
  const info = a ? ACTION_INFO[a] : undefined;
  if (isExpired(a, status, quality)) {
    return (
      <span className={`badge a-EXPIRED${lg ? " lg" : ""}`} title={`${info?.help ?? ""}\n\n⚠ ${STATUS_INFO[status ?? "EXPIRED"]?.help ?? "현재 유효하지 않은 추천"}`} data-testid="action-expired">
        <s>{actionLabel(a)}</s> · {STATUS_INFO[status ?? "EXPIRED"]?.label ?? "만료"}
      </span>
    );
  }
  return <span className={`${actionClass(a)}${lg ? " lg" : ""}`} title={info?.help ?? ""}>{actionLabel(a)}</span>;
}

export function Quality({ q }: { q: string | null | undefined }) {
  const k = q ?? "MISSING";
  const info = QUALITY_INFO[k];
  return <span className={`q-${k}`} title={info?.help ?? ""}>{info ? `${info.label}(${k})` : k}</span>;
}

const STATUS_ICON: Record<string, string> = { CURRENT: "✓", NEEDS_REVALIDATION: "!", PLAN_INVALIDATED: "✕", AGING: "◷", UNVERIFIED: "?", SUPERSEDED: "↻", EXPIRED: "◷" };

export function StatusBadge({ s, reason, action }: { s: string | null | undefined; reason?: string | null; action?: string | null }) {
  if (!s) return null;
  // an undecided call has no plan whose validity could expire: "만료" right after a fresh analysis misleads
  if (action === "DATA INSUFFICIENT") return <span className="status s-HELD" title={reason ?? "데이터가 부족하거나 오래되어 판단하지 않았습니다 — 실행할 계획이 없습니다"}><span aria-hidden>◌</span>판단 보류</span>;
  const info = STATUS_INFO[s];
  return <span className={`status s-${s}`} title={reason ?? info?.help ?? ""}><span aria-hidden>{STATUS_ICON[s] ?? "•"}</span>{info?.label ?? s}</span>;
}

/** A destructive action asked twice in place: the first click shows what will happen with 확인 / 취소 (Escape or
 * leaving cancels) — the app's own look, and no reliance on a browser dialog the desktop webview may not show. */
export function ConfirmButton({ label, confirm, onConfirm, busy = false, disabled = false, className = "sm danger-ghost", icon, ariaLabel, testId }:
  { label: ReactNode; confirm: ReactNode; onConfirm: () => void; busy?: boolean; disabled?: boolean; className?: string; icon?: ReactNode; ariaLabel?: string; testId?: string }) {
  const [asking, setAsking] = useState(false);
  const yes = useRef<HTMLButtonElement | null>(null);
  useEffect(() => { if (asking) yes.current?.focus(); }, [asking]);
  if (!asking) return <button type="button" className={className} disabled={disabled || busy} aria-label={ariaLabel} data-testid={testId} onClick={() => setAsking(true)}>{icon}{label}</button>;
  return (
    <span className="confirm-inline" role="group" aria-label="확인" onKeyDown={(e) => { if (e.key === "Escape") setAsking(false); }}
          onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setAsking(false); }}>
      <span className="q">{confirm}</span>
      <button type="button" ref={yes} className="sm danger-solid" data-testid={testId ? `${testId}-yes` : undefined} onClick={() => { setAsking(false); onConfirm(); }}>확인</button>
      <button type="button" className="sm ghost" onClick={() => setAsking(false)}>취소</button>
    </span>
  );
}

export function Vetoes({ v }: { v: string[] }) {
  if (!v.length) return null;
  return <span className="pill tone-danger veto-pill" title={v.map((x) => VETO_KO[x] ?? x).join(", ")}>거부권 · {v.map((x) => VETO_KO[x] ?? x).join(", ")}</span>;
}

export function Card({ title, children, right, icon, tone, explain, className, testId, sub }: { title?: string; children: ReactNode; right?: ReactNode; icon?: ReactNode; tone?: "pos" | "neg" | "warn"; explain?: ReactNode; className?: string; testId?: string; sub?: boolean }) {
  const H = sub ? "h3" : "h2";
  return (
    <section className={`card${tone ? ` tone-${tone}` : ""}${className ? ` ${className}` : ""}`} data-testid={testId}>
      {(title || right) && (
        <div className="card-head">
          <div style={{ minWidth: 0 }}>
            {title ? <H className="t-card">{icon && <span className="icon" aria-hidden>{icon}</span>}{title}</H> : null}
            {explain && <div className="explain">{explain}</div>}
          </div>
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

/** Section heading: the reading order of a page (the number is decoration, the heading is the h2). */
export function Section({ no, title, sub, id, right }: { no?: string | number; title: string; sub?: ReactNode; id?: string; right?: ReactNode }) {
  return (
    <div className="sec">
      {no !== undefined && <span className="no" aria-hidden>{String(no).padStart(2, "0")}</span>}
      <h2 id={id}>{title}</h2>
      {sub ? <span className="sub">{sub}</span> : null}
      {right ? <span className="right">{right}</span> : null}
    </div>
  );
}

/** One labelled value. */
export function Tile({ title, value, sub, tone, text, testId }: { title: ReactNode; value: ReactNode; sub?: ReactNode; tone?: Tone; text?: boolean; testId?: string }) {
  return (
    <div className={`tile${tone ? ` tone-${tone}` : ""}`} data-testid={testId}>
      <div className="t">{title}</div>
      <div className={`v${text ? " txt" : ""}`}>{value}</div>
      {sub ? <div className="s">{sub}</div> : null}
    </div>
  );
}

/** A key number of the hero strip. */
export function Metric({ title, value, sub, tone, text, testId }: { title: ReactNode; value: ReactNode; sub?: ReactNode; tone?: Tone; text?: boolean; testId?: string }) {
  return (
    <div className={`metric${tone ? ` tone-${tone}` : ""}`} data-testid={testId}>
      <div className="t">{title}</div>
      <div className={`v${text ? " txt" : ""}`}>{value}</div>
      {sub ? <div className="s">{sub}</div> : null}
    </div>
  );
}

export function Pill({ tone, icon, children, title }: { tone?: Tone; icon?: string; children: ReactNode; title?: string }) {
  return <span className={`pill${tone ? ` tone-${tone}` : ""}`} title={title}>{icon && <span aria-hidden>{icon}</span>}{children}</span>;
}

/** Price change: arrow + sign + colour (up/down colours are only ever used for price moves). */
export function Change({ v, digits = 1 }: { v: number | null | undefined; digits?: number }) {
  if (v === null || v === undefined || !Number.isFinite(v)) return <span className="chg-flat">N/A</span>;
  const cls = v > 0 ? "chg-up" : v < 0 ? "chg-down" : "chg-flat";
  return <span className={cls}>{v > 0 ? "▲ +" : v < 0 ? "▼ " : "■ "}{(v * 100).toFixed(digits)}%</span>;
}

/** Safety / data-authority ribbon: only for things that change how far the screen can be trusted. */
export function Ribbon({ tone, cap, children, action, testId }: { tone: "warn" | "danger" | "info"; cap: string; children: ReactNode; action?: ReactNode; testId?: string }) {
  return (
    <div className={`ribbon ${tone}`} role="note" data-testid={testId}>
      <span className="cap">{tone === "danger" ? "⛔ " : tone === "warn" ? "⚠ " : "ℹ "}{cap}</span>
      <div className="msg">{children}</div>
      {action ? <div style={{ marginLeft: "auto" }}>{action}</div> : null}
    </div>
  );
}

/** Folding section. */
export function Disclosure({ title, hint, open, inset, children, testId }: { title: ReactNode; hint?: ReactNode; open?: boolean; inset?: boolean; children: ReactNode; testId?: string }) {
  return (
    <details className={`disclosure${inset ? " inset" : ""}`} open={open || undefined} data-testid={testId}>
      <summary>{title}{hint ? <span className="hint">· {hint}</span> : null}</summary>
      <div className="body">{children}</div>
    </details>
  );
}

/** A folded section for detail material (kept for older call sites). */
export function More({ title, children, hint }: { title: string; children: ReactNode; hint?: string }) {
  return <Disclosure title={title} hint={hint}>{children}</Disclosure>;
}

/** A technical term: a button that opens a short plain-language explanation — by click, Enter/Space or touch —
 * and closes with Escape or a click elsewhere (see glossary.ts). */
export function Term({ k, children }: { k: string; children?: ReactNode }) {
  const e = GLOSSARY[k];
  const [open, setOpen] = useState(false);
  const [right, setRight] = useState(false);
  const id = useId();
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (ev: Event) => { if (ref.current && !ref.current.contains(ev.target as Node)) setOpen(false); };
    const esc = (ev: KeyboardEvent) => { if (ev.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("touchstart", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("touchstart", away); document.removeEventListener("keydown", esc); };
  }, [open]);
  if (!e) return <>{children ?? k}</>;
  const toggle = () => {
    const box = ref.current?.getBoundingClientRect();
    setRight(!!box && typeof window !== "undefined" && box.left > window.innerWidth / 2);
    setOpen((o) => !o);
  };
  return (
    <span className="term" ref={ref}>
      <button type="button" aria-expanded={open} aria-controls={id} title={tip(k)} onClick={(ev) => { ev.preventDefault(); ev.stopPropagation(); toggle(); }}>
        {children ?? e.name}<span className="i" aria-hidden>ⓘ</span>
      </button>
      {open && (
        <span id={id} role="note" className="term-pop" style={right ? { left: "auto", right: 0 } : undefined}>
          <b>{e.name}</b>
          <span style={{ display: "block" }}>{e.short}</span>
          {e.why && <span className="why" style={{ display: "block" }}>왜 중요? {e.why}</span>}
          {e.dir && <span className="dir" style={{ display: "block" }}>{DIR_KO[e.dir]}</span>}
        </span>
      )}
    </span>
  );
}

export function Notice({ tone = "info", icon, children }: { tone?: "info" | "warn" | "neg"; icon?: string; children: ReactNode }) {
  return <div className={`notice ${tone}`} role={tone === "info" ? "note" : "alert"}><span aria-hidden>{icon ?? (tone === "info" ? "ℹ" : "⚠")}</span><div>{children}</div></div>;
}

/** Loading keeps the page's shape (skeleton blocks) and says what is being loaded and in which steps. */
export function Loading({ what, steps, rows = 3 }: { what?: string; steps?: string[]; rows?: number }) {
  return (
    <div className="loading" role="status" aria-live="polite">
      <div className="msg"><span className="spinner" /> {what ? `${what} 불러오는 중…` : "불러오는 중…"}</div>
      {steps && <div className="steps">{steps.join(" → ")}</div>}
      <div className="skel" style={{ height: 150, borderRadius: 22 }} />
      {Array.from({ length: rows }, (_, i) => <div key={i} className="skel" style={{ height: 68 - i * 8, opacity: 1 - i * 0.2 }} />)}
    </div>
  );
}

export function Err({ error, retry }: { error: string | null; retry?: () => void }) {
  if (!error) return null;
  return (
    <div className="err" role="alert">
      <IAlert width={16} height={16} /> <span style={{ flex: 1, minWidth: 0 }}>{error}</span>
      {retry && <button className="sm" onClick={retry}>다시 시도</button>}
    </div>
  );
}

/** A refresh failed but the last good result is still on screen: say when it was received and that it may be old. */
export function StaleData({ error, at, retry, nowMs }: { error: string | null; at: number | null; retry?: () => void; nowMs?: number }) {
  if (!error || at === null) return null;
  const iso = new Date(at).toISOString();
  return (
    <Ribbon tone="warn" cap="새로고침 실패" testId="stale-data" action={retry ? <button className="sm" onClick={retry}>다시 시도</button> : undefined}>
      최신 정보를 받지 못했습니다({error}). 아래는 <b>{stamp(iso)}</b>{nowMs ? ` (${ago(iso, nowMs)})` : ""}에 받은 이전 결과입니다 — 지금 상태와 다를 수 있습니다.
    </Ribbon>
  );
}

export function Empty({ children, hint }: { children: ReactNode; hint?: string }) {
  return <div className="empty" data-testid="empty">{children}{hint && <div className="hint">{hint}</div>}</div>;
}

export type StateKind = "collecting" | "analyzing" | "not_scanned" | "no_candidates" | "insufficient" | "stale" | "out_of_range" | "provider_failure" | "ai_unavailable" | "disconnected";

const STATE: Record<StateKind, { icon: string; tone: "info" | "warn" | "danger"; title: string; what: string; unknown: string; todo: string }> = {
  collecting: { icon: "⏳", tone: "info", title: "데이터를 모으는 중입니다", what: "무료 공급원에서 가격 이력·시가총액·재무 자료를 받고 있습니다.", unknown: "준비가 끝나기 전의 후보 목록은 시장 전체를 대표하지 않습니다.", todo: "앱을 켜 둔 채 기다리세요. 진행률은 설정 → 데이터 준비에서 볼 수 있습니다." },
  analyzing: { icon: "⟳", tone: "info", title: "분석하는 중입니다", what: "종목을 거르고 점수·가격 계획을 계산하고 있습니다.", unknown: "이번 분석에서 나올 새 후보는 끝나야 알 수 있습니다.", todo: "기다리세요. 이전 결과는 그 시각과 함께 계속 보입니다." },
  not_scanned: { icon: "◎", tone: "info", title: "아직 시장 스캔을 하지 않았습니다", what: "저장된 스캔 결과가 없습니다.", unknown: "지금 검토할 후보.", todo: "‘시장 스캔 실행’을 누르세요. 모의 데이터는 수십 초, 실데이터는 몇 분 걸릴 수 있습니다." },
  no_candidates: { icon: "○", tone: "info", title: "지금은 매수 조건을 통과한 종목이 없습니다", what: "이번 스캔에서 점수·가격·손익비·위험 한도를 모두 통과한 종목이 없습니다.", unknown: "‘살 종목이 없다’는 확신은 아닙니다. 이번 기준을 통과한 종목이 없다는 뜻입니다.", todo: "대기·관찰 종목은 ‘종목’ 화면의 후보 목록에서 볼 수 있습니다. 빈자리를 점수 상위 종목으로 채워 보여주지 않습니다." },
  insufficient: { icon: "◌", tone: "warn", title: "자료가 부족해 판단하지 않았습니다", what: "핵심 자료(현재가·가격 이력·재무제표 등)가 없거나 오래되었습니다.", unknown: "이 종목이 좋은지 나쁜지. 자료가 없다는 것이 나쁘다는 뜻은 아닙니다.", todo: "부족한 자료가 채워진 뒤 ‘분석 다시하기’를 누르세요." },
  stale: { icon: "◷", tone: "warn", title: "오래된 판단입니다", what: "분석 이후 거래일이 지나 가격이 달라졌을 수 있습니다.", unknown: "지금 가격으로도 매수 조건을 통과하는지.", todo: "‘분석 다시하기’로 현재가 기준 판단을 확인하세요." },
  out_of_range: { icon: "↕", tone: "warn", title: "가격 조건을 벗어났습니다", what: "현재가가 최대 매수가를 넘었거나, 손절 기준 아래이거나, 손익비가 부족합니다.", unknown: "이 가격에서는 매수 계획이 유효하지 않습니다.", todo: "계획한 가격대로 돌아올 때까지 기다리거나 다시 분석하세요." },
  provider_failure: { icon: "⛔", tone: "danger", title: "데이터 공급자 오류", what: "일부 공급자에게서 데이터를 받지 못했습니다.", unknown: "그 공급자가 주는 값(화면에는 ‘없음’으로 표시).", todo: "잠시 뒤 다시 시도하거나 설정 → 연결 상태에서 원인과 키 설정을 확인하세요. 모의 데이터로 대신 채우지 않습니다." },
  ai_unavailable: { icon: "◈", tone: "info", title: "AI 검토를 쓸 수 없습니다", what: "AI 공급자(LLM)가 설정되지 않았거나 응답하지 않습니다.", unknown: "뉴스 원문을 읽은 AI의 추가 위험 의견.", todo: "점수·추천·가격 계획(결정론적 분석)은 그대로 유효합니다. 설정 → AI 검토에서 무료 로컬 모델을 연결할 수 있습니다." },
  disconnected: { icon: "⚡", tone: "danger", title: "분석 서버에 연결되지 않았습니다", what: "화면이 이 컴퓨터의 MarketLens 백엔드에 닿지 못했습니다.", unknown: "지금 가격과 추천이 아직 유효한지.", todo: "잠시 뒤 다시 시도하세요. 이전에 받은 결과는 받은 시각과 함께 보입니다." },
};

/** One of the named screen states: what it is, what cannot be known right now, and what the user can do. */
export function StatePanel({ kind, title, what, todo, children, actions, testId }: { kind: StateKind; title?: string; what?: ReactNode; todo?: ReactNode; children?: ReactNode; actions?: ReactNode; testId?: string }) {
  const s = STATE[kind];
  return (
    <div className={`state tone-${s.tone}`} role={s.tone === "danger" ? "alert" : "status"} data-testid={testId ?? `state-${kind}`}>
      <div className="ico" aria-hidden>{s.icon}</div>
      <div className="title">{title ?? s.title}</div>
      <dl>
        <dt>무슨 상황</dt><dd>{what ?? s.what}</dd>
        <dt>알 수 없는 것</dt><dd>{s.unknown}</dd>
        <dt>할 수 있는 일</dt><dd>{todo ?? s.todo}</dd>
      </dl>
      {children}
      {actions ? <div className="actions">{actions}</div> : null}
    </div>
  );
}

export function Bar({ value, max = 1, color }: { value: number; max?: number; color?: string }) {
  const w = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="bar" aria-label={`${Math.round(w)}%`}>
      <div style={{ width: `${w}%`, background: color }} />
    </div>
  );
}

/** The score on its 0–100 track with the two decision thresholds marked (the score is not a probability). */
export function ScoreMeter({ score, buy = 80, small = 72, labels = true }: { score: number | null | undefined; buy?: number; small?: number; labels?: boolean }) {
  const v = typeof score === "number" ? Math.max(0, Math.min(100, score)) : null;
  return (
    <div className="meter" aria-label={v === null ? "점수 없음" : `점수 ${v.toFixed(1)} / 100 (매수 기준 ${buy}, 소량 매수 기준 ${small})`}>
      <div className="track">
        {v !== null && <div className="fill" style={{ width: `${v}%` }} />}
        <span className="tick" style={{ left: `${small}%` }} title={`소량 매수 기준 ${small}`} />
        <span className="tick" style={{ left: `${buy}%`, background: "var(--buy)" }} title={`매수 기준 ${buy}`} />
      </div>
      {labels && <div className="labels"><span style={{ left: `${small}%` }}>{small}</span><span style={{ left: `${buy}%` }}>{buy}</span></div>}
    </div>
  );
}

/** Tabs as a segmented control (state is owned by the page so it can live in the URL). */
export function Tabs<T extends string>({ value, onChange, items, label }: { value: T; onChange: (v: T) => void; items: [T, ReactNode, number?][]; label: string }) {
  return (
    <div className="seg" role="tablist" aria-label={label}>
      {items.map(([k, text, n]) => (
        <button key={k} role="tab" aria-selected={value === k} className={value === k ? "on" : ""} onClick={() => onChange(k)}>
          {text}{n !== undefined ? <span className="count">{n}</span> : null}
        </button>
      ))}
    </div>
  );
}

const KIND_KO: Record<string, string> = { FACT: "사실", CALC: "계산", VIEW: "해석", ASSUME: "가정" };
const KIND_HELP: Record<string, string> = {
  FACT: "공급자·공시에서 받은 값 그대로", CALC: "MarketLens가 받은 값으로 계산한 값", VIEW: "정해진 규칙으로 내린 해석", ASSUME: "이 숫자가 기대는 가정",
};
export function KindTag({ k }: { k: string }) {
  return <span className={`kind k-${k}`} title={KIND_HELP[k] ?? ""}>{KIND_KO[k] ?? k}</span>;
}

export interface BriefItem { kind: string; text: string; why?: string | null; tone?: string; source?: string | null; when?: string | null; evidence?: string[]; label?: string | null }

/** One typed statement of the analysis brief: kind tag, the sentence, why it matters, where it comes from. */
export function Stmt({ it, index }: { it: BriefItem; index?: Map<string, Evidence> }) {
  return (
    <div className={`stmt${it.tone && it.tone !== "neutral" ? ` tone-${it.tone}` : ""}`}>
      <KindTag k={it.kind} />
      <div className="body">
        {it.label ? <span className="lab">{it.label}</span> : null}{it.text}
        {index && it.evidence?.length ? <EvidenceChips ids={it.evidence} index={index} /> : null}
      </div>
      {it.why ? <div className="why">{it.why}</div> : null}
      {(it.source || it.when) ? <div className="meta">{it.source ? <span>출처 {it.source}</span> : null}{it.when ? <span>기준 {it.when.includes("T") ? stamp(it.when) : it.when}</span> : null}</div> : null}
    </div>
  );
}

/** Per-type data freshness (why the data is or is not usable). */
export function FreshnessTable({ checks }: { checks: FreshnessCheck[] }) {
  if (!checks.length) return <Empty>신선도 정보 없음</Empty>;
  return (
    <div className="scroll">
      <table>
        <thead><tr><th>데이터</th><th>상태</th><th>기준 시점</th><th>설명</th></tr></thead>
        <tbody>
          {checks.map((c) => (
            <tr key={c.data_type}>
              <td>{DATA_TYPE_KO[c.data_type] ?? c.data_type}</td>
              <td><Quality q={c.quality} /></td>
              <td>{c.effective ? (c.effective.includes("T") ? stamp(c.effective) : c.effective) : "N/A"}{c.published ? <div className="muted">공개 {c.published.includes("T") ? stamp(c.published) : c.published}</div> : null}</td>
              <td style={{ whiteSpace: "normal" }}>{c.reason_ko}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The evidence behind a sentence: a small "근거 n" button opening the source values (label, value, source, time). */
export function EvidenceChips({ ids, index }: { ids: string[] | undefined; index: Map<string, Evidence> }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (ev: Event) => { if (ref.current && !ref.current.contains(ev.target as Node)) setOpen(false); };
    const esc = (ev: KeyboardEvent) => { if (ev.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);
  const found = (ids ?? []).map((id) => index.get(id)).filter((e): e is Evidence => !!e);
  if (!found.length) return null;
  return (
    <span className="ev-wrap" ref={ref}>
      <button type="button" className="ev-btn" aria-expanded={open} onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen((o) => !o); }} title="이 문장의 근거 값과 출처">근거 {found.length}</button>
      {open && (
        <span className="ev-pop" role="note" style={{ display: "block" }}>
          {found.map((e) => (
            <span key={e.evidence_id} className="ev" style={{ display: "block" }}>
              <b>{e.label}</b>: {typeof e.value === "number" ? fmtEv(e.value, e.unit) : String(e.value ?? "N/A")}
              <span className="src" style={{ display: "block" }}>출처 {e.source}{e.source_ts ? ` · ${stamp(e.source_ts)}` : ""} · {QUALITY_INFO[e.quality]?.label ?? e.quality}</span>
            </span>
          ))}
        </span>
      )}
    </span>
  );
}

function fmtEv(v: number, unit?: string | null): string {
  if (unit === "USD") return price(v);
  if (unit === "fraction" || unit === "pct") return `${(v * 100).toFixed(2)}%`;
  return Math.abs(v) >= 1000 ? v.toLocaleString("en-US", { maximumFractionDigits: 0 }) : String(Number(v.toFixed(4)));
}

export function LineChart({ values, height = 120, color = "var(--accent)" }: { values: number[]; height?: number; color?: string }) {
  if (values.length < 2) return <Empty>데이터가 충분하지 않음</Empty>;
  const w = 600;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${height - ((v - min) / span) * (height - 10) - 5}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${height}`} width="100%" height={height} preserveAspectRatio="none" role="img" aria-label="line chart">
      <polygon points={`0,${height} ${pts} ${w},${height}`} fill={color} opacity="0.08" />
      <polyline fill="none" stroke={color} strokeWidth="2" points={pts} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function Stance({ s }: { s: string }) {
  const cls = s === "positive" ? "ok" : s === "negative" ? "danger" : "muted";
  const icon = s === "positive" ? "▲ " : s === "negative" ? "▼ " : "■ ";
  return <span className={cls}>{icon}{STANCE_KO[s] ?? s}</span>;
}

/** Data-source banner: MOCK and LIVE must never be confused. */
export function ModeBanner({ mode }: { mode: string }) {
  if (mode === "MOCK") {
    return <div className="banner mock" data-testid="banner-mock"><IAlert width={15} height={15} /> 모의 데이터(MOCK) — 가상의 가격·재무 데이터입니다. 실제 시장 데이터가 아니며 투자 판단에 사용할 수 없습니다.</div>;
  }
  return <div className="banner live" data-testid="banner-live"><IInfo width={15} height={15} /> 실데이터(LIVE) — 없는 데이터는 ‘없음(MISSING)’으로 표시하며 다른 값으로 대체하지 않습니다. 모든 가격은 미국 달러(USD) 기준입니다.</div>;
}

/** Merge plan levels that would sit on top of each other into one label, and alternate label rows for close
 * neighbours. */
export function ladderMarks(marks: [string, number, string][], lo: number, hi: number, minGap = 0.22): { label: string; pos: number; cls: string; row: number }[] {
  const span = hi - lo || 1;
  const sorted = [...marks].sort((a, b) => a[1] - b[1]);
  const out: { label: string; pos: number; cls: string; row: number }[] = [];
  for (const [l, v, c] of sorted) {
    const pos = (v - lo) / span;
    const last = out[out.length - 1];
    if (last && Math.abs(pos - last.pos) < 0.02) { last.label = `${last.label}·${l}`; continue; }
    out.push({ label: l, pos, cls: c, row: 0 });
  }
  out.forEach((m, i) => { const prev = out[i - 1]; if (prev && m.pos - prev.pos < minGap) m.row = 1 - prev.row; });
  return out;
}

/** Price ladder: where the current price sits against stop · buy zone (ideal → max buy) · targets (backend values). */
export function PriceLadder({ now, stop, ideal, maxBuy, t1, t2 }: { now: number; stop: number; ideal: number; maxBuy: number; t1: number; t2: number }) {
  const lo = Math.min(stop, now) * 0.995;
  const hi = Math.max(t2, now) * 1.005;
  const marks = ladderMarks([["손절", stop, "neg"], ["이상적", ideal, ""], ["최대 매수", maxBuy, "warn"], ["1차 목표", t1, "pos"], ["2차 목표", t2, "pos"]], lo, hi);
  const at = (v: number) => Math.min(100, Math.max(0, ((v - lo) / (hi - lo)) * 100));
  const zLo = Math.min(ideal, maxBuy), zHi = Math.max(ideal, maxBuy);
  return (
    <div className="ladder" role="img" aria-label={`가격 계획 막대: 현재 ${price(now)}, 손절 ${price(stop)}, 매수 구간 ${price(zLo)}~${price(zHi)}, 1차 목표 ${price(t1)}`}>
      <div className="track" />
      <div className="zone" style={{ left: `${at(zLo)}%`, width: `${Math.max(0.8, at(zHi) - at(zLo))}%` }} />
      {marks.map((m) => <div key={m.label} className={`mark ${m.cls}${m.row ? " up" : ""}`} style={{ left: `${Math.min(100, Math.max(0, m.pos * 100))}%` }}><span>{m.label}</span><i /></div>)}
      <div className="mark now" style={{ left: `${at(now)}%` }}><i /><span>현재 {price(now)}</span></div>
    </div>
  );
}

export const SERIES = ["#a494f8", "#72b8ff", "#63d5ea", "#f3c96a", "#f1a189", "#c6b7ff", "#5fe0bd", "#a9afc0"];

/** Weights as one bar with a legend (reads faster than a donut and never distorts small slices). */
export function AllocationBar({ parts }: { parts: [string, number][] }) {
  const total = parts.reduce((a, [, v]) => a + Math.max(0, v), 0) || 1;
  return (
    <div className="stack" style={{ gap: 14 }}>
      <div className="alloc" role="img" aria-label={parts.map(([k, v]) => `${k} ${((v / total) * 100).toFixed(1)}%`).join(", ")}>
        {parts.map(([k, v], i) => <span key={k} style={{ width: `${(Math.max(0, v) / total) * 100}%`, background: SERIES[i % SERIES.length] }} title={`${k} ${((v / total) * 100).toFixed(1)}%`} />)}
      </div>
      <div className="legend-list">
        {parts.map(([k, v], i) => <div className="li" key={k}><span className="sw" style={{ background: SERIES[i % SERIES.length] }} /><span>{k}</span><b className="num">{((v / total) * 100).toFixed(1)}%</b></div>)}
      </div>
    </div>
  );
}

/** Kept for older call sites: the allocation bar under its former name. */
export function Donut({ parts }: { parts: [string, number][]; size?: number }) {
  return <AllocationBar parts={parts} />;
}
