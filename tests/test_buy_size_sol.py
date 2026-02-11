from worker.helius_listener import LogSwapProcessor


def _make_tx(pre_balances, post_balances, fee, keys, pre_tokens=None, post_tokens=None):
    return {
        "transaction": {"message": {"accountKeys": keys}},
        "meta": {
            "preBalances": pre_balances,
            "postBalances": post_balances,
            "fee": fee,
            "preTokenBalances": pre_tokens or [],
            "postTokenBalances": post_tokens or [],
        },
        "slot": 1,
    }


def test_buy_size_sol_signer_spent():
    proc = LogSwapProcessor(lambda e: None)
    keys = [
        {"pubkey": "A", "signer": True, "writable": True},
        {"pubkey": "B", "signer": True, "writable": True},
    ]
    pre = [10_000_000_000, 5_000_000_000]
    post = [8_000_000_000, 5_000_000_000]
    tx = _make_tx(pre, post, 5_000, keys)
    buyer, idx, sol, method, fee = proc._find_buyer_signer(tx)
    assert buyer == "A"
    assert idx == 0
    assert method == "sol"
    assert sol > 1.9


def test_buy_size_sol_wsol_fallback():
    proc = LogSwapProcessor(lambda e: None)
    keys = [{"pubkey": "A", "signer": True, "writable": True}]
    pre = [1_000_000_000]
    post = [1_000_000_000]
    pre_tokens = [
        {"mint": "So11111111111111111111111111111111111111112", "owner": "A", "uiTokenAmount": {"amount": "200000000"}}
    ]
    post_tokens = [
        {"mint": "So11111111111111111111111111111111111111112", "owner": "A", "uiTokenAmount": {"amount": "100000000"}}
    ]
    tx = _make_tx(pre, post, 0, keys, pre_tokens, post_tokens)
    buyer, idx, sol, method, fee = proc._find_buyer_signer(tx)
    assert buyer == "A"
    assert method == "wsol"
    assert sol > 0.09
