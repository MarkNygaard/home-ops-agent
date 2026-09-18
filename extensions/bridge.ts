import { connect } from "node:net";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

// Every tool the agent has, registered from the agent's own registry.
//
// This replaces three hand-written extensions. The reasoning is in
// `agent/tool_bridge.py` and in the README, but the short version: about a
// third of the tools carry credentials and cannot be written here at all, since
// this process has a `bash` tool; and the rest would exist twice forever,
// because the Claude Code backend still needs the Python implementations. Two
// copies drift — the native Kubernetes tools and the Python ones disagreed
// about how a Flux `Ready` condition is reported within a day of both existing.
//
// So there is one implementation and this reaches it. Which tools arrive is the
// agent's decision, made in the same place for both backends: enabled skills,
// the tools withheld from the PR agent, and the workspace tools when a run has
// a checkout. `workspace_commit` is no longer special — it comes through here
// like everything else.

const SOCKET = process.env.HOMEOPS_TOOLS_SOCKET;
const TOKEN = process.env.HOMEOPS_TOOLS_TOKEN;

// A tool can reconcile a Kustomization, wait on the GitHub API, or run a whole
// nested code fix. The timeout exists so a hung bridge fails one call rather
// than the run.
const TIMEOUT_MS = 600_000;

interface Manifest {
  tools?: Array<{ name: string; description: string; parameters: unknown }>;
  error?: string;
}

/** One request, one reply, one connection. The peer is a local process on a
 *  private socket, so newline framing is the whole protocol — no HTTP. */
function request<T>(payload: Record<string, unknown>, signal?: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const socket = connect(SOCKET as string);
    let buffer = "";
    let settled = false;

    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      fn();
    };

    socket.setTimeout(TIMEOUT_MS, () =>
      finish(() => reject(new Error("timed out waiting for the agent")))
    );
    socket.on("error", (err) => finish(() => reject(err)));
    signal?.addEventListener("abort", () => finish(() => reject(new Error("aborted"))), {
      once: true,
    });

    socket.on("connect", () => socket.write(`${JSON.stringify({ token: TOKEN, ...payload })}\n`));
    socket.setEncoding("utf8");
    socket.on("data", (chunk: string) => {
      buffer += chunk;
      const newline = buffer.indexOf("\n");
      if (newline === -1) return;
      const line = buffer.slice(0, newline);
      finish(() => {
        try {
          resolve(JSON.parse(line) as T);
        } catch {
          reject(new Error(`the agent replied with something that was not JSON: ${line.slice(0, 200)}`));
        }
      });
    });
    socket.on("end", () =>
      finish(() => reject(new Error("the agent closed the connection without replying")))
    );
  });
}

// `ExtensionFactory` is `(pi) => void | Promise<void>`, so pi awaits this and
// the tools are registered before the model is asked anything. Without that
// guarantee the manifest would have to be read synchronously from a file.
export default async function (pi: ExtensionAPI) {
  if (!SOCKET || !TOKEN) {
    // Nothing registered rather than tools that always fail. Outside a run
    // started by the agent there is no registry to reach, and a model that is
    // offered a tool it cannot use reports the failure as though the cluster
    // were down.
    console.error("[bridge] no tool socket in the environment; the agent's tools are unavailable.");
    return;
  }

  let manifest: Manifest;
  try {
    manifest = await request<Manifest>({ action: "list" });
  } catch (err) {
    console.error(`[bridge] could not read the tool list: ${err instanceof Error ? err.message : err}`);
    return;
  }
  if (manifest.error || !manifest.tools) {
    console.error(`[bridge] the agent refused the tool list: ${manifest.error ?? "no tools"}`);
    return;
  }

  for (const tool of manifest.tools) {
    pi.registerTool({
      name: tool.name,
      label: tool.name,
      description: tool.description,
      // Passed through untouched. The agent's `input_schema` is JSON Schema and
      // TypeBox schemas are JSON Schema, so the schema the model is shown is
      // the one the handler is written against — not a copy that can disagree.
      parameters: tool.parameters as never,
      promptSnippet: tool.description.split(". ")[0],
      async execute(_toolCallId, params, signal) {
        try {
          const reply = await request<{ result?: string; error?: string }>(
            { action: "call", tool: tool.name, args: params ?? {} },
            signal
          );
          if (reply.error) {
            return { content: [{ type: "text" as const, text: `error: ${reply.error}` }] };
          }
          return { content: [{ type: "text" as const, text: reply.result ?? "" }] };
        } catch (err) {
          const detail = err instanceof Error ? err.message : String(err);
          return { content: [{ type: "text" as const, text: `error: ${detail}` }] };
        }
      },
    });
  }
}
