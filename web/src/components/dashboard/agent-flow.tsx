'use client';

import { useMemo } from 'react';
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
  accent?: boolean;
  size?: 'sm' | 'md';
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
  IconMessageChatbot,
  IconSearch,
  IconTerminal2,
  IconMessage,
};

// Node dimensions for dagre layout (height = circle only, excludes text label)
const AGENT_W = 100;
const AGENT_H = 96;
const STEP_W = 80;
const STEP_H = 72;
// Decision nodes declared wider so dagre gives more room for outgoing edge labels
const DECISION_W = 140;
// Small continuation nodes declared narrower to pack tighter
const SMALL_STEP_W = 50;

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
      <span className="text-sm font-semibold text-foreground">
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
  const accent = data.accent ?? false;
  const small = data.size === 'sm';
  const circleSize = small ? 'size-14' : 'size-18';
  const iconSize = small ? 'size-6' : 'size-8';
  const handleTop = small ? '28px' : '36px';
  return (
    <div className="relative flex flex-col items-center gap-2">
      <Handle
        type="target"
        position={Position.Left}
        style={{ top: handleTop }}
        className="bg-transparent! border-0! w-0! h-0!"
      />
      <div
        className={cn(
          'flex items-center justify-center rounded-full transition-colors',
          circleSize,
          accent
            ? 'bg-accent-orange/10 ring-1 ring-accent-orange/40'
            : 'bg-muted/30 ring-1 ring-foreground/10',
        )}
      >
        <Icon
          className={cn(
            iconSize,
            accent ? 'text-accent-orange' : 'text-muted-foreground',
          )}
        />
      </div>
      <span
        className={cn(
          'max-w-24 text-center leading-tight text-muted-foreground',
          small ? 'text-[0.7rem]' : 'text-xs',
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

function getLayoutedElements(
  nodes: Node[],
  edges: Edge[],
): { nodes: Node[]; edges: Edge[] } {
  const g = new Dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));

  g.setGraph({
    rankdir: 'LR',
    nodesep: 30,
    ranksep: 45,
    marginx: 20,
    marginy: 20,
  });

  for (const node of nodes) {
    const isDecision =
      node.data && 'decision' in node.data && node.data.decision;
    const isSmall = node.data && 'size' in node.data && node.data.size === 'sm';
    const w =
      node.type === 'agent'
        ? AGENT_W
        : isDecision
          ? DECISION_W
          : isSmall
            ? SMALL_STEP_W
            : STEP_W;
    const h = node.type === 'agent' ? AGENT_H : STEP_H;
    g.setNode(node.id, { width: w, height: h });
  }

  for (const edge of edges) {
    g.setEdge(edge.source, edge.target);
  }

  Dagre.layout(g);

  const layoutedNodes = nodes.map((node) => {
    const pos = g.node(node.id);
    const w = node.type === 'agent' ? AGENT_W : STEP_W;
    const h = node.type === 'agent' ? AGENT_H : STEP_H;
    return {
      ...node,
      position: {
        x: pos.x - w / 2,
        y: pos.y - h / 2,
      },
    };
  });

  return { nodes: layoutedNodes, edges };
}

/* ── Edge helpers ────────────────────────────────────────── */

function mainEdge(
  id: string,
  source: string,
  target: string,
  animated?: boolean,
): Edge {
  return {
    id,
    source,
    target,
    type: 'default',
    ...(animated
      ? {
          animated: true,
          style: {
            stroke: 'var(--accent-orange)',
            strokeWidth: 2,
            opacity: 0.5,
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

function makePRReviewFlow(): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [
    {
      id: 'agent',
      type: 'agent',
      position: { x: 0, y: 0 },
      data: { label: 'Agent' },
    },
    {
      id: 's1',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Trigger', icon: 'IconGitPullRequest' },
    },
    {
      id: 's2',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Check PR', icon: 'IconFileSearch' },
    },
    {
      id: 's3',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Read Diff', icon: 'IconFileText' },
    },
    {
      id: 's4',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Release Notes', icon: 'IconNotes' },
    },
    {
      id: 's5',
      type: 'step',
      position: { x: 0, y: 0 },
      data: {
        label: 'Decide',
        icon: 'IconReport',
        accent: true,
        decision: true,
      },
    },
    {
      id: 'b1',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Merge', icon: 'IconCircleCheck', size: 'sm' },
    },
    {
      id: 'b2a',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Code Fix', icon: 'IconCode', size: 'sm' },
    },
    {
      id: 'b2b',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Branch', icon: 'IconGitBranch', size: 'sm' },
    },
    {
      id: 'b2c',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Write Fix', icon: 'IconFileText', size: 'sm' },
    },
    {
      id: 'b2d',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Open PR', icon: 'IconSend', size: 'sm' },
    },
    {
      id: 'b3',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Notify', icon: 'IconAlertCircle', size: 'sm' },
    },
  ];

  const edges: Edge[] = [
    mainEdge('e-a-s1', 'agent', 's1', true),
    mainEdge('e-s1-s2', 's1', 's2'),
    mainEdge('e-s2-s3', 's2', 's3'),
    mainEdge('e-s3-s4', 's3', 's4'),
    mainEdge('e-s4-s5', 's4', 's5'),
    branchEdge('e-s5-b1', 's5', 'b1', false, 'SAFE'),
    branchEdge('e-s5-b2a', 's5', 'b2a', true, 'FIX'),
    mainEdge('e-b2a-b2b', 'b2a', 'b2b'),
    mainEdge('e-b2b-b2c', 'b2b', 'b2c'),
    mainEdge('e-b2c-b2d', 'b2c', 'b2d'),
    branchEdge('e-s5-b3', 's5', 'b3', false, 'REVIEW'),
  ];

  return getLayoutedElements(nodes, edges);
}

function makeAlertFlow(): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [
    {
      id: 'agent',
      type: 'agent',
      position: { x: 0, y: 0 },
      data: { label: 'Agent' },
    },
    {
      id: 's1',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Alert', icon: 'IconAlertTriangle' },
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
        accent: true,
        decision: true,
      },
    },
    {
      id: 'b1a',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Alert Fix', icon: 'IconBolt', size: 'sm' },
    },
    {
      id: 'b1b',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Apply Fix', icon: 'IconBolt', size: 'sm' },
    },
    {
      id: 'b1c',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Verify', icon: 'IconCheck', size: 'sm' },
    },
    {
      id: 'b1d',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Notify', icon: 'IconBell', size: 'sm' },
    },
    {
      id: 'b2',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Notify User', icon: 'IconBell', size: 'sm' },
    },
    {
      id: 'b3',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Ignore', icon: 'IconPlayerSkipForward', size: 'sm' },
    },
  ];

  const edges: Edge[] = [
    mainEdge('e-a-s1', 'agent', 's1', true),
    mainEdge('e-s1-s2', 's1', 's2'),
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

function makeChatFlow(): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [
    {
      id: 'agent',
      type: 'agent',
      position: { x: 0, y: 0 },
      data: { label: 'Agent' },
    },
    {
      id: 's1',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Message', icon: 'IconMessageChatbot' },
    },
    {
      id: 's2',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Context', icon: 'IconSearch' },
    },
    {
      id: 's3',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Run Tools', icon: 'IconTerminal2' },
    },
    {
      id: 's4',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Analyze', icon: 'IconReport' },
    },
    {
      id: 's5',
      type: 'step',
      position: { x: 0, y: 0 },
      data: { label: 'Respond', icon: 'IconMessage' },
    },
  ];

  const edges: Edge[] = [
    mainEdge('e-a-s1', 'agent', 's1', true),
    mainEdge('e-s1-s2', 's1', 's2'),
    mainEdge('e-s2-s3', 's2', 's3'),
    mainEdge('e-s3-s4', 's3', 's4'),
    mainEdge('e-s4-s5', 's4', 's5'),
  ];

  return getLayoutedElements(nodes, edges);
}

const FLOW_BUILDERS: Record<string, () => { nodes: Node[]; edges: Edge[] }> = {
  pr_review: makePRReviewFlow,
  alert: makeAlertFlow,
  chat: makeChatFlow,
};

const AGENT_DESCRIPTIONS: Record<string, string> = {
  pr_review:
    'Reviews Renovate PRs, checks CI, fetches release notes. Auto-merges safe patches or escalates to Code Fix agent.',
  alert:
    'Two-stage pipeline: Triage (Haiku) diagnoses severity, then Alert Fix (Sonnet) takes corrective action when needed.',
  chat: 'Interactive assistant. Answers questions, runs diagnostics, and executes commands on demand.',
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
  const builder = FLOW_BUILDERS[activeAgent] ?? makeChatFlow;
  const { nodes, edges } = useMemo(() => builder(), [builder]);

  const hasBranches = activeAgent !== 'chat';
  const height = hasBranches ? 450 : 250;

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
              {AGENT_DESCRIPTIONS[activeAgent] ?? 'Autonomous cluster operator'}
            </p>
          </TooltipContent>
        </Tooltip>
      </div>
      <div className="relative overflow-hidden rounded-xl" style={{ height }}>
        <ReactFlow
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
