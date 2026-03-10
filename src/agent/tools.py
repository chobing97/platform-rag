"""도구 정의 및 FastAPI 호출."""

import json
import logging

import httpx

from . import config

logger = logging.getLogger(__name__)

# ─── 도구 메타데이터 (프로바이더 중립) ─────────────────

TOOLS_META = [
    {
        "name": "search_knowledge",
        "description": "팀 지식베이스를 자연어로 검색합니다. 노션 문서, 이메일, 로컬 파일에서 관련 정보를 찾아 출처와 함께 반환합니다.",
        "parameters": {
            "query": {"type": "string", "description": "검색 쿼리 (자연어 질문 또는 키워드)", "required": True},
            "top_k": {"type": "integer", "description": "반환할 결과 수 (기본 5)", "required": False, "default": 5},
            "rerank": {"type": "boolean", "description": "Reranker 사용 여부 (기본 true)", "required": False, "default": True},
        },
    },
    {
        "name": "get_document",
        "description": "특정 문서의 전체 내용을 가져옵니다. search_knowledge 결과에서 받은 문서 ID를 사용하세요.",
        "parameters": {
            "doc_id": {"type": "string", "description": "문서(청크) ID", "required": True},
        },
    },
    {
        "name": "list_sources",
        "description": "지식베이스에 수집된 문서 목록을 조회합니다. 소스 유형이나 키워드로 필터링할 수 있습니다.",
        "parameters": {
            "source_type": {"type": "string", "description": "소스 유형 필터 (예: notion, email, file)", "required": False},
            "keyword": {"type": "string", "description": "제목 키워드 검색", "required": False},
        },
    },
    {
        "name": "get_related",
        "description": "특정 문서와 의미적으로 관련된 다른 문서들을 찾습니다. 벡터 유사도 기반.",
        "parameters": {
            "doc_id": {"type": "string", "description": "기준 문서(청크) ID", "required": True},
            "top_k": {"type": "integer", "description": "반환할 관련 문서 수 (기본 5)", "required": False, "default": 5},
        },
    },
]

# ─── Claude 형식 변환 ─────────────────────────────────

_JSON_TYPE_MAP = {"string": "string", "integer": "integer", "boolean": "boolean"}


def to_claude_tools() -> list[dict]:
    """Anthropic Messages API 도구 스키마로 변환한다."""
    tools = []
    for meta in TOOLS_META:
        properties = {}
        required = []
        for pname, pspec in meta["parameters"].items():
            prop = {"type": _JSON_TYPE_MAP[pspec["type"]], "description": pspec["description"]}
            if "default" in pspec:
                prop["default"] = pspec["default"]
            properties[pname] = prop
            if pspec.get("required"):
                required.append(pname)

        tools.append({
            "name": meta["name"],
            "description": meta["description"],
            "input_schema": {"type": "object", "properties": properties, "required": required},
        })
    return tools


# ─── Gemini 형식 변환 ────────────────────────────────

_GEMINI_TYPE_MAP = {"string": "STRING", "integer": "INTEGER", "boolean": "BOOLEAN"}


def to_gemini_declarations() -> list[dict]:
    """Google GenAI function_declarations 형식으로 변환한다."""
    declarations = []
    for meta in TOOLS_META:
        properties = {}
        required = []
        for pname, pspec in meta["parameters"].items():
            properties[pname] = {"type": _GEMINI_TYPE_MAP[pspec["type"]], "description": pspec["description"]}
            if pspec.get("required"):
                required.append(pname)

        declarations.append({
            "name": meta["name"],
            "description": meta["description"],
            "parameters": {"type": "OBJECT", "properties": properties, "required": required},
        })
    return declarations


# ─── 도구 실행 (FastAPI 호출) ─────────────────────────

def execute_tool(name: str, arguments: dict) -> dict:
    """FastAPI 엔드포인트를 호출하여 도구를 실행한다."""
    base = config.SEARCH_API_URL
    try:
        if name == "search_knowledge":
            params = {"q": arguments["query"]}
            if "top_k" in arguments:
                params["top_k"] = arguments["top_k"]
            if "rerank" in arguments:
                params["rerank"] = str(arguments["rerank"]).lower()
            url = f"{base}/search"
            logger.info("HTTP GET %s params=%s", url, params)
            res = httpx.get(url, params=params, timeout=60.0)

        elif name == "get_document":
            url = f"{base}/document/{arguments['doc_id']}"
            logger.info("HTTP GET %s", url)
            res = httpx.get(url, timeout=30.0)

        elif name == "list_sources":
            params = {}
            if arguments.get("source_type"):
                params["source_type"] = arguments["source_type"]
            if arguments.get("keyword"):
                params["keyword"] = arguments["keyword"]
            url = f"{base}/sources"
            logger.info("HTTP GET %s params=%s", url, params)
            res = httpx.get(url, params=params, timeout=30.0)

        elif name == "get_related":
            params = {}
            if "top_k" in arguments:
                params["top_k"] = arguments["top_k"]
            url = f"{base}/related/{arguments['doc_id']}"
            logger.info("HTTP GET %s params=%s", url, params)
            res = httpx.get(url, params=params, timeout=30.0)

        else:
            return {"error": f"알 수 없는 도구: {name}"}

        logger.info("HTTP 응답: %d (%d bytes)", res.status_code, len(res.content))
        res.raise_for_status()
        data = res.json()

        # 검색 결과 신뢰도 경고 추가
        if name == "search_knowledge":
            data = _enrich_search_results(data)

        return data

    except httpx.ConnectError:
        return {"error": f"검색 API 서버에 연결할 수 없습니다 ({base}). 서버가 실행 중인지 확인하세요."}
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return {"error": "문서를 찾을 수 없습니다."}
        return {"error": f"API 오류: HTTP {e.response.status_code}"}
    except httpx.TimeoutException:
        return {"error": "검색 시간 초과. 잠시 후 다시 시도하세요."}


def _enrich_search_results(data: dict) -> dict:
    """검색 결과에 신뢰도 경고를 추가한다."""
    threshold = config.RERANK_SCORE_THRESHOLD
    for result in data.get("results", []):
        score = result.get("rerank_score")
        if score is not None and score < threshold:
            result["_confidence_warning"] = f"rerank_score({score:.3f}) < threshold({threshold}): 낮은 관련도"

    if data.get("count", 0) == 0:
        data["_no_results_warning"] = "검색 결과가 없습니다. 추측하지 말고 데이터 부재를 알려주세요."

    return data
