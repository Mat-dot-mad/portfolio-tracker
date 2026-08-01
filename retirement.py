"""
Retirement feasibility simulation for Polish account structures.

Answers two questions from the same engine:
  - given a spending target, what is the earliest age with an acceptable
    success rate?
  - given a retirement age, what spending level is sustainable?

EVERYTHING IS IN TODAY'S MONEY. Returns supplied here must be REAL (net of
inflation), spending stays flat in real terms, and the ZUS figure is treated as
real. This removes inflation from the arithmetic and makes every output
directly comparable to what things cost now.

The structural point this exists to model is that Polish tax wrappers unlock at
different ages. Retiring before the IKE age means bridging entirely on taxable
accounts, and a plan can fail on that bridge while looking perfectly funded on
total net worth. Money locked in IKZE is no help at 55.

NOT ADVICE. This projects arithmetic consequences of assumptions the user
supplies; it does not recommend anything.

Every Polish rule (ages, tax rates, contribution limits) arrives as a parameter
rather than a constant, because those change and a stale hardcoded figure is a
silent error.
"""

import random

# Bucket names. Order matters for withdrawals: taxable first, because it is the
# only bucket available during an early-retirement bridge and the wrappers are
# more tax-efficient later.
TAXABLE = "taxable"
IKE = "ike"
IKZE = "ikze"
PPK = "ppk"
WITHDRAWAL_ORDER = (TAXABLE, IKE, IKZE, PPK)


class Bucket:
    """A pot of money with a cost basis, so gains can be taxed separately."""

    __slots__ = ("value", "basis")

    def __init__(self, value, basis=None):
        self.value = float(value)
        # Basis unknown means "assume it is all basis", i.e. no taxable gain.
        self.basis = float(value if basis is None else basis)

    def gain_fraction(self):
        if self.value <= 0:
            return 0.0
        return max(0.0, (self.value - self.basis) / self.value)

    def grow(self, rate):
        self.value *= (1 + rate)

    def add(self, amount):
        self.value += amount
        self.basis += amount   # contributions are basis, not gain

    def take(self, gross):
        """Remove `gross` from the bucket, reducing basis proportionally."""
        gross = min(gross, self.value)
        if self.value > 0:
            self.basis -= self.basis * (gross / self.value)
        self.value -= gross
        return gross


def _net_of_tax(gross, bucket_name, bucket, params, age):
    """Net cash received for a gross withdrawal, after the wrapper's tax."""
    if bucket_name == TAXABLE:
        # Belka applies to the gain portion only.
        return gross * (1 - bucket.gain_fraction() * params["belka_rate"])
    if bucket_name == IKE:
        # Tax-free once the qualifying age is reached.
        return gross if age >= params["ike_access_age"] else gross * (1 - params["belka_rate"])
    if bucket_name == IKZE:
        # Flat rate on the whole withdrawal, not just the gain.
        return gross * (1 - params["ikze_withdrawal_rate"])
    if bucket_name == PPK:
        return gross
    return gross


def _gross_for_net(net_wanted, bucket_name, bucket, params, age):
    """Inverse of _net_of_tax: gross needed to receive `net_wanted`."""
    if bucket_name == TAXABLE:
        rate = 1 - bucket.gain_fraction() * params["belka_rate"]
    elif bucket_name == IKE:
        rate = 1.0 if age >= params["ike_access_age"] else (1 - params["belka_rate"])
    elif bucket_name == IKZE:
        rate = 1 - params["ikze_withdrawal_rate"]
    else:
        rate = 1.0
    if rate <= 0:
        return float("inf")
    return net_wanted / rate


def _is_accessible(bucket_name, params, age):
    """Whether a bucket can be drawn on at this age.

    Early access to IKE/IKZE/PPK is possible in reality but carries penalties
    that defeat the purpose, so the model treats them as locked. That is the
    conservative reading and it is what makes the bridge period bite.
    """
    if bucket_name == TAXABLE:
        return True
    if bucket_name == IKE:
        return age >= params["ike_access_age"]
    if bucket_name == IKZE:
        return age >= params["ikze_access_age"]
    if bucket_name == PPK:
        return age >= params["ppk_access_age"]
    return False


def _annual_contributions(params, age):
    """Split the year's saving across wrappers, respecting annual limits.

    Wrappers are filled first because of their tax treatment, with the
    remainder going to taxable. PPK is driven by salary rather than by the
    savings figure, since it is deducted at source.
    """
    out = {TAXABLE: 0.0, IKE: 0.0, IKZE: 0.0, PPK: 0.0}
    if age >= params["retirement_age"]:
        return out

    # PPK: employee + employer + the state's annual top-up, all from salary.
    if params["ppk_enabled"]:
        salary = params["ppk_gross_salary"]
        out[PPK] = (
            salary * params["ppk_employee_rate"]
            + salary * params["ppk_employer_rate"]
            + params["ppk_state_annual"]
        )

    remaining = params["annual_savings"]
    ikze = min(remaining, params["ikze_annual_limit"])
    out[IKZE] = ikze
    remaining -= ikze

    ike = min(remaining, params["ike_annual_limit"])
    out[IKE] = ike
    remaining -= ike

    out[TAXABLE] = max(0.0, remaining)
    return out


def simulate_path(params, returns, rng, stop_on_failure=True):
    """Run one lifetime. Returns (survived, yearly_records).

    A run fails the first year that spending cannot be met from accessible
    money — including when total wealth is ample but locked away.

    `stop_on_failure` exists because the two callers want different things.
    success_rate() only needs the verdict, so it stops at the first shortfall
    and saves work. Charting needs the whole trajectory: stopping early made
    the line end mid-plot with capital still showing, which read as "fine, then
    the chart ends" rather than "ran short here". It also biased the
    percentiles, because failed runs dropped out of the sample and later ages
    were averaged over survivors only.
    """
    buckets = {
        TAXABLE: Bucket(params["start_taxable"], params["start_taxable_basis"]),
        IKE: Bucket(params["start_ike"], params["start_ike_basis"]),
        IKZE: Bucket(params["start_ikze"], params["start_ikze_basis"]),
        PPK: Bucket(params["start_ppk"], params["start_ppk"]),
    }

    age = params["current_age"]
    horizon = params["horizon_age"]
    records = []
    failed = False
    cumulative_shortfall = 0.0
    ppk_installment = 0.0
    ppk_installments_left = 0

    while age < horizon:
        r = returns[rng.randrange(len(returns))]
        for b in buckets.values():
            b.grow(r)

        contributions = _annual_contributions(params, age)
        for name, amount in contributions.items():
            if amount:
                buckets[name].add(amount)

        shortfall = 0.0
        spending = params["annual_spending"] if age >= params["retirement_age"] else 0.0

        if spending > 0:
            # Guaranteed income first — it reduces what capital must cover.
            income = 0.0
            if age >= params["zus_start_age"]:
                income += params["zus_annual"]

            # PPK converts to income at its access age: part lump sum to the
            # taxable pot, the rest paid out over a fixed number of years.
            if age >= params["ppk_access_age"] and buckets[PPK].value > 0 and ppk_installments_left == 0:
                lump = buckets[PPK].value * params["ppk_lump_sum_fraction"]
                buckets[TAXABLE].add(buckets[PPK].take(lump))
                ppk_installments_left = max(1, params["ppk_installment_years"])
                ppk_installment = buckets[PPK].value / ppk_installments_left

            if ppk_installments_left > 0:
                paid = buckets[PPK].take(min(ppk_installment, buckets[PPK].value))
                income += paid
                ppk_installments_left -= 1

            need = max(0.0, spending - income)

            for name in WITHDRAWAL_ORDER:
                if need <= 0:
                    break
                if not _is_accessible(name, params, age):
                    continue
                bucket = buckets[name]
                if bucket.value <= 0:
                    continue
                gross_wanted = _gross_for_net(need, name, bucket, params, age)
                taken = bucket.take(gross_wanted)
                need -= _net_of_tax(taken, name, bucket, params, age)

            shortfall = max(0.0, need)

        total = sum(b.value for b in buckets.values())
        # What could actually be spent at this age. Total capital hides a bridge
        # failure completely: the locked buckets keep compounding while spending
        # goes unfunded, so the total line RISES through the very years the plan
        # cannot pay for anything. This is the number that falls to zero.
        reachable = sum(b.value for name, b in buckets.items()
                        if _is_accessible(name, params, age))
        cumulative_shortfall += shortfall
        records.append({
            "age": age,
            "total": total,
            "reachable": reachable,
            "taxable": buckets[TAXABLE].value,
            "ike": buckets[IKE].value,
            "ikze": buckets[IKZE].value,
            "ppk": buckets[PPK].value,
            "shortfall": shortfall,
            "cumulative_shortfall": cumulative_shortfall,
        })

        if shortfall > 1e-6:
            failed = True
            if stop_on_failure:
                return False, records

        age += 1

    return (not failed), records


def success_rate(params, returns, paths=500, seed=None):
    """Fraction of runs that fund spending to the horizon."""
    rng = random.Random(seed)
    survived = 0
    for _ in range(paths):
        ok, _ = simulate_path(params, returns, rng)
        survived += 1 if ok else 0
    return survived / paths


def earliest_feasible_age(params, returns, threshold=0.9, paths=500,
                          min_age=None, max_age=75, seed=None):
    """Lowest retirement age whose success rate clears `threshold`.

    Returns (age, rate) or (None, best_rate) when nothing in range qualifies.
    """
    start = min_age if min_age is not None else params["current_age"] + 1
    best = 0.0
    for age in range(int(start), int(max_age) + 1):
        trial = dict(params, retirement_age=age)
        rate = success_rate(trial, returns, paths=paths, seed=seed)
        best = max(best, rate)
        if rate >= threshold:
            return age, rate
    return None, best


def sustainable_spending(params, returns, threshold=0.9, paths=300,
                         seed=None, tolerance=1000.0):
    """Highest annual spend at `retirement_age` still meeting `threshold`.

    Binary search: success falls monotonically as spending rises, so bisection
    is valid and far cheaper than scanning.
    """
    low, high = 0.0, max(params["annual_spending"] * 4, 100000.0)

    # Confirm the upper bound genuinely fails, else the answer is off-scale.
    if success_rate(dict(params, annual_spending=high), returns, paths=paths, seed=seed) >= threshold:
        return high

    while high - low > tolerance:
        mid = (low + high) / 2
        rate = success_rate(dict(params, annual_spending=mid), returns, paths=paths, seed=seed)
        if rate >= threshold:
            low = mid
        else:
            high = mid
    return low


def median_path(params, returns, paths=200, seed=None):
    """Percentile bands for charting, plus how many runs have fallen short.

    Runs continue past their first shortfall so every path spans the full
    horizon. Percentiles are therefore taken over ALL runs at every age rather
    than only those still solvent — the previous behaviour dropped failed runs
    from the sample, so late ages were averaged over survivors and looked
    better than they were.
    """
    rng = random.Random(seed)
    runs = [simulate_path(params, returns, rng, stop_on_failure=False)[1]
            for _ in range(paths)]
    if not runs:
        return []

    length = min(len(r) for r in runs)
    out = []
    for i in range(length):
        def percentile(key, f):
            values = sorted(r[i][key] for r in runs)
            return values[min(len(values) - 1, int(f * len(values)))]

        # Share of runs that have hit a shortfall at or before this age. This is
        # what turns "the line stops" into "this is where plans start failing".
        short = sum(1 for r in runs if any(y["shortfall"] > 1e-6 for y in r[:i + 1]))
        out.append({
            "age": runs[0][i]["age"],
            "p10": percentile("total", 0.10),
            "p50": percentile("total", 0.50),
            "p90": percentile("total", 0.90),
            # Spendable capital. Diverges from the totals above by exactly the
            # amount sitting behind an age gate, which is the whole point of the
            # bridge — and unlike the total, it reaches zero when a plan fails.
            "reachable_p10": percentile("reachable", 0.10),
            "reachable_p50": percentile("reachable", 0.50),
            "failed_share": short / len(runs),
        })
    return out


def first_shortfall_ages(params, returns, paths=200, seed=None):
    """Age each run first cannot fund spending; None where it never happens."""
    rng = random.Random(seed)
    ages = []
    for _ in range(paths):
        _ok, rec = simulate_path(params, returns, rng, stop_on_failure=True)
        ages.append(next((y["age"] for y in rec if y["shortfall"] > 1e-6), None))
    return ages
