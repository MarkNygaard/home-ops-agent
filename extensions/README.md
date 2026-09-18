# pi extensions

One extension. It gives models on the `pi` harness the agent's own tools.

## Why a bridge, when this file used to argue against one

It did, and the argument was right at the time: a bridge "adds a hop and a
second failure mode to buy nothing", when the alternative was a thirty-line
SearXNG tool written natively in TypeScript. Two things changed that, and both
only became clear by building it the other way first.

**About a third of the tools cannot be written here at all.** The GitHub tools,
ntfy and `code_fix` carry credentials. pi has a `bash` tool, so a credential in
this process is a credential the model can read — and then use directly, past
whatever guardrail the tool was supposed to enforce. That is not hypothetical:
pi was launched with the entire environment until 0.18.2, which made
`workspace_commit`'s path and branch checks decorative until it was found.

**The rest would exist twice, forever.** The Claude Code backend still needs the
Python implementations, so a native port doubles every tool, every guardrail —
`PROTECTED_NAMESPACES`, `ALLOWED_COMMIT_PATHS` — and every test. And they drift:
the native Kubernetes tools and the Python ones disagreed about how a Flux
`Ready` condition is reported within a day of both existing.

So there is one implementation, in Python, and this reaches it. The hop is a
Unix socket inside the same pod.

What it buys beyond parity: a new skill works on both backends with no extra
work, and the Skills page in Settings finally applies to `pi`. Before this those
toggles did nothing for a GPT chat, which made them quietly misleading.

## bridge.ts

Registers every tool the agent hands it, reading the manifest over the socket
named by `HOMEOPS_TOOLS_SOCKET` and authenticating with `HOMEOPS_TOOLS_TOKEN`.

`ExtensionFactory` is `(pi) => void | Promise<void>`, so pi awaits the factory
and the tools are registered before the model is asked anything. Without that
the manifest would have to be read synchronously from a file.

Tool schemas pass through untouched. The agent's `input_schema` is JSON Schema
and TypeBox schemas are JSON Schema, so the schema a model sees is the one the
handler is written against, rather than a copy that can disagree with it.

**Which tools arrive is not this file's decision.** Enabled skills, the tools
withheld from the PR agent, and the workspace tools when a run has a checkout
are all decided by the agent, in the same place, for both backends. The bridge
serves exactly what it is handed.

`workspace_commit` is no longer special. It is a `ToolDefinition` like any
other, so it arrives like any other — the earlier single-purpose bridge existed
only to carry it.

With no socket in the environment the extension registers nothing and says so.
A tool that is always present and always fails is worse than one that is absent,
because the model keeps choosing it and reports the failure as though the
cluster were down.

## Loading

Extensions are passed explicitly with `-e`, and discovery is disabled with
`-ne`. Nothing on the filesystem can introduce a tool the image did not ship,
and the local `~/.pi` of whoever built the image cannot leak in.

`_extension_args` globs `*.ts` here and passes each one, so a shared helper
dropped in this directory is not a neighbour of the extensions — it is loaded
*as* one, and fails on every run.

A failed extension is loud: pi prints `Error: Failed to load extension <path>`
and continues. A working one prints nothing, so silence on startup is the
success signal.

## What the model still gets natively

pi's own built-ins — `read`, `bash`, `edit`, `write`, `grep`, `find`, `ls`.
Those are the harness's, not the agent's, and they are why a GPT model can work
in a checkout at all.
