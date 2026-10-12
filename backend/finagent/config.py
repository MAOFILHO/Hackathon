"""Demo user and money-rule constants. See CONTEXT.md."""

USER_NAME = "Ravi"
CURRENCY = "USD"
SAVINGS_TARGET = 0.25  # target Savings Rate
EMERGENCY_FUND_MONTHS = 6
DEFAULT_EXTRA_MONTHLY = 500  # extra monthly payment toward Debt Accounts when the user has not chosen one
CAP_SHARE = 0.75  # a proposed Budget Cap is this share of the last 30 days' Spending
DISCRETIONARY_CATEGORIES = ("Dining out", "Shopping", "Ride-hailing", "Subscriptions", "Entertainment")  # the only categories that get a proposed Budget Cap
SAVINGS_TRANSFER_PATTERN = "%transfer to savings%"  # SQL LIKE, matched against Row descriptions
