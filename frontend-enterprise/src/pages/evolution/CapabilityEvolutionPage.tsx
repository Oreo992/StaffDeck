import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  BarChart3,
  BrainCircuit,
  Check,
  ChevronDown,
  CircleCheckBig,
  History,
  Lightbulb,
  Repeat2,
  RefreshCw,
  SearchCheck,
  Sparkles,
  X,
} from 'lucide-react';
import {
  BarChart,
  FunnelChart,
  type BarSeriesOption,
  type FunnelSeriesOption,
} from 'echarts/charts';
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
  type GridComponentOption,
  type LegendComponentOption,
  type TooltipComponentOption,
} from 'echarts/components';
import * as echarts from 'echarts/core';
import type { ComposeOption } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import ReactEChartsCore from 'echarts-for-react/lib/core';

import { api, TENANT_ID } from '@/api/client';
import AppHeader from '@/components/AppHeader';
import { cn } from '@/lib/utils';
import { notify } from '@/components/ui/app-toast';
import type { EnterpriseAuthUser } from '@/auth';
import type {
  AgentProfileRead,
  CapabilityEvolutionProposalRead,
  CapabilityEvolutionSummaryRead,
} from '@/types';
import { employeeDisplayName } from '@/employee';

type EvolutionChartOption = ComposeOption<
  BarSeriesOption
  | FunnelSeriesOption
  | GridComponentOption
  | LegendComponentOption
  | TooltipComponentOption
>;

echarts.use([
  BarChart,
  FunnelChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
]);


function formatTime(value?: string | null): string {
  if (!value) return '—';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}

function formatDay(value?: string | null): string {
  if (!value) return '尚未使用';
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
  }).format(new Date(value));
}

function EmptyState({ children }: { children: string }) {
  return (
    <div className="rounded-[16px] border border-dashed border-[#dfe2e8] bg-white px-[24px] py-[42px] text-center text-[12px] text-[#858b9c]">
      {children}
    </div>
  );
}

function LoadingPage() {
  return (
    <div className="grid min-h-[420px] place-items-center text-[#858b9c]">
      <RefreshCw className="size-[20px] animate-spin" />
    </div>
  );
}

function EvolutionMetric({
  label,
  value,
  tone = 'neutral',
  icon,
}: {
  label: string;
  value: number;
  tone?: 'neutral' | 'success' | 'violet';
  icon: ReactNode;
}) {
  return (
    <article className={cn(
      'flex min-h-[92px] items-center justify-between rounded-[16px] border-[0.5px] px-[17px] py-[15px]',
      tone === 'neutral' && 'border-[#e3e7f1] bg-white',
      tone === 'success' && 'border-transparent bg-[#eaf8ef]',
      tone === 'violet' && 'border-transparent bg-[#f4f2ff]',
    )}>
      <div><p className={cn('text-[12px] text-[#858b9c]', tone === 'success' && 'text-[#27965b]', tone === 'violet' && 'text-[#6f68a8]')}>{label}</p><strong className={cn('mt-[9px] block text-[27px] leading-none font-semibold text-[#18181a]', tone === 'success' && 'text-[#20a35a]', tone === 'violet' && 'text-[#5d5793]')}>{value}</strong></div>
      <span className={cn('grid size-[40px] place-items-center rounded-full bg-[#f6f6f6] text-[#18181a]', tone === 'success' && 'bg-[#d9f2e2] text-[#20a35a]', tone === 'violet' && 'bg-[#e7e3fa] text-[#6861a3]')}>{icon}</span>
    </article>
  );
}

function EvolutionFunnelChart({ summary }: { summary: CapabilityEvolutionSummaryRead | null }) {
  const total = (summary?.completed_work || 0) + (summary?.skill_work || 0) + (summary?.learned_count || 0) + (summary?.reuse_count || 0);
  const option = useMemo<EvolutionChartOption>(() => ({
    animationDuration: 500,
    color: ['#22a559', '#5d8fdf', '#7b68c6', '#c5cad3'],
    tooltip: {
      trigger: 'item',
      backgroundColor: '#18181a',
      borderWidth: 0,
      textStyle: { color: '#ffffff', fontSize: 10 },
      formatter: '{b}：{c}',
    },
    series: [{
      type: 'funnel',
      left: '5%',
      top: 8,
      bottom: 8,
      width: '90%',
      minSize: '28%',
      maxSize: '100%',
      sort: 'descending',
      gap: 4,
      label: { show: true, position: 'inside', color: '#ffffff', fontSize: 10, formatter: '{b}  {c}' },
      labelLine: { show: false },
      itemStyle: { borderColor: '#ffffff', borderWidth: 2, borderRadius: 5 },
      emphasis: { label: { fontSize: 11 } },
      data: [
        { name: '完成工作', value: summary?.completed_work || 0 },
        { name: '能力验证', value: summary?.skill_work || 0 },
        { name: '形成做法', value: summary?.learned_count || 0 },
        { name: '后续复用', value: summary?.reuse_count || 0 },
      ],
    }],
  }), [summary]);

  return total
    ? <ReactEChartsCore echarts={echarts} option={option} style={{ height: 190, width: '100%' }} notMerge lazyUpdate />
    : <div className="grid h-[190px] place-items-center text-[11px] text-[#9298a4]">暂时没有进化记录</div>;
}

function SkillProgressChart({ summary }: { summary: CapabilityEvolutionSummaryRead | null }) {
  const skills = (summary?.skills || []).slice(0, 5);
  const option = useMemo<EvolutionChartOption>(() => ({
    animationDuration: 500,
    color: ['#22a559', '#7b68c6', '#c5cad3'],
    grid: { top: 10, right: 12, bottom: 20, left: 92 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: '#18181a',
      borderWidth: 0,
      textStyle: { color: '#ffffff', fontSize: 10 },
    },
    legend: { bottom: 0, itemWidth: 7, itemHeight: 7, textStyle: { color: '#858b9c', fontSize: 9 } },
    xAxis: { type: 'value', minInterval: 1, axisLabel: { color: '#9298a4', fontSize: 9 }, splitLine: { lineStyle: { color: '#eceef1', type: 'dashed' } } },
    yAxis: { type: 'category', data: skills.map((skill) => skill.label), axisTick: { show: false }, axisLine: { show: false }, axisLabel: { color: '#646a78', fontSize: 9, width: 80, overflow: 'truncate' } },
    series: [
      { name: '验证', type: 'bar', stack: 'total', barWidth: 12, data: skills.map((skill) => skill.verified_count), itemStyle: { borderRadius: [4, 0, 0, 4] } },
      { name: '学会', type: 'bar', stack: 'total', barWidth: 12, data: skills.map((skill) => skill.learned_count) },
      { name: '复用', type: 'bar', stack: 'total', barWidth: 12, data: skills.map((skill) => skill.reuse_count), itemStyle: { borderRadius: [0, 4, 4, 0] } },
    ],
  }), [skills]);

  return skills.length
    ? <ReactEChartsCore echarts={echarts} option={option} style={{ height: 190, width: '100%' }} notMerge lazyUpdate />
    : <div className="grid h-[190px] place-items-center text-[11px] text-[#9298a4]">暂时没有能力使用记录</div>;
}

function ProposalCard({
  proposal,
  busy,
  onApply,
  onReject,
}: {
  proposal: CapabilityEvolutionProposalRead;
  busy: boolean;
  onApply: () => void;
  onReject: () => void;
}) {
  const evidence = proposal.evidence[0];
  const evidenceText = evidence?.summary
    || evidence?.reason
    || evidence?.evidence?.join('；')
    || proposal.summary;
  return (
    <article className="overflow-hidden rounded-[18px] border-[0.5px] border-[#dfe6df] bg-white shadow-[0_8px_24px_rgba(27,58,35,0.04)]">
      <div className="border-b border-[#edf1ed] bg-[linear-gradient(110deg,#f7fbf7_0%,#ffffff_58%)] px-[20px] py-[13px]">
        <div className="flex items-center gap-[8px] text-[10px] font-medium text-[#3d854d]">
          <Sparkles className="size-[13px]" /> QQQ 发现了一种可以长期复用的好做法
        </div>
      </div>
      <div className="px-[20px] py-[18px]">
      <div className="flex items-start gap-[14px] max-[720px]:flex-wrap">
        <span className="grid size-[42px] shrink-0 place-items-center rounded-full bg-[#edf8ef] text-[#319447]">
          <Lightbulb className="size-[19px]" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-[8px]">
            <span className="text-[10px] font-medium text-[#858b9c]">QQQ 想学会</span>
            <span className="rounded-full bg-[#fff3d8] px-[8px] py-[3px] text-[10px] text-[#9b6500]">等你决定</span>
          </div>
          <h3 className="mt-[4px] text-[15px] font-semibold leading-[23px] text-[#18181a]">{proposal.instruction}</h3>

          <div className="mt-[14px] grid grid-cols-2 gap-[10px] max-[640px]:grid-cols-1">
            <div className="rounded-[12px] bg-[#f7f8fa] px-[14px] py-[12px]">
              <p className="text-[10px] font-medium text-[#646a78]">为什么值得学</p>
              <p className="mt-[5px] text-[11px] leading-[18px] text-[#646a78]">{evidenceText}</p>
            </div>
            <div className="rounded-[12px] bg-[#f2f8f3] px-[14px] py-[12px]">
              <p className="text-[10px] font-medium text-[#3d854d]">以后会有什么变化</p>
              <p className="mt-[5px] text-[11px] leading-[18px] text-[#45644c]">
                下次使用「{proposal.target_label}」时，QQQ 会主动按这条经验执行。
              </p>
            </div>
          </div>
          <details className="group mt-[10px]">
            <summary className="flex cursor-pointer list-none items-center gap-[4px] text-[10px] text-[#6861a3]">
              查看来源和技术细节
              <ChevronDown className="size-[12px] transition-transform group-open:rotate-180" />
            </summary>
            <div className="mt-[8px] rounded-[10px] border border-[#eceef1] bg-[#fafafa] p-[12px] text-[10px] leading-[17px] text-[#646a78]">
              <p>来自近期真实会话 · 将更新「{proposal.target_label}」</p>
              <p className="mt-[4px]">原建议名称：{proposal.title}</p>
              <pre className="mt-[8px] max-h-[220px] overflow-auto rounded-[8px] bg-[#17181b] p-[10px] leading-[16px] whitespace-pre-wrap text-[#dce1e8]">{proposal.after_content}</pre>
            </div>
          </details>
        </div>
        <div className="flex shrink-0 gap-[8px] max-[720px]:ml-[54px]">
          <button
            type="button"
            disabled={busy}
            onClick={onReject}
            className="flex h-[36px] items-center gap-[5px] rounded-[9px] border border-[#dfe2e8] px-[12px] text-[11px] text-[#646a78] hover:bg-[#f6f6f6] disabled:opacity-50"
          >
            <X className="size-[13px]" /> 这次不学
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onApply}
            className="flex h-[36px] items-center gap-[5px] rounded-[9px] bg-[#18181a] px-[14px] text-[11px] text-white hover:opacity-85 disabled:opacity-50"
          >
            <Check className="size-[13px]" /> 让 QQQ 学会
          </button>
        </div>
      </div>
      </div>
    </article>
  );
}

export default function CapabilityEvolutionPage({
  agent,
  currentUser,
  onLogout,
}: {
  agent?: AgentProfileRead;
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
}) {
  const [rows, setRows] = useState<CapabilityEvolutionProposalRead[]>([]);
  const [summary, setSummary] = useState<CapabilityEvolutionSummaryRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [learning, setLearning] = useState(false);
  const [busyId, setBusyId] = useState('');
  const [evidenceId, setEvidenceId] = useState('');

  const load = useCallback(async () => {
    if (!agent?.id) {
      setRows([]);
      setSummary(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const [proposalRows, summaryRow] = await Promise.all([
        api.get<CapabilityEvolutionProposalRead[]>(
          `/api/enterprise/agents/${encodeURIComponent(agent.id)}/evolution-proposals?tenant_id=${TENANT_ID}`,
        ),
        api.get<CapabilityEvolutionSummaryRead>(
          `/api/enterprise/agents/${encodeURIComponent(agent.id)}/evolution-summary?tenant_id=${TENANT_ID}&period_days=30`,
        ),
      ]);
      setRows(proposalRows);
      setSummary(summaryRow);
    } catch (error) {
      setRows([]);
      setSummary(null);
      notify.error(error instanceof Error ? error.message : '加载进化建议失败');
    } finally {
      setLoading(false);
    }
  }, [agent?.id]);

  useEffect(() => { void load(); }, [load]);

  const pending = useMemo(() => rows.filter((row) => row.status === 'pending'), [rows]);
  const applied = useMemo(() => rows.filter((row) => row.status === 'applied'), [rows]);
  const rejected = useMemo(() => rows.filter((row) => row.status === 'rejected'), [rows]);

  const learn = async () => {
    if (!agent?.id) return;
    setLearning(true);
    try {
      const created = await api.post<CapabilityEvolutionProposalRead[]>(
        `/api/enterprise/agents/${encodeURIComponent(agent.id)}/evolution-proposals/learn`,
        { tenant_id: TENANT_ID, period_days: 30 },
      );
      notify.success(created.length ? `QQQ 发现了 ${created.length} 条值得学习的做法` : '最近没有发现新的学习建议');
      await load();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '复盘近期会话失败');
    } finally {
      setLearning(false);
    }
  };

  const act = async (proposal: CapabilityEvolutionProposalRead, action: 'apply' | 'reject') => {
    if (!agent?.id) return;
    setBusyId(proposal.id);
    try {
      await api.post(
        `/api/enterprise/agents/${encodeURIComponent(agent.id)}/evolution-proposals/${encodeURIComponent(proposal.id)}/${action}`,
        { tenant_id: TENANT_ID },
      );
      notify.success(action === 'apply' ? 'QQQ 已学会这条做法' : '已跳过这条学习建议');
      await load();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '处理建议失败');
    } finally {
      setBusyId('');
    }
  };

  if (loading) return <LoadingPage />;
  if (!agent) return <main className="p-[24px]"><EmptyState>请选择一名数字员工</EmptyState></main>;

  const percent = (value: number, total: number) => total > 0 ? Math.round((value / total) * 100) : 0;
  const conversionMetrics = [
    { label: '能力参与率', value: percent(summary?.skill_work || 0, summary?.completed_work || 0), detail: `${summary?.skill_work || 0} / ${summary?.completed_work || 0} 项工作`, color: '#22a559' },
    { label: '沉淀转化率', value: percent(summary?.learned_count || 0, summary?.skill_work || 0), detail: `${summary?.learned_count || 0} / ${summary?.skill_work || 0} 次验证`, color: '#7b68c6' },
    { label: '复用验证率', value: percent(summary?.reuse_count || 0, summary?.learned_count || 0), detail: `${summary?.reuse_count || 0} / ${summary?.learned_count || 0} 条经验`, color: '#5688dc' },
    { label: '建议采纳率', value: percent(applied.length, applied.length + rejected.length), detail: `${applied.length} 采纳 · ${rejected.length} 跳过`, color: '#e5a233' },
  ];

  return (
    <main className="mx-auto min-h-full w-full max-w-[1220px] px-[24px] pt-[18px] pb-[40px] max-[900px]:px-0">
      <AppHeader
        onLogout={onLogout}
        userName={currentUser?.username}
        className="mb-[20px]"
        left={(
          <div className="flex min-h-[40px] items-center justify-between gap-[18px] pr-[10px] max-[700px]:flex-col max-[700px]:items-start">
            <div>
              <h1 className="text-[26px] leading-[34px] font-semibold tracking-[-0.02em] text-[#18181a]">能力进化</h1>
              <p className="mt-[4px] text-[11px] text-[#9298a4]">{employeeDisplayName(agent)} 近 30 天的能力验证、沉淀与复用</p>
            </div>
            <button
              type="button"
              disabled={learning}
              onClick={learn}
              className="flex h-[40px] items-center gap-[7px] rounded-[10px] bg-[#18181a] px-[16px] text-[11px] text-white hover:opacity-85 disabled:opacity-50"
            >
              <RefreshCw className={cn('size-[14px]', learning && 'animate-spin')} />
              {learning ? '正在检查' : '检查最近工作'}
            </button>
          </div>
        )}
      />

      <section className="grid grid-cols-4 gap-[12px] max-[900px]:grid-cols-2 max-[480px]:grid-cols-1" aria-label="能力进化指标">
        <EvolutionMetric label="完成工作" value={summary?.completed_work || 0} tone="success" icon={<CircleCheckBig className="size-[18px]" />} />
        <EvolutionMetric label="能力验证" value={summary?.skill_work || 0} icon={<SearchCheck className="size-[18px]" />} />
        <EvolutionMetric label="已经学会" value={summary?.learned_count || 0} tone="violet" icon={<BrainCircuit className="size-[18px]" />} />
        <EvolutionMetric label="后续复用" value={summary?.reuse_count || 0} icon={<Repeat2 className="size-[18px]" />} />
      </section>

      <section className="mt-[14px] grid grid-cols-2 gap-[14px] max-[900px]:grid-cols-1">
        <article className="rounded-[16px] border-[0.5px] border-[#e3e7f1] bg-white px-[18px] pt-[16px] pb-[8px]">
          <header className="flex items-start justify-between">
            <div><h2 className="text-[15px] font-semibold text-[#18181a]">进化路径</h2><p className="mt-[2px] text-[9px] text-[#a0a5b1]">从真实工作到后续复用</p></div>
            <Sparkles className="size-[17px] text-[#6861a3]" />
          </header>
          <EvolutionFunnelChart summary={summary} />
        </article>
        <article className="rounded-[16px] border-[0.5px] border-[#e3e7f1] bg-white px-[18px] pt-[16px] pb-[8px]">
          <header className="flex items-start justify-between">
            <div><h2 className="text-[15px] font-semibold text-[#18181a]">能力使用情况</h2><p className="mt-[2px] text-[9px] text-[#a0a5b1]">各项能力的验证、学会与复用次数</p></div>
            <BarChart3 className="size-[17px] text-[#249358]" />
          </header>
          <SkillProgressChart summary={summary} />
        </article>
      </section>

      <section className="mt-[14px] grid grid-cols-4 gap-[10px] max-[900px]:grid-cols-2 max-[520px]:grid-cols-1" aria-label="进化转化效率">
        {conversionMetrics.map((metric) => (
          <article key={metric.label} className="rounded-[14px] border-[0.5px] border-[#e3e7f1] bg-white px-[14px] py-[12px]">
            <div className="flex items-start justify-between gap-[8px]"><div><p className="text-[10px] text-[#858b9c]">{metric.label}</p><strong className="mt-[5px] block text-[20px] leading-none font-semibold text-[#18181a]">{metric.value}%</strong></div><span className="rounded-full bg-[#f5f6f8] px-[7px] py-[4px] text-[9px] text-[#858b9c]">{metric.detail}</span></div>
            <div className="mt-[10px] h-[5px] overflow-hidden rounded-full bg-[#eef0f3]"><div className="h-full rounded-full transition-[width]" style={{ width: `${Math.max(metric.value, metric.value ? 4 : 0)}%`, backgroundColor: metric.color }} /></div>
          </article>
        ))}
      </section>

      <section className="mt-[14px]">
        <div className="mb-[9px] flex items-end justify-between gap-[12px]"><div><h2 className="text-[15px] font-semibold text-[#18181a]">能力资产</h2><p className="mt-[2px] text-[9px] text-[#a0a5b1]">每项能力的真实使用、验证和沉淀情况</p></div><span className="text-[10px] text-[#858b9c]">{summary?.skills.length || 0} 项能力</span></div>
        <div className="grid grid-cols-3 gap-[10px] max-[900px]:grid-cols-1">
          {(summary?.skills || []).map((skill) => {
            const stage = skill.reuse_count > 0 ? '已复用' : skill.learned_count > 0 ? '已沉淀' : skill.verified_count > 0 ? '验证中' : '待积累';
            const stageTone = skill.reuse_count > 0 ? 'bg-[#eaf8ef] text-[#249358]' : skill.learned_count > 0 ? 'bg-[#f0edff] text-[#6861a3]' : skill.verified_count > 0 ? 'bg-[#edf5ff] text-[#4778bd]' : 'bg-[#f3f4f6] text-[#858b9c]';
            return (
              <article key={skill.skill_id} className="rounded-[16px] border-[0.5px] border-[#e3e7f1] bg-white p-[15px]">
                <div className="flex items-start justify-between gap-[10px]"><div className="min-w-0"><p className="truncate text-[12px] font-semibold text-[#18181a]">{skill.label}</p><p className="mt-[4px] text-[9px] text-[#9298a4]">最近使用 {formatDay(skill.last_used_at)}</p></div><span className={cn('shrink-0 rounded-full px-[8px] py-[4px] text-[9px]', stageTone)}>{stage}</span></div>
                <div className="mt-[13px] grid grid-cols-4 divide-x divide-[#eceef1] rounded-[11px] bg-[#fafbfc] py-[9px] text-center">
                  <div><strong className="block text-[15px] font-semibold text-[#18181a]">{skill.work_count}</strong><span className="mt-[2px] block text-[8px] text-[#9298a4]">触发</span></div>
                  <div><strong className="block text-[15px] font-semibold text-[#249358]">{skill.verified_count}</strong><span className="mt-[2px] block text-[8px] text-[#9298a4]">验证</span></div>
                  <div><strong className="block text-[15px] font-semibold text-[#6861a3]">{skill.learned_count}</strong><span className="mt-[2px] block text-[8px] text-[#9298a4]">学会</span></div>
                  <div><strong className="block text-[15px] font-semibold text-[#5688dc]">{skill.reuse_count}</strong><span className="mt-[2px] block text-[8px] text-[#9298a4]">复用</span></div>
                </div>
                <p className="mt-[10px] text-[9px] leading-[15px] text-[#858b9c]">{stage === '已复用' ? '经验已在后续真实任务中再次生效' : stage === '已沉淀' ? '已形成规则，等待下一次同类任务验证' : stage === '验证中' ? '已有真实实践，继续积累可学习证据' : '尚未产生经过工具验证的真实实践'}</p>
              </article>
            );
          })}
        </div>
      </section>

      <section className="mt-[14px]">
        <div className="mb-[9px] flex items-end justify-between gap-[12px]"><h2 className="text-[15px] font-semibold text-[#18181a]">需要你处理</h2><div className="flex items-center gap-[10px] text-[9px] text-[#858b9c]"><span>{summary?.proposed_count || rows.length} 条建议</span><span>{pending.length} 待确认</span><span>{applied.length} 已采纳</span><span>{rejected.length} 已跳过</span></div></div>
        <div className="space-y-[10px]">
          {pending.length ? pending.map((proposal) => <ProposalCard key={proposal.id} proposal={proposal} busy={busyId === proposal.id} onApply={() => void act(proposal, 'apply')} onReject={() => void act(proposal, 'reject')} />) : (
            <div className="flex items-center gap-[9px] rounded-[13px] border border-[#e4e9e5] bg-white px-[15px] py-[12px] text-[11px] text-[#69716b]"><CircleCheckBig className="size-[15px] text-[#319447]" />目前没有需要确认的新做法</div>
          )}
        </div>
      </section>

      <section className="mt-[14px] grid grid-cols-[minmax(0,1.35fr)_minmax(280px,0.65fr)] gap-[14px] max-[900px]:grid-cols-1">
        <article className="overflow-hidden rounded-[16px] border-[0.5px] border-[#e3e7f1] bg-white">
          <header className="flex h-[52px] items-center justify-between border-b border-[#eceef1] px-[18px]"><h2 className="text-[15px] font-semibold text-[#18181a]">已经学会</h2><span className="text-[10px] text-[#858b9c]">{applied.length} 条做法</span></header>
          {applied.length ? <div className="divide-y divide-[#edf0ed]">{applied.map((proposal) => (
            <div key={proposal.id}>
              <div className="flex items-start gap-[13px] px-[18px] py-[15px]">
                <span className="grid size-[34px] shrink-0 place-items-center rounded-full bg-[#edf8ef] text-[#319447]"><CircleCheckBig className="size-[17px]" /></span>
                <div className="min-w-0 flex-1"><p className="text-[12px] font-medium leading-[20px] text-[#18181a]">{proposal.instruction}</p><div className="mt-[6px] flex flex-wrap gap-x-[14px] gap-y-[4px] text-[10px] text-[#858b9c]"><span>用于 {proposal.target_label}</span><span>{formatTime(proposal.applied_at)} 学会</span><span className="flex items-center gap-[4px]"><History className="size-[12px]" />复用 {proposal.reuse_count} 次</span></div><button type="button" aria-expanded={evidenceId === proposal.id} onClick={() => setEvidenceId(evidenceId === proposal.id ? '' : proposal.id)} className="mt-[9px] flex items-center gap-[4px] text-[10px] font-medium text-[#3d854d]">{evidenceId === proposal.id ? '收起依据' : '查看学习依据'}<ChevronDown className={cn('size-[12px] transition-transform', evidenceId === proposal.id && 'rotate-180')} /></button></div>
              </div>
              {evidenceId === proposal.id && <div className="border-t border-[#edf0ed] bg-[#fafbfa] px-[18px] py-[13px]"><p className="text-[10px] leading-[17px] text-[#646a78]">这条做法来自经过工具验证的真实工作：</p><div className="mt-[8px] grid grid-cols-2 gap-[7px] max-[680px]:grid-cols-1">{(summary?.recent_activity || []).filter((item) => item.skill_label === proposal.target_label).map((item) => <div key={item.session_id} className="rounded-[9px] border border-[#e7eae7] bg-white px-[10px] py-[8px]"><p className="truncate text-[10px] font-medium text-[#35363b]">{item.title}</p><p className="mt-[2px] text-[9px] text-[#969ba6]">{formatDay(item.occurred_at)}</p></div>)}</div></div>}
            </div>
          ))}</div> : <div className="p-[14px]"><EmptyState>确认后的学习建议会出现在这里。</EmptyState></div>}
        </article>

        <article className="overflow-hidden rounded-[16px] border-[0.5px] border-[#e3e7f1] bg-white">
          <header className="flex h-[52px] items-center justify-between border-b border-[#eceef1] px-[16px]"><h2 className="text-[15px] font-semibold text-[#18181a]">最近能力实践</h2><SearchCheck className="size-[16px] text-[#249358]" /></header>
          {(summary?.recent_activity || []).length ? <div className="divide-y divide-[#eceef1]">{(summary?.recent_activity || []).slice(0, 6).map((item) => <div key={`${item.session_id}-${item.occurred_at}`} className="px-[16px] py-[11px]"><p className="truncate text-[11px] font-medium text-[#35363b]">{item.title}</p><div className="mt-[4px] flex items-center justify-between gap-[8px] text-[9px] text-[#9298a4]"><span className="truncate">{item.skill_label}</span><span className="shrink-0">{formatDay(item.occurred_at)}</span></div></div>)}</div> : <div className="grid min-h-[150px] place-items-center px-[16px] text-[11px] text-[#9298a4]">暂无能力实践记录</div>}
        </article>
      </section>

    </main>
  );
}
