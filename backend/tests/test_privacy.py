from finagent.privacy import mask


def test_masks_runs_of_8_or_more_digits_keeping_last_4():
    assert mask("Card 4111111111111111 payment") == "Card ••••1111 payment"


def test_masks_digits_split_by_spaces_or_dashes():
    assert mask("Acct 1234-5678-9012") == "Acct ••••9012"


def test_leaves_short_numbers_and_amounts_alone():
    assert mask("POS 88213 MALL 1,200.50 on 2026-09-04") == "POS 88213 MALL 1,200.50 on 2026-09-04"
