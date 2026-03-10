"use client";

import { useState } from "react";

const API_URL = typeof window !== "undefined" ? `http://${window.location.hostname}:8000` : "http://localhost:8000";

type Endpoint = "sources" | "document" | "related";

export default function ApiTestPanel() {
  const [endpoint, setEndpoint] = useState<Endpoint>("sources");
  const [isLoading, setIsLoading] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // sources params
  const [sourceType, setSourceType] = useState("");
  const [keyword, setKeyword] = useState("");

  // document params
  const [docId, setDocId] = useState("");

  // related params
  const [relatedDocId, setRelatedDocId] = useState("");
  const [relatedTopK, setRelatedTopK] = useState(5);

  const callApi = async () => {
    setIsLoading(true);
    setError(null);
    setResult(null);

    try {
      let url = "";

      switch (endpoint) {
        case "sources": {
          const params = new URLSearchParams();
          if (sourceType) params.set("source_type", sourceType);
          if (keyword) params.set("keyword", keyword);
          const qs = params.toString();
          url = `${API_URL}/sources${qs ? `?${qs}` : ""}`;
          break;
        }
        case "document":
          if (!docId.trim()) throw new Error("문서 ID를 입력하세요");
          url = `${API_URL}/document/${docId.trim()}`;
          break;
        case "related":
          if (!relatedDocId.trim()) throw new Error("문서 ID를 입력하세요");
          url = `${API_URL}/related/${relatedDocId.trim()}?top_k=${relatedTopK}`;
          break;
      }

      const res = await fetch(url);
      if (!res.ok) throw new Error(`API 오류: ${res.status}`);
      const data = await res.json();
      setResult(JSON.stringify(data, null, 2));
    } catch (err) {
      setError(err instanceof Error ? err.message : "요청 실패");
    } finally {
      setIsLoading(false);
    }
  };

  const endpoints: { key: Endpoint; label: string; desc: string }[] = [
    { key: "sources", label: "GET /sources", desc: "문서 목록 조회" },
    { key: "document", label: "GET /document/{id}", desc: "문서 전체 내용" },
    { key: "related", label: "GET /related/{id}", desc: "관련 문서 검색" },
  ];

  return (
    <div className="space-y-6">
      {/* Endpoint selector */}
      <div className="flex gap-2">
        {endpoints.map((ep) => (
          <button
            key={ep.key}
            onClick={() => {
              setEndpoint(ep.key);
              setResult(null);
              setError(null);
            }}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              endpoint === ep.key
                ? "bg-blue-600 text-white"
                : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            {ep.label}
          </button>
        ))}
      </div>

      <p className="text-sm text-gray-500">
        {endpoints.find((e) => e.key === endpoint)?.desc}
      </p>

      {/* Parameters */}
      <div className="bg-white border border-gray-200 rounded-lg p-5 space-y-4">
        {endpoint === "sources" && (
          <>
            <div className="flex gap-4">
              <label className="flex-1">
                <span className="block text-sm font-medium text-gray-700 mb-1">
                  source_type (선택)
                </span>
                <input
                  type="text"
                  value={sourceType}
                  onChange={(e) => setSourceType(e.target.value)}
                  placeholder="notion, email, file ..."
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </label>
              <label className="flex-1">
                <span className="block text-sm font-medium text-gray-700 mb-1">
                  keyword (선택)
                </span>
                <input
                  type="text"
                  value={keyword}
                  onChange={(e) => setKeyword(e.target.value)}
                  placeholder="제목 검색 키워드"
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </label>
            </div>
          </>
        )}

        {endpoint === "document" && (
          <label>
            <span className="block text-sm font-medium text-gray-700 mb-1">
              doc_id (필수)
            </span>
            <input
              type="text"
              value={docId}
              onChange={(e) => setDocId(e.target.value)}
              placeholder="검색 결과에서 복사한 문서 ID"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </label>
        )}

        {endpoint === "related" && (
          <div className="flex gap-4">
            <label className="flex-1">
              <span className="block text-sm font-medium text-gray-700 mb-1">
                doc_id (필수)
              </span>
              <input
                type="text"
                value={relatedDocId}
                onChange={(e) => setRelatedDocId(e.target.value)}
                placeholder="기준 문서 ID"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </label>
            <label className="w-28">
              <span className="block text-sm font-medium text-gray-700 mb-1">
                top_k
              </span>
              <select
                value={relatedTopK}
                onChange={(e) => setRelatedTopK(Number(e.target.value))}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
              >
                {[3, 5, 10, 20].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}

        <button
          onClick={callApi}
          disabled={isLoading}
          className="px-6 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isLoading ? "요청 중..." : "요청 보내기"}
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
          {error}
        </div>
      )}

      {/* Result */}
      {result && (
        <div className="relative">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium text-gray-700">응답</span>
            <button
              onClick={() => navigator.clipboard.writeText(result)}
              className="text-xs text-blue-500 hover:underline"
            >
              복사
            </button>
          </div>
          <pre className="bg-gray-900 text-green-400 p-4 rounded-lg text-xs overflow-auto max-h-[600px] leading-relaxed">
            {result}
          </pre>
        </div>
      )}
    </div>
  );
}
