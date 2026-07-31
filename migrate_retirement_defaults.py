"""
One-time migration: correct retirement settings still holding superseded
defaults.

Saving the planner form persists every field, so a value that was never
consciously chosen — it just happened to be the default at the time — becomes
sticky. Changing RETIREMENT_DEFAULTS afterwards has no effect on it.

This replaces a stored value ONLY when it still equals the old default. Anything
deliberately changed is left alone, so re-running is safe and nothing the user
picked gets overwritten.

    IKE / IKZE limits — the previous defaults were stale. 2026 figures are
    28,260 (3x projected average wage 9,420) and 11,304 for employees
    (1.2x). Self-employed IKZE is 16,956 (1.8x) — set that manually if it
    applies.

Run with --dry-run to preview.
"""

import sys

import db

# key: (superseded default, replacement)
SUPERSEDED = {
    "ike_annual_limit":  (26019, 28260),
    "ikze_annual_limit": (10407, 11304),
    "ppk_employee_rate": (0.02, 0.04),
    "ppk_employer_rate": (0.015, 0.04),
    "ppk_enabled":       (0, 1),
}


def main():
    dry_run = "--dry-run" in sys.argv
    db.init_db()

    stored = db.get_retirement_settings()
    if not stored:
        print("No saved retirement settings — nothing to migrate. "
              "Fresh installs pick up the new defaults automatically.")
        return

    updates, kept = {}, []
    for key, (old, new) in SUPERSEDED.items():
        if key not in stored:
            continue
        try:
            current = float(stored[key])
        except (TypeError, ValueError):
            continue

        if abs(current - old) < 1e-9:
            updates[key] = new
            print(f"  {key:<20} {old} -> {new}")
        elif abs(current - new) > 1e-9:
            kept.append((key, current))

    for key, current in kept:
        print(f"  {key:<20} left at {current} (deliberately set — not touched)")

    if not updates:
        print("\nNothing to change.")
        return

    if dry_run:
        print(f"\n--dry-run: {len(updates)} setting(s) would change.")
    else:
        db.save_retirement_settings(updates)
        print(f"\nUpdated {len(updates)} setting(s).")


if __name__ == "__main__":
    main()
