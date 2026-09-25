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


def xirr(cash_flows):
    """Annual money-weighted return using actual dates and a 365-day year.

    cash_flows contains (ISO date, signed amount) pairs, from the investor's
    perspective. Same-day flows are netted. Solve in log(1+r) space, using
    derivative roots to partition monotonic intervals and detect multiple
    solutions rather than returning whichever rate a starting guess reaches.
    Scaled discount factors avoid overflow near -100%.
    """
    import math
    from datetime import date

    def unavailable(reason):
        return {'rate': None, 'reason': reason}

    daily = {}
    try:
        for when, amount in cash_flows:
            when = date.fromisoformat(when)
            amount = float(amount)
            if not math.isfinite(amount):
                return unavailable('Cash-flow amounts must be finite.')
            daily[when] = daily.get(when, 0.0) + amount
    except (TypeError, ValueError):
        return unavailable('Cash-flow dates or amounts are invalid.')
    values = [(d, v) for d, v in sorted(daily.items()) if v != 0]
    if len(values) < 2 or values[0][0] == values[-1][0]:
        return unavailable('XIRR needs cash flows on at least two different dates.')
    if not any(v < 0 for _, v in values) or not any(v > 0 for _, v in values):
        return unavailable('XIRR needs both money invested and a positive withdrawal or ending value.')
    start = values[0][0]
    scale = max(abs(v) for _, v in values)
    terms = [((d - start).days / 365.0, v / scale) for d, v in values]
    low, high = math.log(1e-12), math.log1p(1e6)

    def evaluate(terms, log_rate):
        exponents = [-log_rate * years for years, _ in terms]
        largest = max(exponents)
        weighted = [amount * math.exp(exponent - largest)
                    for (_, amount), exponent in zip(terms, exponents)]
        # Relative residual: multiplying by a positive scaling factor changes
        # neither roots nor signs, but keeps both extreme endpoints finite.
        return math.fsum(weighted) / math.fsum(abs(v) for v in weighted)

    def bisect(terms, left, right):
        f_left = evaluate(terms, left)
        for _ in range(200):
            middle = (left + right) / 2
            value = evaluate(terms, middle)
            if value == 0 or right - left < 1e-13:
                return middle
            if (value > 0) == (f_left > 0):
                left, f_left = middle, value
            else:
                right = middle
        raise ValueError('XIRR did not converge.')

    def roots(terms, depth=0):
        # Factoring out the earliest exponential leaves a constant first term
        # without changing roots. Its derivative then has one fewer term.
        first = terms[0][0]
        terms = [(t - first, c) for t, c in terms]
        signs = [c > 0 for _, c in terms]
        changes = sum(a != b for a, b in zip(signs, signs[1:]))
        if changes == 0:
            return []
        if changes == 1:
            left, right = evaluate(terms, low), evaluate(terms, high)
            if left == 0:
                return [low]
            if right == 0:
                return [high]
            return [bisect(terms, low, high)] if left * right < 0 else []
        if depth >= 200:
            raise ValueError('This cash-flow pattern is too complex to establish a unique XIRR safely.')
        derivative = [(t, -t * c) for t, c in terms[1:]]
        maximum = max(abs(c) for _, c in derivative)
        derivative = [(t, c / maximum) for t, c in derivative]
        turning = roots(derivative, depth + 1)
        points = [low, *turning, high]
        found = []
        # Include tangencies: a repeated root need not change the NPV sign.
        for point in points:
            if abs(evaluate(terms, point)) < 1e-12:
                found.append(point)
        for left, right in zip(points, points[1:]):
            f_left, f_right = evaluate(terms, left), evaluate(terms, right)
            if abs(f_left) >= 1e-12 and abs(f_right) >= 1e-12 and f_left * f_right < 0:
                found.append(bisect(terms, left, right))
        distinct = []
        for point in sorted(found):
            if not distinct or abs(point - distinct[-1]) > 1e-8:
                distinct.append(point)
        return distinct

    try:
        solutions = roots(terms)
    except (ValueError, ZeroDivisionError, OverflowError):
        return unavailable('A reliable XIRR could not be established for this cash-flow pattern.')
    if not solutions:
        return unavailable('No XIRR found within the supported annual return range (-100% to 100,000,000%).')
    if len(solutions) > 1:
        return unavailable('These cash flows have multiple XIRR solutions; no unique annual return is shown.')
    return {'rate': math.expm1(solutions[0]), 'reason': None}


def portfolio_xirr(events, snapshot_date, ending_value):
    """Exclude post-snapshot events; PPK and debt are absent from ending_value."""
    if not snapshot_date:
        return {'rate': None, 'reason': 'Import a holdings snapshot first.'}
    if ending_value < 0:
        return {'rate': None, 'reason': 'XIRR requires a non-negative ending portfolio value.'}
    flows = [(e['event_date'], -e['value_pln'] if e['operation'] == 'deposit' else e['value_pln'])
             for e in events if e['event_date'] <= snapshot_date]
    flows.append((snapshot_date, ending_value))
    return xirr(flows)
