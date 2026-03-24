"""
CLI entry point for testing SidanTrip agents locally.

Usage:
    python -m sidantrip.main                          # Interactive planner (default: Seoul)
    python -m sidantrip.main --mode planner           # Interactive planner
    python -m sidantrip.main --mode researcher        # Run researcher crew
    python -m sidantrip.main --destination tokyo      # Different destination
    python -m sidantrip.main --model gpt-4o           # Override LLM model
"""

import argparse
import json
import sys
from dotenv import load_dotenv

load_dotenv()


def run_planner(args):
    from .planner.agent import PlannerAgent
    from .planner.parser import format_itinerary
    from .tools.db_tools import load_activity_detail

    agent = PlannerAgent(destination=args.destination)
    agent.load_context()

    itinerary_state = {"days": {}}
    conversation_history = []
    total_tokens = {"input": 0, "output": 0}

    print(f"\n🗺️  SidanTrip Planner — {args.destination.title()}")
    print(f"📅 {args.start_date} → {args.end_date}")
    print(f"🏨 {args.accommodation}")
    print(f"🤖 Model: {args.model or 'default'}")
    print("─" * 50)
    print("Commands: /itinerary  /usage  /detail <id>  /quit")
    print("─" * 50)

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 Bye!")
            break

        if not user_input:
            continue

        if user_input == "/quit":
            print("👋 Bye!")
            break

        if user_input == "/itinerary":
            print(format_itinerary(itinerary_state))
            continue

        if user_input == "/usage":
            print(f"Total tokens — input: {total_tokens['input']}, output: {total_tokens['output']}")
            continue

        if user_input.startswith("/detail "):
            activity_id = user_input.split(" ", 1)[1].strip()
            detail = load_activity_detail(args.destination, activity_id)
            print(f"\n{detail}")
            continue

        print("\n🤔 Thinking...")
        result = agent.chat_sync(
            message=user_input,
            conversation_history=conversation_history,
            itinerary_state=itinerary_state,
            start_date=args.start_date,
            end_date=args.end_date,
            accommodation=args.accommodation,
            llm_model=args.model,
        )

        print(f"\n🗺️  {result['text']}")

        if result["deltas"]:
            print(f"\n📝 Itinerary updated ({len(result['deltas'])} changes)")
            itinerary_state = result["itinerary"]

        if result.get("parse_errors"):
            print(f"   ⚠️  Parse issues: {result['parse_errors']}")

        if result["usage"]:
            total_tokens["input"] += result["usage"].get("input", 0)
            total_tokens["output"] += result["usage"].get("output", 0)
            print(f"   [{result['usage'].get('total', '?')} tokens]")

        conversation_history.append({"role": "user", "content": user_input})
        conversation_history.append({"role": "assistant", "content": result["text"]})


def run_researcher(args):
    from .crews.researcher_crew import ResearcherCrew

    config = {
        "destination": args.destination,
        "category": args.category,
        "num_activities": args.num,
        "neighborhood_focus": args.focus or "across the city",
    }
    if args.model:
        config["llm_model"] = args.model

    print(f"\n🔬 SidanTrip Researcher")
    print(f"📍 {args.destination.title()} — {args.category} × {args.num}")
    print(f"🔎 Focus: {config['neighborhood_focus']}")
    print("─" * 50)

    crew = ResearcherCrew(config)
    result = crew.run()

    print("\n" + "=" * 50)
    print("RESEARCH + REVIEW OUTPUT:")
    print("=" * 50)
    print(result["output"])

    if result["usage"]:
        print(f"\n📊 Tokens used: {result['usage'].get('total', '?')}")


def main():
    parser = argparse.ArgumentParser(description="SidanTrip AI Agent CLI")
    parser.add_argument("--mode", choices=["planner", "researcher"], default="planner")
    parser.add_argument("--destination", default="seoul")
    parser.add_argument("--model", help="LLM model override (e.g. gpt-4o, gemini/gemini-2.5-flash)")

    # Planner args
    parser.add_argument("--start-date", default="2026-05-21")
    parser.add_argument("--end-date", default="2026-05-25")
    parser.add_argument("--accommodation", default="Union Hotel, Myeongdong")

    # Researcher args
    parser.add_argument("--category", choices=["sightseeing", "experience", "food"], default="food")
    parser.add_argument("--num", type=int, default=5)
    parser.add_argument("--focus", help="Neighborhood focus (e.g. 'Hongdae/Mapo area')")

    args = parser.parse_args()

    if args.mode == "planner":
        run_planner(args)
    else:
        run_researcher(args)


if __name__ == "__main__":
    main()
