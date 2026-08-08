import {
  ArrowUpRight,
  BookOpen,
  CalendarDays,
  ChevronDown,
  CircleCheck,
  Clock,
  FileText,
  Layers3,
  ListTodo,
  Play,
  Sparkles,
  TrendingUp,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
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
} from '@/types';

type DashboardView = 'overview' | 'today';
type PeriodKey = '7d' | '30d';
type Tone = 'neutral' | 'warning' | 'success' | 'violet';

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
        'flex min-h-[104px] items-center justify-between rounded-[16px] border-[0.5px] px-[20px] py-[18px]',
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
            'mt-[12px] block text-[30px] leading-none font-semibold text-[#18181a]',
            tone === 'success' && 'text-[#20a35a]',
            tone === 'violet' && 'text-[#5d5793]',
          )}
        >
          {value}
        </strong>
      </div>
      <span
        className={cn(
          'grid size-[46px] shrink-0 place-items-center rounded-full bg-[#f6f6f6] text-[#18181a]',
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
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const draw = () => {
      const box = canvas.getBoundingClientRect();
      if (!box.width || !box.height) return;
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.round(box.width * ratio);
      canvas.height = Math.round(box.height * ratio);
      const context = canvas.getContext('2d');
      if (!context) return;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, box.width, box.height);

      const padding = { top: 28, right: 14, bottom: 34, left: 32 };
      const chartWidth = Math.max(1, box.width - padding.left - padding.right);
      const chartHeight = Math.max(1, box.height - padding.top - padding.bottom);
      const maxValue = Math.max(1, ...points.map((point) => point.value));

      context.font = '10px "Geist Variable", sans-serif';
      context.textBaseline = 'middle';
      context.strokeStyle = '#eceef1';
      context.fillStyle = '#a0a5b1';
      context.lineWidth = 1;
      for (let index = 0; index <= 4; index += 1) {
        const y = padding.top + (chartHeight * index) / 4;
        context.setLineDash([4, 5]);
        context.beginPath();
        context.moveTo(padding.left, y);
        context.lineTo(padding.left + chartWidth, y);
        context.stroke();
        const label = Math.round(maxValue * (1 - index / 4));
        context.fillText(String(label), 4, y);
      }

      const xFor = (index: number) => (
        points.length <= 1
          ? padding.left + chartWidth / 2
          : padding.left + (chartWidth * index) / (points.length - 1)
      );
      const yFor = (value: number) => padding.top + chartHeight - (value / maxValue) * chartHeight;

      if (points.length > 0) {
        context.setLineDash([]);
        context.strokeStyle = '#18181a';
        context.lineWidth = 2.5;
        context.beginPath();
        points.forEach((point, index) => {
          const x = xFor(index);
          const y = yFor(point.value);
          if (index === 0) context.moveTo(x, y);
          else context.lineTo(x, y);
        });
        context.stroke();

        points.forEach((point, index) => {
          const x = xFor(index);
          const y = yFor(point.value);
          context.fillStyle = '#18181a';
          context.beginPath();
          context.arc(x, y, 3.5, 0, Math.PI * 2);
          context.fill();
          if (points.length <= 8 || index === points.length - 1 || index % 5 === 0) {
            context.fillStyle = '#858b9c';
            context.textAlign = 'center';
            context.fillText(point.label, x, box.height - 12);
          }
        });
      }
    };

    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [points]);

  const summary = points.map((point) => `${point.label} ${point.value}`).join('，');
  return (
    <canvas
      ref={canvasRef}
      className="h-[300px] w-full"
      role="img"
      aria-label={`有效完成趋势：${summary || '暂无数据'}`}
    />
  );
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
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    if (!agent?.id) {
      setData(null);
      setLoading(false);
      return () => { cancelled = true; };
    }

    setLoading(true);
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai';
    const periodDays = period === '7d' ? 7 : 30;
    api.get<AgentOperationsSummaryRead>(
      `/api/enterprise/agents/${encodeURIComponent(agent.id)}/operations-summary?tenant_id=${TENANT_ID}&period_days=${periodDays}&timezone=${encodeURIComponent(timezone)}`,
    )
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((error) => {
        if (cancelled) return;
        setData(null);
        notify.error(error instanceof Error ? error.message : '加载经营数据失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [agent?.id, period]);

  const overviewItems = useMemo(
    () => (data?.attention_items || []).map(toWorkItem),
    [data?.attention_items],
  );
  const todayItems = useMemo(
    () => (data?.today_items || []).map(toWorkItem),
    [data?.today_items],
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
    running: data?.metrics.running || 0,
    waiting: data?.metrics.awaiting_confirmation || 0,
    completed: data?.metrics.completed || 0,
    capabilityChanges: data?.metrics.capability_changes || 0,
  };

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
              <p className="mt-[5px] text-[12px] leading-[18px] text-[#858b9c]">
                {view === 'overview'
                  ? `查看 ${agentName} 的工作进展、待确认结果与能力沉淀`
                  : `${agentName} 今天有 ${todayItems.length} 项工作，${pendingConfirmations.length} 项需要你确认`}
              </p>
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
          <section className="grid grid-cols-4 gap-[16px] max-[980px]:grid-cols-2 max-[560px]:grid-cols-1" aria-label="经营指标">
            <KpiCard label="进行中" value={metricValues.running} tone="neutral" icon={<Play className="size-[20px]" />} />
            <KpiCard label="待确认" value={metricValues.waiting} tone="warning" icon={<Clock className="size-[20px]" />} />
            <KpiCard label={period === '7d' ? '本周完成' : '近 30 天完成'} value={metricValues.completed} tone="success" icon={<CircleCheck className="size-[20px]" />} />
            <KpiCard label={period === '7d' ? '本周沉淀' : '近 30 天沉淀'} value={metricValues.capabilityChanges} tone="violet" icon={<Layers3 className="size-[20px]" />} />
          </section>

          <section className="mt-[16px] grid grid-cols-[minmax(0,1.55fr)_minmax(300px,1fr)] gap-[16px] max-[980px]:grid-cols-1">
            <article className="overflow-hidden rounded-[16px] border-[0.5px] border-[#e3e7f1] bg-white">
              <header className="flex h-[60px] items-center justify-between border-b border-[#eceef1] px-[20px]">
                <h2 className="text-[16px] font-semibold text-[#18181a]">需要关注</h2>
                <span className="text-[10px] text-[#858b9c]">全部 {overviewItems.length}</span>
              </header>
              <WorkList items={overviewItems} onOpen={openWorkItem} />
            </article>

            <article className="rounded-[16px] border-[0.5px] border-[#e3e7f1] bg-white px-[20px] pt-[19px] pb-[12px]">
              <header className="flex items-center justify-between">
                <div>
                  <h2 className="text-[16px] font-semibold text-[#18181a]">有效完成趋势</h2>
                  <p className="mt-[4px] text-[10px] text-[#a0a5b1]">按成功对话轮次与定时任务运行统计</p>
                </div>
                <TrendingUp className="size-[18px] text-[#858b9c]" />
              </header>
              <ReplyTrendChart points={trendPoints} />
            </article>
          </section>

          <section className="mt-[16px] flex min-h-[108px] items-center gap-[18px] rounded-[16px] border-[0.5px] border-[#e3e7f1] bg-white px-[20px] py-[16px] max-[760px]:flex-wrap">
            <span className="grid size-[48px] shrink-0 place-items-center rounded-full bg-[#f4f2ff] text-[#6861a3]">
              <Sparkles className="size-[21px]" />
            </span>
            <div className="min-w-0 flex-1">
              <h2 className="text-[15px] font-semibold text-[#18181a]">最近沉淀</h2>
              {latestCapabilityChange ? (
                <p className="mt-[5px] truncate text-[11px] text-[#858b9c]">
                  {latestCapabilityChange.instruction || latestCapabilityChange.label} · {formatShortDate(latestCapabilityChange.timestamp)}
                </p>
              ) : (
                <p className="mt-[5px] text-[11px] text-[#a0a5b1]">当前周期还没有经审核写入的经验</p>
              )}
            </div>
            <div className="min-w-[160px] border-l border-[#eceef1] pl-[24px] max-[760px]:border-l-0 max-[760px]:pl-0">
              <p className="text-[10px] text-[#858b9c]">当前周期</p>
              <p className="mt-[5px] text-[12px] text-[#18181a]">共 {capabilityChanges.length} 项，复用 {capabilityChanges.reduce((sum, item) => sum + item.reuse_count, 0)} 次</p>
            </div>
            <button
              type="button"
              disabled={!latestCapabilityChange}
              onClick={() => latestCapabilityChange && navigate(EnterpriseRoute.Evolution)}
              className="h-[40px] rounded-[10px] border-[0.5px] border-[#d9dce3] bg-white px-[18px] text-[11px] text-[#18181a] transition-colors hover:bg-[#f6f6f6] disabled:cursor-not-allowed disabled:opacity-40"
            >
              查看沉淀
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
