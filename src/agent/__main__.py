"""CLI 진입점 — 대화형 에이전트 루프."""

import argparse
import logging
import sys

from .agent import Agent


def _setup_logging():
    """에이전트 로깅 설정."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        "\033[90m[%(asctime)s %(name)s] %(message)s\033[0m",
        datefmt="%H:%M:%S",
    ))
    for name in ("agent.agent", "agent.llm", "agent.tools", "agent.api", "agent.sdk_runner"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)
        lg.addHandler(handler)


def main():
    parser = argparse.ArgumentParser(description="Platform RAG AI Agent")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--claude", action="store_const", const="claude", dest="provider", help="Claude API 사용")
    group.add_argument("--gemini", action="store_const", const="gemini", dest="provider", help="Gemini API 사용")
    parser.add_argument("-m", "--model", type=str, help="사용할 모델 (예: claude-haiku-4-5-20251001, gemini-2.5-pro)")
    parser.add_argument("-q", "--query", type=str, help="단일 질문 모드 (대화형 대신 한 번만 실행)")
    parser.add_argument("--serve", action="store_true", help="API 서버 모드 (SSE 스트리밍)")
    parser.add_argument("--port", type=int, default=8001, help="API 서버 포트 (기본 8001)")
    args = parser.parse_args()

    _setup_logging()

    # API 서버 모드
    if args.serve:
        import uvicorn
        print(f"Platform RAG Agent API — http://0.0.0.0:{args.port}")
        uvicorn.run("agent.api:app", host="0.0.0.0", port=args.port, log_level="info")
        return

    agent = Agent(provider=args.provider, model=args.model)
    model_display = agent.llm.model
    print(f"Platform RAG Agent ({model_display})")
    print("종료: quit / exit / Ctrl+C")
    print("-" * 40)

    # 단일 질문 모드
    if args.query:
        answer = agent.ask(args.query)
        print(answer)
        return

    # 대화형 모드
    while True:
        try:
            user_input = input("\n🙋 질문: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n👋 종료합니다.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("👋 종료합니다.")
            break
        if user_input.lower() == "reset":
            agent.reset()
            print("🔄 대화 기록이 초기화되었습니다.")
            continue

        answer = agent.ask(user_input)
        print(f"\n🤖 답변:\n{answer}")


if __name__ == "__main__":
    main()
