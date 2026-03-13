"use client";

import { useState } from "react";

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <button
      onClick={handleCopy}
      className={`px-2 py-0.5 rounded text-xs transition-colors ${
        copied
          ? "bg-green-100 text-green-600"
          : "bg-gray-100 text-gray-500 hover:bg-blue-100 hover:text-blue-600"
      }`}
    >
      {copied ? "복사됨" : "복사"}
    </button>
  );
}

function CodeBlock({ code, language = "" }: { code: string; language?: string }) {
  return (
    <div className="relative group">
      <div className="absolute right-2 top-2">
        <CopyButton text={code} />
      </div>
      <pre className="bg-gray-900 text-green-400 p-4 rounded-lg text-xs overflow-auto leading-relaxed whitespace-pre-wrap">
        {language && (
          <span className="text-gray-500 text-[10px] block mb-2">{language}</span>
        )}
        {code}
      </pre>
    </div>
  );
}

export default function McpSetupGuide() {
  const serverUrl =
    typeof window !== "undefined"
      ? `http://${window.location.hostname}:3001/mcp`
      : "http://<서버IP>:3001/mcp";

  const claudeCodeJson = JSON.stringify(
    {
      mcpServers: {
        "platform-rag": {
          type: "http",
          url: serverUrl,
        },
      },
    },
    null,
    2,
  );

  const claudeDesktopJson = JSON.stringify(
    {
      mcpServers: {
        "platform-rag": {
          type: "http",
          url: serverUrl,
        },
      },
    },
    null,
    2,
  );

  const settingsJson = JSON.stringify(
    {
      projects: {
        "/your/project/path": {
          mcpServers: {
            "platform-rag": {
              type: "http",
              url: serverUrl,
            },
          },
        },
      },
    },
    null,
    2,
  );

  return (
    <div className="space-y-6">
      {/* 개요 */}
      <div className="bg-white border border-gray-200 rounded-lg p-5">
        <h3 className="font-semibold text-gray-900 mb-2">MCP 서버 연결 가이드</h3>
        <p className="text-sm text-gray-600">
          Platform RAG의 MCP 서버에 연결하면 Claude가 지식베이스를 직접 검색할 수 있습니다.
          아래 방법 중 하나를 선택하여 설정하세요.
        </p>
        <div className="mt-3 flex items-center gap-2 px-3 py-2 bg-blue-50 border border-blue-200 rounded-lg">
          <span className="text-blue-600 font-mono text-sm font-medium">{serverUrl}</span>
          <CopyButton text={serverUrl} />
        </div>
      </div>

      {/* 방법 1: Claude Code CLI */}
      <div className="bg-white border border-gray-200 rounded-lg p-5">
        <h3 className="font-semibold text-gray-900 mb-1">
          1. Claude Code (CLI / VS Code)
        </h3>
        <p className="text-xs text-gray-500 mb-3">
          프로젝트 루트에 <code className="bg-gray-100 px-1 rounded">.mcp.json</code> 파일을 생성합니다.
        </p>
        <CodeBlock code={claudeCodeJson} language=".mcp.json" />

        <div className="mt-4 border-t border-gray-100 pt-4">
          <p className="text-xs text-gray-500 mb-2">
            또는 전역 설정 파일에 추가할 수도 있습니다:
          </p>
          <p className="text-xs text-gray-400 mb-2 font-mono">
            ~/.claude/settings.json
          </p>
          <CodeBlock code={settingsJson} language="~/.claude/settings.json" />
        </div>

        <div className="mt-4 border-t border-gray-100 pt-4">
          <p className="text-xs text-gray-500 mb-2">
            설정 후 Claude Code에서 확인:
          </p>
          <CodeBlock code="claude mcp list" language="터미널" />
        </div>
      </div>

      {/* 방법 2: Claude Desktop */}
      <div className="bg-white border border-gray-200 rounded-lg p-5">
        <h3 className="font-semibold text-gray-900 mb-1">
          2. Claude Desktop
        </h3>
        <p className="text-xs text-gray-500 mb-3">
          Claude Desktop 앱의 설정에서 MCP 서버를 추가합니다.
        </p>

        <div className="space-y-3">
          <div className="flex items-start gap-3">
            <span className="flex-shrink-0 w-6 h-6 rounded-full bg-gray-100 text-gray-600 text-xs flex items-center justify-center font-medium">1</span>
            <p className="text-sm text-gray-700">
              Claude Desktop 실행 &rarr; 설정(Settings) &rarr; Developer &rarr; Edit Config
            </p>
          </div>
          <div className="flex items-start gap-3">
            <span className="flex-shrink-0 w-6 h-6 rounded-full bg-gray-100 text-gray-600 text-xs flex items-center justify-center font-medium">2</span>
            <div className="flex-1">
              <p className="text-sm text-gray-700 mb-2">
                <code className="bg-gray-100 px-1 rounded text-xs">claude_desktop_config.json</code>에 아래 내용을 추가:
              </p>
              <CodeBlock code={claudeDesktopJson} language="claude_desktop_config.json" />
            </div>
          </div>
          <div className="flex items-start gap-3">
            <span className="flex-shrink-0 w-6 h-6 rounded-full bg-gray-100 text-gray-600 text-xs flex items-center justify-center font-medium">3</span>
            <p className="text-sm text-gray-700">
              Claude Desktop를 재시작합니다.
            </p>
          </div>
        </div>
      </div>

      {/* 사용 가능한 도구 */}
      <div className="bg-white border border-gray-200 rounded-lg p-5">
        <h3 className="font-semibold text-gray-900 mb-3">사용 가능한 도구</h3>
        <div className="space-y-3">
          <div className="flex items-start gap-3 p-3 bg-gray-50 rounded-lg">
            <code className="text-xs font-bold text-blue-600 flex-shrink-0 mt-0.5">search_knowledge</code>
            <p className="text-xs text-gray-600">키워드로 지식베이스를 검색합니다. Hybrid Search(Vector + BM25) + Reranker 파이프라인을 사용합니다.</p>
          </div>
          <div className="flex items-start gap-3 p-3 bg-gray-50 rounded-lg">
            <code className="text-xs font-bold text-blue-600 flex-shrink-0 mt-0.5">get_document</code>
            <p className="text-xs text-gray-600">문서 ID로 전체 내용과 메타데이터를 가져옵니다.</p>
          </div>
          <div className="flex items-start gap-3 p-3 bg-gray-50 rounded-lg">
            <code className="text-xs font-bold text-blue-600 flex-shrink-0 mt-0.5">list_sources</code>
            <p className="text-xs text-gray-600">수집된 문서 목록을 소스별로 조회합니다.</p>
          </div>
          <div className="flex items-start gap-3 p-3 bg-gray-50 rounded-lg">
            <code className="text-xs font-bold text-blue-600 flex-shrink-0 mt-0.5">get_related</code>
            <p className="text-xs text-gray-600">특정 문서와 유사한 관련 문서를 탐색합니다.</p>
          </div>
          <div className="flex items-start gap-3 p-3 bg-gray-50 rounded-lg">
            <code className="text-xs font-bold text-blue-600 flex-shrink-0 mt-0.5">list_email_contacts</code>
            <p className="text-xs text-gray-600">이메일 인물 목록을 검색합니다.</p>
          </div>
          <div className="flex items-start gap-3 p-3 bg-gray-50 rounded-lg">
            <code className="text-xs font-bold text-blue-600 flex-shrink-0 mt-0.5">get_search_filters</code>
            <p className="text-xs text-gray-600">검색에 사용 가능한 소스/타입 필터 목록을 반환합니다.</p>
          </div>
        </div>
      </div>

      {/* 트러블슈팅 */}
      <div className="bg-white border border-gray-200 rounded-lg p-5">
        <h3 className="font-semibold text-gray-900 mb-3">트러블슈팅</h3>
        <div className="space-y-3 text-sm">
          <div>
            <p className="font-medium text-gray-700">연결이 안 되는 경우</p>
            <ul className="mt-1 ml-4 list-disc text-xs text-gray-500 space-y-1">
              <li>MCP 서버가 실행 중인지 확인: <code className="bg-gray-100 px-1 rounded">curl {serverUrl}</code></li>
              <li>방화벽에서 포트 3001이 열려 있는지 확인</li>
              <li>IP 주소가 올바른지 확인 (같은 네트워크에 있어야 합니다)</li>
            </ul>
          </div>
          <div>
            <p className="font-medium text-gray-700">도구가 보이지 않는 경우</p>
            <ul className="mt-1 ml-4 list-disc text-xs text-gray-500 space-y-1">
              <li>Claude Code: <code className="bg-gray-100 px-1 rounded">claude mcp list</code>로 연결 상태 확인</li>
              <li>Claude Desktop: 앱 재시작 후 대화창 하단의 MCP 아이콘 확인</li>
              <li>Search API 서버(포트 8000)도 함께 실행 중이어야 합니다</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
