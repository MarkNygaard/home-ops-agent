import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

// No default. An earlier version fell back to a service name from one specific
// cluster, which for anyone else is a tool that is always present and always
// fails -- worse than not having it, because the model keeps choosing it and
// reporting the failure as though the web were down.
//
// Set SEARXNG_URL to a reachable instance. Prefer the in-cluster service over a
// public hostname: the agent runs in the cluster, so a public name leaves
// through the gateway and comes back for nothing, and stops working entirely
// when external DNS does -- exactly the incident the agent is likely to be
// asked about.
const SEARXNG = process.env.SEARXNG_URL;

export default function (pi: ExtensionAPI) {
  if (!SEARXNG) {
    // Registering nothing is deliberate. The tool simply does not exist, so the
    // model never offers a web search it cannot perform.
    console.error(
      "[searxng] SEARXNG_URL is not set; web_search is unavailable. " +
        "Point it at a SearXNG instance with the JSON format enabled."
    );
    return;
  }

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
