from spread_radar.exchanges import _depth_usd


def test_depth_uses_only_valid_top_five_book_levels() -> None:
    depth, levels = _depth_usd(
        [
            [2.0, 3.0],
            [1.5, 4.0],
            [0, 100],
            [1.0, -1],
            [1.0, 1.0],
            [9.0, 9.0],
        ]
    )

    assert depth == 13.0
    assert levels == 3
