import { query } from "./_generated/server";
import { v } from "convex/values";

const ACTIVE_STATUSES = new Set(["active"]);
const VISIBLE_STATUSES = new Set(["active", "resolved", "archived"]);

/**
 * Small, read-optimized projection for the game detail screen. The full event
 * trace stays available in beliefEvents; this query intentionally returns only
 * current entities and the latest value of each named metric up to `turn`.
 */
export const getGameBeliefDashboard = query({
  args: { gameId: v.string(), turn: v.optional(v.number()) },
  handler: async (ctx, { gameId, turn }) => {
    const [entities, metrics] = await Promise.all([
      ctx.db
        .query("beliefEntities")
        .withIndex("by_gameId", (q) => q.eq("gameId", gameId))
        .collect(),
      ctx.db
        .query("beliefMetrics")
        .withIndex("by_game_turn", (q) => q.eq("gameId", gameId))
        .collect(),
    ]);

    const current = entities.filter((entity) => VISIBLE_STATUSES.has(entity.status));
    const forType = (types: string[], activeOnly = false) =>
      current
        .filter((entity) => types.includes(entity.entityType))
        .filter((entity) => !activeOnly || ACTIVE_STATUSES.has(entity.status))
        .sort((a, b) => b.lastTurn - a.lastTurn || b.updatedAt - a.updatedAt);

    const maxTurn = turn ?? Number.POSITIVE_INFINITY;
    const latestMetrics = new Map<string, (typeof metrics)[number]>();
    for (const metric of metrics) {
      if (metric.turn > maxTurn) continue;
      const previous = latestMetrics.get(metric.metric);
      if (!previous || metric.turn > previous.turn || metric.updatedAt > previous.updatedAt) {
        latestMetrics.set(metric.metric, metric);
      }
    }

    return {
      beliefs: forType(["belief", "hypothesis"], true),
      predictions: forType(["prediction"], false),
      plans: forType(["plan"], true),
      surprises: forType(["surprise"], false),
      contradictions: forType(["contradiction", "belief_gap"], false),
      metrics: [...latestMetrics.values()].sort((a, b) => a.metric.localeCompare(b.metric)),
    };
  },
});
