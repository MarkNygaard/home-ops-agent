import { connect } from "node:net";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

// `workspace_commit` — the single write path out of a checkout.
//
// This is the one tool that does not do its own work. Every other tool here is
// native TypeScript, because pi is the agent runtime and a proxy would add a
// hop for nothing. Committing is different, and the difference is the
// credential: pushing needs the GitHub token, this process has a `bash` tool,
// and a token the model can read is a token the model can `git push` with —
// walking straight past the path and branch guardrails rather than through
// them.
//
// So the agent keeps the token and this asks it to commit. What arrives here is
// a socket path and a single-run token that authorises exactly one action:
// "commit this workspace". The model can read that token out of the
// environment, and it does not matter — it buys the model nothing it does not
// already have, which is the whole point of the split.
//
// Registered only when both variables are present. Without a checkout there is
// nothing to commit, and a tool that exists but always fails is worse than one
// that is absent.

const SOCKET = process.env.HOMEOPS_WORKSPACE_SOCKET;
const TOKEN = process.env.HOMEOPS_WORKSPACE_TOKEN;

// The push is a network round trip to GitHub behind a git clone, so this is
// generous. It exists so a hung bridge fails the tool rather than the run.
const TIMEOUT_MS = 120_000;

interface CommitResult {
  status?: string;
  error?: string;
  message?: string;
  branch?: string;
  sha?: string;
  files?: string[];
  blocked_paths?: string[];
}

function requestCommit(message: string, signal?: AbortSignal): Promise<CommitResult> {
  return new Promise((resolve, reject) => {
    const socket = connect(SOCKET as string);
    let response = "";
    let settled = false;

    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      fn();
    };

    socket.setTimeout(TIMEOUT_MS, () =>
      finish(() => reject(new Error("timed out waiting for the agent to commit")))
    );
    socket.on("error", (err) => finish(() => reject(err)));
    signal?.addEventListener("abort", () => finish(() => reject(new Error("aborted"))), {
      once: true,
    });

    socket.on("connect", () => {
      // One JSON line in, one JSON line out. No HTTP: the peer is a local
      // process on a private socket, and framing on newlines is the whole
      // protocol.
      socket.write(`${JSON.stringify({ token: TOKEN, action: "commit", message })}\n`);
    });
    socket.setEncoding("utf8");
    socket.on("data", (chunk: string) => {
      response += chunk;
      const newline = response.indexOf("\n");
      if (newline === -1) return;
      const line = response.slice(0, newline);
      finish(() => {
        try {
          resolve(JSON.parse(line));
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

function render(result: CommitResult): string {
  if (result.status === "ok") {
    const files = result.files ?? [];
    return [
      `Committed and pushed to ${result.branch}.`,
      `sha: ${result.sha}`,
      `files (${files.length}):`,
      ...files.map((f) => `  ${f}`),
    ].join("\n");
  }
  if (result.status === "no_changes") {
    return result.message ?? "Nothing to commit — no files were modified.";
  }
  if (result.status === "blocked") {
    // Reported rather than thrown, and with the offending paths named, because
    // this is a recoverable mistake: the model can move or revert the file and
    // call again within the same run.
    const paths = result.blocked_paths ?? [];
    return [
      result.error ?? "Commit blocked.",
      ...(paths.length ? ["Rejected paths:", ...paths.map((p) => `  ${p}`)] : []),
      "The change has been unstaged. Revert those edits, then commit again.",
    ].join("\n");
  }
  return result.error ?? `Commit failed: ${JSON.stringify(result)}`;
}

export default function (pi: ExtensionAPI) {
  if (!SOCKET || !TOKEN) {
    // Silent, unlike the other extensions. There is no checkout on most runs —
    // a chat about cluster state has nothing to commit — so saying so on stderr
    // every time would be noise rather than a signal.
    return;
  }

  pi.registerTool({
    name: "workspace_commit",
    label: "Commit workspace",
    description:
      "Commit every change you have made in the working directory and push it to " +
      "the branch this checkout is on. Only files under the allowed paths may be " +
      "committed; a commit touching anything else is rejected and unstaged, and the " +
      "rejected paths are named so you can correct them. Call this once, after you " +
      "have made and validated every edit.",
    promptSnippet: "workspace_commit — commit and push your edits to the PR branch",
    parameters: Type.Object({
      message: Type.String({ description: "Commit message describing the fix" }),
    }),
    async execute(_id, params, signal) {
      const message = (params.message ?? "").trim();
      if (!message) {
        return { content: [{ type: "text" as const, text: "A commit message is required." }] };
      }
      try {
        return { content: [{ type: "text" as const, text: render(await requestCommit(message, signal)) }] };
      } catch (err) {
        const detail = err instanceof Error ? err.message : String(err);
        return { content: [{ type: "text" as const, text: `Commit failed: ${detail}` }] };
      }
    },
  });
}
