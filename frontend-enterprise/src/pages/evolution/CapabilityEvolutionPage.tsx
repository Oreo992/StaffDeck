import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowRight, Check, ChevronDown, History, Lightbulb, RefreshCw, ShieldCheck, Sparkles, X } from 'lucide-react';

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

  return (
    <main className="mx-auto min-h-full w-full max-w-[1120px] px-[24px] pt-[18px] pb-[40px] max-[900px]:px-0">
      <AppHeader
        onLogout={onLogout}
        userName={currentUser?.username}
        className="mb-[20px]"
        left={(
          <div className="flex min-h-[54px] items-start justify-between gap-[18px] pr-[10px] max-[700px]:flex-col">
            <div>
              <h1 className="text-[26px] leading-[34px] font-semibold tracking-[-0.02em] text-[#18181a]">QQQ 学会了什么</h1>
              <p className="mt-[5px] max-w-[620px] text-[12px] leading-[19px] text-[#858b9c]">
                {employeeDisplayName(agent)} 会复盘做过的工作，把有效做法变成下次能直接使用的经验。只有你同意后，它才会真正学会。
              </p>
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

      <section className="mb-[18px] rounded-[18px] border-[0.5px] border-[#dfe6df] bg-[#f8fbf8] px-[20px] py-[17px]">
        <div className="flex items-center gap-[8px] text-[12px] font-semibold text-[#233727]">
          <ShieldCheck className="size-[17px] text-[#319447]" /> 它是怎么变得更聪明的？
        </div>
        <div className="mt-[14px] grid grid-cols-[1fr_auto_1fr_auto_1fr] items-center gap-[12px] max-[720px]:grid-cols-1">
          {[
            ['1', '回看真实工作', '找出做得好的方法或反复出现的问题'],
            ['2', '提出学习建议', '告诉你以后准备怎么做、为什么'],
            ['3', '你确认后生效', '不会偷偷修改，下次同类工作开始复用'],
          ].map(([step, title, description], index) => (
            <div key={step} className="contents max-[720px]:block">
              <div className="flex items-start gap-[10px]">
                <span className="grid size-[24px] shrink-0 place-items-center rounded-full bg-[#319447] text-[10px] font-semibold text-white">{step}</span>
                <div>
                  <p className="text-[11px] font-medium text-[#233727]">{title}</p>
                  <p className="mt-[3px] text-[10px] leading-[16px] text-[#78817a]">{description}</p>
                </div>
              </div>
              {index < 2 && <ArrowRight className="size-[15px] text-[#aeb8b0] max-[720px]:hidden" />}
            </div>
          ))}
        </div>
      </section>

      <section className="grid grid-cols-3 gap-[12px] max-[700px]:grid-cols-1">
        {[
          ['等你决定', pending.length, '条建议还没有生效'],
          ['已经学会', applied.length, '条做法已加入能力'],
          ['后来帮上忙', totalReuse, '次在后续工作中复用'],
        ].map(([label, value, description]) => (
          <div key={String(label)} className="rounded-[14px] border-[0.5px] border-[#e3e7f1] bg-white px-[18px] py-[16px]">
            <p className="text-[10px] text-[#858b9c]">{label}</p>
            <p className="mt-[5px] text-[24px] font-semibold text-[#18181a]">{value}</p>
            <p className="mt-[2px] text-[10px] text-[#a0a5b0]">{description}</p>
          </div>
        ))}
      </section>

      <section className="mt-[20px]">
        <div className="mb-[10px] flex items-center justify-between">
          <h2 className="text-[16px] font-semibold text-[#18181a]">QQQ 想学的新做法</h2>
          <span className="text-[10px] text-[#858b9c]">你不确认，就不会生效</span>
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
          )) : <EmptyState>现在没有需要决定的建议。点击“检查最近工作”，看看 QQQ 有没有新的发现。</EmptyState>}
        </div>
      </section>

      <section className="mt-[24px]">
        <h2 className="mb-[10px] text-[16px] font-semibold text-[#18181a]">QQQ 已经学会的做法</h2>
        {applied.length ? (
          <div className="overflow-hidden rounded-[16px] border-[0.5px] border-[#e3e7f1] bg-white">
            {applied.map((proposal, index) => (
              <div key={proposal.id} className={cn('grid grid-cols-[minmax(0,1fr)_120px_120px] items-center gap-[16px] px-[20px] py-[16px] max-[700px]:grid-cols-1', index > 0 && 'border-t border-[#eceef1]')}>
                <div className="min-w-0">
                  <p className="truncate text-[12px] font-medium text-[#18181a]">{proposal.instruction}</p>
                  <p className="mt-[4px] text-[10px] text-[#858b9c]">用于 {proposal.target_label} · {formatTime(proposal.applied_at)} 学会</p>
                </div>
                <span className="flex items-center gap-[5px] text-[10px] text-[#858b9c]"><History className="size-[13px]" />后来用过 {proposal.reuse_count} 次</span>
                <span className="text-right text-[10px] text-[#249358] max-[700px]:text-left">已成为 QQQ 的习惯</span>
              </div>
            ))}
          </div>
        ) : <EmptyState>你同意的学习建议会出现在这里；以后真正用到时，还会累计次数。</EmptyState>}
      </section>
    </main>
  );
}
