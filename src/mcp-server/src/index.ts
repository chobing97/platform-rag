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

// ─── API 호출 헬퍼 ─────────────────────────────────────

async function apiFetch(path: string): Promise<unknown> {
  const res = await fetch(`${API_URL}${path}`);
  if (!res.ok) throw new Error(`API error: ${res.status} ${res.statusText}`);
  return res.json();
}

// ─── 도구 핸들러 ──────────────────────────────────────

type ToolInput = Record<string, unknown>;
type ToolResult = { content: Array<{ type: "text"; text: string }>; isError?: boolean };

const HANDLERS: Record<string, (input: ToolInput) => Promise<ToolResult>> = {
  search_knowledge: async (input) => {
    const { query, top_k, rerank, source, source_type, sender, recipient, participant, direction } = input;
    const params = new URLSearchParams({
      q: String(query),
      top_k: String(top_k),
      rerank: String(rerank),
    });
    if (source) params.set("source", String(source));
    if (source_type) params.set("source_type", String(source_type));
    if (sender) params.set("sender", String(sender));
    if (recipient) params.set("recipient", String(recipient));
    if (participant) params.set("participant", String(participant));
    if (direction) params.set("direction", String(direction));

    const data = (await apiFetch(`/search?${params}`)) as {
      query: string;
      count: number;
      results: Array<{
        id: string;
        text: string;
        metadata: Record<string, string>;
        rrf_score: number | null;
        rerank_score: number | null;
      }>;
      timings: Record<string, number>;
    };

    if (data.count === 0) {
      return { content: [{ type: "text", text: `"${query}" 검색 결과가 없습니다. 다른 키워드로 다시 검색해 주세요.` }] };
    }

    const resultText = data.results
      .map((r, i) => {
        const meta = r.metadata;
        const info = [
          meta.title && `제목: ${meta.title}`,
          meta.source && `출처: ${meta.source}`,
          meta.file_name && `파일: ${meta.file_name}`,
          meta.url && `URL: ${meta.url}`,
          r.rrf_score != null && `RRF: ${r.rrf_score.toFixed(4)}`,
          r.rerank_score != null && `Rerank: ${r.rerank_score.toFixed(3)}`,
        ].filter(Boolean).join(" | ");
        return `### [${i + 1}] ${meta.title || "제목 없음"} (ID: ${r.id})\n${info}\n\n${r.text}`;
      })
      .join("\n\n---\n\n");

    return {
      content: [{
        type: "text",
        text: `"${data.query}" 검색 결과 ${data.count}건 (${data.timings.total.toFixed(1)}초)\n\n${resultText}`,
      }],
    };
  },

  get_document: async ({ doc_id }) => {
    try {
      const data = (await apiFetch(`/document/${doc_id}`)) as {
        id: string;
        text: string;
        metadata: Record<string, string>;
      };
      const meta = Object.entries(data.metadata).map(([k, v]) => `${k}: ${v}`).join("\n");
      return { content: [{ type: "text", text: `## 문서 메타데이터\n${meta}\n\n## 내용\n${data.text}` }] };
    } catch {
      return { content: [{ type: "text", text: `문서 ID "${doc_id}"를 찾을 수 없습니다.` }], isError: true };
    }
  },

  list_sources: async ({ source_type, keyword }) => {
    const params = new URLSearchParams();
    if (source_type) params.set("source_type", String(source_type));
    if (keyword) params.set("keyword", String(keyword));
    const qs = params.toString();

    const data = (await apiFetch(`/sources${qs ? `?${qs}` : ""}`)) as {
      sources: Array<{ file_name: string; title: string; source: string; url: string; chunk_count: number }>;
    };

    if (data.sources.length === 0) {
      return { content: [{ type: "text", text: "조건에 맞는 문서가 없습니다." }] };
    }

    const list = data.sources
      .map((s) => `- **${s.title || s.file_name}** (${s.source || "unknown"}, ${s.chunk_count}개 청크)${s.url ? ` [링크](${s.url})` : ""}`)
      .join("\n");
    return { content: [{ type: "text", text: `총 ${data.sources.length}개 문서\n\n${list}` }] };
  },

  get_related: async ({ doc_id, top_k }) => {
    const data = (await apiFetch(`/related/${doc_id}?top_k=${top_k}`)) as {
      results: Array<{ id: string; text: string; metadata: Record<string, string>; score: number }>;
    };

    if (data.results.length === 0) {
      return { content: [{ type: "text", text: `문서 "${doc_id}"의 관련 문서를 찾을 수 없습니다.` }] };
    }

    const list = data.results
      .map((r, i) => `### [${i + 1}] ${r.metadata.title || "제목 없음"} (ID: ${r.id})\n유사도: ${r.score.toFixed(4)}\n\n${r.text}`)
      .join("\n\n---\n\n");
    return { content: [{ type: "text", text: `관련 문서 ${data.results.length}건\n\n${list}` }] };
  },

  list_email_contacts: async ({ keyword, limit }) => {
    const params = new URLSearchParams();
    if (keyword) params.set("keyword", String(keyword));
    params.set("limit", String(limit));

    const data = (await apiFetch(`/contacts?${params}`)) as {
      contacts: Array<{ email: string; names: string[]; mail_count: number }>;
    };

    if (data.contacts.length === 0) {
      return {
        content: [{
          type: "text",
          text: keyword ? `"${keyword}" 키워드에 해당하는 인물을 찾을 수 없습니다.` : "등록된 이메일 인물이 없습니다.",
        }],
      };
    }

    const list = data.contacts
      .map((c) => `- **${c.names.join(" / ") || "(이름 없음)"}** <${c.email}> (${c.mail_count}건)`)
      .join("\n");
    return { content: [{ type: "text", text: `이메일 인물 ${data.contacts.length}명\n\n${list}` }] };
  },

  get_search_filters: async () => {
    const data = (await apiFetch("/filters")) as {
      sources: Array<{ value: string; count: number }>;
      source_types: Array<{ value: string; count: number }>;
    };
    const sourceList = data.sources.map((s) => `- **${s.value}** (${s.count}건)`).join("\n");
    const typeList = data.source_types.map((s) => `- **${s.value}** (${s.count}건)`).join("\n");
    return {
      content: [{
        type: "text",
        text: `## 데이터 소스 (source)\n${sourceList || "없음"}\n\n## 콘텐츠 유형 (source_type)\n${typeList || "없음"}`,
      }],
    };
  },
};

// ─── MCP 서버 + 도구 동적 등록 ────────────────────────

const server = new McpServer({ name: "platform-rag", version: "0.1.0" });

for (const tool of TOOLS_SPEC) {
  const handler = HANDLERS[tool.name];
  if (!handler) {
    console.error(`[경고] 핸들러 없음: ${tool.name}`);
    continue;
  }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  server.registerTool(tool.name, { description: tool.description, inputSchema: buildZodSchema(tool.parameters) }, handler as any);
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
