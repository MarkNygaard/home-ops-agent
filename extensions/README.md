# pi extensions

The cluster tools the agent exposes to models running on the `pi` harness.

## Why native extensions rather than a bridge

An earlier sketch had a single generic extension proxying every tool call back
into the Python process over localhost. That treats pi as an executor, which it
is not — `ExtensionAPI` is the agent runtime: `registerTool`, `registerCommand`,
`registerProvider`, plus hooks over the whole loop (`before_agent_start`,
`context`, `tool_call`, `before_provider_request`). A bridge would add a hop and
a second failure mode to buy nothing.

Tools are therefore written here, in TypeScript, and run in pi's process.

## Loading

Extensions are passed explicitly with `-e`, and discovery is disabled with
`-ne`. Nothing on the filesystem can introduce a tool the image did not ship,
and the local `~/.pi` of whoever built the image cannot leak in.

A failed extension is loud: pi prints `Error: Failed to load extension <path>`
and continues. A working one prints nothing, so silence on startup is the
success signal.

## searxng.ts

`web_search`, backed by the self-hosted SearXNG in `productivity`.

Reached over cluster DNS rather than `search.mnygaard.io`: the agent runs in
this cluster, so the public hostname would leave through the gateway and come
back for no reason — and would fail entirely whenever external DNS is down,
which is exactly the kind of incident the agent is most likely to be asked
about.

Verified end to end against `gpt-6-astra`:

    TOOLCALL   web_search {"query": "external-dns 1.22.0 release notes", "limit": 5}
    ASSISTANT  Release external-dns-helm-chart-1.22.0 · kubernetes-sigs/external-dns

Note that SearXNG's usefulness depends on its own version — engine scrapers
break upstream constantly. A stale image returns zero results for every query
while looking healthy.
