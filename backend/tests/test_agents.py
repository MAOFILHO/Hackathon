from datetime import date

import json

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from finagent import agents, db, finance, llm, seed
from finagent.agents import CapProposal, InsightOut, payoff_choice, verify_cuts, verify_insight_amount


def call(tool, **args):
    return {"tool": tool, "args": args, "rows": None}


def test_no_payoff_calls_means_no_apply_button():
    assert payoff_choice([{"calls": [call("get_debts")]}]) is None


def test_compare_only_applies_avalanche_with_the_stated_extra():
    got = payoff_choice([{"calls": [call("compare_payoff", extra_monthly=750)]}])
    assert got == {"extra_monthly": 750, "strategy": "avalanche", "custom_order": None}


def test_simulation_wins_over_comparison_in_either_order():
    sim = call("simulate_payoff", extra_monthly=1000, strategy="snowball")
    cmp_ = call("compare_payoff", extra_monthly=1000)
    expected = {"extra_monthly": 1000, "strategy": "snowball", "custom_order": None}
    assert payoff_choice([{"calls": [cmp_, sim]}]) == expected
    assert payoff_choice([{"calls": [sim, cmp_]}]) == expected


def test_custom_order_is_carried_through():
    got = payoff_choice([{"calls": [call("simulate_payoff", extra_monthly=500, strategy="custom", custom_order=["A", "B"])]}])
    assert got["custom_order"] == ["A", "B"]


# --- numbers come from tools, not from the model ------------------------------------------------

def done(tool, result, **args):
    return {"tool": tool, "args": args, "rows": None, "result": result}


CAPS = done("propose_caps", [{"category": "Dining out", "last_30_days": 412.4, "proposed_cap": 310.0},
                             {"category": "Shopping", "last_30_days": 100.0, "proposed_cap": 75.0}], days=30)
GOAL = done("savings_goal", {"monthly_amount": 650.0, "months_to_goal": 14}, target_months=6)


def test_verify_cuts_overwrites_the_models_numbers_with_the_tool_caps():
    cuts = [CapProposal(category="Dining out", last_30_days=400, proposed_cap=250), CapProposal(category="Shopping", last_30_days=90, proposed_cap=60)]
    got = verify_cuts(cuts, [{"calls": [CAPS]}])
    assert [(c.category, c.last_30_days, c.proposed_cap) for c in got] == [("Dining out", 412.4, 310.0), ("Shopping", 100.0, 75.0)]


def test_verify_cuts_blanks_a_cap_no_tool_computed():
    got = verify_cuts([CapProposal(category="Groceries", last_30_days=500, proposed_cap=350)], [{"calls": [CAPS]}])
    assert got[0].proposed_cap is None


def test_verify_cuts_without_any_tool_call_blanks_every_cap():
    assert verify_cuts([CapProposal(category="Dining out", last_30_days=1, proposed_cap=1)], [{"calls": []}])[0].proposed_cap is None


def test_verify_cuts_uses_the_latest_matching_call():
    newer = done("propose_caps", [{"category": "Shopping", "last_30_days": 200.0, "proposed_cap": 150.0}], days=30)
    got = verify_cuts([CapProposal(category="Shopping", last_30_days=1, proposed_cap=1)], [{"calls": [CAPS]}, {"calls": [newer]}])
    assert got[0].proposed_cap == 150.0


def insight(kind, **kw):
    return InsightOut(title="t", body="b", action_kind=kind, **kw)


def test_verify_insight_amount_set_cap_takes_the_tool_cap_for_its_category():
    got = verify_insight_amount(insight("set_cap", category="dining out", amount=123), [{"calls": [CAPS]}])
    assert got.amount == 310.0


def test_verify_insight_amount_auto_save_takes_the_savings_goal_amount():
    assert verify_insight_amount(insight("auto_save", amount=999), [{"calls": [GOAL]}]).amount == 650.0


@pytest.mark.parametrize("ins", [insight("set_cap", category="Groceries", amount=5), insight("auto_save", amount=5),
                                 insight("add_to_plan", amount=5)])
def test_verify_insight_amount_is_none_when_no_tool_computed_it(ins):
    assert verify_insight_amount(ins, [{"calls": []}]).amount is None


# --- run_specialist with a scripted chat model --------------------------------------------------------

class ScriptedModel:
    """Stands in for llm.sonnet(): replays the given AIMessages, repeating the last one."""
    def __init__(self, *script):
        self.script, self.invocations = list(script), 0

    def __call__(self, effort="medium"):
        return self

    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages):
        msg = self.script[min(self.invocations, len(self.script) - 1)]
        self.invocations += 1
        return msg


def ask_for(tool, call_id="c1", **args):
    return AIMessage("", tool_calls=[{"name": tool, "args": args, "id": call_id, "type": "tool_call"}])


@pytest.fixture()
def con(tmp_path, monkeypatch):
    c = db.connect(tmp_path / "t.duckdb")
    seed.load(c)
    monkeypatch.setattr(db, "get", lambda: c)
    monkeypatch.setattr(finance, "today", lambda: date(2026, 10, 10))
    return c


def test_run_specialist_runs_the_tool_loop_and_emits_events(con, monkeypatch):
    model = ScriptedModel(ask_for("get_debts"), AIMessage("You owe $37,500."))
    monkeypatch.setattr(llm, "sonnet", model)
    events = []
    r = agents.run_specialist("Debt Analyzer", "what do I owe?", events.append)
    assert r["findings"] == "You owe $37,500." and model.invocations == 2
    [call] = r["calls"]
    assert call["tool"] == "get_debts" and call["rows"] == 4 and len(call["result"]) == 4
    assert r["sources"] and agents.STATUS["Debt Analyzer"] == "done"
    assert [e["type"] for e in events] == ["agent_start", "tool", "agent_done"]
    assert events[1]["rows"] == 4 and events[1]["tool"] == "get_debts"
    assert events[2]["calls"][0]["tool"] == "get_debts" and "result" not in events[2]["calls"][0]


def test_run_specialist_stops_after_max_steps(con, monkeypatch):
    model = ScriptedModel(ask_for("get_debts"))
    monkeypatch.setattr(llm, "sonnet", model)
    r = agents.run_specialist("Debt Analyzer", "loop forever")
    assert model.invocations == agents.MAX_STEPS and len(r["calls"]) == agents.MAX_STEPS
    assert r["findings"] == ""


def test_run_specialist_without_tool_calls_just_answers(con, monkeypatch):
    monkeypatch.setattr(llm, "sonnet", ScriptedModel(AIMessage("Nothing to do.")))
    r = agents.run_specialist("Debt Analyzer", "hi")
    assert r["findings"] == "Nothing to do." and r["calls"] == []


def test_budget_coach_and_savings_strategist_can_call_the_cap_and_savings_goal_tools():
    assert "propose_caps" in [t.name for t in agents.SPECIALISTS["Budget Coach"][1]]
    assert "savings_goal" in [t.name for t in agents.SPECIALISTS["Savings Strategist"][1]]
    assert "propose_caps" in agents.SPECIALISTS["Budget Coach"][0]
    assert "savings_goal" in agents.SPECIALISTS["Savings Strategist"][0]


def fake_structured(**outputs):
    """Replaces agents.structured: each parser's model class maps to a canned object."""
    return lambda _model, parser: RunnableLambda(lambda _: outputs[parser.pydantic_object.__name__])


def test_the_graph_emits_only_cuts_whose_caps_a_tool_computed(con, monkeypatch):
    route = agents.Route(intent="cut spending", agents=["Budget Coach"])
    invented = agents.Answer(markdown="Cut dining.", cuts=[
        CapProposal(category="Dining out", last_30_days=1, proposed_cap=1), CapProposal(category="Groceries", last_30_days=2, proposed_cap=2)])
    monkeypatch.setattr(agents, "structured", fake_structured(Route=route, Answer=invented))
    monkeypatch.setattr(llm, "sonnet", ScriptedModel(ask_for("propose_caps", days=30), AIMessage("Dining is high.")))
    events = list(agents.graph.stream({"question": "where can I cut?"}, stream_mode="custom", version="v2"))
    answer = next(e["data"] for e in events if e["data"]["type"] == "answer")
    expected = {c["category"]: c for c in finance.propose_caps(finance.spend_by_category(con, 30))}["Dining out"]
    assert answer["cuts"] == [{"category": "Dining out", "last_30_days": expected["last_30_days"],
                               "proposed_cap": expected["proposed_cap"]}]


def test_generate_insights_stores_the_tool_amount_not_the_models(con, monkeypatch):
    results = {"Debt Analyzer": [], "Budget Coach": [CAPS], "Savings Strategist": [GOAL]}
    monkeypatch.setattr(agents, "run_specialist", lambda name, task, emit=None: {
        "agent": name, "findings": "f", "calls": results[name], "sources": [], "seconds": 0})
    made = {"Debt Analyzer": insight("add_to_plan", amount=7), "Budget Coach": insight("set_cap", category="Dining out", amount=7),
            "Savings Strategist": insight("auto_save", amount=7)}
    current = iter(made.values())
    monkeypatch.setattr(agents, "structured", lambda _m, _p: RunnableLambda(lambda _: next(current)))
    agents.generate_insights()
    stored = {r["agent"]: json.loads(r["action_payload"])["amount"] for r in db.rows(con, "SELECT agent, action_payload FROM insights")}
    assert stored == {"Debt Analyzer": None, "Budget Coach": 310.0, "Savings Strategist": 650.0}


def test_answer_prompt_uses_glossary_names_for_comparison_tables():
    from finagent.agents import ANSWER_PROMPT

    text = " ".join(str(m.prompt.template) for m in ANSWER_PROMPT.messages)
    assert "Payoff Strategy" in text


# --- follow-up questions keep the conversation ---------------------------------------------------------

def test_conversation_is_empty_without_history_and_keeps_only_the_last_turns_cut_short():
    assert agents.conversation([]) == ""
    turns = [agents.Turn(question=f"q{i}", answer=f"a{i}" + "x" * 5000) for i in range(5)]
    text = agents.conversation(turns)
    assert "q0" not in text and "q1" not in text and "q2" in text and "q4" in text
    assert len(text) < 3 * (agents.ANSWER_CHARS + 100)


def test_the_conversation_reaches_the_routing_the_specialists_and_the_answer(con, monkeypatch):
    seen = {}
    monkeypatch.setattr(agents, "structured", lambda _m, parser: RunnableLambda(lambda prompt: (
        seen.setdefault(parser.pydantic_object.__name__, prompt.to_string()),
        agents.Route(intent="x", agents=["Payoff Optimizer"]) if parser.pydantic_object is agents.Route
        else agents.Answer(markdown="ok"))[1]))
    monkeypatch.setattr(agents, "run_specialist", lambda name, task, emit=None: (
        seen.setdefault("task", task), {"agent": name, "findings": "f", "calls": [], "sources": [], "seconds": 0})[1])
    monkeypatch.setattr(llm, "sonnet", lambda **_: None)
    list(agents.graph.stream({"question": "What about snowball?", "history": "EARLIER: avalanche was cheaper"},
                             stream_mode="custom", version="v2"))
    assert "EARLIER: avalanche was cheaper" in seen["Route"]
    assert "EARLIER: avalanche was cheaper" in seen["task"]
    assert "EARLIER: avalanche was cheaper" in seen["Answer"]


def test_prompts_are_built_from_config_values(monkeypatch):
    import importlib.util

    from finagent import config

    monkeypatch.setattr(config, "SAVINGS_TARGET", 0.4)
    monkeypatch.setattr(config, "EMERGENCY_FUND_MONTHS", 9)
    monkeypatch.setattr(config, "DEFAULT_EXTRA_MONTHLY", 700)
    spec = importlib.util.spec_from_file_location("finagent._agents_probe", agents.__file__)
    probe = importlib.util.module_from_spec(spec)  # a fresh copy, so the real module and its classes stay untouched
    spec.loader.exec_module(probe)
    assert "40%" in probe.SPECIALISTS["Savings Strategist"][0] and "25%" not in probe.SPECIALISTS["Savings Strategist"][0]
    assert "9 months" in probe.INSIGHT_TASKS["Savings Strategist"] and "6 months" not in probe.INSIGHT_TASKS["Savings Strategist"]
    assert "use 700 if none" in probe.SPECIALISTS["Payoff Optimizer"][0]


def test_prompts_name_the_savings_goal_not_a_plan():
    assert "savings_plan" not in agents.SPECIALISTS["Savings Strategist"][0]
