"use client";

import { useState, useRef, useEffect, useCallback, type ChangeEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

const ACCEPTED_FILE_TYPES = [
  "image/png", "image/jpeg", "image/gif", "image/webp",
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-excel",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/vnd.ms-powerpoint",
].join(",");

interface AttachedFile {
  file: File;
  preview?: string; // data URL for images
}

const AGENT_API_URL = typeof window !== "undefined" ? `http://${window.location.hostname}:8001` : "http://localhost:8001";
const SEARCH_API_URL = typeof window !== "undefined" ? `http://${window.location.hostname}:8000` : "http://localhost:8000";

const SESSION_KEY = "agent_session_id";
const TOKEN_KEY = "agent_api_token";
const PAGE_SIZE = 5;

interface ChatMessage {
  id?: number;
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

function generateUUID(): string {
  // crypto.randomUUID()는 secure context(HTTPS/localhost)에서만 사용 가능.
  // LAN IP로 접근 시 fallback 사용.
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // fallback: crypto.getRandomValues 기반 UUID v4
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 1
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function getOrCreateSessionId(): string {
  if (typeof window === "undefined") return generateUUID();
  const stored = localStorage.getItem(SESSION_KEY);
  if (stored) return stored;
  const id = generateUUID();
  localStorage.setItem(SESSION_KEY, id);
  return id;
}

function saveMessage(sessionId: string, role: string, content: string) {
  fetch(`${SEARCH_API_URL}/chat/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, role, content }),
  }).catch(() => {});
}

export default function AgentChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [statusEvents, setStatusEvents] = useState<StatusEvent[]>([]);
  const [models, setModels] = useState<ModelsMap>({});
  const [selectedProvider, setSelectedProvider] = useState("");
  const [selectedModel, setSelectedModel] = useState("");
  const [sessionId, setSessionId] = useState(() => getOrCreateSessionId());
  const [hasMore, setHasMore] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [isRestoring, setIsRestoring] = useState(true);
  const isPrependingRef = useRef(false);

  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);

  // API 토큰 설정
  const [apiToken, setApiToken] = useState(() => {
    if (typeof window === "undefined") return "";
    return localStorage.getItem(TOKEN_KEY) || "";
  });
  const [showTokenModal, setShowTokenModal] = useState(false);
  const [showTokenHelp, setShowTokenHelp] = useState(false);
  const [tokenInput, setTokenInput] = useState("");

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesAreaRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

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

  // 초기 대화 복원
  useEffect(() => {
    fetch(`${SEARCH_API_URL}/chat/messages/${sessionId}?limit=${PAGE_SIZE}`)
      .then((res) => res.json())
      .then((data: { messages: ChatMessage[]; has_more: boolean }) => {
        if (data.messages.length > 0) {
          setMessages(data.messages);
          setHasMore(data.has_more);
        }
      })
      .catch(() => {})
      .finally(() => setIsRestoring(false));
  }, [sessionId]);

  // 새 메시지 시 스크롤 아래로 (복원 완료 후에만, 이전 대화 로딩 시 제외)
  useEffect(() => {
    if (!isRestoring && !isPrependingRef.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
    isPrependingRef.current = false;
  }, [messages, statusEvents, isRestoring]);

  // 복원 완료 직후 스크롤 아래로 (한 번만)
  useEffect(() => {
    if (!isRestoring && messages.length > 0) {
      messagesEndRef.current?.scrollIntoView({ behavior: "auto" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isRestoring]);

  useEffect(() => {
    if (!isLoading) inputRef.current?.focus();
  }, [isLoading]);

  // 스크롤 상단 도달 시 이전 메시지 로드
  const loadOlderMessages = useCallback(async () => {
    if (!hasMore || isLoadingMore || messages.length === 0) return;
    const oldestId = messages[0]?.id;
    if (oldestId == null) return;

    setIsLoadingMore(true);
    const area = messagesAreaRef.current;
    const prevScrollHeight = area?.scrollHeight ?? 0;

    try {
      const res = await fetch(
        `${SEARCH_API_URL}/chat/messages/${sessionId}?limit=${PAGE_SIZE}&before_id=${oldestId}`
      );
      const data: { messages: ChatMessage[]; has_more: boolean } = await res.json();
      if (data.messages.length > 0) {
        isPrependingRef.current = true;
        setMessages((prev) => [...data.messages, ...prev]);
        setHasMore(data.has_more);
        // 스크롤 위치 보정: prepend 후 기존 위치 유지
        requestAnimationFrame(() => {
          if (area) {
            area.scrollTop = area.scrollHeight - prevScrollHeight;
          }
        });
      } else {
        setHasMore(false);
      }
    } catch {
      // 네트워크 에러 시 무시
    } finally {
      setIsLoadingMore(false);
    }
  }, [hasMore, isLoadingMore, messages, sessionId]);

  const handleScroll = useCallback(() => {
    const area = messagesAreaRef.current;
    if (!area) return;
    if (area.scrollTop < 50 && hasMore && !isLoadingMore) {
      loadOlderMessages();
    }
  }, [hasMore, isLoadingMore, loadOlderMessages]);

  const handleProviderChange = (provider: string) => {
    setSelectedProvider(provider);
    const providerModels = models[provider] || [];
    setSelectedModel(providerModels[0] || "");
  };

  const handleFileSelect = (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    const newFiles: AttachedFile[] = files.map((file) => {
      const af: AttachedFile = { file };
      if (file.type.startsWith("image/")) {
        af.preview = URL.createObjectURL(file);
      }
      return af;
    });
    setAttachedFiles((prev) => [...prev, ...newFiles]);
    // reset input so same file can be re-selected
    e.target.value = "";
  };

  const removeFile = (index: number) => {
    setAttachedFiles((prev) => {
      const removed = prev[index];
      if (removed.preview) URL.revokeObjectURL(removed.preview);
      return prev.filter((_, i) => i !== index);
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const query = input.trim();
    if (!query || isLoading) return;

    const filesToSend = [...attachedFiles];
    setInput("");
    setAttachedFiles([]);
    setMessages((prev) => [...prev, { role: "user", content: query + (filesToSend.length ? ` [${filesToSend.map((f) => f.file.name).join(", ")}]` : "") }]);
    saveMessage(sessionId, "user", query);
    setStatusEvents([]);
    setIsLoading(true);

    // cleanup previews
    filesToSend.forEach((f) => { if (f.preview) URL.revokeObjectURL(f.preview); });

    try {
      const formData = new FormData();
      formData.append("query", query);
      formData.append("session_id", sessionId);
      if (selectedProvider) formData.append("provider", selectedProvider);
      if (selectedModel) formData.append("model", selectedModel);
      if (apiToken) formData.append("api_key", apiToken);
      for (const af of filesToSend) {
        formData.append("files", af.file);
      }

      const res = await fetch(`${AGENT_API_URL}/agent/ask`, {
        method: "POST",
        body: formData,
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
              saveMessage(sessionId, "assistant", data.text);
              fetch(`${SEARCH_API_URL}/log/chat`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  session_id: sessionId,
                  provider: selectedProvider || "unknown",
                  model: selectedModel || "",
                }),
              }).catch(() => {});
            } else if (data.type === "error") {
              setMessages((prev) => [
                ...prev,
                { role: "assistant", content: `오류: ${data.message}` },
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
          session_id: sessionId,
          provider: selectedProvider || undefined,
          model: selectedModel || undefined,
        }),
      });
    } catch {}
    const newId = generateUUID();
    localStorage.setItem(SESSION_KEY, newId);
    setSessionId(newId);
    setMessages([]);
    setStatusEvents([]);
    setHasMore(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  const handleTokenSave = () => {
    const trimmed = tokenInput.trim();
    if (trimmed) {
      localStorage.setItem(TOKEN_KEY, trimmed);
      setApiToken(trimmed);
    }
    setTokenInput("");
    setShowTokenModal(false);
  };

  const handleTokenClear = () => {
    localStorage.removeItem(TOKEN_KEY);
    setApiToken("");
    setTokenInput("");
    setShowTokenModal(false);
  };

  const providers = Object.keys(models);
  const currentModels = Array.isArray(models[selectedProvider]) ? models[selectedProvider] : [];

  return (
    <div className="flex flex-col h-[calc(100vh-18rem)]">
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

        <div className="ml-auto flex items-center gap-1">
          {/* Token status indicator */}
          {apiToken && (
            <span className="text-[10px] text-green-600 bg-green-50 px-1.5 py-0.5 rounded">
              내 토큰
            </span>
          )}

          {/* Token settings button */}
          <button
            type="button"
            onClick={() => { setTokenInput(apiToken); setShowTokenModal(true); }}
            className={`p-1.5 rounded-lg transition-colors ${
              apiToken
                ? "text-green-600 hover:bg-green-50"
                : "text-gray-400 hover:bg-gray-100"
            }`}
            title="API 토큰 설정"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
            </svg>
          </button>
        </div>
      </div>

      {/* Token Settings Modal */}
      {showTokenModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={() => setShowTokenModal(false)}>
          <div className="bg-white rounded-2xl shadow-xl w-[420px] max-w-[90vw] p-6" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold text-gray-900">API 토큰 설정</h3>
              <button
                type="button"
                onClick={() => setShowTokenHelp(true)}
                className="text-xs text-blue-500 hover:text-blue-700 flex items-center gap-1"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                토큰 발급 방법
              </button>
            </div>

            <p className="text-xs text-gray-500 mb-3">
              개인 Anthropic API 토큰을 설정하면 서버 토큰 대신 내 토큰으로 요청합니다.
              설정하지 않으면 서버에 설정된 공용 토큰을 사용합니다.
            </p>

            <input
              type="password"
              value={tokenInput}
              onChange={(e) => setTokenInput(e.target.value)}
              placeholder="sk-ant-api... 또는 sk-ant-oat..."
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent mb-4"
              autoFocus
              onKeyDown={(e) => { if (e.key === "Enter") handleTokenSave(); }}
            />

            <div className="flex gap-2">
              <button
                type="button"
                onClick={handleTokenSave}
                disabled={!tokenInput.trim()}
                className="flex-1 px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                저장
              </button>
              {apiToken && (
                <button
                  type="button"
                  onClick={handleTokenClear}
                  className="px-4 py-2 text-red-500 text-sm rounded-lg border border-red-200 hover:bg-red-50"
                >
                  삭제
                </button>
              )}
              <button
                type="button"
                onClick={() => setShowTokenModal(false)}
                className="px-4 py-2 text-gray-500 text-sm rounded-lg border border-gray-200 hover:bg-gray-50"
              >
                취소
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Token Help Popup */}
      {showTokenHelp && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/30" onClick={() => setShowTokenHelp(false)}>
          <div className="bg-white rounded-2xl shadow-xl w-[600px] max-w-[90vw] p-6" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-semibold text-gray-900 mb-3">Claude OAuth 토큰 발급 방법</h3>
            <p className="text-xs text-gray-500 mb-3">
              Claude Max/Team/Enterprise 구독자는 CLI를 통해 OAuth 토큰을 발급받아 사용할 수 있습니다.
            </p>
            <ol className="text-xs text-gray-600 space-y-2.5 list-decimal list-inside mb-4">
              <li>
                터미널에서 Claude Code CLI를 설치합니다:
                <code className="block bg-gray-100 px-2 py-1 rounded text-[11px] mt-1 ml-4">curl -fsSL https://claude.ai/install.sh | bash</code>
              </li>
              <li>
                로그인하여 OAuth 인증을 완료합니다:
                <code className="block bg-gray-100 px-2 py-1 rounded text-[11px] mt-1 ml-4">claude setup-token</code>
                <span className="text-gray-400 ml-4 block mt-0.5">브라우저가 열리면 Claude 계정으로 로그인합니다.</span>
              </li>
              <li>
                발급된 OAuth 토큰을 확인합니다:
                <code className="block bg-gray-100 px-2 py-1 rounded text-[11px] mt-1 ml-4">
✓ Long-lived authentication token created successfully!<br/><br/>
Your OAuth token (valid for 1 year):<br/><br/>
<span className="text-green-600 font-bold">sk-ant-oat-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX</span>  &lt;- 이 토큰을 복사하세요
                </code>
                <span className="text-gray-400 ml-4 block mt-0.5">
                  <code className="bg-gray-100 px-1 rounded text-[11px]">sk-ant-oat...</code> 형식의 토큰을 복사하여 위 입력창에 붙여넣습니다.
                </span>
              </li>
              <li>
                복사한 토큰을 API 토큰 설정 창에 붙여넣고 저장합니다.
              </li>
            </ol>
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-4">
              <p className="text-xs text-amber-700">
                <strong>참고:</strong> OAuth 토큰은 Claude Max/Team/Enterprise 구독이 필요합니다.
                토큰을 설정하지 않으면 서버의 공용 토큰을 사용합니다.
              </p>
            </div>
            <button
              type="button"
              onClick={() => setShowTokenHelp(false)}
              className="w-full px-4 py-2 bg-gray-100 text-gray-700 text-sm rounded-lg hover:bg-gray-200"
            >
              닫기
            </button>
          </div>
        </div>
      )}

      {/* Messages Area */}
      <div
        ref={messagesAreaRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto space-y-4 pb-4"
      >
        {/* Load More Indicator */}
        {isLoadingMore && (
          <div className="text-center text-xs text-gray-400 py-2">
            이전 대화 불러오는 중...
          </div>
        )}
        {hasMore && !isLoadingMore && (
          <button
            onClick={loadOlderMessages}
            className="w-full text-center text-xs text-gray-400 py-2 hover:text-gray-600"
          >
            이전 대화 더 보기
          </button>
        )}

        {messages.length === 0 && !isLoading && !isRestoring && (
          <div className="flex items-center justify-center h-full text-gray-400">
            <div className="text-center">
              <div className="text-4xl mb-4">&#x1F9E0;</div>
              <p className="text-lg font-medium">플랫폼전략본부 AI Agent</p>
              <p className="text-sm mt-1">
                팀 지식베이스를 검색하고 분석하여 답변합니다
              </p>
            </div>
          </div>
        )}

        {isRestoring && (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            대화 복원 중...
          </div>
        )}

        {messages.map((msg, i) => (
          <div
            key={msg.id ?? `local-${i}`}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[80%] rounded-2xl px-4 py-3 ${
                msg.role === "user"
                  ? "bg-blue-600 text-white"
                  : "bg-white border border-gray-200 text-gray-900"
              }`}
            >
              {msg.role === "user" ? (
                <div className="text-sm leading-relaxed whitespace-pre-wrap">
                  <Linkify text={msg.content} isUser />
                </div>
              ) : (
                <MarkdownContent content={msg.content} />
              )}
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
        {/* Attached Files Preview */}
        {attachedFiles.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-2">
            {attachedFiles.map((af, i) => (
              <div key={i} className="relative group flex items-center gap-1.5 bg-gray-100 rounded-lg px-2.5 py-1.5 text-xs text-gray-600">
                {af.preview ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={af.preview} alt={af.file.name} className="h-6 w-6 rounded object-cover" />
                ) : (
                  <FileTypeIcon name={af.file.name} />
                )}
                <span className="max-w-[120px] truncate">{af.file.name}</span>
                <button
                  type="button"
                  onClick={() => removeFile(i)}
                  className="ml-0.5 text-gray-400 hover:text-red-500"
                  title="제거"
                >
                  &#x2715;
                </button>
              </div>
            ))}
          </div>
        )}

        <form onSubmit={handleSubmit} className="flex gap-2 items-end">
          {/* Hidden file input */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ACCEPTED_FILE_TYPES}
            onChange={handleFileSelect}
            className="hidden"
          />
          {/* Attach button */}
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={isLoading}
            className="px-3 py-3 text-gray-400 hover:text-gray-600 disabled:opacity-50 flex-shrink-0"
            title="파일 첨부 (이미지, PDF, Excel, PPT)"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
            </svg>
          </button>
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

function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="text-sm leading-relaxed prose prose-sm max-w-none prose-headings:mt-3 prose-headings:mb-1 prose-p:my-1.5 prose-ul:my-1 prose-ol:my-1 prose-li:my-0.5 prose-pre:my-2 prose-blockquote:my-2 prose-hr:my-3 prose-table:my-2">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:text-blue-800 underline break-all">
              {children}
            </a>
          ),
          pre: ({ children }) => (
            <pre className="not-prose bg-[#1e1e2e] rounded-lg p-4 overflow-x-auto my-2 text-[13px] leading-6 [&>code]:bg-transparent [&>code]:p-0 [&>code]:rounded-none [&>code]:text-[#cdd6f4] [&>code]:text-[13px]">
              {children}
            </pre>
          ),
          code: ({ className, children, ...props }) => {
            if (className?.startsWith("language-")) {
              return <code className={className} {...props}>{children}</code>;
            }
            return <code className="bg-gray-100 text-gray-800 px-1.5 py-0.5 rounded text-xs font-mono" {...props}>{children}</code>;
          },
          table: ({ children }) => (
            <div className="overflow-x-auto">
              <table className="border-collapse border border-gray-300 text-xs w-full">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border border-gray-300 bg-gray-50 px-2 py-1 text-left font-medium">{children}</th>
          ),
          td: ({ children }) => (
            <td className="border border-gray-300 px-2 py-1">{children}</td>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

const URL_REGEX = /(https?:\/\/[^\s<>)"]+)/g;

function Linkify({ text, isUser }: { text: string; isUser?: boolean }) {
  const parts = text.split(URL_REGEX);
  return (
    <>
      {parts.map((part, i) =>
        URL_REGEX.test(part) ? (
          <a
            key={i}
            href={part}
            target="_blank"
            rel="noopener noreferrer"
            className={`underline break-all ${isUser ? "text-blue-100 hover:text-white" : "text-blue-600 hover:text-blue-800"}`}
          >
            {part}
          </a>
        ) : (
          <span key={i}>{part}</span>
        )
      )}
    </>
  );
}

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

function FileTypeIcon({ name }: { name: string }) {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  let label = "FILE";
  let color = "bg-gray-400";
  if (ext === "pdf") { label = "PDF"; color = "bg-red-500"; }
  else if (["xlsx", "xls"].includes(ext)) { label = "XLS"; color = "bg-green-600"; }
  else if (["pptx", "ppt"].includes(ext)) { label = "PPT"; color = "bg-orange-500"; }
  return (
    <span className={`inline-flex items-center justify-center h-6 w-6 rounded text-[9px] font-bold text-white ${color}`}>
      {label}
    </span>
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
