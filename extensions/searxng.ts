import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

// Cluster DNS, not search.mnygaard.io. The agent runs in this cluster, so the
// public hostname would leave through the gateway and come back for no reason,
// and would break entirely whenever external DNS is down -- which is exactly
// the kind of incident the agent is most likely to be asked about.
const SEARXNG =
  process.env.SEARXNG_URL ?? "http://searxng.productivity.svc.cluster.local:8080";

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "web_search",
    label: "Web search",
    description:
      "Search the web via the self-hosted SearXNG instance. Use for changelogs, " +
      "release notes, upstream issues and whether a regression is known.",
    promptSnippet: "web_search — search the web through SearXNG",
    parameters: Type.Object({
      query: Type.String({ description: "Search query" }),
      limit: Type.Optional(Type.Number({ description: "Max results (default 8)" })),
    }),
    async execute(_toolCallId, params, signal) {
      const limit = params.limit ?? 8;
      const url = `${SEARXNG}/search?q=${encodeURIComponent(params.query)}&format=json`;
      const res = await fetch(url, { signal });
      if (!res.ok) {
        return { content: [{ type: "text", text: `SearXNG returned HTTP ${res.status}` }] };
      }
      const data: any = await res.json();
      const results = (data.results ?? []).slice(0, limit);
      if (results.length === 0) {
        const dead = (data.unresponsive_engines ?? []).map((e: any[]) => e[0]).join(", ");
        return {
          content: [{ type: "text", text: `No results. Unresponsive engines: ${dead || "none"}` }],
        };
      }
      const text = results
        .map((r: any, i: number) => `${i + 1}. ${r.title}\n   ${r.url}\n   ${(r.content ?? "").slice(0, 200)}`)
        .join("\n\n");
      return {
        content: [{ type: "text", text }],
        details: { count: results.length },
      };
    },
  });
}
