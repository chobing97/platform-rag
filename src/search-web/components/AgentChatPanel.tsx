"use client";

import { useState, useRef, useEffect } from "react";

const AGENT_API_URL = "http://localhost:8001";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

interface StatusEvent {
  type: "status" | "tool_call" | "tool_result" | "error";
  message: string;
  tool?: string;
  args?: Record<string, unknown>;
}

type ModelsMap = Record<string, string[]>;

export default function AgentChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [statusEvents, setStatusEvents] = useState<StatusEvent[]>([]);
  const [models, setModels] = useState<ModelsMap>({});
  const [selectedProvider, setSelectedProvider] = useState("");
  const [selectedModel, setSelectedModel] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // 모델 목록 로드
  useEffect(() => {
    fetch(`${AGENT_API_URL}/agent/models`)
      .then((res) => res.json())
      .then((data: ModelsMap) => {
        setModels(data);
        const providers = Object.keys(data);
        if (providers.length > 0) {
          const firstProvider = providers[0];
          setSelectedProvider(firstProvider);
          setSelectedModel(data[firstProvider][0] || "");
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, statusEvents]);

  useEffect(() => {
    if (!isLoading) inputRef.current?.focus();
  }, [isLoading]);

  const handleProviderChange = (provider: string) => {
    setSelectedProvider(provider);
    const providerModels = models[provider] || [];
    setSelectedModel(providerModels[0] || "");
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const query = input.trim();
    if (!query || isLoading) return;

    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: query }]);
    setStatusEvents([]);
    setIsLoading(true);

    try {
      const res = await fetch(`${AGENT_API_URL}/agent/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query,
          provider: selectedProvider || undefined,
          model: selectedModel || undefined,
        }),
      });

      if (!res.ok) throw new Error(`API 오류: ${res.status}`);
      if (!res.body) throw new Error("스트리밍 미지원");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        let currentEventType = "";
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            currentEventType = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            const data = JSON.parse(line.slice(6));

            if (currentEventType === "result") {
              setMessages((prev) => [
                ...prev,
                { role: "assistant", content: data.text },
              ]);
              setStatusEvents([]);
            } else {
              setStatusEvents((prev) => [...prev, data as StatusEvent]);
            }
          }
        }
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `오류: ${err instanceof Error ? err.message : "알 수 없는 오류"}`,
        },
      ]);
      setStatusEvents([]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleReset = async () => {
    try {
      await fetch(`${AGENT_API_URL}/agent/reset`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: selectedProvider || undefined,
          model: selectedModel || undefined,
        }),
      });
    } catch {}
    setMessages([]);
    setStatusEvents([]);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  const providers = Object.keys(models);
  const currentModels = models[selectedProvider] || [];

  return (
    <div className="flex flex-col h-[calc(100vh-12rem)]">
      {/* Model Selector */}
      <div className="flex items-center gap-3 pb-4 mb-4 border-b border-gray-100">
        {providers.map((provider) => (
          <button
            key={provider}
            onClick={() => handleProviderChange(provider)}
            className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
              selectedProvider === provider
                ? "bg-blue-100 text-blue-700"
                : "bg-gray-100 text-gray-500 hover:bg-gray-200"
            }`}
          >
            {provider === "claude" ? "Claude" : "Gemini"}
          </button>
        ))}

        {currentModels.length > 0 && (
          <select
            value={selectedModel}
            onChange={(e) => setSelectedModel(e.target.value)}
            className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 text-gray-600 focus:outline-none focus:ring-1 focus:ring-blue-500"
          >
            {currentModels.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Messages Area */}
      <div className="flex-1 overflow-y-auto space-y-4 pb-4">
        {messages.length === 0 && !isLoading && (
          <div className="flex items-center justify-center h-full text-gray-400">
            <div className="text-center">
              <div className="text-4xl mb-4">&#x1F9E0;</div>
              <p className="text-lg font-medium">Platform RAG Agent</p>
              <p className="text-sm mt-1">
                팀 지식베이스를 검색하고 분석하여 답변합니다
              </p>
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[80%] rounded-2xl px-4 py-3 ${
                msg.role === "user"
                  ? "bg-blue-600 text-white"
                  : "bg-white border border-gray-200 text-gray-900"
              }`}
            >
              <div className="text-sm leading-relaxed whitespace-pre-wrap">
                {msg.content}
              </div>
            </div>
          </div>
        ))}

        {/* Thinking Status */}
        {isLoading && statusEvents.length > 0 && (
          <div className="flex justify-start">
            <div className="max-w-[80%] rounded-2xl px-4 py-3 bg-white border border-gray-200">
              <ThinkingIndicator events={statusEvents} />
            </div>
          </div>
        )}

        {isLoading && statusEvents.length === 0 && (
          <div className="flex justify-start">
            <div className="max-w-[80%] rounded-2xl px-4 py-3 bg-white border border-gray-200">
              <div className="flex items-center gap-2 text-sm text-gray-500">
                <PulsingDot />
                <span>연결 중...</span>
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input Area */}
      <div className="border-t border-gray-200 pt-4">
        <form onSubmit={handleSubmit} className="flex gap-2 items-end">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="질문을 입력하세요..."
            rows={1}
            disabled={isLoading}
            className="flex-1 px-4 py-3 border border-gray-300 rounded-xl text-sm resize-none focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-50"
            style={{ maxHeight: "120px" }}
            onInput={(e) => {
              const target = e.target as HTMLTextAreaElement;
              target.style.height = "auto";
              target.style.height = Math.min(target.scrollHeight, 120) + "px";
            }}
          />
          <button
            type="submit"
            disabled={isLoading || !input.trim()}
            className="px-5 py-3 bg-blue-600 text-white rounded-xl text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed flex-shrink-0"
          >
            {isLoading ? "..." : "전송"}
          </button>
          {messages.length > 0 && (
            <button
              type="button"
              onClick={handleReset}
              disabled={isLoading}
              className="px-3 py-3 text-gray-400 hover:text-gray-600 text-sm disabled:opacity-50 flex-shrink-0"
              title="대화 초기화"
            >
              초기화
            </button>
          )}
        </form>
      </div>
    </div>
  );
}

/* ─── Sub Components ──────────────────────────────── */

function PulsingDot() {
  return (
    <span className="relative flex h-2.5 w-2.5">
      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
      <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-blue-500" />
    </span>
  );
}

function ThinkingIndicator({ events }: { events: StatusEvent[] }) {
  const lastEvent = events[events.length - 1];

  return (
    <div className="space-y-2">
      {/* Previous events (collapsed) */}
      {events.slice(0, -1).map((evt, i) => (
        <div key={i} className="flex items-center gap-2 text-xs text-gray-400">
          <StatusIcon type={evt.type} done />
          <span>{evt.message}</span>
        </div>
      ))}

      {/* Current event (active) */}
      {lastEvent && (
        <div className="flex items-center gap-2 text-sm text-gray-600">
          <PulsingDot />
          <span>{lastEvent.message}</span>
        </div>
      )}
    </div>
  );
}

function StatusIcon({ type, done }: { type: string; done?: boolean }) {
  if (done) {
    return <span className="text-green-500">&#x2713;</span>;
  }
  if (type === "tool_call" || type === "tool_result") {
    return <span>&#x1F50D;</span>;
  }
  if (type === "error") {
    return <span className="text-red-500">&#x2717;</span>;
  }
  return <span>&#x1F4AD;</span>;
}
