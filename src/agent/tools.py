"""도구 정의 및 FastAPI 호출.

도구 스펙 SSOT: src/mcp-server/tools_spec.json
이 파일은 해당 JSON을 로드하여 Claude/Gemini 형식으로 변환하고 실행 로직을 제공한다.
"""

import json
import logging
from pathlib import Path

import httpx

from . import config

logger = logging.getLogger(__name__)

# ─── 도구 스펙 로드 ────────────────────────────────────

_SPEC_PATH = Path(__file__).parent.parent / "mcp-server" / "tools_spec.json"

with _SPEC_PATH.open(encoding="utf-8") as _f:
    TOOLS_SPEC: list[dict] = json.load(_f)

# name → spec 인덱스 (execute_tool에서 O(1) 조회)
_SPEC_INDEX: dict[str, dict] = {t["name"]: t for t in TOOLS_SPEC}

# ─── Claude 형식 변환 ─────────────────────────────────

_JSON_TYPE_MAP = {"string": "string", "integer": "integer", "boolean": "boolean"}


def to_claude_tools() -> list[dict]:
    """Anthropic Messages API 도구 스키마로 변환한다."""
    tools = []
    for tool in TOOLS_SPEC:
        properties = {}
        required = []
        for p in tool["parameters"]:
            prop = {"type": _JSON_TYPE_MAP[p["type"]], "description": p["description"]}
            if "default" in p:
                prop["default"] = p["default"]
            properties[p["name"]] = prop
            if p.get("required"):
                required.append(p["name"])
        tools.append({
            "name": tool["name"],
            "description": tool["description"],
            "input_schema": {"type": "object", "properties": properties, "required": required},
        })
    return tools


# ─── Gemini 형식 변환 ────────────────────────────────

_GEMINI_TYPE_MAP = {"string": "STRING", "integer": "INTEGER", "boolean": "BOOLEAN"}


def to_gemini_declarations() -> list[dict]:
    """Google GenAI function_declarations 형식으로 변환한다."""
    declarations = []
    for tool in TOOLS_SPEC:
        properties = {}
        required = []
        for p in tool["parameters"]:
            properties[p["name"]] = {"type": _GEMINI_TYPE_MAP[p["type"]], "description": p["description"]}
            if p.get("required"):
                required.append(p["name"])
        declarations.append({
            "name": tool["name"],
            "description": tool["description"],
            "parameters": {"type": "OBJECT", "properties": properties, "required": required},
        })
    return declarations


# ─── 도구 실행 (FastAPI 호출) ─────────────────────────

def execute_tool(name: str, arguments: dict) -> dict:
    """tools_spec.json의 api 설정을 기반으로 FastAPI 엔드포인트를 호출한다."""
    spec = _SPEC_INDEX.get(name)
    if spec is None:
        return {"error": f"알 수 없는 도구: {name}"}

    api = spec["api"]
    method = api["method"].upper()
    timeout = float(api.get("timeout", 30.0))
    rename: dict = api.get("param_rename", {})

    # 1. path param 치환 ({doc_id} → 실제 값)
    path: str = api["path"]
    path_params = {k for k in arguments if f"{{{k}}}" in path}
    for k in path_params:
        path = path.replace(f"{{{k}}}", str(arguments[k]))

    # 2. 나머지 인자 구성 (path param 제외, None 제외, rename 적용)
    rest = {
        rename.get(k, k): v
        for k, v in arguments.items()
        if k not in path_params and v is not None
    }

    url = f"{config.SEARCH_API_URL}{path}"
    logger.info("HTTP %s %s %s", method, url, rest)

    try:
        if method == "GET":
            res = httpx.get(url, params=rest, timeout=timeout)
        elif method == "POST":
            res = httpx.post(url, json=rest, timeout=timeout)
        elif method == "PUT":
            res = httpx.put(url, json=rest, timeout=timeout)
        elif method == "DELETE":
            res = httpx.delete(url, timeout=timeout)
        else:
            return {"error": f"지원하지 않는 HTTP 메서드: {method}"}

        logger.info("HTTP 응답: %d (%d bytes)", res.status_code, len(res.content))
        res.raise_for_status()
        data = res.json()

        # 도메인 후처리 (search_knowledge 전용)
        if name == "search_knowledge":
            data = _enrich_search_results(data)

        return data

    except httpx.ConnectError:
        return {"error": f"검색 API 서버에 연결할 수 없습니다 ({config.SEARCH_API_URL}). 서버가 실행 중인지 확인하세요."}
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
