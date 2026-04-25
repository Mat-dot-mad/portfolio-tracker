"""
Fetch exchange rates from the NBP (National Bank of Poland) API.

Endpoint docs: https://api.nbp.pl/en.html
Table A contains mid rates for major currencies (EUR, USD, etc.).
"""

import requests
from datetime import datetime, timedelta


def get_rate(currency, date_str):
    """Get the NBP mid exchange rate for a currency on a given date.

    Args:
        currency: Currency code, e.g. "EUR", "USD". "PLN" returns 1.0.
        date_str: Date string in YYYY-MM-DD format.

    Returns:
        dict with "rate" (float) and "effective_date" (str YYYY-MM-DD).

    Raises:
        ValueError if the rate cannot be fetched after retries.
    """
    if currency.upper() == "PLN":
        return {"rate": 1.0, "effective_date": date_str}

    # NBP doesn't publish rates on weekends/holidays.
    # Try the requested date, then go back up to 7 days to find the latest rate.
    date = datetime.strptime(date_str, "%Y-%m-%d").date()

    for days_back in range(8):
        check_date = date - timedelta(days=days_back)
        url = (
            f"https://api.nbp.pl/api/exchangerates/rates/a/"
            f"{currency.upper()}/{check_date.isoformat()}/"
        )
        resp = requests.get(url, params={"format": "json"}, timeout=10)

        if resp.status_code == 200:
            data = resp.json()
            rate_info = data["rates"][0]
            return {
                "rate": rate_info["mid"],
                "effective_date": rate_info["effectiveDate"],
            }

        # 404 means no data for that date — try the previous day
        if resp.status_code == 404:
            continue

        # Other errors — stop retrying
        resp.raise_for_status()

    raise ValueError(
        f"Could not find NBP rate for {currency} near {date_str} "
        f"(tried 8 days back)"
    )
