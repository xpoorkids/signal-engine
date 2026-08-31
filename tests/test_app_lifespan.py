import asyncio

from app import main


def test_app_lifespan_starts_and_stops_background_services(monkeypatch):
    calls = []

    async def start():
        calls.append("start")

    async def stop():
        calls.append("stop")

    monkeypatch.setattr(main, "log_storage_configuration", lambda: calls.append("log"))
    monkeypatch.setattr(main, "start_background_workers", start)
    monkeypatch.setattr(main, "stop_background_workers", stop)

    async def run():
        async with main.app_lifespan(main.app):
            calls.append("serve")

    asyncio.run(run())

    assert calls == ["log", "start", "serve", "stop"]
