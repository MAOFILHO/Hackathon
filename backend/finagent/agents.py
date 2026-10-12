"""Orchestrator + specialist agents (LCEL tool loops) wired as a LangGraph graph. See CONTEXT.md (Agents, Route)."""

import json
import time
from datetime import datetime
from typing import Literal, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from . import citations, config, db, finance, llm, tools
from .llm import structured

AgentName = Literal["Debt Analyzer", "Payoff Optimizer", "Budget Coach", "Savings Strategist"]
MAX_STEPS = 6

COMMON = ("You are part of FinAgent, a personal financial coach for {user}. Today is {today}. Money is USD. "
          "Only use numbers that come from your tools; never estimate or do arithmetic in your head when a tool can. "
          "Spending excludes Transfers (card payments, moves to savings) and Debt Service (installments, interest, fees). "
          "Write dates as 'Apr 2028', never as 2028-04-01. A month named without a year, such as 'March', means its next occurrence after today. "
          "Be concise: findings with exact figures, then one concrete next step.")

SPECIALISTS: dict[str, tuple[str, list]] = {
    "Debt Analyzer": ("You analyze the user's Debt Accounts: balances, APRs, and what each costs per month in interest. "
                      "Call get_debts and debt_cost once each. Do not simulate payoffs or analyse spending; other agents do that.",
                      [tools.get_debts, tools.debt_cost]),
    "Payoff Optimizer": ("You compare Payoff Strategies (avalanche = highest APR first, snowball = smallest balance first) "
                         f"Call compare_payoff once with exactly the extra monthly amount the user stated (use {config.DEFAULT_EXTRA_MONTHLY:g} if none), then "
                         "call simulate_payoff once with that same amount for the cheaper strategy. Report debt-free dates, "
                         "interest and payoff order.",
                         [tools.get_debts, tools.simulate_payoff, tools.compare_payoff]),
    "Budget Coach": ("You review Spending by category against Budget Caps and find specific, realistic cuts "
                     "(name the merchants or subscriptions). Always call spending_by_category with days=30, then call propose_caps with days=30. "
                     "Quote each cap exactly as propose_caps returns it, as 'category: last 30 days -> cap'; never compute or "
                     "adjust a cap yourself, and propose no cap for a category propose_caps omits. Unused or duplicate "
                     "subscriptions are good targets.",
                     [tools.spending_by_category, tools.propose_caps, tools.money_summary, tools.run_sql]),
    "Savings Strategist": (f"You advise on savings: emergency fund coverage (target {config.EMERGENCY_FUND_MONTHS} months of Spending + Debt Service), "
                           f"Savings Rate versus the {config.SAVINGS_TARGET:.0%} target, and a monthly auto-save amount. Call emergency_fund, then call "
                           f"savings_goal with target_months={config.EMERGENCY_FUND_MONTHS}. Quote its monthly_amount and goal_date exactly; never "
                           "work out an amount or a date yourself. If it has no goal_date, say the surplus is too small.",
                           [tools.emergency_fund, tools.savings_goal, tools.money_summary, tools.run_sql]),
}

STATUS: dict[str, str] = {"Orchestrator": "idle", **{n: "idle" for n in SPECIALISTS}}


# --- Structured outputs ------------------------------------------------------------

class Route(BaseModel):
    intent: str = Field(description="Short description of what the user wants, e.g. 'debt payoff + budget cuts'")
    agents: list[AgentName] = Field(description="Specialists to run, in order. Use only the ones needed.")


class Turn(BaseModel):
    question: str
    answer: str


HISTORY_TURNS = 3
ANSWER_CHARS = 1200


def conversation(history: list[Turn]) -> str:
    """The last few turns as prompt text, so a follow-up such as 'What about snowball?' keeps its subject. '' when none."""
    if not history:
        return ""
    turns = "\n\n".join(f"User: {t.question}\nAdvisor: {t.answer[:ANSWER_CHARS]}" for t in history[-HISTORY_TURNS:])
    return f"Earlier in this conversation:\n{turns}\n\nThe question below may refer to it.\n\n"


class CapProposal(BaseModel):
    category: str
    last_30_days: float
    proposed_cap: float | None  # None when no tool computed it; verify_cuts sets this


class Answer(BaseModel):
    markdown: str = Field(description="Final answer for the user in Markdown. Use a table when comparing accounts or options.")
    cuts: list[CapProposal] = Field(default_factory=list, description="Proposed Budget Caps, only if spending cuts are proposed")


class InsightOut(BaseModel):
    title: str = Field(description="One-line headline with the key number, e.g. 'Your card costs $244/month in interest'")
    body: str = Field(description="One or two sentences: why it matters and the suggested action")
    action_kind: Literal["add_to_plan", "set_cap", "auto_save"]
    category: str | None = Field(default=None, description="For set_cap: the Spending category")
    amount: float | None = Field(default=None, description="For set_cap: the monthly cap. For auto_save: monthly amount")


route_parser = PydanticOutputParser(pydantic_object=Route)
answer_parser = PydanticOutputParser(pydantic_object=Answer)
insight_parser = PydanticOutputParser(pydantic_object=InsightOut)

ROUTE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "You are the Orchestrator of FinAgent. Pick which specialists answer the question.\n"
               "- Debt Analyzer: what debts exist and what they cost\n"
               "- Payoff Optimizer: payoff order, debt-free date, extra payments, avalanche vs snowball\n"
               "- Budget Coach: spending, categories, budget caps, what to cut\n"
               "- Savings Strategist: emergency fund, savings rate, savings goals, affordability\n"
               "A short follow-up (such as 'What about snowball?') continues the earlier conversation: route it by that topic."),
    ("human", "{history}{question}"),
])

ANSWER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", COMMON + " You write the final answer from the specialists' findings. Lead with the direct answer and "
               "the key date or amount in bold. Keep it under 180 words plus an optional table. In a table comparing strategies, title its first column 'Payoff Strategy'. Do not do arithmetic: quote figures from the findings only. Do not invent numbers; "
               "if the findings lack a number, say what is missing. If you propose cutting spending, fill cuts with one proposed "
               "Budget Cap per category using the figures from the propose_caps findings; never invent a cap. Cite your figures: put the marker [n] right after "
               "each figure or claim, where n is the number of the matching entry in the numbered Citations list. Use only numbers "
               "that appear in that list; never cite a source that does not back the figure."),
    ("human", "{history}Question: {question}\n\nSpecialist findings:\n{findings}\n\nNumbered Citations:\n{sources}"),
])


def _ctx() -> dict:
    return {"user": config.USER_NAME, "today": finance.today().isoformat()}


# --- One specialist: LCEL tool loop -----------------------------------------------------

def run_specialist(name: str, task: str, emit=lambda e: None) -> dict:
    prompt, toolset = SPECIALISTS[name]
    by_name = {t.name: t for t in toolset}
    model = llm.sonnet(effort="low").bind_tools(toolset)
    messages = [SystemMessage((COMMON + " " + prompt).format(**_ctx())), HumanMessage(task)]
    calls, sources, t0 = [], [], time.monotonic()
    STATUS[name] = "running"
    emit({"type": "agent_start", "agent": name})
    try:
        for _ in range(MAX_STEPS):
            ai: AIMessage = model.invoke(messages)
            messages.append(ai)
            if not ai.tool_calls:
                break
            for call in ai.tool_calls:
                result = by_name[call["name"]].invoke(call["args"])
                payload = json.loads(result)
                rows = payload.get("result")
                n = len(rows) if isinstance(rows, list) else None
                calls.append({"tool": call["name"], "args": call["args"], "rows": n, "result": rows})
                sources += payload.get("sources", [])
                emit({"type": "tool", "agent": name, "tool": call["name"], "args": call["args"], "rows": n})
                messages.append(ToolMessage(result, tool_call_id=call["id"]))
        text = messages[-1].text if isinstance(messages[-1], AIMessage) else ""
    finally:
        STATUS[name] = "done"
    seconds = round(time.monotonic() - t0, 1)
    emit({"type": "agent_done", "agent": name, "seconds": seconds,
          "calls": [{k: v for k, v in c.items() if k != "result"} for c in calls]})
    return {"agent": name, "findings": text, "calls": calls, "sources": sources, "seconds": seconds}


# --- Graph -----------------------------------------------------------------------------------

class State(TypedDict, total=False):
    question: str
    history: str
    pending: list[str]
    results: list[dict]


def orchestrate(state: State) -> State:
    writer = get_stream_writer()
    STATUS["Orchestrator"] = "running"
    route = (ROUTE_PROMPT | structured(llm.sonnet(effort="low"), route_parser)).invoke(
        {"history": state.get("history", ""), "question": state["question"]})
    if not route.agents:
        route.agents = ["Budget Coach"]
    writer({"type": "route", "intent": route.intent, "agents": route.agents})
    return {"pending": list(route.agents), "results": []}


def specialist(state: State) -> State:
    writer = get_stream_writer()
    name, rest = state["pending"][0], state["pending"][1:]
    previous = "\n\n".join(f"{r['agent']} found:\n{r['findings']}" for r in state["results"])
    task = state.get("history", "") + state["question"] + (f"\n\nEarlier findings from other agents:\n{previous}" if previous else "")
    return {"pending": rest, "results": state["results"] + [run_specialist(name, task, writer)]}


def payoff_choice(results: list[dict]) -> dict | None:
    """The Payoff Strategy and extra payment the answer settled on, for the 'Apply Payoff Strategy' button."""
    choice = None
    for r in results:
        for c in r["calls"]:
            if c["tool"] == "simulate_payoff":
                choice = {"extra_monthly": c["args"]["extra_monthly"], "strategy": c["args"].get("strategy", "avalanche"),
                          "custom_order": c["args"].get("custom_order")}
            elif c["tool"] == "compare_payoff" and choice is None:
                choice = {"extra_monthly": c["args"]["extra_monthly"], "strategy": "avalanche", "custom_order": None}
    return choice


def _latest_result(results: list[dict], tool: str):
    """The result of the last call to `tool` in the specialists' traces, or None."""
    found = None
    for r in results:
        for c in r["calls"]:
            if c["tool"] == tool and c.get("result") is not None:
                found = c["result"]
    return found


def verify_cuts(cuts: list[CapProposal], results: list[dict]) -> list[CapProposal]:
    """Overwrite each CapProposal's figures with the propose_caps result for its category; None if no tool computed it."""
    proposed = {r["category"].casefold(): r for r in _latest_result(results, "propose_caps") or []}
    out = []
    for c in cuts:
        p = proposed.get(c.category.casefold())
        out.append(c.model_copy(update={"last_30_days": p["last_30_days"], "proposed_cap": p["proposed_cap"]})
                   if p else c.model_copy(update={"proposed_cap": None}))
    return out


def verify_insight_amount(insight: InsightOut, results: list[dict]) -> InsightOut:
    """Replace the model's amount with the tool's: set_cap from propose_caps, auto_save from savings_goal; else None."""
    amount = None
    if insight.action_kind == "set_cap" and insight.category:
        match = [r for r in _latest_result(results, "propose_caps") or [] if r["category"].casefold() == insight.category.casefold()]
        amount = match[0]["proposed_cap"] if match else None
    elif insight.action_kind == "auto_save":
        goal = _latest_result(results, "savings_goal")
        amount = goal["monthly_amount"] if goal else None
    return insight.model_copy(update={"amount": amount})


def synthesize(state: State) -> State:
    writer = get_stream_writer()
    findings = "\n\n".join(f"## {r['agent']}\n{r['findings']}" for r in state["results"])
    sources = citations.merge_sources([s for r in state["results"] for s in r["sources"]])  # [n] = position in this list
    numbered = "\n".join(f"[{n}] {s['document']}: {s['rows']}" + (f" ({s['pages']})" if s.get("pages") else "")
                         for n, s in enumerate(sources, start=1)) or "(none)"
    answer = (ANSWER_PROMPT | structured(llm.sonnet(effort="medium"), answer_parser)).invoke(
        {**_ctx(), "history": state.get("history", ""), "question": state["question"], "findings": findings, "sources": numbered})
    STATUS["Orchestrator"] = "idle"
    payoff = payoff_choice(state["results"])
    cuts = [c for c in verify_cuts(answer.cuts, state["results"]) if c.proposed_cap is not None]  # the UI applies only computed caps
    writer({"type": "answer", "markdown": answer.markdown, "cuts": [c.model_dump() for c in cuts],
            "sources": sources, "payoff": payoff})
    return {}


def _next(state: State) -> str:
    return "specialist" if state["pending"] else "synthesize"


graph = (
    StateGraph(State)
    .add_node("orchestrate", orchestrate)
    .add_node("specialist", specialist)
    .add_node("synthesize", synthesize)
    .add_edge(START, "orchestrate")
    .add_conditional_edges("orchestrate", _next, ["specialist", "synthesize"])
    .add_conditional_edges("specialist", _next, ["specialist", "synthesize"])
    .add_edge("synthesize", END)
    .compile()
)


# --- Insights for the dashboard ------------------------------------------------------------------

INSIGHT_TASKS = {
    "Debt Analyzer": "Find the single most expensive problem in the user's debts and a concrete fix.",
    "Budget Coach": "Find the one Spending category most over a sensible level this month and propose a monthly cap using propose_caps.",
    "Savings Strategist": f"Assess the emergency fund and propose a monthly auto-save amount and the month it reaches {config.EMERGENCY_FUND_MONTHS} months.",
}
INSIGHT_ACTION = {"Debt Analyzer": "add_to_plan", "Budget Coach": "set_cap", "Savings Strategist": "auto_save"}


def generate_insights() -> list[dict]:
    con = db.get()
    out = []
    for agent, task in INSIGHT_TASKS.items():
        r = run_specialist(agent, task)
        prompt = ChatPromptTemplate.from_messages([
            ("system", "Turn these findings into one dashboard insight card. Use only numbers present in the findings. "
                       f"action_kind must be {INSIGHT_ACTION[agent]}."),
            ("human", "{findings}"),
        ])
        ins = (prompt | structured(llm.sonnet(effort="low"), insight_parser)).invoke({"findings": r["findings"]})
        ins = verify_insight_amount(ins, [r])  # the amount comes from a tool, never from the model
        sources = citations.merge_sources(r["sources"])
        con.execute("DELETE FROM insights WHERE agent = ? AND status = 'new'", [agent])
        if finance.is_duplicate_insight(db.rows(con, "SELECT agent, title, status FROM insights"), agent, ins.title):
            continue
        con.execute("INSERT INTO insights VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)", [
            db.next_id(con), agent, ins.title, ins.body, ins.action_kind,
            json.dumps({"category": ins.category, "amount": ins.amount}), json.dumps(sources), datetime.now(),
            json.dumps([{"tool": c["tool"], "rows": c["rows"]} for c in r["calls"]])])
        out.append(ins.model_dump())
    return out
