def test_duplicate_token_mints_are_not_checked_twice():
    pools = [
        make_pool(mint="TOKEN_A"),
        make_pool(mint="TOKEN_A"),
        make_pool(mint="TOKEN_B"),
    ]

    def fake_security(mint):
        return make_security(mint=mint)

    def fake_holder(mint, creator_address=None):
        return make_holder_snapshot(mint=mint)

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=pools,
    ), patch(
        "desk.security_scan.check_token_security",
        side_effect=fake_security,
    ) as mocked_security, patch(
        "desk.security_scan.fetch_holder_snapshot",
        side_effect=fake_holder,
    ) as mocked_holder:
        result = scan_pool_security([{}], max_checks=2)

    assert result["checks_attempted"] == 2
    assert result["holder_checks_attempted"] == 2

    assert mocked_security.call_count == 2
    assert [
        call.args[0] for call in mocked_security.call_args_list
    ] == ["TOKEN_A", "TOKEN_B"]

    assert mocked_holder.call_count == 2
    assert [
        call.args[0] for call in mocked_holder.call_args_list
    ] == ["TOKEN_A", "TOKEN_B"]
