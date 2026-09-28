"""Тести ручного запуску звітів (python -m app.cli)."""
import dataclasses
import json
from datetime import datetime, timezone

import pytest

from app import cli
from app.reports import Digest, MarketUnavailable

NOW = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)


class FakeNotifier:
    instances: list["FakeNotifier"] = []

    def __init__(self, token, chat_ids):
        self.sent: list[str] = []
        FakeNotifier.instances.append(self)

    async def broadcast(self, text):
        self.sent.append(text)

    async def aclose(self):
        pass


@pytest.fixture
def cli_env(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    test_config = dataclasses.replace(
        cli.config,
        api_base="https://api.test",
        regions=["EU"],
        state_path=str(state_path),
        tg_token="1:x",
        tg_chat_ids=["@test"],
    )
    monkeypatch.setattr(cli, "config", test_config)
    FakeNotifier.instances = []
    monkeypatch.setattr(cli, "Notifier", FakeNotifier)
    return state_path


def test_unknown_command_is_rejected():
    with pytest.raises(SystemExit):
        cli.parse_args(["sell-everything"])


def test_flags_are_parsed():
    args = cli.parse_args(["digest", "--region", "RU", "--dry-run"])
    assert (args.command, args.region, args.dry_run) == ("digest", "RU", True)


@pytest.mark.asyncio
async def test_digest_is_sent_to_channel(cli_env, monkeypatch, capsys):
    async def fake_digest(api, region, market, now):
        return Digest(["📊 дайджест"], {"a1:0": {"median": 1, "count": 9}})

    monkeypatch.setattr(cli, "build_digest", fake_digest)
    assert await cli.main(["digest"], now=NOW) == 0

    assert FakeNotifier.instances[0].sent == ["📊 дайджест"]
    assert "📊 дайджест" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_dry_run_does_not_send(cli_env, monkeypatch, capsys):
    async def fake_digest(api, region, market, now):
        return Digest(["📊 дайджест"], {})

    monkeypatch.setattr(cli, "build_digest", fake_digest)
    assert await cli.main(["digest", "--dry-run"], now=NOW) == 0

    assert FakeNotifier.instances == []  # навіть не створювали відправника
    assert "📊 дайджест" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_manual_digest_compares_with_saved_snapshot_but_never_overwrites_it(
    cli_env, monkeypatch
):
    """Інакше ручний запуск зсунув би щоденний розклад і базу порівняння."""
    saved = {"EU": {"market": {"taken_at": "2026-09-27T17:00:00Z", "prices": {"a1:0": {"median": 5, "count": 9}}}}}
    cli_env.write_text(json.dumps(saved), encoding="utf-8")
    seen = []

    async def fake_digest(api, region, market, now):
        seen.append(market)
        return Digest(["📊"], {"a1:0": {"median": 999, "count": 9}})

    monkeypatch.setattr(cli, "build_digest", fake_digest)
    await cli.main(["digest"], now=NOW)

    assert seen == [saved["EU"]["market"]]
    assert json.loads(cli_env.read_text(encoding="utf-8")) == saved


@pytest.mark.asyncio
async def test_top_after_last_emission(cli_env, monkeypatch):
    since = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)
    until = datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc)
    windows = []

    async def fake_window(api, region, now, delay):
        return since, until

    async def fake_top(api, region, s, u):
        windows.append((s, u))
        return "📦 топ"

    monkeypatch.setattr(cli, "last_emission_window", fake_window)
    monkeypatch.setattr(cli, "build_top_report", fake_top)
    assert await cli.main(["top"], now=NOW) == 0

    assert windows == [(since, until)]
    assert FakeNotifier.instances[0].sent == ["📦 топ"]


@pytest.mark.asyncio
async def test_top_with_nothing_listed_sends_nothing(cli_env, monkeypatch, capsys):
    async def fake_window(api, region, now, delay):
        return NOW, NOW

    async def fake_top(api, region, s, u):
        return None

    monkeypatch.setattr(cli, "last_emission_window", fake_window)
    monkeypatch.setattr(cli, "build_top_report", fake_top)
    assert await cli.main(["top"], now=NOW) == 0

    assert FakeNotifier.instances[0].sent == []
    assert "нема що надсилати" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_top_without_emission_data_fails_with_exit_code(cli_env, monkeypatch):
    async def fake_window(api, region, now, delay):
        return None

    monkeypatch.setattr(cli, "last_emission_window", fake_window)
    assert await cli.main(["top"], now=NOW) == 1


@pytest.mark.asyncio
async def test_market_down_fails_with_exit_code(cli_env, monkeypatch):
    async def fake_digest(api, region, market, now):
        raise MarketUnavailable("EU: не вдалося отримати лоти 90 з 103 артефактів")

    monkeypatch.setattr(cli, "build_digest", fake_digest)
    assert await cli.main(["digest"], now=NOW) == 1


@pytest.mark.asyncio
async def test_region_flag_limits_to_one_region(cli_env, monkeypatch):
    monkeypatch.setattr(cli, "config", dataclasses.replace(cli.config, regions=["EU", "RU"]))
    regions = []

    async def fake_digest(api, region, market, now):
        regions.append(region)
        return Digest(["x"], {})

    monkeypatch.setattr(cli, "build_digest", fake_digest)
    await cli.main(["digest", "--region", "RU", "--dry-run"], now=NOW)
    assert regions == ["RU"]
