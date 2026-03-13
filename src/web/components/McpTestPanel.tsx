"use client";

import { useCallback, useRef, useState } from "react";

const DEFAULT_MCP_URL = "http://localhost:3001/mcp";

interface ToolSchema {
  name: string;
  description: string;
  inputSchema: {
    properties?: Record<
      string,
      { type?: string; description?: string; default?: unknown; enum?: unknown[] }
    >;
    required?: string[];
  };
}

interface LogEntry {
  id: number;
  direction: "send" | "recv";
  timestamp: string;
  data: string;
}

let logIdCounter = Date.now();

function ClipboardIcon({ className = "w-3.5 h-3.5" }: { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className={className}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 17.25v3.375c0 .621-.504 1.125-1.125 1.125h-9.75a1.125 1.125 0 0 1-1.125-1.125V7.875c0-.621.504-1.125 1.125-1.125H6.75a9.06 9.06 0 0 1 1.5.124m7.5 10.376h3.375c.621 0 1.125-.504 1.125-1.125V11.25c0-4.46-3.243-8.161-7.5-8.876a9.06 9.06 0 0 0-1.5-.124H9.375c-.621 0-1.125.504-1.125 1.125v3.5m7.5 10.375H9.375a1.125 1.125 0 0 1-1.125-1.125v-9.25m12 6.625v-1.875a3.375 3.375 0 0 0-3.375-3.375h-1.5a1.125 1.125 0 0 1-1.125-1.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H9.75" />
    </svg>
  );
}

function CheckIcon({ className = "w-3.5 h-3.5" }: { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className={className}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
    </svg>
  );
}

function CopyButton({
  text,
  label,
  className = "",
  iconClassName = "w-3.5 h-3.5",
}: {
  text: string;
  label?: string;
  className?: string;
  iconClassName?: string;
}) {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <button
      onClick={handleCopy}
      className={`flex items-center gap-1 transition-colors ${
        copied ? "text-green-500" : "text-gray-400 hover:text-blue-500"
      } ${className}`}
      title={copied ? "복사됨" : "복사"}
    >
      {copied ? <CheckIcon className={iconClassName} /> : <ClipboardIcon className={iconClassName} />}
      {label && <span>{copied ? "복사됨" : label}</span>}
    </button>
  );
}

async function sendRpc(
  url: string,
  body: unknown,
  sessionId?: string,
): Promise<{ parsed: unknown; raw: string; sessionId?: string }> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json, text/event-stream",
  };
  if (sessionId) {
    headers["mcp-session-id"] = sessionId;
  }

  const res = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });

  // 응답 헤더에서 세션 ID 추출
  const respSessionId = res.headers.get("mcp-session-id") ?? undefined;

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status}: ${text}`);
  }

  const contentType = res.headers.get("content-type") ?? "";

  if (contentType.includes("text/event-stream")) {
    const text = await res.text();
    const lines = text.split("\n");
    const dataLines = lines
      .filter((l) => l.startsWith("data: "))
      .map((l) => l.slice(6));
    const lastData = dataLines[dataLines.length - 1];
    if (lastData) {
      return { parsed: JSON.parse(lastData), raw: lastData, sessionId: respSessionId };
    }
    return { parsed: null, raw: text, sessionId: respSessionId };
  }

  const raw = await res.text();
  return { parsed: JSON.parse(raw), raw, sessionId: respSessionId };
}

export default function McpTestPanel() {
  const [mcpUrl, setMcpUrl] = useState(DEFAULT_MCP_URL);
  const [connected, setConnected] = useState(false);
  const [serverInfo, setServerInfo] = useState<string>("");
  const [tools, setTools] = useState<ToolSchema[]>([]);
  const [selectedTool, setSelectedTool] = useState<string>("");
  const [toolParams, setToolParams] = useState<Record<string, string>>({});
  const [toolResult, setToolResult] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [customRpc, setCustomRpc] = useState(
    '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
  );

  const rpcIdRef = useRef(1);
  const nextId = () => rpcIdRef.current++;
  const sessionIdRef = useRef<string | undefined>(undefined);

  const addLog = useCallback(
    (direction: "send" | "recv", data: string) => {
      const entry: LogEntry = {
        id: logIdCounter++,
        direction,
        timestamp: new Date().toLocaleTimeString(),
        data,
      };
      setLogs((prev) => [...prev, entry]);
    },
    []
  );

  const rpc = useCallback(
    async (method: string, params: unknown = {}) => {
      const id = nextId();
      const body = { jsonrpc: "2.0", id, method, params };
      addLog("send", JSON.stringify(body, null, 2));

      const { parsed, raw, sessionId: sid } = await sendRpc(mcpUrl, body, sessionIdRef.current);
      if (sid) sessionIdRef.current = sid;
      addLog("recv", JSON.stringify(parsed ?? raw, null, 2));
      return parsed as Record<string, unknown>;
    },
    [mcpUrl, addLog]
  );

  // Initialize
  const handleConnect = async () => {
    setIsLoading(true);
    setTools([]);
    setSelectedTool("");
    setToolResult(null);
    sessionIdRef.current = undefined; // 새 연결 시 세션 초기화

    try {
      const initRes = await rpc("initialize", {
        protocolVersion: "2024-11-05",
        capabilities: {},
        clientInfo: { name: "web-mcp-client", version: "0.1.0" },
      });

      const result = initRes.result as {
        serverInfo?: { name: string; version: string };
        protocolVersion?: string;
      };
      setServerInfo(
        `${result?.serverInfo?.name} v${result?.serverInfo?.version} (protocol ${result?.protocolVersion})`
      );

      // Send initialized notification (no id)
      const notifBody = {
        jsonrpc: "2.0",
        method: "notifications/initialized",
        params: {},
      };
      addLog("send", JSON.stringify(notifBody, null, 2));
      const notifHeaders: Record<string, string> = {
        "Content-Type": "application/json",
        Accept: "application/json, text/event-stream",
      };
      if (sessionIdRef.current) {
        notifHeaders["mcp-session-id"] = sessionIdRef.current;
      }
      await fetch(mcpUrl, {
        method: "POST",
        headers: notifHeaders,
        body: JSON.stringify(notifBody),
      });
      addLog("recv", "(notification accepted)");

      // List tools
      const toolsRes = await rpc("tools/list", {});
      const toolsResult = toolsRes.result as { tools: ToolSchema[] };
      setTools(toolsResult?.tools ?? []);
      setConnected(true);
    } catch (err) {
      addLog(
        "recv",
        `ERROR: ${err instanceof Error ? err.message : String(err)}`
      );
    } finally {
      setIsLoading(false);
    }
  };

  // Select tool
  const handleSelectTool = (name: string) => {
    setSelectedTool(name);
    setToolResult(null);
    const tool = tools.find((t) => t.name === name);
    if (tool?.inputSchema?.properties) {
      const defaults: Record<string, string> = {};
      for (const [key, schema] of Object.entries(
        tool.inputSchema.properties
      )) {
        defaults[key] =
          schema.default !== undefined ? String(schema.default) : "";
      }
      setToolParams(defaults);
    } else {
      setToolParams({});
    }
  };

  // Call tool
  const handleCallTool = async () => {
    if (!selectedTool) return;
    setIsLoading(true);
    setToolResult(null);

    try {
      const tool = tools.find((t) => t.name === selectedTool);
      const args: Record<string, unknown> = {};

      if (tool?.inputSchema?.properties) {
        for (const [key, schema] of Object.entries(
          tool.inputSchema.properties
        )) {
          const val = toolParams[key];
          if (val === "" || val === undefined) continue;
          if (schema.type === "number" || schema.type === "integer") {
            args[key] = Number(val);
          } else if (schema.type === "boolean") {
            args[key] = val === "true";
          } else {
            args[key] = val;
          }
        }
      }

      const res = await rpc("tools/call", { name: selectedTool, arguments: args });
      const result = res.result as {
        content?: Array<{ type: string; text: string }>;
        isError?: boolean;
      };
      setToolResult(JSON.stringify(result, null, 2));
    } catch (err) {
      setToolResult(
        `ERROR: ${err instanceof Error ? err.message : String(err)}`
      );
    } finally {
      setIsLoading(false);
    }
  };

  // Custom RPC
  const handleCustomRpc = async () => {
    setIsLoading(true);
    try {
      const body = JSON.parse(customRpc);
      addLog("send", JSON.stringify(body, null, 2));
      const { parsed, raw, sessionId: sid } = await sendRpc(mcpUrl, body, sessionIdRef.current);
      if (sid) sessionIdRef.current = sid;
      const formatted = JSON.stringify(parsed ?? raw, null, 2);
      addLog("recv", formatted);
      setToolResult(formatted);
    } catch (err) {
      const msg = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
      addLog("recv", msg);
      setToolResult(msg);
    } finally {
      setIsLoading(false);
    }
  };

  const currentTool = tools.find((t) => t.name === selectedTool);

  return (
    <div className="space-y-6">
      {/* Connection */}
      <div className="bg-white border border-gray-200 rounded-lg p-5">
        <h3 className="font-semibold text-gray-900 mb-3">MCP 서버 연결</h3>
        <div className="flex gap-2">
          <input
            type="text"
            value={mcpUrl}
            onChange={(e) => setMcpUrl(e.target.value)}
            placeholder="MCP Server URL"
            className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            onClick={handleConnect}
            disabled={isLoading}
            className={`px-4 py-2 rounded-lg text-sm font-medium text-white ${
              connected
                ? "bg-green-600 hover:bg-green-700"
                : "bg-blue-600 hover:bg-blue-700"
            } disabled:opacity-50`}
          >
            {isLoading
              ? "연결 중..."
              : connected
                ? "재연결"
                : "Initialize"}
          </button>
        </div>
        {serverInfo && (
          <p className="mt-2 text-xs text-green-600">{serverInfo}</p>
        )}
      </div>

      {/* Tools */}
      {connected && tools.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-lg p-5">
          <h3 className="font-semibold text-gray-900 mb-3">
            도구 목록 ({tools.length}개)
          </h3>

          <div className="flex flex-wrap gap-2 mb-4">
            {tools.map((tool) => (
              <button
                key={tool.name}
                onClick={() => handleSelectTool(tool.name)}
                className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
                  selectedTool === tool.name
                    ? "bg-blue-600 text-white"
                    : "bg-gray-100 text-gray-700 hover:bg-gray-200"
                }`}
              >
                {tool.name}
              </button>
            ))}
          </div>

          {currentTool && (
            <div className="border-t border-gray-100 pt-4 space-y-4">
              <div>
                <p className="text-sm font-medium text-gray-700">
                  {currentTool.name}
                </p>
                <p className="text-xs text-gray-500 mt-1">
                  {currentTool.description}
                </p>
              </div>

              {/* Parameters */}
              {currentTool.inputSchema?.properties && (
                <div className="space-y-3">
                  {Object.entries(currentTool.inputSchema.properties).map(
                    ([key, schema]) => {
                      const required =
                        currentTool.inputSchema.required?.includes(key);
                      return (
                        <label key={key} className="block">
                          <span className="text-sm font-medium text-gray-700">
                            {key}
                            {required && (
                              <span className="text-red-500 ml-0.5">*</span>
                            )}
                            <span className="font-normal text-gray-400 ml-2">
                              {schema.type}
                            </span>
                          </span>
                          {schema.description && (
                            <span className="block text-xs text-gray-400">
                              {schema.description}
                            </span>
                          )}
                          {schema.type === "boolean" ? (
                            <select
                              value={toolParams[key] ?? ""}
                              onChange={(e) =>
                                setToolParams((p) => ({
                                  ...p,
                                  [key]: e.target.value,
                                }))
                              }
                              className="mt-1 w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                            >
                              <option value="true">true</option>
                              <option value="false">false</option>
                            </select>
                          ) : (
                            <input
                              type={
                                schema.type === "number" ||
                                schema.type === "integer"
                                  ? "number"
                                  : "text"
                              }
                              value={toolParams[key] ?? ""}
                              onChange={(e) =>
                                setToolParams((p) => ({
                                  ...p,
                                  [key]: e.target.value,
                                }))
                              }
                              placeholder={
                                schema.default !== undefined
                                  ? `기본값: ${schema.default}`
                                  : ""
                              }
                              className="mt-1 w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                            />
                          )}
                        </label>
                      );
                    }
                  )}
                </div>
              )}

              <button
                onClick={handleCallTool}
                disabled={isLoading}
                className="px-6 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
              >
                {isLoading ? "호출 중..." : `tools/call → ${selectedTool}`}
              </button>
            </div>
          )}
        </div>
      )}

      {/* Tool Result */}
      {toolResult && (
        <div className="bg-white border border-gray-200 rounded-lg p-5">
          <div className="flex items-center justify-between mb-2">
            <h3 className="font-semibold text-gray-900">도구 응답</h3>
            <CopyButton text={toolResult} />
          </div>
          <pre className="bg-gray-900 text-green-400 p-4 rounded-lg text-xs overflow-auto max-h-[400px] leading-relaxed whitespace-pre-wrap">
            {toolResult}
          </pre>
        </div>
      )}

      {/* Custom JSON-RPC */}
      <div className="bg-white border border-gray-200 rounded-lg p-5">
        <h3 className="font-semibold text-gray-900 mb-3">
          Custom JSON-RPC 요청
        </h3>
        <textarea
          value={customRpc}
          onChange={(e) => setCustomRpc(e.target.value)}
          rows={4}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          onClick={handleCustomRpc}
          disabled={isLoading}
          className="mt-2 px-6 py-2 bg-gray-800 text-white rounded-lg text-sm font-medium hover:bg-gray-900 disabled:opacity-50"
        >
          {isLoading ? "전송 중..." : "전송"}
        </button>
      </div>

      {/* Message Log */}
      {logs.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-lg p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold text-gray-900">
              메시지 로그 ({logs.length})
            </h3>
            <div className="flex items-center gap-3">
              <CopyButton
                text={logs
                  .map((e) => `[${e.direction.toUpperCase()}] ${e.timestamp}\n${e.data}`)
                  .join("\n\n---\n\n")}
                label="전체"
                className="text-xs"
                iconClassName="w-3 h-3"
              />
              <button
                onClick={() => setLogs([])}
                className="text-xs text-red-500 hover:underline"
              >
                초기화
              </button>
            </div>
          </div>
          <div className="space-y-2 max-h-[500px] overflow-auto">
            {logs.map((entry) => (
              <div
                key={entry.id}
                className={`p-3 rounded-lg text-xs font-mono ${
                  entry.direction === "send"
                    ? "bg-blue-50 border border-blue-200"
                    : "bg-gray-50 border border-gray-200"
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span
                    className={`font-bold ${
                      entry.direction === "send"
                        ? "text-blue-600"
                        : "text-gray-600"
                    }`}
                  >
                    {entry.direction === "send" ? "SEND" : "RECV"}
                  </span>
                  <span className="text-gray-400">{entry.timestamp}</span>
                  <CopyButton
                    text={entry.data}
                    className="ml-auto"
                    iconClassName="w-3 h-3"
                  />
                </div>
                <pre className="whitespace-pre-wrap break-all leading-relaxed">
                  {entry.data}
                </pre>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
