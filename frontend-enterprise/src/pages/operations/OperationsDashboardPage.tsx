import {
  ArrowUpRight,
  BarChart3,
  BookOpen,
  CalendarDays,
  ChevronDown,
  CircleCheck,
  FileText,
  Layers3,
  ListTodo,
  Sparkles,
  TrendingUp,
  Workflow,
  Wrench,
  Zap,
} from 'lucide-react';
import {
  LineChart,
  PieChart,
  type LineSeriesOption,
  type PieSeriesOption,
} from 'echarts/charts';
import {
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
  type GridComponentOption,
  type LegendComponentOption,
  type TitleComponentOption,
  type TooltipComponentOption,
} from 'echarts/components';
import * as echarts from 'echarts/core';
import type { ComposeOption } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';

import { api, TENANT_ID } from '@/api/client';
import type { EnterpriseAuthUser } from '@/auth';
import AppHeader from '@/components/AppHeader';
import { notify } from '@/components/ui/app-toast';
import { employeeDisplayName } from '@/employee';
import { EnterpriseRoute } from '@/enums/routes';
import { cn } from '@/lib/utils';
import type {
  AgentOperationsItemRead,
  AgentOperationsSummaryRead,
  AgentProfileRead,
  CapabilityEvolutionSummaryRead,
} from '@/types';

type DashboardView = 'overview' | 'today';
type PeriodKey = '7d' | '30d';
type Tone = 'neutral' | 'warning' | 'success' | 'violet';
type DashboardChartOption = ComposeOption<
  LineSeriesOption
  | PieSeriesOption
  | GridComponentOption
  | LegendComponentOption
  | TitleComponentOption
  | TooltipComponentOption
>;

echarts.use([
  LineChart,
  PieChart,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
  CanvasRenderer,
]);

type WorkItem = {
  id: string;
  title: string;
  description: string;
  status: string;
  timestamp: string;
  tone: Tone;
  sessionId?: string;
  route?: EnterpriseRoute;
};

function dateValue(value?: string | null): number {
  if (!value) return 0;
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatShortTime(value?: string | null): string {
  const timestamp = dateValue(value);
  if (!timestamp) return '--';
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(timestamp);
}

function formatShortDate(value?: string | null): string {
  const timestamp = dateValue(value);
  if (!timestamp) return '--';
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' }).format(timestamp);
}

function statusTone(status: string): Tone {
  if (status === '待确认' || status === '待回答') return 'warning';
  if (status === '已完成' || status === '已验证完成') return 'success';
  if (status === '已沉淀') return 'violet';
  return 'neutral';
}

function toWorkItem(item: AgentOperationsItemRead): WorkItem {
  return {
    id: item.id,
    title: item.title,
    description: item.description,
    status: item.status,
    timestamp: item.timestamp,
    tone: statusTone(item.status),
    sessionId: item.session_id,
    route: item.kind === 'scheduled_run' || item.kind === 'scheduled_task'
      ? EnterpriseRoute.ScheduledTasks
      : item.kind === 'evolution_proposal'
        ? EnterpriseRoute.Evolution
        : undefined,
  };
}

function KpiCard({
  label,
  value,
  tone,
  icon,
}: {
  label: string;
  value: number;
  tone: Tone;
  icon: ReactNode;
}) {
  return (
    <article
      className={cn(
        'flex min-h-[92px] items-center justify-between rounded-[16px] border-[0.5px] px-[17px] py-[15px]',
        tone === 'success' && 'border-transparent bg-[#eaf8ef]',
        tone === 'violet' && 'border-transparent bg-[#f4f2ff]',
        tone === 'warning' && 'border-[#f2e4c7] bg-white',
        tone === 'neutral' && 'border-[#e3e7f1] bg-white',
      )}
    >
      <div className="min-w-0">
        <p
          className={cn(
            'text-[12px] leading-none text-[#858b9c]',
            tone === 'success' && 'text-[#27965b]',
            tone === 'violet' && 'text-[#6f68a8]',
          )}
        >
          {label}
        </p>
        <strong
          className={cn(
            'mt-[10px] block text-[27px] leading-none font-semibold text-[#18181a]',
            tone === 'success' && 'text-[#20a35a]',
            tone === 'violet' && 'text-[#5d5793]',
          )}
        >
          {value}
        </strong>
      </div>
      <span
        className={cn(
          'grid size-[40px] shrink-0 place-items-center rounded-full bg-[#f6f6f6] text-[#18181a]',
          tone === 'warning' && 'bg-[#fff6e5]',
          tone === 'success' && 'bg-[#d9f2e2] text-[#20a35a]',
          tone === 'violet' && 'bg-[#e7e3fa] text-[#6861a3]',
        )}
      >
        {icon}
      </span>
    </article>
  );
}

function StatusBadge({ label, tone }: { label: string; tone: Tone }) {
  return (
    <span
      className={cn(
        'inline-flex h-[24px] items-center rounded-full px-[10px] text-[10px] leading-none',
        tone === 'neutral' && 'bg-[#f1f2f4] text-[#858b9c]',
        tone === 'warning' && 'bg-[#ffe9bc] text-[#8b5b00]',
        tone === 'success' && 'bg-[#eaf8ef] text-[#249358]',
        tone === 'violet' && 'bg-[#f0edff] text-[#6861a3]',
      )}
    >
      {label}
    </span>
  );
}

function EmptyPanel({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-[118px] place-items-center rounded-[12px] border border-dashed border-[#e3e7f1] bg-[#fafbfc] px-[20px] text-center text-[12px] text-[#858b9c]">
      {children}
    </div>
  );
}

function LoadingPage() {
  return (
    <div className="mx-auto min-h-full w-full max-w-[1220px] px-[24px] pt-[24px] pb-[40px] max-[900px]:px-0">
      <div className="h-[64px] animate-pulse rounded-[16px] bg-white" />
      <div className="mt-[20px] grid grid-cols-4 gap-[16px] max-[900px]:grid-cols-2">
        {[0, 1, 2, 3].map((item) => (
          <div key={item} className="h-[104px] animate-pulse rounded-[16px] bg-white" />
        ))}
      </div>
      <div className="mt-[16px] h-[420px] animate-pulse rounded-[16px] bg-white" />
    </div>
  );
}

function ReplyTrendChart({ points }: { points: Array<{ label: string; value: number }> }) {
  const option = useMemo<DashboardChartOption>(() => ({
    animationDuration: 500,
    grid: { top: 22, right: 12, bottom: 24, left: 30 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#18181a',
      borderWidth: 0,
      textStyle: { color: '#ffffff', fontSize: 11 },
      axisPointer: { type: 'line', lineStyle: { color: '#cfd6d0', type: 'dashed' } },
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: points.map((point) => point.label),
      axisLine: { lineStyle: { color: '#e6e9ed' } },
      axisTick: { show: false },
      axisLabel: { color: '#9298a4', fontSize: 9 },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLabel: { color: '#9298a4', fontSize: 9 },
      splitLine: { lineStyle: { color: '#eceef1', type: 'dashed' } },
    },
    series: [{
      name: '有效完成',
      type: 'line',
      data: points.map((point) => point.value),
      smooth: 0.32,
      symbol: 'circle',
      symbolSize: 6,
      showSymbol: true,
      lineStyle: { color: '#22a559', width: 2.5 },
      itemStyle: { color: '#22a559', borderColor: '#ffffff', borderWidth: 1.5 },
      areaStyle: { color: 'rgba(34,165,89,0.08)' },
    }],
  }), [points]);

  return <ReactEChartsCore echarts={echarts} option={option} style={{ height: 185, width: '100%' }} notMerge lazyUpdate />;
}

function WorkList({
  items,
  onOpen,
}: {
  items: WorkItem[];
  onOpen: (item: WorkItem) => void;
}) {
  if (items.length === 0) {
    return <EmptyPanel>当前没有需要关注的工作</EmptyPanel>;
  }
  return (
    <div className="divide-y divide-[#eceef1]">
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          onClick={() => onOpen(item)}
          className={cn(
            'grid w-full grid-cols-[minmax(0,1fr)_96px_92px] items-center gap-[12px] px-[20px] py-[14px] text-left transition-colors hover:bg-[#fafbfc] max-[700px]:grid-cols-[minmax(0,1fr)_auto]',
            item.tone === 'warning' && 'bg-[#fff9ed] hover:bg-[#fff6e5]',
          )}
        >
          <span className="flex min-w-0 items-center gap-[12px]">
            <span
              className={cn(
                'grid size-[34px] shrink-0 place-items-center rounded-full bg-[#f6f6f6] text-[#858b9c]',
                item.tone === 'warning' && 'bg-white text-[#9b6500]',
                item.tone === 'success' && 'bg-[#eaf8ef] text-[#249358]',
              )}
            >
              {item.tone === 'warning' ? <FileText className="size-[16px]" /> : <ListTodo className="size-[16px]" />}
            </span>
            <span className="min-w-0">
              <strong className="block truncate text-[12px] font-medium text-[#18181a]">{item.title}</strong>
              <span className="mt-[3px] block truncate text-[10px] text-[#a0a5b1]">{item.description}</span>
            </span>
          </span>
          <span className="max-[700px]:justify-self-end"><StatusBadge label={item.status} tone={item.tone} /></span>
          <span className="flex items-center justify-end gap-[6px] text-[10px] text-[#858b9c] max-[700px]:hidden">
            {formatShortTime(item.timestamp)}
            <ArrowUpRight className="size-[13px]" />
          </span>
        </button>
      ))}
    </div>
  );
}

const OUTPUT_COLORS = ['#22a559', '#5688dc', '#7b68c6', '#e5a233', '#a3a8b2'];

function outputType(title: string): string {
  const normalized = title.toLowerCase();
  if (normalized.includes('asin') || title.includes('选品')) return '选品报告';
  if (title.includes('价格') || title.includes('竞品')) return '数据看板';
  if (normalized.includes('listing') || title.includes('优化')) return '优化建议';
  if (title.includes('关键词')) return '关键词清单';
  return '研究报告';
}

function RecentResultsTable({ items, onOpen }: { items: WorkItem[]; onOpen: (item: WorkItem) => void }) {
  if (!items.length) return <EmptyPanel>当前周期还没有完成成果</EmptyPanel>;
  return (
    <div className="divide-y divide-[#eceef1]">
      {items.slice(0, 6).map((item) => (
        <button
          key={item.id}
          type="button"
          onClick={() => onOpen(item)}
          className="grid w-full grid-cols-[minmax(0,1fr)_72px_86px_72px] items-center gap-[10px] px-[18px] py-[12px] text-left transition-colors hover:bg-[#fafbfc] max-[720px]:grid-cols-[minmax(0,1fr)_auto]"
        >
          <span className="flex min-w-0 items-center gap-[10px]">
            <span className="grid size-[32px] shrink-0 place-items-center rounded-[9px] bg-[#edf8f0] text-[#249358]">
              <FileText className="size-[15px]" />
            </span>
            <span className="min-w-0">
              <strong className="block truncate text-[11px] font-medium text-[#18181a]">{item.title}</strong>
              <span className="mt-[2px] block truncate text-[9px] text-[#a0a5b1]">{item.description}</span>
            </span>
          </span>
          <span className="text-[10px] text-[#858b9c] max-[720px]:hidden">{formatShortDate(item.timestamp)}</span>
          <span className="text-[10px] text-[#646a78] max-[720px]:hidden">{outputType(item.title)}</span>
          <span className="flex items-center justify-end gap-[5px] text-[10px] text-[#249358]">
            <CircleCheck className="size-[13px]" /> 完成
          </span>
        </button>
      ))}
    </div>
  );
}

function WorkComposition({ items }: { items: WorkItem[] }) {
  const groups = useMemo(() => {
    const counts = new Map<string, number>();
    items.forEach((item) => counts.set(outputType(item.title), (counts.get(outputType(item.title)) || 0) + 1));
    return [...counts.entries()].sort((left, right) => right[1] - left[1]);
  }, [items]);
  const total = groups.reduce((sum, [, count]) => sum + count, 0);
  const option = useMemo<DashboardChartOption>(() => ({
    animationDuration: 500,
    color: OUTPUT_COLORS,
    title: {
      text: String(total),
      subtext: '成果',
      left: '27%',
      top: '33%',
      textAlign: 'center',
      textStyle: { color: '#18181a', fontSize: 21, fontWeight: 600 },
      subtextStyle: { color: '#9298a4', fontSize: 9, lineHeight: 16 },
    },
    tooltip: {
      trigger: 'item',
      backgroundColor: '#18181a',
      borderWidth: 0,
      textStyle: { color: '#ffffff', fontSize: 10 },
      formatter: '{b}：{c}',
    },
    legend: {
      orient: 'vertical',
      right: 4,
      top: 'middle',
      itemWidth: 7,
      itemHeight: 7,
      itemGap: 8,
      textStyle: { color: '#646a78', fontSize: 9 },
      formatter: (name: string) => {
        const count = groups.find(([label]) => label === name)?.[1] || 0;
        return `${name}  ${count}`;
      },
    },
    series: [{
      type: 'pie',
      radius: ['48%', '68%'],
      center: ['28%', '50%'],
      avoidLabelOverlap: true,
      label: { show: false },
      labelLine: { show: false },
      itemStyle: { borderColor: '#ffffff', borderWidth: 2 },
      emphasis: { scaleSize: 4 },
      data: groups.slice(0, 5).map(([name, value]) => ({ name, value })),
    }],
  }), [groups, total]);

  return <ReactEChartsCore echarts={echarts} option={option} style={{ height: 132, width: '100%' }} notMerge lazyUpdate />;
}

export default function OperationsDashboardPage({
  view,
  agent,
  currentUser,
  onLogout,
}: {
  view: DashboardView;
  agent?: AgentProfileRead;
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
}) {
  const navigate = useNavigate();
  const [period, setPeriod] = useState<PeriodKey>('7d');
  const [data, setData] = useState<AgentOperationsSummaryRead | null>(null);
  const [evolution, setEvolution] = useState<CapabilityEvolutionSummaryRead | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    if (!agent?.id) {
      setData(null);
      setEvolution(null);
      setLoading(false);
      return () => { cancelled = true; };
    }

    setLoading(true);
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai';
    const periodDays = period === '7d' ? 7 : 30;
    Promise.all([
      api.get<AgentOperationsSummaryRead>(
        `/api/enterprise/agents/${encodeURIComponent(agent.id)}/operations-summary?tenant_id=${TENANT_ID}&period_days=${periodDays}&timezone=${encodeURIComponent(timezone)}`,
      ),
      api.get<CapabilityEvolutionSummaryRead>(
        `/api/enterprise/agents/${encodeURIComponent(agent.id)}/evolution-summary?tenant_id=${TENANT_ID}&period_days=${periodDays}`,
      ),
    ])
      .then(([operationsResult, evolutionResult]) => {
        if (!cancelled) {
          setData(operationsResult);
          setEvolution(evolutionResult);
        }
      })
      .catch((error) => {
        if (cancelled) return;
        setData(null);
        setEvolution(null);
        notify.error(error instanceof Error ? error.message : '加载经营数据失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [agent?.id, period]);

  const todayItems = useMemo(
    () => (data?.today_items || []).map(toWorkItem),
    [data?.today_items],
  );
  const recentItems = useMemo(
    () => (data?.recent_items || []).map(toWorkItem),
    [data?.recent_items],
  );
  const pendingConfirmations = useMemo(
    () => (data?.attention_items || []).filter((item) => item.status === '待确认'),
    [data?.attention_items],
  );
  const capabilityChanges = data?.capability_changes || [];
  const latestCapabilityChange = capabilityChanges[0];
  const trendPoints = useMemo(
    () => (data?.completion_trend || []).map((point) => {
      const date = new Date(`${point.date}T00:00:00`);
      return { label: `${date.getMonth() + 1}/${date.getDate()}`, value: point.value };
    }),
    [data?.completion_trend],
  );

  const openWorkItem = (item: WorkItem) => {
    if (item.sessionId) navigate(`${EnterpriseRoute.Chat}/${encodeURIComponent(item.sessionId)}`);
    else if (item.route) navigate(item.route);
  };

  if (loading) return <LoadingPage />;
  if (!agent) {
    return (
      <div className="mx-auto min-h-full w-full max-w-[1220px] px-[24px] pt-[24px] pb-[40px] max-[900px]:px-0">
        <EmptyPanel>请选择一名数字员工后查看经营数据</EmptyPanel>
      </div>
    );
  }

  const agentName = employeeDisplayName(agent);
  const metricValues = {
    completed: data?.metrics.completed || 0,
    effectiveTasks: data?.metrics.effective_tasks || 0,
    skillWork: evolution?.skill_work || 0,
    capabilityChanges: data?.metrics.capability_changes || 0,
    reuse: evolution?.reuse_count || 0,
  };
  const activeSkills = (evolution?.skills || []).filter((skill) => skill.work_count > 0);
  const sopCount = agent.resources.filter((resource) => resource.status === 'active' && resource.resource_type === 'skill').length;

  return (
    <main className="mx-auto min-h-full w-full max-w-[1220px] px-[24px] pt-[18px] pb-[40px] max-[900px]:px-0">
      <AppHeader
        onLogout={onLogout}
        userName={currentUser?.username}
        className="mb-[20px]"
        left={(
          <div className="flex min-h-[54px] items-start justify-between gap-[18px] pr-[10px] max-[700px]:flex-col">
            <div>
              <h1 className="text-[26px] leading-[34px] font-semibold tracking-[-0.02em] text-[#18181a]">
                {view === 'overview' ? '经营总览' : '早上好，老板'}
              </h1>
              {view === 'today' && (
                <p className="mt-[5px] text-[12px] leading-[18px] text-[#858b9c]">
                  {agentName} 今天有 {todayItems.length} 项工作，{pendingConfirmations.length} 项需要你确认
                </p>
              )}
            </div>
            {view === 'overview' ? (
              <label className="relative shrink-0">
                <CalendarDays className="pointer-events-none absolute top-[12px] left-[12px] size-[15px] text-[#858b9c]" />
                <select
                  aria-label="经营数据周期"
                  value={period}
                  onChange={(event) => setPeriod(event.target.value as PeriodKey)}
                  className="h-[40px] appearance-none rounded-[10px] border-[0.5px] border-[#e3e7f1] bg-white pr-[34px] pl-[38px] text-[12px] text-[#18181a] outline-none transition-shadow focus:ring-2 focus:ring-[#18181a]/10"
                >
                  <option value="7d">近 7 天</option>
                  <option value="30d">近 30 天</option>
                </select>
                <ChevronDown className="pointer-events-none absolute top-[13px] right-[12px] size-[14px] text-[#858b9c]" />
              </label>
            ) : (
              <div className="flex h-[40px] shrink-0 items-center gap-[8px] rounded-[10px] border-[0.5px] border-[#e3e7f1] bg-white px-[13px] text-[12px] text-[#464c5e]">
                <CalendarDays className="size-[15px] text-[#858b9c]" />
                {new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date())}
              </div>
            )}
          </div>
        )}
      />

      {view === 'overview' ? (
        <>
          <section className="grid grid-cols-5 gap-[12px] max-[1080px]:grid-cols-3 max-[760px]:grid-cols-2 max-[480px]:grid-cols-1" aria-label="经营指标">
            <KpiCard label={period === '7d' ? '本周完成' : '近 30 天完成'} value={metricValues.completed} tone="success" icon={<CircleCheck className="size-[18px]" />} />
            <KpiCard label="有效任务" value={metricValues.effectiveTasks} tone="neutral" icon={<ListTodo className="size-[18px]" />} />
            <KpiCard label="专业能力调用" value={metricValues.skillWork} tone="neutral" icon={<Zap className="size-[18px]" />} />
            <KpiCard label="已沉淀" value={metricValues.capabilityChanges} tone="violet" icon={<Layers3 className="size-[18px]" />} />
            <KpiCard label="后续复用" value={metricValues.reuse} tone="neutral" icon={<TrendingUp className="size-[18px]" />} />
          </section>

          <section className="mt-[14px] grid grid-cols-[minmax(0,1.55fr)_minmax(300px,0.9fr)] gap-[14px] max-[980px]:grid-cols-1">
            <article className="overflow-hidden rounded-[16px] border-[0.5px] border-[#e3e7f1] bg-white">
              <header className="flex h-[54px] items-center justify-between border-b border-[#eceef1] px-[18px]">
                <h2 className="text-[15px] font-semibold text-[#18181a]">最近成果</h2>
                <button type="button" onClick={() => navigate(EnterpriseRoute.Today)} className="flex items-center gap-[4px] text-[10px] text-[#858b9c] hover:text-[#18181a]">
                  查看全部 <ArrowUpRight className="size-[12px]" />
                </button>
              </header>
              <div className="grid grid-cols-[minmax(0,1fr)_72px_86px_72px] gap-[10px] border-b border-[#eceef1] px-[18px] py-[8px] text-[9px] text-[#a0a5b1] max-[720px]:hidden">
                <span>工作名称</span><span>日期</span><span>产出类型</span><span className="text-right">状态</span>
              </div>
              <RecentResultsTable items={recentItems} onOpen={openWorkItem} />
            </article>

            <div className="grid gap-[14px]">
              <article className="rounded-[16px] border-[0.5px] border-[#e3e7f1] bg-white px-[18px] pt-[16px] pb-[8px]">
                <header className="flex items-center justify-between">
                  <h2 className="text-[15px] font-semibold text-[#18181a]">有效完成趋势</h2>
                  <TrendingUp className="size-[17px] text-[#858b9c]" />
                </header>
                <ReplyTrendChart points={trendPoints} />
              </article>
              <article className="rounded-[16px] border-[0.5px] border-[#e3e7f1] bg-white px-[18px] py-[16px]">
                <header className="mb-[8px] flex items-center justify-between">
                  <h2 className="text-[15px] font-semibold text-[#18181a]">工作成果构成</h2>
                  <BarChart3 className="size-[17px] text-[#858b9c]" />
                </header>
                <WorkComposition items={recentItems} />
              </article>
            </div>
          </section>

          <section className="mt-[14px] grid grid-cols-4 gap-[12px] max-[980px]:grid-cols-2 max-[560px]:grid-cols-1">
            <article className="rounded-[16px] border-[0.5px] border-[#e3e7f1] bg-white p-[16px]">
              <div className="flex items-center justify-between"><h2 className="text-[13px] font-semibold text-[#18181a]">能力调用</h2><Zap className="size-[16px] text-[#249358]" /></div>
              <strong className="mt-[13px] block text-[22px] font-semibold text-[#249358]">{activeSkills.length} 项技能</strong>
              <div className="mt-[10px] space-y-[6px]">{activeSkills.slice(0, 3).map((skill) => <p key={skill.skill_id} className="truncate text-[10px] text-[#646a78]">• {skill.label} <span className="float-right text-[#a0a5b1]">{skill.work_count} 次</span></p>)}</div>
            </article>
            <article className="rounded-[16px] border-[0.5px] border-[#e3e7f1] bg-white p-[16px]">
              <div className="flex items-center justify-between"><h2 className="text-[13px] font-semibold text-[#18181a]">SOP 使用</h2><Workflow className="size-[16px] text-[#6861a3]" /></div>
              <strong className="mt-[13px] block text-[22px] font-semibold text-[#18181a]">{sopCount} 个 SOP</strong>
            </article>
            <article className="rounded-[16px] border-[0.5px] border-[#e3e7f1] bg-white p-[16px]">
              <div className="flex items-center justify-between"><h2 className="text-[13px] font-semibold text-[#18181a]">工具验证研究</h2><Wrench className="size-[16px] text-[#249358]" /></div>
              <strong className="mt-[13px] block text-[22px] font-semibold text-[#249358]">{metricValues.skillWork} 项研究</strong>
            </article>
            <button type="button" disabled={!latestCapabilityChange} onClick={() => latestCapabilityChange && navigate(EnterpriseRoute.Evolution)} className="rounded-[16px] border-[0.5px] border-[#e3e7f1] bg-white p-[16px] text-left transition-colors hover:border-[#cec8ef] disabled:cursor-default">
              <div className="flex items-center justify-between"><h2 className="text-[13px] font-semibold text-[#18181a]">学习沉淀</h2><Sparkles className="size-[16px] text-[#6861a3]" /></div>
              <strong className="mt-[13px] block text-[22px] font-semibold text-[#6861a3]">{capabilityChanges.length} 条规则</strong>
              <p className="mt-[10px] line-clamp-2 text-[10px] leading-[16px] text-[#646a78]">{latestCapabilityChange?.instruction || '尚未形成新经验'}</p>
            </button>
          </section>
        </>
      ) : (
        <div className="space-y-[18px]">
          <section className="overflow-hidden rounded-[16px] border-[0.5px] border-[#e3e7f1] bg-white">
            <header className="flex h-[60px] items-center justify-between border-b border-[#eceef1] px-[20px]">
              <div className="flex items-center gap-[10px]">
                <span className="grid size-[32px] place-items-center rounded-full bg-[#f6f6f6] text-[#858b9c]"><ListTodo className="size-[16px]" /></span>
                <h2 className="text-[16px] font-semibold text-[#18181a]">正在做</h2>
              </div>
              <button type="button" onClick={() => navigate(EnterpriseRoute.Operations)} className="h-[32px] rounded-[9px] bg-[#f6f6f6] px-[14px] text-[10px] text-[#858b9c] hover:text-[#18181a]">
                查看全部工作
              </button>
            </header>
            <WorkList items={todayItems} onOpen={openWorkItem} />
          </section>

          <section>
            <h2 className="mb-[10px] text-[16px] font-semibold text-[#18181a]">等你确认</h2>
            {pendingConfirmations.length === 0 ? (
              <EmptyPanel>今天没有需要人工确认的工作</EmptyPanel>
            ) : (
              <div className="space-y-[10px]">
                {pendingConfirmations.slice(0, 3).map((handoff) => (
                  <article key={handoff.id} className="flex min-h-[124px] items-center gap-[18px] rounded-[16px] border border-[#f4e3bd] bg-[#fff9ed] px-[22px] py-[18px] max-[760px]:flex-wrap">
                    <span className="grid size-[48px] shrink-0 place-items-center rounded-full bg-white text-[#a87517]"><FileText className="size-[21px]" /></span>
                    <div className="min-w-0 flex-1">
                      <h3 className="truncate text-[14px] font-semibold text-[#18181a]">{handoff.title}</h3>
                      <p className="mt-[5px] line-clamp-2 text-[11px] leading-[18px] text-[#858b9c]">{handoff.description}</p>
                    </div>
                    <span className="text-[10px] text-[#8b6a28]">{formatShortTime(handoff.timestamp)} 更新</span>
                    <button type="button" onClick={() => handoff.kind === 'evolution_proposal' ? navigate(EnterpriseRoute.Evolution) : handoff.session_id && navigate(`${EnterpriseRoute.Chat}/${encodeURIComponent(handoff.session_id)}`)} className="h-[44px] rounded-[10px] bg-[#18181a] px-[24px] text-[12px] font-medium text-white transition-opacity hover:opacity-80">
                      打开并确认
                    </button>
                  </article>
                ))}
              </div>
            )}
          </section>

          <section>
            <h2 className="mb-[10px] text-[16px] font-semibold text-[#18181a]">本周沉淀</h2>
            <article className="flex min-h-[134px] items-center gap-[18px] rounded-[16px] border-[0.5px] border-[#e3e7f1] bg-white px-[22px] py-[18px] max-[760px]:flex-wrap">
              <span className="grid size-[48px] shrink-0 place-items-center rounded-full bg-[#f4f2ff] text-[#6861a3]"><BookOpen className="size-[21px]" /></span>
              <div className="min-w-0 flex-1">
                <h3 className="truncate text-[14px] font-semibold text-[#18181a]">{latestCapabilityChange?.instruction || '本周还没有新的沉淀'}</h3>
                <p className="mt-[5px] text-[11px] text-[#858b9c]">{latestCapabilityChange ? `已写入 ${latestCapabilityChange.label}，后续真实复用 ${latestCapabilityChange.reuse_count} 次` : '会话经验经你审核并写入 Skill 后才会显示在这里'}</p>
              </div>
              <div className="min-w-[180px] border-l border-[#eceef1] pl-[24px] max-[760px]:border-l-0 max-[760px]:pl-0">
                <p className="text-[10px] text-[#858b9c]">本周变化</p>
                <p className="mt-[5px] text-[13px] font-medium text-[#18181a]">{capabilityChanges.length} 项已沉淀</p>
              </div>
              <button type="button" disabled={!latestCapabilityChange} onClick={() => latestCapabilityChange && navigate(EnterpriseRoute.Evolution)} className="h-[40px] rounded-[10px] border-[0.5px] border-[#d9dce3] bg-white px-[18px] text-[11px] text-[#18181a] hover:bg-[#f6f6f6] disabled:cursor-not-allowed disabled:opacity-40">
                查看沉淀详情
              </button>
            </article>
          </section>
        </div>
      )}
    </main>
  );
}
