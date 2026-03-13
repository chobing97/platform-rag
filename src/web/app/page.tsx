"use client";

import { useState } from "react";
import AgentChatPanel from "@/components/AgentChatPanel";
import SearchBar from "@/components/SearchBar";
import ResultCard from "@/components/ResultCard";
import ApiTestPanel from "@/components/ApiTestPanel";
import McpTestPanel from "@/components/McpTestPanel";
import McpSetupGuide from "@/components/McpSetupGuide";
import DashboardPanel from "@/components/DashboardPanel";

const API_URL = typeof window !== "undefined" ? `http://${window.location.hostname}:8000` : "http://localhost:8000";

type Tab = "agent" | "search" | "api" | "mcp" | "mcp-setup" | "dashboard";

interface SearchResult {
  id: string;
  text: string;
  metadata: Record<string, string>;
  rrf_score: number | null;
  rerank_score: number | null;
}

interface Timings {
  embedding: number;
  vector_search: number;
  bm25_search: number;
  rrf_fusion: number;
  reranker: number;
  total: number;
}

interface SearchResponse {
  query: string;
  count: number;
  results: SearchResult[];
  timings: Timings;
}

export default function Home() {
  const [tab, setTab] = useState<Tab>("agent");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [searchInfo, setSearchInfo] = useState<{
    query: string;
    count: number;
    timings: Timings;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSearch = async (
    query: string,
    options: { topK: number; rerank: boolean }
  ) => {
    setIsLoading(true);
    setError(null);

    try {
      const params = new URLSearchParams({
        q: query,
        top_k: String(options.topK),
        rerank: String(options.rerank),
      });

      const res = await fetch(`${API_URL}/search?${params}`);
      if (!res.ok) throw new Error(`API 오류: ${res.status}`);

      const data: SearchResponse = await res.json();

      setResults(data.results);
      setSearchInfo({
        query: data.query,
        count: data.count,
        timings: data.timings,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "검색 중 오류 발생");
      setResults([]);
      setSearchInfo(null);
    } finally {
      setIsLoading(false);
    }
  };

  const tabs: { key: Tab; label: string }[] = [
    { key: "agent", label: "AI Agent" },
    { key: "search", label: "검색" },
    { key: "api", label: "API 테스트" },
    { key: "mcp", label: "MCP 테스트" },
    { key: "mcp-setup", label: "MCP 설정 방법" },
    { key: "dashboard", label: "대시보드" },
  ];

  return (
    <main className="max-w-4xl mx-auto px-4 py-12">
      <h1 className="text-3xl font-bold text-center mb-8">
        다올투자증권 플랫폼전략본부 Knowledge Lake
      </h1>

      {/* Tab Navigation */}
      <div className="flex border-b border-gray-200 mb-8">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-6 py-3 text-sm font-medium border-b-2 transition-colors ${
              tab === t.key
                ? "border-blue-600 text-blue-600"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Agent Tab */}
      {tab === "agent" && <AgentChatPanel />}

      {/* Search Tab */}
      {tab === "search" && (
        <>
          <SearchBar onSearch={handleSearch} isLoading={isLoading} />

          {error && (
            <div className="mt-6 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
              {error}
            </div>
          )}

          {searchInfo && (
            <div className="mt-6">
              <p className="text-sm text-gray-500">
                &quot;{searchInfo.query}&quot; 검색 결과 {searchInfo.count}건 (
                {searchInfo.timings.total.toFixed(1)}초)
              </p>

              <div className="mt-2 flex flex-wrap gap-3 text-xs text-gray-400">
                <span className="bg-gray-100 px-2 py-1 rounded">
                  Embedding {searchInfo.timings.embedding.toFixed(2)}s
                </span>
                <span className="bg-gray-100 px-2 py-1 rounded">
                  Vector {searchInfo.timings.vector_search.toFixed(2)}s
                </span>
                <span className="bg-gray-100 px-2 py-1 rounded">
                  BM25 {searchInfo.timings.bm25_search.toFixed(2)}s
                </span>
                <span className="bg-gray-100 px-2 py-1 rounded">
                  RRF {searchInfo.timings.rrf_fusion.toFixed(3)}s
                </span>
                {searchInfo.timings.reranker > 0 && (
                  <span className="bg-amber-50 text-amber-600 px-2 py-1 rounded">
                    Reranker {searchInfo.timings.reranker.toFixed(1)}s
                  </span>
                )}
              </div>
            </div>
          )}

          <div className="mt-4 flex flex-col gap-4">
            {results.map((result, i) => (
              <ResultCard
                key={result.id}
                id={result.id}
                query={searchInfo?.query ?? ""}
                rank={i + 1}
                text={result.text}
                metadata={result.metadata}
                rrfScore={result.rrf_score}
                rerankScore={result.rerank_score}
              />
            ))}
          </div>

          {searchInfo && results.length === 0 && !error && (
            <p className="mt-8 text-center text-gray-400">
              검색 결과가 없습니다.
            </p>
          )}
        </>
      )}

      {/* API Test Tab */}
      {tab === "api" && <ApiTestPanel />}

      {/* MCP Test Tab */}
      {tab === "mcp" && <McpTestPanel />}

      {/* MCP Setup Guide Tab */}
      {tab === "mcp-setup" && <McpSetupGuide />}

      {/* Dashboard Tab */}
      {tab === "dashboard" && <DashboardPanel />}
    </main>
  );
}
