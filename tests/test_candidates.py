from spread_radar.candidates import build_okx_auth_headers, parse_geckoterminal_payload


def test_okx_auth_headers_sign_the_exact_get_path_and_query() -> None:
    headers = build_okx_auth_headers(
        access_key="key",
        secret_key="secret",
        passphrase="passphrase",
        method="GET",
        path="/api/v6/dex/market/token/hot-token",
        params={"rankingType": "4", "chainIndex": "56"},
        timestamp="2026-09-05T12:00:00.000Z",
    )

    assert headers["OK-ACCESS-KEY"] == "key"
    assert headers["OK-ACCESS-PASSPHRASE"] == "passphrase"
    assert headers["OK-ACCESS-TIMESTAMP"] == "2026-09-05T12:00:00.000Z"
    assert headers["OK-ACCESS-SIGN"] == "1loNYpdWKVsUBOf0yq+qhmuOPtz6r8TmQdOZtJgfonU="


def test_parse_gecko_filters_stables_and_keeps_deepest_volume() -> None:
    payload = {
        "data": [
            {
                "attributes": {
                    "base_token_price_usd": "1",
                    "volume_usd": {"h24": "100000"},
                    "reserve_in_usd": "1000",
                },
                "relationships": {"base_token": {"data": {"id": "bsc_usdt"}}},
            },
            {
                "attributes": {
                    "base_token_price_usd": "2.0",
                    "volume_usd": {"h24": "5000"},
                    "reserve_in_usd": "2000",
                },
                "relationships": {"base_token": {"data": {"id": "bsc_foo"}}},
            },
            {
                "attributes": {
                    "base_token_price_usd": "2.1",
                    "volume_usd": {"h24": "9000"},
                    "reserve_in_usd": "3000",
                },
                "relationships": {"base_token": {"data": {"id": "bsc_foo"}}},
            },
        ],
        "included": [
            {"id": "bsc_usdt", "type": "token", "attributes": {"symbol": "USDT", "address": "0x1"}},
            {"id": "bsc_foo", "type": "token", "attributes": {"symbol": "foo", "address": "0x2"}},
        ],
    }

    candidates = parse_geckoterminal_payload(payload, "bsc", 100)

    assert len(candidates) == 1
    assert candidates[0].symbol == "FOO"
    assert candidates[0].volume_24h_usd == 9000
    assert candidates[0].onchain_price_usd == 2.1
