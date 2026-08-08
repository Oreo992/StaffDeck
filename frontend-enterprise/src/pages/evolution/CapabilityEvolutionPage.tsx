import { useCallback, useEffect, useMemo, useState } from 'react';
import { Check, ChevronDown, History, RefreshCw, Sparkles, X } from 'lucide-react';

import { api, TENANT_ID } from '@/api/client';
import AppHeader from '@/components/AppHeader';
import { cn } from '@/lib/utils';
import { notify } from '@/components/ui/app-toast';
import type { EnterpriseAuthUser } from '@/auth';
import type { AgentProfileRead, CapabilityEvolutionProposalRead } from '@/types';
import { employeeDisplayName } from '@/employee';


function formatTime(value?: string | null): string {
  if (!value) return '—';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
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
  return (
    <article className="rounded-[16px] border-[0.5px] border-[#e3e7f1] bg-white px-[20px] py-[18px]">
      <div className="flex items-start gap-[14px] max-[720px]:flex-wrap">
        <span className="grid size-[40px] shrink-0 place-items-center rounded-full bg-[#f4f2ff] text-[#6861a3]">
          <Sparkles className="size-[18px]" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-[8px]">
            <h3 className="text-[14px] font-semibold text-[#18181a]">{proposal.title}</h3>
            <span className="rounded-full bg-[#fff3d8] px-[8px] py-[3px] text-[10px] text-[#9b6500]">待你确认</span>
          </div>
          <p className="mt-[5px] text-[11px] leading-[18px] text-[#858b9c]">{proposal.summary}</p>
          <div className="mt-[13px] rounded-[12px] bg-[#f7f8fa] px-[14px] py-[12px]">
            <p className="text-[10px] text-[#858b9c]">建议写入「{proposal.target_label}」</p>
            <p className="mt-[5px] text-[12px] leading-[19px] text-[#18181a]">+ {proposal.instruction}</p>
          </div>
          {evidence && (
            <p className="mt-[10px] text-[10px] leading-[17px] text-[#858b9c]">
              证据：{evidence.summary || evidence.reason || evidence.evidence?.join('；') || '来自近期差评复盘'}
            </p>
          )}
          <details className="group mt-[10px]">
            <summary className="flex cursor-pointer list-none items-center gap-[4px] text-[10px] text-[#6861a3]">
              查看完整 Diff
              <ChevronDown className="size-[12px] transition-transform group-open:rotate-180" />
            </summary>
            <pre className="mt-[8px] max-h-[260px] overflow-auto rounded-[10px] bg-[#17181b] p-[12px] text-[10px] leading-[16px] whitespace-pre-wrap text-[#dce1e8]">
              {proposal.after_content}
            </pre>
          </details>
        </div>
        <div className="flex shrink-0 gap-[8px] max-[720px]:ml-[54px]">
          <button
            type="button"
            disabled={busy}
            onClick={onReject}
            className="flex h-[36px] items-center gap-[5px] rounded-[9px] border border-[#dfe2e8] px-[12px] text-[11px] text-[#646a78] hover:bg-[#f6f6f6] disabled:opacity-50"
          >
            <X className="size-[13px]" /> 忽略
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onApply}
            className="flex h-[36px] items-center gap-[5px] rounded-[9px] bg-[#18181a] px-[14px] text-[11px] text-white hover:opacity-85 disabled:opacity-50"
          >
            <Check className="size-[13px]" /> 确认沉淀
          </button>
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
  const [loading, setLoading] = useState(true);
  const [learning, setLearning] = useState(false);
  const [busyId, setBusyId] = useState('');

  const load = useCallback(async () => {
    if (!agent?.id) {
      setRows([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const result = await api.get<CapabilityEvolutionProposalRead[]>(
        `/api/enterprise/agents/${encodeURIComponent(agent.id)}/evolution-proposals?tenant_id=${TENANT_ID}`,
      );
      setRows(result);
    } catch (error) {
      setRows([]);
      notify.error(error instanceof Error ? error.message : '加载进化建议失败');
    } finally {
      setLoading(false);
    }
  }, [agent?.id]);

  useEffect(() => { void load(); }, [load]);

  const pending = useMemo(() => rows.filter((row) => row.status === 'pending'), [rows]);
  const applied = useMemo(() => rows.filter((row) => row.status === 'applied'), [rows]);
  const totalReuse = applied.reduce((sum, row) => sum + row.reuse_count, 0);

  const learn = async () => {
    if (!agent?.id) return;
    setLearning(true);
    try {
      const created = await api.post<CapabilityEvolutionProposalRead[]>(
        `/api/enterprise/agents/${encodeURIComponent(agent.id)}/evolution-proposals/learn`,
        { tenant_id: TENANT_ID, period_days: 30 },
      );
      notify.success(created.length ? `发现 ${created.length} 条可沉淀经验` : '近期没有新的可沉淀经验');
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
      notify.success(action === 'apply' ? '经验已写入技能' : '建议已忽略');
      await load();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '处理建议失败');
    } finally {
      setBusyId('');
    }
  };

  if (loading) return <LoadingPage />;
  if (!agent) return <main className="p-[24px]"><EmptyState>请选择一名数字员工</EmptyState></main>;

  return (
    <main className="mx-auto min-h-full w-full max-w-[1120px] px-[24px] pt-[18px] pb-[40px] max-[900px]:px-0">
      <AppHeader
        onLogout={onLogout}
        userName={currentUser?.username}
        className="mb-[20px]"
        left={(
          <div className="flex min-h-[54px] items-start justify-between gap-[18px] pr-[10px] max-[700px]:flex-col">
            <div>
              <h1 className="text-[26px] leading-[34px] font-semibold tracking-[-0.02em] text-[#18181a]">能力进化</h1>
              <p className="mt-[5px] text-[12px] text-[#858b9c]">从 {employeeDisplayName(agent)} 的真实会话中提炼经验，经你确认后再写入技能</p>
            </div>
            <button
              type="button"
              disabled={learning}
              onClick={learn}
              className="flex h-[40px] items-center gap-[7px] rounded-[10px] bg-[#18181a] px-[16px] text-[11px] text-white hover:opacity-85 disabled:opacity-50"
            >
              <RefreshCw className={cn('size-[14px]', learning && 'animate-spin')} />
              {learning ? '正在复盘' : '学习近期会话'}
            </button>
          </div>
        )}
      />

      <section className="grid grid-cols-3 gap-[12px] max-[700px]:grid-cols-1">
        {[
          ['待确认建议', pending.length],
          ['已沉淀经验', applied.length],
          ['后续真实复用', totalReuse],
        ].map(([label, value]) => (
          <div key={String(label)} className="rounded-[14px] border-[0.5px] border-[#e3e7f1] bg-white px-[18px] py-[16px]">
            <p className="text-[10px] text-[#858b9c]">{label}</p>
            <p className="mt-[5px] text-[24px] font-semibold text-[#18181a]">{value}</p>
          </div>
        ))}
      </section>

      <section className="mt-[20px]">
        <div className="mb-[10px] flex items-center justify-between">
          <h2 className="text-[16px] font-semibold text-[#18181a]">等你审核</h2>
          <span className="text-[10px] text-[#858b9c]">应用前不会修改任何能力</span>
        </div>
        <div className="space-y-[10px]">
          {pending.length ? pending.map((proposal) => (
            <ProposalCard
              key={proposal.id}
              proposal={proposal}
              busy={busyId === proposal.id}
              onApply={() => void act(proposal, 'apply')}
              onReject={() => void act(proposal, 'reject')}
            />
          )) : <EmptyState>没有待审核建议。点击“学习近期会话”检查新的差评与纠正。</EmptyState>}
        </div>
      </section>

      <section className="mt-[24px]">
        <h2 className="mb-[10px] text-[16px] font-semibold text-[#18181a]">已经沉淀</h2>
        {applied.length ? (
          <div className="overflow-hidden rounded-[16px] border-[0.5px] border-[#e3e7f1] bg-white">
            {applied.map((proposal, index) => (
              <div key={proposal.id} className={cn('grid grid-cols-[minmax(0,1fr)_120px_120px] items-center gap-[16px] px-[20px] py-[16px] max-[700px]:grid-cols-1', index > 0 && 'border-t border-[#eceef1]')}>
                <div className="min-w-0">
                  <p className="truncate text-[12px] font-medium text-[#18181a]">{proposal.instruction}</p>
                  <p className="mt-[4px] text-[10px] text-[#858b9c]">已写入 {proposal.target_label} · {formatTime(proposal.applied_at)}</p>
                </div>
                <span className="flex items-center gap-[5px] text-[10px] text-[#858b9c]"><History className="size-[13px]" />复用 {proposal.reuse_count} 次</span>
                <span className="text-right text-[10px] text-[#249358] max-[700px]:text-left">已验证写入</span>
              </div>
            ))}
          </div>
        ) : <EmptyState>审核通过的经验会出现在这里，并持续统计后续复用。</EmptyState>}
      </section>
    </main>
  );
}
