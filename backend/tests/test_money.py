from datetime import date

from finagent.money import Txn, classify_flow, spending_by_category, summarize


# --- Transfer detection --------------------------------------------------

def test_card_payment_from_bank_is_a_transfer_not_spending():
    assert classify_flow("Other", "CREDIT CARD PAYMENT - VISA") == "transfer"


def test_card_payment_received_on_card_statement_is_a_transfer():
    assert classify_flow("Other", "PAYMENT - THANK YOU") == "transfer"


def test_move_to_savings_is_a_transfer():
    assert classify_flow("Other", "Transfer to savings") == "transfer"


def test_loan_installment_is_debt_service():
    assert classify_flow("Other", "Personal loan installment") == "debt_service"
    assert classify_flow("Other", "BNPL installment 3/6") == "debt_service"


def test_card_interest_and_fees_are_debt_service():
    assert classify_flow("Other", "INTEREST CHARGE ON PURCHASES") == "debt_service"
    assert classify_flow("Other", "LATE FEE") == "debt_service"


def test_salary_credit_is_income():
    assert classify_flow("Income", "Salary credit - employer") == "income"


def test_card_purchase_is_spending():
    assert classify_flow("Dining out", "Restaurant") == "spending"


def test_refund_is_negative_spending_not_income():
    assert classify_flow("Shopping", "Refund - store") == "spending"


def test_category_transfer_wins_even_without_keyword():
    assert classify_flow("Transfer", "Zelle to self") == "transfer"


# --- Spending and Savings Rate --------------------------------------------

def _month():
    d = date(2026, 9, 10)
    return [
        Txn(d, 6850, "Income", "income"),
        Txn(d, -1900, "Housing", "spending"),
        Txn(d, -300, "Dining out", "spending"),
        Txn(d, 20, "Dining out", "spending"),  # refund
        Txn(d, -1200, "Transfer", "transfer"),
        Txn(d, -480, "Debt Service", "debt_service"),
        Txn(date(2026, 8, 31), -999, "Housing", "spending"),  # outside window
    ]


def test_summary_excludes_transfers_and_separates_debt_service():
    s = summarize(_month(), date(2026, 9, 1), date(2026, 9, 30))
    assert s.income == 6850
    assert s.spending == 2180
    assert s.debt_service == 480
    assert s.transfers == 1200


def test_savings_rate_is_share_left_after_spending_and_debt_service():
    s = summarize(_month(), date(2026, 9, 1), date(2026, 9, 30))
    assert round(s.savings_rate, 4) == round((6850 - 2180 - 480) / 6850, 4)


def test_savings_rate_is_none_without_income():
    s = summarize([Txn(date(2026, 9, 1), -10, "Groceries", "spending")],
                  date(2026, 9, 1), date(2026, 9, 30))
    assert s.savings_rate is None


def test_spending_by_category_nets_refunds_and_skips_non_spending():
    by_cat = spending_by_category(_month(), date(2026, 9, 1), date(2026, 9, 30))
    assert by_cat == {"Housing": 1900, "Dining out": 280}
