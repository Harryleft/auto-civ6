"use client";

import { useQuery } from "convex/react";
import {
  Activity,
  BrainCircuit,
  CircleAlert,
  ClipboardList,
  Target,
} from "lucide-react";
import { api } from "../../convex/_generated/api";
import { CONVEX_MODE } from "./convex-provider";

type BeliefEntity = {
  _id: string;
  entityId: string;
  entityType: string;
  status: "active" | "resolved" | "archived" | "deleted";
  lastTurn: number;
  data: unknown;
};

type BeliefMetric = {
  _id: string;
  metric: string;
  value: number;
  turn: number;
};

type Dashboard = {
  beliefs: BeliefEntity[];
  predictions: BeliefEntity[];
  plans: BeliefEntity[];
  surprises: BeliefEntity[];
  contradictions: BeliefEntity[];
  metrics: BeliefMetric[];
};

/**
 * Convex is optional for local diary playback. Keep the page usable in that
 * mode while showing the panel whenever the real-time store is configured.
 */
export function BeliefEnginePanel({ gameId, turn }: { gameId: string; turn?: number }) {
  if (!CONVEX_MODE) return null;
  return <BeliefEnginePanelConvex gameId={gameId} turn={turn} />;
}

function BeliefEnginePanelConvex({ gameId, turn }: { gameId: string; turn?: number }) {
  const dashboard = useQuery(
    api.belief.getGameBeliefDashboard,
    { gameId, ...(turn !== undefined ? { turn } : {}) },
  ) as Dashboard | undefined;

  if (dashboard === undefined) {
    return <div className="mx-auto mb-4 h-24 w-full max-w-2xl animate-pulse rounded-sm border border-marble-300/50 bg-marble-50" />;
  }

  const itemCount = dashboard.beliefs.length + dashboard.predictions.length +
    dashboard.plans.length + dashboard.surprises.length + dashboard.contradictions.length;

  return (
    <section className="mx-auto mb-4 w-full max-w-2xl rounded-sm border border-marble-300/50 bg-marble-50" aria-label="Belief Engine">
      <div className="flex items-start gap-3 border-b border-marble-300/30 px-3 py-2.5">
        <div className="mt-0.5 rounded-sm bg-gold/15 p-1.5 text-gold-dark">
          <BrainCircuit className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="font-display text-sm font-bold uppercase tracking-[0.08em] text-marble-800">Belief Engine</h2>
          <p className="mt-0.5 text-xs text-marble-500">
            {itemCount === 0
              ? "No belief events have been written for this game yet."
              : "Current world-model projection; events remain available for audit."}
          </p>
        </div>
        {turn !== undefined && dashboard.metrics.length > 0 && (
          <span className="font-mono text-[11px] tabular-nums text-marble-400">metrics ≤ T{turn}</span>
        )}
      </div>

      {itemCount > 0 && (
        <div className="grid gap-3 px-3 py-3 md:grid-cols-2">
          <EntityGroup icon={BrainCircuit} title="Active beliefs" entities={dashboard.beliefs} accent="text-gold-dark" />
          <EntityGroup icon={Target} title="Predictions" entities={dashboard.predictions} accent="text-patina" />
          <EntityGroup icon={ClipboardList} title="Plans" entities={dashboard.plans} accent="text-marine" />
          <EntityGroup icon={CircleAlert} title="Surprises & contradictions" entities={[...dashboard.surprises, ...dashboard.contradictions]} accent="text-terracotta" />
        </div>
      )}

      {dashboard.metrics.length > 0 && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-marble-300/30 px-3 py-2 text-xs text-marble-500">
          <Activity className="h-3.5 w-3.5 text-marble-400" />
          {dashboard.metrics.slice(0, 5).map((metric) => (
            <span key={metric._id} className="font-mono tabular-nums">
              {humanize(metric.metric)} <strong className="font-semibold text-marble-700">{formatMetric(metric.value)}</strong>
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

function EntityGroup({
  icon: Icon,
  title,
  entities,
  accent,
}: {
  icon: typeof BrainCircuit;
  title: string;
  entities: BeliefEntity[];
  accent: string;
}) {
  if (entities.length === 0) return null;

  return (
    <div>
      <div className={`mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.07em] ${accent}`}>
        <Icon className="h-3.5 w-3.5" />
        {title} <span className="font-mono font-normal opacity-70">{entities.length}</span>
      </div>
      <ul className="space-y-1.5">
        {entities.slice(0, 3).map((entity) => <EntityRow key={entity._id} entity={entity} />)}
        {entities.length > 3 && <li className="text-xs text-marble-400">+{entities.length - 3} more</li>}
      </ul>
    </div>
  );
}

function EntityRow({ entity }: { entity: BeliefEntity }) {
  const data = asRecord(entity.data);
  const title = text(data.statement) ?? text(data.title) ?? text(data.goal) ??
    text(data.description) ?? text(data.summary) ?? humanize(entity.entityId);
  const probability = number(data.probability);
  const confidence = number(data.confidence);
  const deadline = number(data.deadline_turn) ?? number(data.deadlineTurn) ?? number(data.review_turn) ?? number(data.reviewTurn);

  return (
    <li className="rounded-sm border border-marble-300/30 bg-white/30 px-2 py-1.5">
      <p className="line-clamp-2 text-xs leading-snug text-marble-700">{title}</p>
      <div className="mt-1 flex flex-wrap gap-x-2 font-mono text-[10px] tabular-nums text-marble-400">
        {probability !== undefined && <span>P {Math.round(probability * 100)}%</span>}
        {confidence !== undefined && <span>C {Math.round(confidence * 100)}%</span>}
        {deadline !== undefined && <span>T{deadline}</span>}
        {entity.status !== "active" && <span>{entity.status}</span>}
      </div>
    </li>
  );
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function number(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function humanize(value: string): string {
  return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatMetric(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}
