from app.services.manual_position_service import ManualPositionService


def test_initial_buy_and_persistence_after_restart(tmp_path):
    service = ManualPositionService(tmp_path / "positions.db")
    position = service.mark_bought(token="token-a", symbol="AAA", token_quantity=1000, gross_usd=250, fees_usd=5)

    assert position["status"] == "open"
    assert position["current_token_quantity"] == 1000
    assert position["original_token_quantity"] == 1000
    assert position["total_cash_invested_usd"] == 255
    assert position["average_entry_price_usd"] == 0.255
    assert position["remaining_unrecovered_principal_usd"] == 255

    reloaded = ManualPositionService(tmp_path / "positions.db").get_position(position["position_id"])
    assert reloaded["token"] == "token-a"
    assert reloaded["risk_profile"] == "aggressive"
    assert reloaded["exit_style"] == "catalyst_runner"


def test_multiple_buys_update_average_entry_and_fees(tmp_path):
    service = ManualPositionService(tmp_path / "positions.db")
    position = service.mark_bought(token="token-a", token_quantity=100, gross_usd=100, fees_usd=1)
    updated = service.record_buy(position["position_id"], token_quantity=100, gross_usd=200, fees_usd=2)

    assert updated["original_token_quantity"] == 200
    assert updated["current_token_quantity"] == 200
    assert updated["total_cash_invested_usd"] == 303
    assert updated["total_fees_usd"] == 3
    assert updated["average_entry_price_usd"] == 1.515


def test_partial_sells_realized_proceeds_principal_and_full_close(tmp_path):
    service = ManualPositionService(tmp_path / "positions.db")
    position = service.mark_bought(token="token-a", token_quantity=100, gross_usd=100, fees_usd=0)
    first = service.record_sell(position["position_id"], token_quantity=25, gross_usd=75, fees_usd=5)

    assert first["current_token_quantity"] == 75
    assert first["realized_proceeds_usd"] == 70
    assert first["remaining_unrecovered_principal_usd"] == 30
    assert first["principal_recovered"] is False

    second = service.record_sell(position["position_id"], token_quantity=25, gross_usd=80, fees_usd=0)
    assert second["realized_proceeds_usd"] == 150
    assert second["remaining_unrecovered_principal_usd"] == 0
    assert second["principal_recovered"] is True

    closed = service.record_sell(position["position_id"], full=True, gross_usd=200)
    assert closed["status"] == "closed"
    assert closed["current_token_quantity"] == 0
    assert closed["closed_ts"] is not None


def test_executable_value_tracks_return_peak_and_drawdown(tmp_path):
    service = ManualPositionService(tmp_path / "positions.db")
    position = service.mark_bought(token="token-a", token_quantity=100, gross_usd=100)
    peak = service.update_executable_value(position["position_id"], executable_value_usd=200)
    lower = service.update_executable_value(position["position_id"], executable_value_usd=150)

    assert peak["current_executable_return_pct"] == 100
    assert lower["highest_executable_position_value_usd"] == 200
    assert lower["drawdown_from_executable_peak_pct"] == -25


def test_close_and_reopen_position(tmp_path):
    service = ManualPositionService(tmp_path / "positions.db")
    position = service.mark_bought(token="token-a", token_quantity=100, gross_usd=100)

    assert service.close_position(position["position_id"])["status"] == "closed"
    assert service.reopen_position(position["position_id"])["status"] == "open"


def test_position_history_and_catalyst_lifecycle(tmp_path):
    service = ManualPositionService(tmp_path / "positions.db")
    position = service.mark_bought(token="token-a", token_quantity=100, gross_usd=100)
    catalyst = service.create_catalyst(
        token="token-a",
        title="Exchange rumor confirmed",
        verification_status="verified",
        secondary_confirmations=["source-a", "source-b"],
        catalyst_confidence_pct=80,
    )
    attached = service.attach_catalyst(position["position_id"], catalyst["catalyst_id"])
    invalid = service.mark_catalyst_invalid(catalyst["catalyst_id"], "source retracted")
    history = service.position_history(position["position_id"])

    assert attached["catalyst_mode"] is True
    assert attached["catalyst_id"] == catalyst["catalyst_id"]
    assert invalid["verification_status"] == "invalidated"
    assert invalid["catalyst_invalidation_reason"] == "source retracted"
    assert len(history["fills"]) == 1
