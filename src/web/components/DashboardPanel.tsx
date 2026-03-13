"use client";

import { useState, useEffect, useCallback } from "react";
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

const API_URL =
  typeof window !== "undefined"
    ? `http://${window.location.hostname}:8000`
    : "http://localhost:8000";

interface Summary {
  today_searches: number;
  week_searches: number;
  today_clicks: number;
  today_chats: number;
  ctr: number;
  active_sessions: number;
}

interface DailyRow {
  date: string;
  count: number;
}

interface QueryRow {
  query: string;
  count: number;
}

interface DocRow {
  doc_id: string;
  clicks: number;
  last_clicked: string;
}

interface TimingRow {
  date: string;
  avg_total: number;
  avg_embedding: number;
  avg_vector: number;
  avg_bm25: number;
  avg_reranker: number;
}

interface ProviderRow {
  provider: string;
  count: number;
}

const PIE_COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444"];

export default function DashboardPanel() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [daily, setDaily] = useState<DailyRow[]>([]);
  const [topQueries, setTopQueries] = useState<QueryRow[]>([]);
  const [topDocs, setTopDocs] = useState<DocRow[]>([]);
  const [timings, setTimings] = useState<TimingRow[]>([]);
  const [providers, setProviders] = useState<ProviderRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [sumRes, dailyRes, qRes, dRes, tRes, pRes] = await Promise.all([
        fetch(`${API_URL}/stats/summary`),
        fetch(`${API_URL}/stats/daily?days=30`),
        fetch(`${API_URL}/stats/top-queries?limit=10`),
        fetch(`${API_URL}/stats/top-docs?limit=10`),
        fetch(`${API_URL}/stats/timings?days=7`),
        fetch(`${API_URL}/stats/providers`),
      ]);

      if (!sumRes.ok) throw new Error("API 연결 실패");

      setSummary(await sumRes.json());
      setDaily((await dailyRes.json()).data);
      setTopQueries((await qRes.json()).data);
      setTopDocs((await dRes.json()).data);
      setTimings((await tRes.json()).data);
      setProviders((await pRes.json()).data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "데이터 로딩 실패");
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  if (error) {
    return (
      <div className="p-6 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
        {error} — FastAPI 서버(:8000)가 실행 중인지 확인하세요.
      </div>
    );
  }

  if (!summary) {
    return <div className="text-center text-gray-400 py-12">로딩 중...</div>;
  }

  return (
    <div className="space-y-8">
      {/* KPI Cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <KpiCard label="오늘 검색" value={summary.today_searches} unit="건" />
        <KpiCard
          label="클릭률 (CTR)"
          value={Math.round(summary.ctr * 100)}
          unit="%"
        />
        <KpiCard label="오늘 채팅" value={summary.today_chats} unit="건" />
        <KpiCard label="이번 주 검색" value={summary.week_searches} unit="건" />
        <KpiCard label="전체 세션" value={summary.active_sessions} unit="개" />
      </div>

      {/* Row 1: Daily + Top Queries */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <ChartCard title="일별 검색 추이 (30일)">
          {daily.length > 0 ? (
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={daily}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 11 }}
                  tickFormatter={(v: string) => v.slice(5)}
                />
                <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                <Tooltip />
                <Line
                  type="monotone"
                  dataKey="count"
                  stroke="#3b82f6"
                  strokeWidth={2}
                  dot={{ r: 3 }}
                  name="검색 수"
                />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <EmptyState />
          )}
        </ChartCard>

        <ChartCard title="인기 검색어 Top 10">
          {topQueries.length > 0 ? (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart
                data={topQueries}
                layout="vertical"
                margin={{ left: 20 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis type="number" tick={{ fontSize: 11 }} allowDecimals={false} />
                <YAxis
                  type="category"
                  dataKey="query"
                  tick={{ fontSize: 11 }}
                  width={120}
                />
                <Tooltip />
                <Bar dataKey="count" fill="#3b82f6" radius={[0, 4, 4, 0]} name="검색 횟수" />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <EmptyState />
          )}
        </ChartCard>
      </div>

      {/* Row 2: Timings + Providers */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <ChartCard title="평균 응답시간 추이 (7일)">
          {timings.length > 0 ? (
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={timings}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 11 }}
                  tickFormatter={(v: string) => v.slice(5)}
                />
                <YAxis tick={{ fontSize: 11 }} unit="s" />
                <Tooltip formatter={(v) => `${v}s`} />
                <Line
                  type="monotone"
                  dataKey="avg_total"
                  stroke="#3b82f6"
                  strokeWidth={2}
                  name="전체"
                />
                <Line
                  type="monotone"
                  dataKey="avg_reranker"
                  stroke="#f59e0b"
                  strokeWidth={1.5}
                  strokeDasharray="4 2"
                  name="Reranker"
                />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <EmptyState />
          )}
        </ChartCard>

        <ChartCard title="Agent 프로바이더 비율">
          {providers.length > 0 ? (
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie
                  data={providers}
                  dataKey="count"
                  nameKey="provider"
                  cx="50%"
                  cy="50%"
                  outerRadius={80}
                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  label={(props: any) =>
                    `${props.provider} (${props.count})`
                  }
                >
                  {providers.map((_, i) => (
                    <Cell
                      key={i}
                      fill={PIE_COLORS[i % PIE_COLORS.length]}
                    />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <EmptyState />
          )}
        </ChartCard>
      </div>

      {/* Top Docs Table */}
      <ChartCard title="가장 많이 클릭된 문서 Top 10">
        {topDocs.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-gray-500 border-b border-gray-100">
                  <th className="pb-2 pr-4 font-medium">#</th>
                  <th className="pb-2 pr-4 font-medium">문서 ID</th>
                  <th className="pb-2 pr-4 font-medium text-right">클릭 수</th>
                  <th className="pb-2 font-medium text-right">최근 클릭</th>
                </tr>
              </thead>
              <tbody>
                {topDocs.map((doc, i) => (
                  <tr key={doc.doc_id} className="border-b border-gray-50">
                    <td className="py-2 pr-4 text-gray-400">{i + 1}</td>
                    <td className="py-2 pr-4 font-mono text-xs truncate max-w-[300px]">
                      {doc.doc_id}
                    </td>
                    <td className="py-2 pr-4 text-right font-medium">
                      {doc.clicks}
                    </td>
                    <td className="py-2 text-right text-gray-400">
                      {doc.last_clicked?.slice(0, 16)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState />
        )}
      </ChartCard>

      {/* Refresh Button */}
      <div className="text-center">
        <button
          onClick={fetchAll}
          className="px-4 py-2 text-sm text-gray-500 border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
        >
          새로고침
        </button>
      </div>
    </div>
  );
}

/* ─── Sub Components ──────────────────────────────── */

function KpiCard({
  label,
  value,
  unit,
}: {
  label: string;
  value: number;
  unit: string;
}) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className="text-2xl font-bold text-gray-900">
        {value}
        <span className="text-sm font-normal text-gray-400 ml-1">{unit}</span>
      </p>
    </div>
  );
}

function ChartCard({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-5">
      <h3 className="text-sm font-medium text-gray-700 mb-4">{title}</h3>
      {children}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex items-center justify-center h-[220px] text-gray-300 text-sm">
      데이터가 아직 없습니다
    </div>
  );
}
