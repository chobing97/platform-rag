import { createServer } from "node:http";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";

const API_URL = process.env.SEARCH_API_URL ?? "http://localhost:8000";
const MCP_PORT = Number(process.env.MCP_PORT ?? "3001");

async function apiFetch(path: string): Promise<unknown> {
  const res = await fetch(`${API_URL}${path}`);
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

const server = new McpServer({
  name: "platform-rag",
  version: "0.1.0",
});

// Tool 1: search_knowledge
server.tool(
  "search_knowledge",
  "다올투자증권 플랫폼전략팀 지식베이스를 자연어로 검색합니다. 노션 문서, 이메일, 로컬 파일에서 관련 정보를 찾아 출처와 함께 반환합니다. 사용 가능한 필터 값은 get_search_filters 도구로 확인하세요.",
  {
    query: z.string().describe("검색 쿼리 (자연어 질문 또는 키워드)"),
    top_k: z
      .number()
      .int()
      .min(1)
      .max(50)
      .default(5)
      .describe("반환할 결과 수 (기본 5)"),
    rerank: z
      .boolean()
      .default(true)
      .describe("Reranker 사용 여부 (기본 true)"),
    source: z
      .string()
      .optional()
      .describe("데이터 소스 필터 (예: notion, daolemail)"),
    source_type: z
      .string()
      .optional()
      .describe("콘텐츠 유형 필터 (예: document, email_body, email_attachment)"),
    sender: z
      .string()
      .optional()
      .describe("발신자 이메일 주소로 필터링"),
    recipient: z
      .string()
      .optional()
      .describe("수신자 이메일 주소로 필터링 (To+CC)"),
    participant: z
      .string()
      .optional()
      .describe("참여자 이메일로 필터링 (발신+수신+참조 모두)"),
    direction: z
      .string()
      .optional()
      .describe("메일 방향 필터 (sent: 보낸 메일, received: 받은 메일)"),
  },
  async ({ query, top_k, rerank, source, source_type, sender, recipient, participant, direction }) => {
    const params = new URLSearchParams({
      q: query,
      top_k: String(top_k),
      rerank: String(rerank),
    });
    if (source) params.set("source", source);
    if (source_type) params.set("source_type", source_type);
    if (sender) params.set("sender", sender);
    if (recipient) params.set("recipient", recipient);
    if (participant) params.set("participant", participant);
    if (direction) params.set("direction", direction);

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
      return {
        content: [
          {
            type: "text" as const,
            text: `"${query}" 검색 결과가 없습니다. 다른 키워드로 다시 검색해 주세요.`,
          },
        ],
      };
    }

    const resultText = data.results
      .map((r, i) => {
        const meta = r.metadata;
        const source = [
          meta.title && `제목: ${meta.title}`,
          meta.source && `출처: ${meta.source}`,
          meta.file_name && `파일: ${meta.file_name}`,
          meta.url && `URL: ${meta.url}`,
          r.rrf_score != null && `RRF: ${r.rrf_score.toFixed(4)}`,
          r.rerank_score != null && `Rerank: ${r.rerank_score.toFixed(3)}`,
        ]
          .filter(Boolean)
          .join(" | ");

        return `### [${i + 1}] ${meta.title || "제목 없음"} (ID: ${r.id})\n${source}\n\n${r.text}`;
      })
      .join("\n\n---\n\n");

    const summary = `"${data.query}" 검색 결과 ${data.count}건 (${data.timings.total.toFixed(1)}초)`;

    return {
      content: [
        { type: "text" as const, text: `${summary}\n\n${resultText}` },
      ],
    };
  }
);

// Tool 2: get_document
server.tool(
  "get_document",
  "특정 문서의 전체 내용을 가져옵니다. search_knowledge 결과에서 받은 문서 ID를 사용하세요.",
  {
    doc_id: z.string().describe("문서(청크) ID"),
  },
  async ({ doc_id }) => {
    try {
      const data = (await apiFetch(`/document/${doc_id}`)) as {
        id: string;
        text: string;
        metadata: Record<string, string>;
      };

      const meta = Object.entries(data.metadata)
        .map(([k, v]) => `${k}: ${v}`)
        .join("\n");

      return {
        content: [
          {
            type: "text" as const,
            text: `## 문서 메타데이터\n${meta}\n\n## 내용\n${data.text}`,
          },
        ],
      };
    } catch {
      return {
        content: [
          {
            type: "text" as const,
            text: `문서 ID "${doc_id}"를 찾을 수 없습니다.`,
          },
        ],
        isError: true,
      };
    }
  }
);

// Tool 3: list_sources
server.tool(
  "list_sources",
  "지식베이스에 수집된 문서 목록을 조회합니다. 소스 유형이나 키워드로 필터링할 수 있습니다.",
  {
    source_type: z
      .string()
      .optional()
      .describe("소스 유형 필터 (예: notion, email, file)"),
    keyword: z.string().optional().describe("제목 키워드 검색"),
  },
  async ({ source_type, keyword }) => {
    const params = new URLSearchParams();
    if (source_type) params.set("source_type", source_type);
    if (keyword) params.set("keyword", keyword);

    const qs = params.toString();
    const data = (await apiFetch(`/sources${qs ? `?${qs}` : ""}`)) as {
      sources: Array<{
        file_name: string;
        title: string;
        source: string;
        url: string;
        chunk_count: number;
      }>;
    };

    if (data.sources.length === 0) {
      return {
        content: [
          { type: "text" as const, text: "조건에 맞는 문서가 없습니다." },
        ],
      };
    }

    const list = data.sources
      .map(
        (s) =>
          `- **${s.title || s.file_name}** (${s.source || "unknown"}, ${s.chunk_count}개 청크)${s.url ? ` [링크](${s.url})` : ""}`
      )
      .join("\n");

    return {
      content: [
        {
          type: "text" as const,
          text: `총 ${data.sources.length}개 문서\n\n${list}`,
        },
      ],
    };
  }
);

// Tool 4: get_related
server.tool(
  "get_related",
  "특정 문서와 의미적으로 관련된 다른 문서들을 찾습니다. 벡터 유사도 기반.",
  {
    doc_id: z.string().describe("기준 문서(청크) ID"),
    top_k: z
      .number()
      .int()
      .min(1)
      .max(20)
      .default(5)
      .describe("반환할 관련 문서 수 (기본 5)"),
  },
  async ({ doc_id, top_k }) => {
    const data = (await apiFetch(
      `/related/${doc_id}?top_k=${top_k}`
    )) as {
      results: Array<{
        id: string;
        text: string;
        metadata: Record<string, string>;
        score: number;
      }>;
    };

    if (data.results.length === 0) {
      return {
        content: [
          {
            type: "text" as const,
            text: `문서 "${doc_id}"의 관련 문서를 찾을 수 없습니다.`,
          },
        ],
      };
    }

    const list = data.results
      .map(
        (r, i) =>
          `### [${i + 1}] ${r.metadata.title || "제목 없음"} (ID: ${r.id})\n유사도: ${r.score.toFixed(4)}\n\n${r.text}`
      )
      .join("\n\n---\n\n");

    return {
      content: [
        {
          type: "text" as const,
          text: `관련 문서 ${data.results.length}건\n\n${list}`,
        },
      ],
    };
  }
);

// Tool 5: list_email_contacts
server.tool(
  "list_email_contacts",
  "이메일에 등장하는 인물(발신자/수신자/참조자) 목록을 조회합니다. 이름이나 이메일 주소로 검색할 수 있습니다. 특정 인물의 이메일 주소를 확인한 뒤 search_knowledge의 sender/recipient/participant 필터에 사용하세요.",
  {
    keyword: z
      .string()
      .optional()
      .describe("이름 또는 이메일 주소 검색어"),
    limit: z
      .number()
      .int()
      .min(1)
      .max(500)
      .default(20)
      .describe("반환할 결과 수 (기본 20)"),
  },
  async ({ keyword, limit }) => {
    const params = new URLSearchParams();
    if (keyword) params.set("keyword", keyword);
    params.set("limit", String(limit));

    const data = (await apiFetch(`/contacts?${params}`)) as {
      contacts: Array<{
        email: string;
        names: string[];
        mail_count: number;
      }>;
    };

    if (data.contacts.length === 0) {
      return {
        content: [
          {
            type: "text" as const,
            text: keyword
              ? `"${keyword}" 키워드에 해당하는 인물을 찾을 수 없습니다.`
              : "등록된 이메일 인물이 없습니다.",
          },
        ],
      };
    }

    const list = data.contacts
      .map(
        (c) =>
          `- **${c.names.join(" / ") || "(이름 없음)"}** <${c.email}> (${c.mail_count}건)`
      )
      .join("\n");

    return {
      content: [
        {
          type: "text" as const,
          text: `이메일 인물 ${data.contacts.length}명\n\n${list}`,
        },
      ],
    };
  }
);

// Tool 6: get_search_filters
server.tool(
  "get_search_filters",
  "검색에 사용할 수 있는 필터 옵션(데이터 소스, 콘텐츠 유형)을 조회합니다. search_knowledge의 source, source_type 파라미터에 사용할 값을 확인하세요.",
  {},
  async () => {
    const data = (await apiFetch("/filters")) as {
      sources: Array<{ value: string; count: number }>;
      source_types: Array<{ value: string; count: number }>;
    };

    const sourceList = data.sources
      .map((s) => `- **${s.value}** (${s.count}건)`)
      .join("\n");

    const typeList = data.source_types
      .map((s) => `- **${s.value}** (${s.count}건)`)
      .join("\n");

    return {
      content: [
        {
          type: "text" as const,
          text: `## 데이터 소스 (source)\n${sourceList || "없음"}\n\n## 콘텐츠 유형 (source_type)\n${typeList || "없음"}`,
        },
      ],
    };
  }
);

// 서버 시작 (Streamable HTTP — stateless: 요청마다 새 transport 생성)
async function main() {
  const httpServer = createServer(async (req, res) => {
    // CORS 헤더
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
        const transport = new StreamableHTTPServerTransport({
          sessionIdGenerator: undefined, // stateless 모드
        });
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
