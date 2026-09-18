'use client';

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSettings } from '@/hooks/use-settings';
import { fetchStatus } from '@/lib/api';
import {
  ReactFlow,
  type Node,
  type Edge,
  Position,
  Handle,
  type NodeProps,
  MarkerType,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import Dagre from '@dagrejs/dagre';
import {
  IconRobot,
  IconGitPullRequest,
  IconFileSearch,
  IconFileText,
  IconNotes,
  IconReport,
  IconCircleCheck,
  IconCode,
  IconGitBranch,
  IconSend,
  IconAlertCircle,
  IconAlertTriangle,
  IconBox,
  IconFileAnalytics,
  IconChartLine,
  IconBolt,
  IconCheck,
  IconBell,
  IconPlayerSkipForward,
  IconMessageChatbot,
  IconSearch,
  IconTerminal2,
  IconMessage,
  IconEye,
} from '@tabler/icons-react';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

/* ── Custom node components ──────────────────────────────── */

type AgentNodeData = { label: string };
type StepNodeData = {
  label: string;
  icon: string;
  subagent?: boolean;
  decision?: boolean;
  /** Shown on hover. Where a node stands for several underlying steps, this is
   *  where those went — the diagram is short of width, not of detail. */
  hint?: string;
  /** The progress step this node stands for. The backend reports steps by what
   *  happened; the mapping to a circle lives here, because this is the thing
   *  that knows about circles. */
  step?: string;
  /** Set during a live run when `step` matches where it has got to. */
  active?: boolean;
};

const ICONS: Record<string, typeof IconRobot> = {
  IconGitPullRequest,
  IconFileSearch,
  IconFileText,
  IconNotes,
  IconReport,
  IconCircleCheck,
  IconCode,
  IconGitBranch,
  IconSend,
  IconAlertCircle,
  IconAlertTriangle,
  IconBox,
  IconFileAnalytics,
  IconChartLine,
  IconBolt,
  IconCheck,
  IconBell,
  IconPlayerSkipForward,
  IconEye,
  IconMessageChatbot,
  IconSearch,
  IconTerminal2,
  IconMessage,
};

// Boxes dagre lays out. These must match what is actually rendered, including
// the caption under each circle -- the height used to be the circle alone, so
// dagre packed branches as though the labels were not there.
//
// Widths are uniform. They used to vary per node (decision nodes declared 140
// wide "for edge labels", continuation nodes 50 "to pack tighter"), which made
// the horizontal gaps different for every pair of neighbours. Edge labels get
// their room from ranksep instead, which applies evenly.
const NODE_W = 96; // matches the caption's max-w-24
const AGENT_H = 128; // size-24 circle + gap + caption
const STEP_H = 104; // size-18 circle + gap + caption

function AgentNode({ data }: NodeProps<Node<AgentNodeData>>) {
  return (
    <div className="relative flex flex-col items-center gap-3">
      <div
        className="flex size-24 items-center justify-center rounded-full"
        style={{
          background:
            'linear-gradient(135deg, var(--accent-orange-light), var(--accent-orange))',
          padding: '2px',
        }}
      >
        <div className="flex size-full items-center justify-center rounded-full bg-background">
          <IconRobot className="size-11 text-accent-orange" />
        </div>
      </div>
      <span className="whitespace-nowrap text-xs font-semibold text-foreground">
        {data.label}
      </span>
      <Handle
        type="source"
        position={Position.Right}
        style={{ top: '48px' }}
        className="bg-transparent! border-0! w-0! h-0!"
      />
    </div>
  );
}

function StepNode({ data }: NodeProps<Node<StepNodeData>>) {
  const Icon = ICONS[data.icon] ?? IconReport;
  const subagent = data.subagent ?? false;
  // Every step is the same size. Steps used to come in two, and which one a
  // node got depended only on whether it was declared in the main chain or in a
  // branch -- not on anything true of the workflow. The distinctions that do
  // mean something are drawn instead: the entry agent is larger, and a step
  // that invokes another agent is filled orange.
  const circleSize = 'size-18';
  const iconSize = 'size-8';
  const handleTop = '36px';
  // A node the flow forks at. The flag already existed but only fed a width
  // fudge in the layout, so it was invisible; now it is the one thing a reader
  // most needs to pick out, drawn as a dashed edge rather than another size.
  const decision = data.decision ?? false;
  const active = data.active ?? false;

  const iconClass = active
    ? 'text-accent-orange'
    : subagent
      ? 'text-accent-orange'
      : 'text-muted-foreground';

  const circle = subagent ? (
        <div
          className={cn(
            'flex items-center justify-center rounded-full',
            circleSize,
            decision && 'outline outline-1 outline-offset-2 outline-dashed outline-foreground/25',
            active && 'animate-pulse ring-2 ring-accent-orange ring-offset-2 ring-offset-background',
          )}
          style={{
            background: 'linear-gradient(135deg, var(--accent-orange-light), var(--accent-orange))',
            padding: '1.5px',
          }}
        >
          <div className={cn('flex size-full items-center justify-center rounded-full bg-accent-orange/10')}>
            <Icon className={cn(iconSize, iconClass)} />
          </div>
        </div>
  ) : (
    <div
      className={cn(
        'flex items-center justify-center rounded-full bg-muted/30 transition-colors',
        circleSize,
        decision
          ? 'border border-dashed border-foreground/25'
          : 'ring-1 ring-foreground/10',
        active &&
          'animate-pulse bg-accent-orange/15 ring-2 ring-accent-orange ring-offset-2 ring-offset-background',
      )}
    >
      <Icon className={cn(iconSize, iconClass)} />
    </div>
  );

  return (
    <div className="relative flex flex-col items-center gap-2">
      <Handle
        type="target"
        position={Position.Left}
        style={{ top: handleTop }}
        className="bg-transparent! border-0! w-0! h-0!"
      />
      {data.hint ? (
        <Tooltip>
          <TooltipTrigger className="cursor-help">{circle}</TooltipTrigger>
          <TooltipContent side="bottom" className="max-w-xs">
            <p className="text-sm">{data.hint}</p>
          </TooltipContent>
        </Tooltip>
      ) : (
        circle
      )}
      <span
        className={cn(
          'max-w-24 text-center text-xs leading-tight',
          active ? 'font-medium text-foreground' : 'text-muted-foreground',
        )}
      >
        {data.label}
      </span>
      <Handle
        type="source"
        position={Position.Right}
        style={{ top: handleTop }}
        className="bg-transparent! border-0! w-0! h-0!"
      />
    </div>
  );
}

const nodeTypes = {
  agent: AgentNode,
  step: StepNode,
};

/* ── Auto-layout with Dagre ──────────────────────────────── */

/** A laid-out graph, plus the box it occupies.
 *
 * The bounds come back because the container height used to be a fixed 450px
 * whatever the flow was. These graphs are wide and short, so `fitView` scaled
 * them down until the *width* fitted and left the leftover height as dead
 * space — a strip of diagram across the top of a large empty panel. Sizing the
 * panel to the graph's own proportions removes it.
 */
type Flow = {
  nodes: Node[];
  edges: Edge[];
  bounds: { width: number; height: number };
};

function getLayoutedElements(nodes: Node[], edges: Edge[]): Flow {
  const g = new Dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));

  g.setGraph({
    rankdir: 'LR',
    // nodesep separates branches stacked vertically; ranksep is the horizontal
    // step-to-step gap and is what gives the edge labels their room now that
    // node widths no longer vary to make it.
    nodesep: 36,
    ranksep: 56,
    marginx: 20,
    marginy: 20,
  });

  const sizeOf = (node: Node) => ({
    width: NODE_W,
    height: node.type === 'agent' ? AGENT_H : STEP_H,
  });

  for (const node of nodes) {
    g.setNode(node.id, sizeOf(node));
  }

  for (const edge of edges) {
    g.setEdge(edge.source, edge.target);
  }

  Dagre.layout(g);

  const layoutedNodes = nodes.map((node) => {
    const pos = g.node(node.id);
    // The same box dagre was given. This previously re-centred every step as if
    // it were STEP_W wide, whatever width it had actually been laid out at, so
    // a decision node ended up 30px right of where dagre put it and a small one
    // 15px left -- which is where the uneven spacing came from.
    const { width, height } = sizeOf(node);
    return {
      ...node,
      position: { x: pos.x - width / 2, y: pos.y - height / 2 },
    };
  });

  // The union of every node's box. Dagre reports a graph size too, but it
  // ignores the widths we hand it for nodes it has already placed, so measuring
  // the result is the honest number.
  const xs = layoutedNodes.map((n) => n.position.x);
  const ys = layoutedNodes.map((n) => n.position.y);
  const rights = layoutedNodes.map((n) => n.position.x + sizeOf(n).width);
  const bottoms = layoutedNodes.map((n) => n.position.y + sizeOf(n).height);

  return {
    nodes: layoutedNodes,
    edges,
    bounds: {
      width: Math.max(...rights) - Math.min(...xs),
      height: Math.max(...bottoms) - Math.min(...ys),
    },
  };
}

/* ── Edge helpers ────────────────────────────────────────── */

/*
 * An accented (orange) edge means one thing, consistently: this route ends with
 * an agent writing to the repository or the cluster. Gray routes end in a
 * notification, or simply stop and wait for a person.
 *
 * It previously meant nothing in particular. The accent was set on the single
 * edge *into* the code-fix branch and on nothing else, so the entry to the path
 * was highlighted while the path itself — Code Fix, Write Fix, Push Fix, Merge —
 * was drawn in the same gray as the branch that just notifies you. There was no
 * rule a reader could infer, because there was not one.
 *
 * `animated` is kept separate and used only on the agent's entry edge, where
 * movement reads as "this is where a run begins" rather than as emphasis.
 */
function mainEdge(
  id: string,
  source: string,
  target: string,
  animated?: boolean,
  accent?: boolean,
): Edge {
  return {
    id,
    source,
    target,
    type: 'default',
    ...(animated || accent
      ? {
          ...(animated ? { animated: true } : {}),
          style: {
            stroke: 'var(--accent-orange)',
            strokeWidth: 2,
            opacity: animated ? 0.5 : 0.4,
          },
        }
      : {}),
  };
}

function branchEdge(
  id: string,
  source: string,
  target: string,
  accent?: boolean,
  label?: string,
): Edge {
  return {
    id,
    source,
    target,
    type: 'default',
    ...(label
      ? {
          label,
          labelStyle: {
            fill: 'var(--muted-foreground)',
            opacity: 0.4,
            fontSize: 9,
            fontWeight: 600,
            letterSpacing: '0.1em',
          },
        }
      : {}),
    ...(accent
      ? {
          style: {
            stroke: 'var(--accent-orange)',
            strokeWidth: 2,
            opacity: 0.4,
          },
        }
      : {}),
  };
}

/* ── Flow definitions (no positions needed!) ─────────────── */

/** Terminal "Notify" nodes, one per branch that ends by waiting for a person.
 *
 * A single shared Notify meant three long edges converging from across the
 * chart, crossing everything between them -- and dagre lifted it above Deep
 * Review to untangle that, which is what put Deep Review's Merge underneath the
 * line to it. Merge is already duplicated three times for exactly this reason.
 */
const NOTIFY_STEPS: Record<string, string> = {
  n1: 'notify_scope',
  n2: 'notify_rereview',
  n3: 'notify_deep',
};

function notifyNodes(ids: string[]): Node[] {
  return ids.map((id) => ({
    id,
    type: 'step',
    position: { x: 0, y: 0 },
    data: {
      label: 'Notify',
      icon: 'IconAlertCircle',
      step: NOTIFY_STEPS[id],
      hint: 'Sends an ntfy notification and stops. The PR is left for you; nothing further happens to it automatically.',
    },
  }));
}

function makePRReviewFlow(prMode: string): Flow {
  const pos = { x: 0, y: 0 };

  // Common review chain shared by all modes.
  //
  // Every rank costs width, and width is the only thing this diagram is short
  // of: at thirteen ranks the captions rendered at 7px. "Trigger" was the
  // cheapest to lose — it said the schedule had fired, which the agent node and
  // the countdown in the header both already say, and it is the one step you
  // can neither act on nor learn anything from.
  const reviewNodes: Node[] = [
    { id: 'agent', type: 'agent', position: pos, data: { label: 'PR Review' } },
    {
      id: 's2',
      type: 'step',
      position: pos,
      data: {
        label: 'Check PR',
        icon: 'IconFileSearch',
        step: 'check_pr',
        hint: 'Reads the PR: author, labels, CI status and head SHA. Skips it if this SHA was already reviewed.',
      },
    },
    {
      id: 's3',
      type: 'step',
      position: pos,
      data: {
        label: 'Read Diff',
        icon: 'IconFileText',
        step: 'read_diff',
        hint: 'Reads the changed files and classifies them — tooling-only, cluster OS, cluster workloads or bootstrap — before judging risk by component name.',
      },
    },
    {
      id: 's4',
      type: 'step',
      position: pos,
      data: {
        label: 'Release Notes',
        icon: 'IconNotes',
        step: 'release_notes',
        hint: 'Fetches the upstream release notes, and where those are silent, the upstream CHANGELOG or a chart values.yaml at both tags. Can search the web when the Web Search skill is on.',
      },
    },
    {
      id: 's5',
      type: 'step',
      position: pos,
      data: {
        label: 'Decide',
        icon: 'IconReport',
        decision: true,
        step: 'decide',
        hint: 'Ends the review with two lines: SAFE_TO_MERGE and FIXABLE. The routing reads those, not the prose.',
      },
    },
  ];
  const reviewEdges: Edge[] = [
    mainEdge('e-a-s2', 'agent', 's2', true),
    mainEdge('e-s2-s3', 's2', 's3'),
    mainEdge('e-s3-s4', 's3', 's4'),
    mainEdge('e-s4-s5', 's4', 's5'),
  ];

  // comment_only: review chain → Comment (no merge, no fix, no deep review)
  if (prMode === 'comment_only') {
    return getLayoutedElements(
      [
        ...reviewNodes,
        { id: 'c1', type: 'step', position: pos, data: { label: 'Comment', icon: 'IconMessage' } },
        { id: 'c2', type: 'step', position: pos, data: { label: 'Notify', icon: 'IconBell' } },
      ],
      [
        ...reviewEdges,
        mainEdge('e-s5-c1', 's5', 'c1'),
        mainEdge('e-c1-c2', 'c1', 'c2'),
      ],
    );
  }

  // auto_merge_all: full flow with deep review escalation
  if (prMode === 'auto_merge_all') {
    return getLayoutedElements(
      [
        ...reviewNodes,
        { id: 'b1', type: 'step', position: pos, data: { label: 'Merge', icon: 'IconCircleCheck', step: 'merge_safe' } },
        { id: 'g1', type: 'step', position: pos, data: { label: 'In Scope?', icon: 'IconFileSearch', decision: true, step: 'in_scope' } },
        // Write Fix and Push Fix used to be siblings here. They are steps *inside*
        // the Code Fix sub-agent, not stages of the review flow, and they cost two
        // of the thirteen ranks that made every caption 7px. The detail is on hover.
        {
          id: 'b2a',
          type: 'step',
          position: pos,
          data: {
            label: 'Code Fix',
            icon: 'IconCode',
            subagent: true,
            step: 'code_fix',
            hint: 'Checks the branch out into a git worktree, searches the repository, edits as many files as the fix needs, validates with kubeconform, then pushes one commit through the guarded commit tool. Only files under kubernetes/apps/ can be committed.',
          },
        },
        { id: 'b2e', type: 'step', position: pos, data: { label: 'Re-review', icon: 'IconEye', decision: true, step: 're_review' } },
        { id: 'b2d', type: 'step', position: pos, data: { label: 'Merge', icon: 'IconCircleCheck', step: 'merge_after_fix' } },
        { id: 'b3', type: 'step', position: pos, data: { label: 'Deep Review', icon: 'IconEye', subagent: true, decision: true, step: 'deep_review' } },
        { id: 'b3a', type: 'step', position: pos, data: { label: 'Merge', icon: 'IconCircleCheck', step: 'merge_after_deep' } },
        // One Notify per branch rather than a single node every branch reaches
        // across the chart into. Three long edges converged on it, crossing
        // everything between, and dagre lifted it above Deep Review to
        // untangle them -- which put Deep Review's Merge below the line to it.
        //
        // Duplicating a terminal is already this diagram's convention: Merge
        // appears three times for the same reason. Now each fork reads locally,
        // with the continuing path on top and the stop-and-notify path beneath.
        ...notifyNodes(['n1', 'n2', 'n3']),
      ],
      [
        ...reviewEdges,
        // Accented: every route that ends in a write. Merging is a write to
        // main, so the SAFE branch is accented too.
        branchEdge('e-s5-b1', 's5', 'b1', true, 'SAFE'),
        branchEdge('e-s5-g1', 's5', 'g1', true, 'FIXABLE'),
        branchEdge('e-g1-b2a', 'g1', 'b2a', true, 'YES'),
        mainEdge('e-b2a-b2e', 'b2a', 'b2e', false, true),
        branchEdge('e-b2e-b2d', 'b2e', 'b2d', true, 'OK'),
        branchEdge('e-b3-b3a', 'b3', 'b3a', true, 'OK'),
        branchEdge('e-b3-g1', 'b3', 'g1', true, 'FIXABLE'),
        // Gray: every route that stops and waits for a person.
        branchEdge('e-g1-n1', 'g1', 'n1', false, 'NO'),
        branchEdge('e-b2e-n2', 'b2e', 'n2', false, 'RISK'),
        branchEdge('e-s5-b3', 's5', 'b3', false, 'REVIEW'),
        branchEdge('e-b3-n3', 'b3', 'n3', false, 'RISK'),
      ],
    );
  }

  // auto_merge / auto_merge_minor: merge + code fix, but no deep review
  return getLayoutedElements(
    [
      ...reviewNodes,
      { id: 'b1', type: 'step', position: pos, data: { label: 'Merge', icon: 'IconCircleCheck', step: 'merge_safe' } },
      { id: 'g1', type: 'step', position: pos, data: { label: 'In Scope?', icon: 'IconFileSearch', decision: true, step: 'in_scope' } },
      // Write Fix and Push Fix used to be siblings here. They are steps *inside*
      // the Code Fix sub-agent, not stages of the review flow, and they cost two
      // of the thirteen ranks that made every caption 7px. The detail is on hover.
      {
        id: 'b2a',
        type: 'step',
        position: pos,
        data: {
          label: 'Code Fix',
          icon: 'IconCode',
          subagent: true,
          step: 'code_fix',
          hint: 'Checks the branch out into a git worktree, searches the repository, edits as many files as the fix needs, validates with kubeconform, then pushes one commit through the guarded commit tool. Only files under kubernetes/apps/ can be committed.',
        },
      },
      { id: 'b2e', type: 'step', position: pos, data: { label: 'Re-review', icon: 'IconEye', decision: true, step: 're_review' } },
      { id: 'b2d', type: 'step', position: pos, data: { label: 'Merge', icon: 'IconCircleCheck', step: 'merge_after_fix' } },
      ...notifyNodes(['n1', 'n2', 'n3']),
    ],
    [
      ...reviewEdges,
      // Accented: routes that end in a write.
      branchEdge('e-s5-b1', 's5', 'b1', true, 'SAFE'),
      branchEdge('e-s5-g1', 's5', 'g1', true, 'FIXABLE'),
      branchEdge('e-g1-b2a', 'g1', 'b2a', true, 'YES'),
      mainEdge('e-b2a-b2e', 'b2a', 'b2e', false, true),
      branchEdge('e-b2e-b2d', 'b2e', 'b2d', true, 'OK'),
      // Gray: routes that stop and wait for a person.
      branchEdge('e-g1-n1', 'g1', 'n1', false, 'NO'),
      branchEdge('e-b2e-n2', 'b2e', 'n2', false, 'RISK'),
      branchEdge('e-s5-n3', 's5', 'n3', false, 'REVIEW'),
    ],
  );
}

function makeAlertFlow(): Flow {
  const nodes: Node[] = [
    {
      id: 'agent',
      type: 'agent',
      position: { x: 0, y: 0 },
      data: { label: 'Alert Triage' },
    },
    {
      id: 's2',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Check Pods', icon: 'IconBox' },
    },
    {
      id: 's3',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Read Logs', icon: 'IconFileAnalytics' },
    },
    {
      id: 's4',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Metrics', icon: 'IconChartLine' },
    },
    {
      id: 's5',
      type: 'step',
      position: { x: 0, y: 0 },
      data: {
        label: 'Triage',
        icon: 'IconReport',
        decision: true,
      },
    },
    {
      id: 'b1a',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Alert Fix', icon: 'IconBolt', subagent: true },
    },
    {
      id: 'b1b',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Apply Fix', icon: 'IconBolt' },
    },
    {
      id: 'b1c',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Verify', icon: 'IconCheck' },
    },
    {
      id: 'b1d',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Notify', icon: 'IconBell' },
    },
    {
      id: 'b2',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Notify User', icon: 'IconBell' },
    },
    {
      id: 'b3',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Ignore', icon: 'IconPlayerSkipForward' },
    },
  ];

  const edges: Edge[] = [
    mainEdge('e-a-s2', 'agent', 's2', true),
    mainEdge('e-s2-s3', 's2', 's3'),
    mainEdge('e-s3-s4', 's3', 's4'),
    mainEdge('e-s4-s5', 's4', 's5'),
    branchEdge('e-s5-b1a', 's5', 'b1a', true, 'FIX'),
    mainEdge('e-b1a-b1b', 'b1a', 'b1b'),
    mainEdge('e-b1b-b1c', 'b1b', 'b1c'),
    mainEdge('e-b1c-b1d', 'b1c', 'b1d'),
    branchEdge('e-s5-b2', 's5', 'b2', false, 'NOTIFY'),
    branchEdge('e-s5-b3', 's5', 'b3', false, 'IGNORE'),
  ];

  return getLayoutedElements(nodes, edges);
}


const FLOW_BUILDERS: Record<string, (prMode?: string) => Flow> = {
  pr_review: (prMode) => makePRReviewFlow(prMode ?? 'comment_only'),
  alert: () => makeAlertFlow(),
};

const PR_MODE_DESCRIPTIONS: Record<string, string> = {
  comment_only:
    'Reviews Renovate PRs, checks CI, fetches release notes. Posts a comment with risk assessment — no automated merge or fix actions.',
  auto_merge:
    'Reviews PRs and auto-merges safe patch/digest updates. A review that knows the fix hands it to the Code Fix agent — but only if the PR touches paths a fix is allowed to commit. Every pushed fix is re-reviewed before it merges. Anything else notifies you.',
  auto_merge_minor:
    'Reviews PRs and auto-merges safe patch, digest, and minor updates. A review that knows the fix hands it to the Code Fix agent — but only if the PR touches paths a fix is allowed to commit. Every pushed fix is re-reviewed before it merges. Anything else notifies you.',
  auto_merge_all:
    'Fully autonomous: merges all safe PRs including critical components. A review that knows the fix hands it to the Code Fix agent, if the PR only touches paths a fix may commit. Everything else goes to Opus for Deep Review, which can hand a fix over itself once it has read the changelogs. Every pushed fix is re-reviewed before it merges.',
};

const AGENT_DESCRIPTIONS: Record<string, string> = {
  pr_review: PR_MODE_DESCRIPTIONS.comment_only,
  alert:
    'Two-stage pipeline: Triage (Haiku) diagnoses severity, then Alert Fix (Sonnet) takes corrective action when needed.',
};

const defaultEdgeOptions = {
  animated: true,
  style: {
    stroke: 'var(--muted-foreground)',
    strokeWidth: 2,
    opacity: 0.2,
  },
  markerEnd: {
    type: MarkerType.ArrowClosed,
    width: 12,
    height: 12,
    color: 'var(--muted-foreground)',
  },
};

/* ── Main component ──────────────────────────────────────── */

interface AgentFlowProps {
  activeAgent: string;
}

export function AgentFlow({ activeAgent }: AgentFlowProps) {
  const { data: settings } = useSettings();
  const prMode = settings?.pr_mode ?? 'comment_only';
  const builder = FLOW_BUILDERS[activeAgent];

  // Shares react-query's cache with the status bar, so this adds no request of
  // its own — it only asks for a faster refetch while something is running.
  // Polling rather than a socket: the only WebSocket in the app is per-connection
  // and chat-shaped, and background workers have no broadcast channel, so pushing
  // would mean building a hub to move one short string.
  const { data: statusData } = useQuery({
    queryKey: ['status'],
    queryFn: fetchStatus,
    refetchInterval: (query) =>
      query.state.data?.run || query.state.data?.pr_check_running ? 2000 : 30000,
  });

  const run = statusData?.run ?? null;
  const activeStep = run?.step ?? null;

  const { nodes, edges, bounds } = useMemo(() => {
    const flow = builder
      ? builder(prMode)
      : { nodes: [], edges: [], bounds: { width: 1, height: 1 } };
    if (!activeStep) return flow;
    return {
      ...flow,
      nodes: flow.nodes.map((node) =>
        (node.data as StepNodeData).step === activeStep
          ? { ...node, data: { ...node.data, active: true } }
          : node
      ),
    };
  }, [builder, prMode, activeStep]);

  if (!builder) return null;

  // Proportional to the graph rather than fixed, so the panel is the size of
  // what is in it. Clamped because a very wide flow on a narrow screen would
  // otherwise collapse to a sliver, and a short one on a wide screen would
  // stretch the nodes apart.
  const aspect = bounds.width / Math.max(bounds.height, 1);
  const description =
    activeAgent === 'pr_review'
      ? PR_MODE_DESCRIPTIONS[prMode ?? 'comment_only'] ?? AGENT_DESCRIPTIONS[activeAgent]
      : AGENT_DESCRIPTIONS[activeAgent] ?? 'Autonomous cluster operator';

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <h2 className="text-sm font-medium text-muted-foreground">
          Agent Workflow
        </h2>
        <Tooltip>
          <TooltipTrigger className="cursor-help text-xs text-muted-foreground/50 hover:text-muted-foreground">
            ?
          </TooltipTrigger>
          <TooltipContent side="right" className="max-w-xs">
            <p className="text-sm">
              {description}
            </p>
          </TooltipContent>
        </Tooltip>
        {/* Says the highlight is live rather than a static emphasis, and what it
            is working on — a pulsing circle with no explanation invites the
            question this answers. */}
        {run && (
          <span className="flex items-center gap-1.5 text-[11px] text-accent-orange">
            <span className="inline-block size-1.5 animate-pulse rounded-full bg-accent-orange" />
            running{run.detail ? ` · ${run.detail}` : ''}
          </span>
        )}
        {/* The accent colour carries meaning, so it needs saying somewhere.
            Without this it reads as decoration and the distinction is lost. */}
        {activeAgent === 'pr_review' && (
          <div className="ml-auto flex items-center gap-4 text-[10px] tracking-wide text-muted-foreground/60">
            <span className="flex items-center gap-1.5">
              <span
                aria-hidden
                className="inline-block h-px w-4"
                style={{ backgroundColor: 'var(--accent-orange)', opacity: 0.6 }}
              />
              ends in a write
            </span>
            <span className="flex items-center gap-1.5">
              <span
                aria-hidden
                className="inline-block h-px w-4 bg-muted-foreground/40"
              />
              waits for you
            </span>
            <span className="flex items-center gap-1.5">
              <span
                aria-hidden
                className="inline-block size-2.5 rounded-full border border-dashed border-foreground/40"
              />
              branches
            </span>
          </div>
        )}
      </div>
      <div
        className="relative overflow-hidden rounded-xl"
        style={{ aspectRatio: aspect, minHeight: 200, maxHeight: 520 }}
      >
        <ReactFlow
          key={`${activeAgent}-${prMode}`}
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          defaultEdgeOptions={defaultEdgeOptions}
          colorMode="dark"
          fitView
          fitViewOptions={{ padding: 0.05 }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          panOnDrag={false}
          zoomOnScroll={false}
          zoomOnDoubleClick={false}
          zoomOnPinch={false}
          preventScrolling={false}
          proOptions={{ hideAttribution: true }}
          className="bg-transparent!"
        />
      </div>
    </div>
  );
}
