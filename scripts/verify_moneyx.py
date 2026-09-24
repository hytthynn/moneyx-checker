from __future__ import annotations

import argparse
import getpass
from decimal import Decimal, ROUND_HALF_UP

from asyncio_compat import run
from config import MONEYX_WEB_URL
from moneyx.client import MoneyXClient
from moneyx.domain import derive_api_url, parse_web_url
from moneyx.rates import normalize_rate


async def main() -> None:
    parser = argparse.ArgumentParser(description="Safely verify the Money-X API contract")
    parser.add_argument("--web-url", default=MONEYX_WEB_URL)
    parser.add_argument("--wallet-sample", action="store_true")
    parser.add_argument("--expected-visible-rate", type=Decimal)
    args = parser.parse_args()
    web_url = parse_web_url(args.web_url)
    token = getpass.getpass("Money-X token (input is hidden): ").strip()
    mxi = getpass.getpass("mxi_token, or Enter to omit (input is hidden): ").strip() or None
    async with MoneyXClient(web_url, derive_api_url(web_url), token=token, mxi_token=mxi) as client:
        await client.authenticate()
        pairs = await client.list_crypto()
        print(f"Authentication OK; valid currency/network rows: {len(pairs)}")
        rates_in_list = sum(item.rate is not None for item in pairs)
        print(f"Rows with a rate already in crypto/list: {rates_in_list}")
        for pair in pairs:
            print(f"- {pair.currency} ({pair.network}); rate_in_list={pair.rate is not None}")
        if args.wallet_sample:
            print(
                "WARNING: this makes exactly one crypto/wallet request. Check account history manually."
            )
            raw = await client.wallet_rate(pairs[0])
            visible = normalize_rate(raw)
            rounded = visible.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
            print(
                f"Sample: {pairs[0].currency} ({pairs[0].network}); "
                f"raw={raw}; normalized={rounded}"
            )
            if args.expected_visible_rate is not None:
                expected = args.expected_visible_rate.quantize(
                    Decimal("0.001"), rounding=ROUND_HALF_UP
                )
                print(
                    "Formula match:",
                    rounded == expected,
                    f"(expected={expected})",
                )


if __name__ == "__main__":
    run(main())
