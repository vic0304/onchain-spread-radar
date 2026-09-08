from spread_radar.security import _parse_profile


def test_tax_profile_parses_fee_on_transfer_tax_as_bps() -> None:
    profile = _parse_profile({"buy_tax": "0.025", "sell_tax": "0.04"})

    assert profile.risk_reason is None
    assert profile.buy_tax_bps == 250
    assert profile.sell_tax_bps == 400


def test_tax_profile_rejects_unknown_or_modifiable_tax() -> None:
    assert _parse_profile({"buy_tax": "", "sell_tax": "0"}).risk_reason == "unknown tax"
    assert _parse_profile(
        {"buy_tax": "0", "sell_tax": "0", "slippage_modifiable": "1"}
    ).risk_reason == "modifiable tax"
