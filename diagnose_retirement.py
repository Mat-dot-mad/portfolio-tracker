"""Read-only dump of what the retirement planner actually simulated.

Exists because the page can disagree with the engine in ways the UI cannot
show: the sliders render stored settings, while the simulation runs merged
params (derived saving rate, snapshot-tracked PPK balance, defaults filling
gaps). When the headline numbers look impossible, this prints both sides plus a
single failing run bucket by bucket, so a bridge failure is visible as the
liquidity problem it is rather than as a total-capital line that keeps rising.

Writes nothing. Safe to run against the live database.

    cd /opt/portfolio/app
    sudo -u portfolio DATABASE_PATH=/var/lib/portfolio/portfolio.db \
        venv/bin/python diagnose_retirement.py
"""

import argparse
import random

import app as A
import db
import retirement


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--retirement-age", type=float)
    ap.add_argument("--annual-spending", type=float)
    ap.add_argument("--annual-savings", type=float)
    ap.add_argument("--horizon-age", type=float)
    ap.add_argument("--max-age", type=int, default=72,
                    help="last age shown in the failing-run trace")
    args = ap.parse_args()

    data = A._build_dashboard_data()
    if not data["timeline"]:
        print("No snapshots — nothing to simulate.")
        return

    settings = db.get_retirement_settings()
    params, balances, basis_ratio = A._retirement_params(settings, data)

    overrides = {
        "retirement_age": args.retirement_age,
        "annual_spending": args.annual_spending,
        "annual_savings": args.annual_savings,
        "horizon_age": args.horizon_age,
    }
    overrides = {k: v for k, v in overrides.items() if v is not None}
    params.update(overrides)
    if overrides:
        print("CLI overrides applied:", overrides, "\n")

    print("=== stored settings (what the sliders show) ===")
    if settings:
        for k in sorted(settings):
            print(f"  {k:<28} {settings[k]}")
    else:
        print("  (none saved — every field is showing a default)")

    print("\n=== params actually simulated ===")
    for k in sorted(params):
        flag = ""
        if k in overrides:
            flag = "   <-- CLI override"
        elif k in settings and str(settings[k]) != str(params[k]):
            flag = f"   <-- DIFFERS from stored {settings[k]!r}"
        elif k not in settings and not k.startswith("start_"):
            flag = "   (default)"
        print(f"  {k:<28} {params[k]}{flag}")

    print("\n=== balances ===")
    for k, v in balances.items():
        print(f"  {k:<10} {v:>14,.0f}")
    total = sum(balances.values())
    reachable = balances["taxable"]
    print(f"  {'TOTAL':<10} {total:>14,.0f}")
    if total:
        print(f"  reachable before the IKE age: {reachable:,.0f} "
              f"({reachable / total:.0%} of capital)")
    print(f"  basis_ratio {basis_ratio:.3f}")

    if params["use_historical_returns"]:
        returns = A._real_return_pool(data, params["inflation_rate"])
        source = "historical (real)"
    else:
        returns = None
        source = "fixed"
    if not returns:
        returns = [params["expected_real_return"]]
        source = "fixed"

    print(f"\n=== returns: {source} ===")
    print(f"  n={len(returns)}  geometric={A._geometric_mean(returns):.4f}  "
          f"arithmetic={sum(returns) / len(returns):.4f}  "
          f"min={min(returns):.3f}  max={max(returns):.3f}")

    threshold = params["success_threshold"]
    rate = retirement.success_rate(params, returns, paths=300, seed=42)
    age, best = retirement.earliest_feasible_age(
        params, returns, threshold=threshold, paths=300,
        min_age=int(params["current_age"]) + 1, max_age=75, seed=42)
    sustainable = retirement.sustainable_spending(
        params, returns, threshold=threshold, paths=200, seed=42)
    shortfalls = [a for a in retirement.first_shortfall_ages(
        params, returns, paths=200, seed=42) if a is not None]

    print("\n=== headline results (compare these against the page) ===")
    print(f"  success at chosen age      {rate:.1%}")
    print(f"  earliest feasible age      {age}  (threshold {threshold:.0%}, "
          f"best seen {best:.1%})")
    print(f"  sustainable spend          {sustainable:,.0f}")
    print(f"  runs falling short         {len(shortfalls)}/200")
    if shortfalls:
        print(f"  median first shortfall age {sorted(shortfalls)[len(shortfalls) // 2]}")

    rng = random.Random(42)
    for _ in range(500):
        ok, rec = retirement.simulate_path(params, returns, rng,
                                           stop_on_failure=False)
        if not ok:
            print("\n=== one failing run, bucket by bucket ===")
            print("total capital can RISE while the run is already short: the "
                  "locked\nbuckets keep compounding and cannot be spent.\n")
            print(f"{'age':>4} {'taxable':>12} {'ike':>12} {'ikze':>12} "
                  f"{'ppk':>10} {'TOTAL':>13} {'shortfall':>11}")
            for y in rec:
                if y["age"] <= args.max_age:
                    print(f"{y['age']:>4.0f} {y['taxable']:>12,.0f} "
                          f"{y['ike']:>12,.0f} {y['ikze']:>12,.0f} "
                          f"{y['ppk']:>10,.0f} {y['total']:>13,.0f} "
                          f"{y['shortfall']:>11,.0f}")
            break
    else:
        print("\n(no failing run found in 500 tries)")


if __name__ == "__main__":
    main()
