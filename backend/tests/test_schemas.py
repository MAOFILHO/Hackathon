from datetime import date

import pytest
from langchain_core.exceptions import OutputParserException
from pydantic import ValidationError

from finagent.schemas import Categorized, Transaction, extraction_parser, categorization_parser


def test_amounts_in_any_printed_format_become_signed_floats():
    assert Transaction(row_no=1, date="2026-09-01", description="x", amount="$1,200.50").amount == 1200.5
    assert Transaction(row_no=1, date="2026-09-01", description="x", amount="(42.00)").amount == -42.0
    assert Transaction(row_no=1, date="2026-09-01", description="x", amount="-7.255").amount == -7.26


@pytest.mark.parametrize("raw", ["2026-09-04", "09/04/2026", "Sep 4, 2026", date(2026, 9, 4)])
def test_dates_in_common_formats_become_iso_dates(raw):
    assert Transaction(row_no=1, date=raw, description="x", amount=1).date == date(2026, 9, 4)


def test_description_is_trimmed_and_masked():
    t = Transaction(row_no=1, date="2026-09-01", description="  CARD 4111 2222 3333 4417  ", amount=-1)
    assert t.description == "CARD ••••4417"


def test_unknown_category_becomes_other_and_flow_is_derived():
    t = Transaction(row_no=1, date="2026-09-01", description="Mystery", amount=-5, category="Weird")
    assert t.category == "Other"
    assert t.flow == "spending"
    card = Transaction(row_no=2, date="2026-09-01", description="PAYMENT - THANK YOU", amount=1200, category="Income")
    assert card.flow == "transfer"


def test_category_agrees_with_detected_transfer_or_debt_service():
    assert Transaction(row_no=1, date="2026-09-01", description="CREDIT CARD PAYMENT", amount=-1200,
                       category="Other").category == "Transfer"
    assert Transaction(row_no=1, date="2026-09-01", description="INTEREST CHARGE", amount=-244,
                       category="Shopping").category == "Debt Service"


def test_blank_cells_become_zero_amount():
    assert Transaction(row_no=1, date="2026-09-01", description="x", amount=float("nan")).amount == 0.0


def test_low_confidence_needs_review():
    assert Transaction(row_no=1, date="2026-09-01", description="x", amount=-1, confidence=0.5).needs_review
    assert not Transaction(row_no=1, date="2026-09-01", description="x", amount=-1, confidence=0.9).needs_review


def test_confidence_out_of_range_is_rejected():
    with pytest.raises(ValidationError):
        Transaction(row_no=1, date="2026-09-01", description="x", amount=-1, confidence=1.5)


def test_categorization_parser_turns_model_json_into_validated_rows():
    out = categorization_parser.parse('{"rows": [{"row_no": 3, "category": "Groceries", "confidence": 0.92}]}')
    assert out.rows[0].category == "Groceries"


def test_extraction_parser_rejects_malformed_model_output():
    with pytest.raises(OutputParserException):
        extraction_parser.parse('{"doc_type": "spending", "transactions": [{"date": "not a date"}]}')


def test_payslip_masks_numbers_and_parses_amounts():
    from finagent.schemas import Payslip

    p = Payslip(period="Sep 2026", employer="Acme acct 4111 2222 3333 4417", gross="8,900.00", net=6850)
    assert p.employer == "Acme acct ••••4417"
    assert (p.gross, p.net) == (8900.0, 6850.0)


def test_categorized_unknown_category_becomes_other():
    assert Categorized(row_no=1, category="Groceries", confidence=0.9).category == "Groceries"
    assert Categorized(row_no=1, category="Gadgets", confidence=0.9).category == "Other"
