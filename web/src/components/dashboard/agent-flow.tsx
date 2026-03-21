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
type BranchLabelData = { label: string };

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

function AgentNode({ data }: NodeProps<Node<AgentNodeData>>) {
  return (
    <div className="flex flex-col items-center gap-3">
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
        className="bg-transparent! border-0! w-0! h-0!"
      />
    </div>
  );
}

function StepNode({ data }: NodeProps<Node<StepNodeData>>) {
  const Icon = ICONS[data.icon] ?? IconReport;
  const accent = data.accent ?? false;
  const small = data.size === 'sm';
  const circleSize = small ? 'size-13' : 'size-16';
  const iconSize = small ? 'size-5' : 'size-7';
  return (
    <div className="flex flex-col items-center gap-2">
      <Handle
        type="target"
        position={Position.Left}
        className="bg-transparent! border-0! w-0! h-0!"
      />
      <div
        className={cn(
          'flex items-center justify-center rounded-full transition-colors',
          circleSize,
          accent
            ? 'bg-accent-orange/10 ring-2 ring-accent-orange/40'
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
        className="bg-transparent! border-0! w-0! h-0!"
      />
    </div>
  );
}

function BranchLabelNode({ data }: NodeProps<Node<BranchLabelData>>) {
  return (
    <div className="flex items-center">
      <Handle
        type="target"
        position={Position.Left}
        className="bg-transparent! border-0! w-0! h-0!"
      />
      <span className="text-[0.65rem] font-semibold uppercase tracking-widest text-muted-foreground/40">
        {data.label}
      </span>
      <Handle
        type="source"
        position={Position.Right}
        className="bg-transparent! border-0! w-0! h-0!"
      />
    </div>
  );
}

const nodeTypes = {
  agent: AgentNode,
  step: StepNode,
  branchLabel: BranchLabelNode,
};

/* ── Flow definitions ────────────────────────────────────── */

// Layout constants
const STEP = 120; // horizontal spacing between main steps
const BRANCH_Y = 100; // vertical spacing between branches
const BRANCH_X = 100; // horizontal spacing between branch steps
const MAIN_Y = 0; // y position of main flow
const DECIDE_X = 5 * STEP;

// Helper to make edges consistent
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
            strokeWidth: 2.5,
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
): Edge {
  return {
    id,
    source,
    target,
    type: 'default',
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

function makePRReviewFlow(): { nodes: Node[]; edges: Edge[] } {
  const bx = DECIDE_X + 100; // branch label x
  const bsx = bx + 90; // branch step start x

  const nodes: Node[] = [
    {
      id: 'agent',
      type: 'agent',
      position: { x: 0, y: MAIN_Y },
      data: { label: 'Agent' },
    },
    {
      id: 's1',
      type: 'step',
      position: { x: STEP, y: MAIN_Y + 10 },
      data: { label: 'Trigger', icon: 'IconGitPullRequest' },
    },
    {
      id: 's2',
      type: 'step',
      position: { x: 2 * STEP, y: MAIN_Y + 10 },
      data: { label: 'Check PR', icon: 'IconFileSearch' },
    },
    {
      id: 's3',
      type: 'step',
      position: { x: 3 * STEP, y: MAIN_Y + 10 },
      data: { label: 'Read Diff', icon: 'IconFileText' },
    },
    {
      id: 's4',
      type: 'step',
      position: { x: 4 * STEP, y: MAIN_Y + 10 },
      data: { label: 'Release Notes', icon: 'IconNotes' },
    },
    {
      id: 's5',
      type: 'step',
      position: { x: DECIDE_X, y: MAIN_Y + 10 },
      data: { label: 'Decide', icon: 'IconReport', accent: true },
    },
    // Branch 1: Safe → Merge
    {
      id: 'bl1',
      type: 'branchLabel',
      position: { x: bx, y: MAIN_Y - BRANCH_Y + 20 },
      data: { label: 'Safe' },
    },
    {
      id: 'b1',
      type: 'step',
      position: { x: bsx, y: MAIN_Y - BRANCH_Y },
      data: { label: 'Merge', icon: 'IconCircleCheck', size: 'sm' },
    },
    // Branch 2: Fix → Code Fix pipeline
    {
      id: 'bl2',
      type: 'branchLabel',
      position: { x: bx, y: MAIN_Y + 20 },
      data: { label: 'Fix' },
    },
    {
      id: 'b2a',
      type: 'step',
      position: { x: bsx, y: MAIN_Y },
      data: { label: 'Code Fix', icon: 'IconCode', accent: true, size: 'sm' },
    },
    {
      id: 'b2b',
      type: 'step',
      position: { x: bsx + BRANCH_X, y: MAIN_Y },
      data: { label: 'Branch', icon: 'IconGitBranch', size: 'sm' },
    },
    {
      id: 'b2c',
      type: 'step',
      position: { x: bsx + 2 * BRANCH_X, y: MAIN_Y },
      data: { label: 'Write Fix', icon: 'IconFileText', size: 'sm' },
    },
    {
      id: 'b2d',
      type: 'step',
      position: { x: bsx + 3 * BRANCH_X, y: MAIN_Y },
      data: { label: 'Open PR', icon: 'IconSend', size: 'sm' },
    },
    // Branch 3: Review → Notify
    {
      id: 'bl3',
      type: 'branchLabel',
      position: { x: bx, y: MAIN_Y + BRANCH_Y + 20 },
      data: { label: 'Review' },
    },
    {
      id: 'b3',
      type: 'step',
      position: { x: bsx, y: MAIN_Y + BRANCH_Y },
      data: { label: 'Notify', icon: 'IconAlertCircle', size: 'sm' },
    },
  ];

  const edges: Edge[] = [
    mainEdge('e-a-s1', 'agent', 's1', true),
    mainEdge('e-s1-s2', 's1', 's2'),
    mainEdge('e-s2-s3', 's2', 's3'),
    mainEdge('e-s3-s4', 's3', 's4'),
    mainEdge('e-s4-s5', 's4', 's5'),
    branchEdge('e-s5-bl1', 's5', 'bl1'),
    branchEdge('e-bl1-b1', 'bl1', 'b1'),
    branchEdge('e-s5-bl2', 's5', 'bl2', true),
    branchEdge('e-bl2-b2a', 'bl2', 'b2a', true),
    mainEdge('e-b2a-b2b', 'b2a', 'b2b'),
    mainEdge('e-b2b-b2c', 'b2b', 'b2c'),
    mainEdge('e-b2c-b2d', 'b2c', 'b2d'),
    branchEdge('e-s5-bl3', 's5', 'bl3'),
    branchEdge('e-bl3-b3', 'bl3', 'b3'),
  ];

  return { nodes, edges };
}

function makeAlertFlow(): { nodes: Node[]; edges: Edge[] } {
  const bx = DECIDE_X + 100;
  const bsx = bx + 90;

  const nodes: Node[] = [
    {
      id: 'agent',
      type: 'agent',
      position: { x: 0, y: MAIN_Y },
      data: { label: 'Agent' },
    },
    {
      id: 's1',
      type: 'step',
      position: { x: STEP, y: MAIN_Y + 10 },
      data: { label: 'Alert', icon: 'IconAlertTriangle' },
    },
    {
      id: 's2',
      type: 'step',
      position: { x: 2 * STEP, y: MAIN_Y + 10 },
      data: { label: 'Check Pods', icon: 'IconBox' },
    },
    {
      id: 's3',
      type: 'step',
      position: { x: 3 * STEP, y: MAIN_Y + 10 },
      data: { label: 'Read Logs', icon: 'IconFileAnalytics' },
    },
    {
      id: 's4',
      type: 'step',
      position: { x: 4 * STEP, y: MAIN_Y + 10 },
      data: { label: 'Metrics', icon: 'IconChartLine' },
    },
    {
      id: 's5',
      type: 'step',
      position: { x: DECIDE_X, y: MAIN_Y + 10 },
      data: { label: 'Triage', icon: 'IconReport', accent: true },
    },
    // Branch 1: Fix
    {
      id: 'bl1',
      type: 'branchLabel',
      position: { x: bx, y: MAIN_Y - BRANCH_Y + 20 },
      data: { label: 'Fix' },
    },
    {
      id: 'b1a',
      type: 'step',
      position: { x: bsx, y: MAIN_Y - BRANCH_Y },
      data: { label: 'Alert Fix', icon: 'IconBolt', accent: true, size: 'sm' },
    },
    {
      id: 'b1b',
      type: 'step',
      position: { x: bsx + BRANCH_X, y: MAIN_Y - BRANCH_Y },
      data: { label: 'Apply Fix', icon: 'IconBolt', size: 'sm' },
    },
    {
      id: 'b1c',
      type: 'step',
      position: { x: bsx + 2 * BRANCH_X, y: MAIN_Y - BRANCH_Y },
      data: { label: 'Verify', icon: 'IconCheck', size: 'sm' },
    },
    {
      id: 'b1d',
      type: 'step',
      position: { x: bsx + 3 * BRANCH_X, y: MAIN_Y - BRANCH_Y },
      data: { label: 'Notify', icon: 'IconBell', size: 'sm' },
    },
    // Branch 2: Notify
    {
      id: 'bl2',
      type: 'branchLabel',
      position: { x: bx, y: MAIN_Y + 20 },
      data: { label: 'Notify' },
    },
    {
      id: 'b2',
      type: 'step',
      position: { x: bsx, y: MAIN_Y },
      data: { label: 'Notify User', icon: 'IconBell', size: 'sm' },
    },
    // Branch 3: Ignore
    {
      id: 'bl3',
      type: 'branchLabel',
      position: { x: bx, y: MAIN_Y + BRANCH_Y + 20 },
      data: { label: 'Ignore' },
    },
    {
      id: 'b3',
      type: 'step',
      position: { x: bsx, y: MAIN_Y + BRANCH_Y },
      data: { label: 'Ignore', icon: 'IconPlayerSkipForward', size: 'sm' },
    },
  ];

  const edges: Edge[] = [
    mainEdge('e-a-s1', 'agent', 's1', true),
    mainEdge('e-s1-s2', 's1', 's2'),
    mainEdge('e-s2-s3', 's2', 's3'),
    mainEdge('e-s3-s4', 's3', 's4'),
    mainEdge('e-s4-s5', 's4', 's5'),
    branchEdge('e-s5-bl1', 's5', 'bl1', true),
    branchEdge('e-bl1-b1a', 'bl1', 'b1a', true),
    mainEdge('e-b1a-b1b', 'b1a', 'b1b'),
    mainEdge('e-b1b-b1c', 'b1b', 'b1c'),
    mainEdge('e-b1c-b1d', 'b1c', 'b1d'),
    branchEdge('e-s5-bl2', 's5', 'bl2'),
    branchEdge('e-bl2-b2', 'bl2', 'b2'),
    branchEdge('e-s5-bl3', 's5', 'bl3'),
    branchEdge('e-bl3-b3', 'bl3', 'b3'),
  ];

  return { nodes, edges };
}

function makeChatFlow(): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [
    {
      id: 'agent',
      type: 'agent',
      position: { x: 0, y: MAIN_Y },
      data: { label: 'Agent' },
    },
    {
      id: 's1',
      type: 'step',
      position: { x: STEP, y: MAIN_Y + 10 },
      data: { label: 'Message', icon: 'IconMessageChatbot' },
    },
    {
      id: 's2',
      type: 'step',
      position: { x: 2 * STEP, y: MAIN_Y + 10 },
      data: { label: 'Context', icon: 'IconSearch' },
    },
    {
      id: 's3',
      type: 'step',
      position: { x: 3 * STEP, y: MAIN_Y + 10 },
      data: { label: 'Run Tools', icon: 'IconTerminal2' },
    },
    {
      id: 's4',
      type: 'step',
      position: { x: 4 * STEP, y: MAIN_Y + 10 },
      data: { label: 'Analyze', icon: 'IconReport' },
    },
    {
      id: 's5',
      type: 'step',
      position: { x: 5 * STEP, y: MAIN_Y + 10 },
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

  return { nodes, edges };
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
  style: {
    stroke: 'var(--muted-foreground)',
    strokeWidth: 2,
    opacity: 0.2,
  },
  markerEnd: {
    type: MarkerType.ArrowClosed,
    width: 16,
    height: 16,
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
  const height = hasBranches ? 450 : 200;

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
          fitViewOptions={{ padding: 0.02 }}
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
