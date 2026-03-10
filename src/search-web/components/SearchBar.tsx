"use client";

import { useState, useEffect } from "react";

interface SearchBarProps {
  onSearch: (query: string, options: { topK: number; rerank: boolean }) => void;
  isLoading: boolean;
}

const STORAGE_KEY = "search-preferences";

function loadPreferences() {
  if (typeof window === "undefined") return { topK: 20, rerank: true };
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) return JSON.parse(saved);
  } catch {}
  return { topK: 20, rerank: true };
}

export default function SearchBar({ onSearch, isLoading }: SearchBarProps) {
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(20);
  const [rerank, setRerank] = useState(true);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const prefs = loadPreferences();
    setTopK(prefs.topK);
    setRerank(prefs.rerank);
    setLoaded(true);
  }, []);

  useEffect(() => {
    if (!loaded) return;
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ topK, rerank }));
  }, [topK, rerank, loaded]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    onSearch(query.trim(), { topK, rerank });
  };

  return (
    <form onSubmit={handleSubmit} className="w-full max-w-3xl mx-auto">
      <div className="flex gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="검색어를 입력하세요..."
          className="flex-1 px-4 py-3 border border-gray-300 rounded-lg text-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        />
        <button
          type="submit"
          disabled={isLoading || !query.trim()}
          className="px-6 py-3 bg-blue-600 text-white rounded-lg text-lg font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isLoading ? "검색 중..." : "검색"}
        </button>
      </div>

      <div className="flex items-center gap-6 mt-3 text-sm text-gray-600">
        <label className="flex items-center gap-2">
          결과 수
          <select
            value={topK}
            onChange={(e) => setTopK(Number(e.target.value))}
            className="border border-gray-300 rounded px-2 py-1"
          >
            {[5, 10, 20, 30, 50].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={rerank}
            onChange={(e) => setRerank(e.target.checked)}
            className="w-4 h-4"
          />
          Reranker 사용
        </label>
      </div>
    </form>
  );
}
