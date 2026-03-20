from worker.creator_score import compute_creator_score


def test_compute_creator_score_preserves_expected_default_shape():
    result = compute_creator_score(
        "creator-1",
        stats={"deploys_24h": 1, "deploys_lifetime": 5, "first_seen": 0},
        funded_by_cluster=False,
        prior_profitable=True,
        wallet_age_days=45,
    )

    assert result["score"] == 1.0
    assert "prior_profitable" in result["reasons"]
    assert "wallet_age>30d" in result["reasons"]
    assert "low_deploy_frequency" in result["reasons"]


def test_compute_creator_score_applies_penalties_from_policy_defaults():
    result = compute_creator_score(
        "creator-2",
        stats={"deploys_24h": 8, "deploys_lifetime": 1, "first_seen": 0},
        funded_by_cluster=True,
        prior_profitable=False,
        wallet_age_days=2,
    )

    assert result["score"] == 0.0
    assert "deploys_24h>5" in result["reasons"]
    assert "deploys_lifetime<2" in result["reasons"]
    assert "funded_by_cluster" in result["reasons"]
