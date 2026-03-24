"""
Researcher Crew — async crew that researches new activities and produces YAML entries.
Runs as a background job (triggered via queue), not in the chat loop.
"""

import os
from crewai import Agent, Task, Crew, Process, LLM
from crewai.project import CrewBase, agent, task, crew
from crewai_tools import SerperDevTool, ScrapeWebsiteTool

from ..tools.db_tools import load_schema_template, load_city_index, load_city_meta


@CrewBase
class ResearcherCrew:
    """Two-agent crew: Researcher finds activities, Reviewer validates them."""

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self, research_config: dict):
        """
        research_config: {
            destination: str,
            category: str,              # sightseeing | experience | food
            num_activities: int,
            neighborhood_focus: str,    # e.g. "focusing on Hongdae/Mapo area"
            llm_model: str (optional),
        }
        """
        self.config = research_config

    def _get_llm(self) -> LLM:
        model = self.config.get("llm_model", os.environ.get(
            "SIDANTRIP_LLM_MODEL", "anthropic/claude-sonnet-4-20250514"
        ))
        return LLM(model=model, temperature=0.3)  # Lower temp for factual research

    @agent
    def researcher(self) -> Agent:
        return Agent(
            config=self.agents_config["researcher"],
            llm=self._get_llm(),
            tools=[
                SerperDevTool(),        # Web search
                ScrapeWebsiteTool(),    # Read web pages
                load_schema_template,    # Get YAML schema
                load_city_index,         # See existing activities (avoid duplicates)
                load_city_meta,          # City context
            ],
            verbose=True,
            max_iter=15,
            allow_delegation=False,
        )

    @agent
    def reviewer(self) -> Agent:
        return Agent(
            config=self.agents_config["reviewer"],
            llm=self._get_llm(),
            tools=[load_schema_template, load_city_index],
            verbose=True,
            max_iter=10,
            allow_delegation=False,
        )

    @task
    def research_activities(self) -> Task:
        return Task(
            config=self.tasks_config["research_activities"],
        )

    @task
    def review_activities(self) -> Task:
        return Task(
            config=self.tasks_config["review_activities"],
            context=[self.research_activities()],  # Gets output of research task as input
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,  # Research first, then review
            verbose=True,
            memory=True,
        )

    def run(self) -> dict:
        """
        Execute the research + review pipeline.

        Returns: {
            "entries": str,       # YAML entries (approved ones)
            "review": str,        # Review report
            "usage": dict,        # Token usage
        }
        """
        # Load schema template for the category
        schema = load_schema_template.run(category=self.config["category"])

        result = self.crew().kickoff(inputs={
            "destination": self.config["destination"],
            "category": self.config["category"],
            "num_activities": str(self.config["num_activities"]),
            "neighborhood_focus": self.config.get("neighborhood_focus", "across the city"),
            "schema_template": schema,
            "activity_entries": "{{research_activities output}}",  # Filled by CrewAI context
        })

        usage = {}
        if hasattr(result, "token_usage") and result.token_usage:
            usage = {
                "input": getattr(result.token_usage, "prompt_tokens", 0),
                "output": getattr(result.token_usage, "completion_tokens", 0),
                "total": getattr(result.token_usage, "total_tokens", 0),
            }

        return {
            "output": result.raw,
            "usage": usage,
        }
