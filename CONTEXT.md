# FinAgent

A personal financial coach. Specialist agents read the user's own financial documents and turn them into cited, actionable advice.

## Data

**Document**:
A file the user uploads (bank export, card statement, payslip, loan schedule, receipt). Agents only know what Documents contain.
_Avoid_: Data source, file, upload

**Row**:
One extracted line of a Document. Every number an agent states cites the Rows it came from.
_Avoid_: Record, entry, line item

**Needs Review**:
A Row whose category the model was unsure of. It still counts, under its best-guess category, until the user corrects it.
_Avoid_: Unconfirmed, pending

**Debt Account**:
One liability with a balance, an APR and a minimum payment (credit card, personal loan, car loan, BNPL).
_Avoid_: Loan (when meaning all debts), account

## Money flows

**Income**:
Net take-home money credited to the user's bank. Payslips cross-check Income; they are not its source.
_Avoid_: Salary (when meaning all Income), gross pay

**Spending**:
Money spent on goods and services, including purchases made on a credit card. Excludes Transfers and Debt Service.
_Avoid_: Expenses, outflow

**Transfer**:
Money moving between the user's own accounts, including paying off a credit card balance. Never counted as Spending.
_Avoid_: Credit card payment (as a Spending category)

**Debt Service**:
Money owed to a Debt Account beyond Transfers: scheduled repayments such as a monthly loan installment, plus interest and fees charged. Tracked apart from Spending.
_Avoid_: Debt payment (as a Spending category), EMI

**Savings Rate**:
The share of Income left after Spending and Debt Service.

## Advice

**Plan**:
The set of actions the user has committed to. Insights become part of the Plan only when the user adds them.
_Avoid_: Using "plan" for anything else below

**Insight**:
One finding by one agent, with its cited Rows and a suggested action the user can add to the Plan or dismiss.
_Avoid_: Tip, recommendation, alert

**Citation**:
A link from a number an agent states to the Document and Rows it came from.
_Avoid_: Source, reference, footnote

**Evidence**:
The tool calls behind an Insight: what the agent looked up to reach its finding.
_Avoid_: Proof, trace (when meaning an Insight's basis)

**Budget Cap**:
The user's monthly spending limit for one Spending category.
_Avoid_: Budget plan, target (for categories)

**Payoff Strategy**:
The order in which extra money is applied to Debt Accounts: Avalanche (highest APR first), Snowball (smallest balance first) or Custom.
_Avoid_: Payoff plan

**Route**:
The Orchestrator's choice of which agents answer one question, in what order.
_Avoid_: Orchestrator plan

## Agents

**Orchestrator**:
Reads the user's question, picks the Route and merges the agents' answers.

**Debt Analyzer**:
Finds and describes the Debt Accounts and what they cost.

**Payoff Optimizer**:
Compares Payoff Strategies for a given extra monthly payment.

**Budget Coach**:
Categorizes Spending and sets and checks Budget Caps.

**Savings Strategist**:
Proposes how to reach savings goals, such as an emergency fund.
