import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";

const __dirname = dirname(fileURLToPath(import.meta.url));

const API_URL = process.env.SEARCH_API_URL ?? "http://localhost:8000";
const MCP_PORT = Number(process.env.MCP_PORT ?? "3001");

// ─── 도구 스펙 로드 (tools_spec.json이 SSOT) ──────────────

interface ToolParam {
  name: string;
  type: "string" | "integer" | "boolean";
  description: string;
  required: boolean;
  default?: unknown;
}

interface ToolApi {
  method: "GET" | "POST" | "PUT" | "DELETE";
  path: string;
  timeout?: number;
  param_rename?: Record<string, string>;
}

interface ToolSpec {
  name: string;
  description: string;
  api: ToolApi;
  parameters: ToolParam[];
}

const TOOLS_SPEC: ToolSpec[] = JSON.parse(
  readFileSync(join(__dirname, "../tools_spec.json"), "utf-8")
);

// ─── Zod 스키마 동적 생성 ──────────────────────────────

function buildZodSchema(params: ToolParam[]): Record<string, z.ZodTypeAny> {
  const schema: Record<string, z.ZodTypeAny> = {};
  for (const p of params) {
    let base: z.ZodTypeAny =
      p.type === "integer" ? z.number().int()
      : p.type === "boolean" ? z.boolean()
      : z.string();

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const field: z.ZodTypeAny = p.default !== undefined
      ? (base as any).default(p.default).describe(p.description)
      : !p.required
        ? base.optional().describe(p.description)
        : base.describe(p.description);

    schema[p.name] = field;
  }
  return schema;
}

// ─── 제네릭 API 호출 (tools_spec.json의 api 섹션 기반) ────

type ToolInput = Record<string, unknown>;

async function executeApiCall(spec: ToolSpec, input: ToolInput): Promise<unknown> {
  // 1. path param 치환
  let path = spec.api.path;
  const pathParams = new Set<string>();
  for (const key of Object.keys(input)) {
    if (path.includes(`{${key}}`)) {
      path = path.replace(`{${key}}`, String(input[key]));
      pathParams.add(key);
    }
  }

  // 2. 나머지 인자 구성 (path param 제외, null/undefined 제외, rename 적용)
  const rename = spec.api.param_rename ?? {};
  const rest = Object.fromEntries(
    Object.entries(input)
      .filter(([k, v]) => !pathParams.has(k) && v != null)
      .map(([k, v]) => [rename[k] ?? k, v])
  );

  const url = `${API_URL}${path}`;
  const method = spec.api.method;

  let res: Response;
  if (method === "GET") {
    const qs = new URLSearchParams(rest as Record<string, string>).toString();
    res = await fetch(`${url}${qs ? `?${qs}` : ""}`);
  } else {
    res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(rest),
    });
  }

  if (!res.ok) throw new Error(`API error: ${res.status} ${res.statusText}`);
  return res.json();
}

// ─── 응답 포맷 헬퍼 ────────────────────────────────────

function text(t: string) {
  return { content: [{ type: "text" as const, text: t }] };
}

// ─── MCP 서버 + 도구 등록 ──────────────────────────────

const server = new McpServer({ name: "platform-rag", version: "0.1.0" });

for (const spec of TOOLS_SPEC) {
  server.registerTool(
    spec.name,
    { description: spec.description, inputSchema: buildZodSchema(spec.parameters) },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (async (input: ToolInput) => {
      try {
        const data = await executeApiCall(spec, input);
        return formatResponse(spec.name, input, data);
      } catch (err) {
        return { ...text(`오류: ${String(err)}`), isError: true };
      }
    }) as any
  );
}

function formatResponse(name: string, input: ToolInput, data: unknown) {
  switch (name) {
    case "search_knowledge": {
      const d = data as {
        query: string; count: number;
        results: Array<{ id: string; text: string; metadata: Record<string, string>; rrf_score: number | null; rerank_score: number | null }>;
        timings: Record<string, number>;
      };
      if (d.count === 0) return text(`"${input.query}" 검색 결과가 없습니다. 다른 키워드로 다시 검색해 주세요.`);

      const resultText = d.results.map((r, i) => {
        const m = r.metadata;
        const info = [
          m.title && `제목: ${m.title}`,
          m.source && `출처: ${m.source}`,
          m.file_name && `파일: ${m.file_name}`,
          m.url && `URL: ${m.url}`,
          r.rrf_score != null && `RRF: ${r.rrf_score.toFixed(4)}`,
          r.rerank_score != null && `Rerank: ${r.rerank_score.toFixed(3)}`,
        ].filter(Boolean).join(" | ");
        return `### [${i + 1}] ${m.title || "제목 없음"} (ID: ${r.id})\n${info}\n\n${r.text}`;
      }).join("\n\n---\n\n");

      return text(`"${d.query}" 검색 결과 ${d.count}건 (${d.timings.total.toFixed(1)}초)\n\n${resultText}`);
    }

    case "get_document": {
      const d = data as { id: string; text: string; metadata: Record<string, string> };
      const meta = Object.entries(d.metadata).map(([k, v]) => `${k}: ${v}`).join("\n");
      return text(`## 문서 메타데이터\n${meta}\n\n## 내용\n${d.text}`);
    }

    case "list_sources": {
      const d = data as { sources: Array<{ file_name: string; title: string; source: string; url: string; chunk_count: number }> };
      if (d.sources.length === 0) return text("조건에 맞는 문서가 없습니다.");
      const list = d.sources
        .map((s) => `- **${s.title || s.file_name}** (${s.source || "unknown"}, ${s.chunk_count}개 청크)${s.url ? ` [링크](${s.url})` : ""}`)
        .join("\n");
      return text(`총 ${d.sources.length}개 문서\n\n${list}`);
    }

    case "get_related": {
      const d = data as { results: Array<{ id: string; text: string; metadata: Record<string, string>; score: number }> };
      if (d.results.length === 0) return text(`문서 "${input.doc_id}"의 관련 문서를 찾을 수 없습니다.`);
      const list = d.results
        .map((r, i) => `### [${i + 1}] ${r.metadata.title || "제목 없음"} (ID: ${r.id})\n유사도: ${r.score.toFixed(4)}\n\n${r.text}`)
        .join("\n\n---\n\n");
      return text(`관련 문서 ${d.results.length}건\n\n${list}`);
    }

    case "list_email_contacts": {
      const d = data as { contacts: Array<{ email: string; names: string[]; mail_count: number }> };
      if (d.contacts.length === 0) {
        return text(input.keyword ? `"${input.keyword}" 키워드에 해당하는 인물을 찾을 수 없습니다.` : "등록된 이메일 인물이 없습니다.");
      }
      const list = d.contacts
        .map((c) => `- **${c.names.join(" / ") || "(이름 없음)"}** <${c.email}> (${c.mail_count}건)`)
        .join("\n");
      return text(`이메일 인물 ${d.contacts.length}명\n\n${list}`);
    }

    case "get_search_filters": {
      const d = data as { sources: Array<{ value: string; count: number }>; source_types: Array<{ value: string; count: number }> };
      const sourceList = d.sources.map((s) => `- **${s.value}** (${s.count}건)`).join("\n");
      const typeList = d.source_types.map((s) => `- **${s.value}** (${s.count}건)`).join("\n");
      return text(`## 데이터 소스 (source)\n${sourceList || "없음"}\n\n## 콘텐츠 유형 (source_type)\n${typeList || "없음"}`);
    }

    default:
      return { ...text(`알 수 없는 도구: ${name}`), isError: true };
  }
}

// ─── 서버 시작 ────────────────────────────────────────

async function main() {
  const httpServer = createServer(async (req, res) => {
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type, mcp-session-id");

    if (req.method === "OPTIONS") {
      res.writeHead(204);
      res.end();
      return;
    }

    if (req.url === "/mcp") {
      try {
        const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
        await server.close();
        await server.connect(transport);
        await transport.handleRequest(req, res);
      } catch (err) {
        console.error("MCP request error:", err);
        if (!res.headersSent) {
          res.writeHead(500);
          res.end(String(err));
        }
      }
    } else {
      res.writeHead(404);
      res.end("Not Found");
    }
  });

  httpServer.listen(MCP_PORT, "0.0.0.0", () => {
    console.error(`Platform RAG MCP Server listening on http://0.0.0.0:${MCP_PORT}/mcp`);
  });
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
