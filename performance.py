"""Shared approximate period returns for capital tracked by the cash-flow export.

PPK is excluded because its payroll contributions are not recorded. Mortgage
liabilities are excluded: this measures invested capital, not leveraged net worth.
Contributions are treated as arriving at period end, rather than time-weighted.
"""


def annotate(timeline):
    previous = None
    for row in timeline:
        wealth = row["portfolio_total"] - row.get("ppk_total", 0) + row["cash_total"]
        row["tracked_wealth"] = wealth
        row["market_gain"] = None if previous is None else wealth - previous - row.get("net_contributions", 0)
        row["market_return"] = (row["market_gain"] / previous
                                if previous is not None and previous > 0 else None)
        previous = wealth
