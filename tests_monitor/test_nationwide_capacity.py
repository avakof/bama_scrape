"""Offline coverage of the nationwide detail plan and its concurrency limit."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from monitor_helpers import make_context

from bama_monitor.inventory_comparison import StudyObservation
from bama_monitor.models import DetailResult, DetailVerdict
from bama_monitor.runner import DailyRunner
from bama_monitor.scrapers import BamaDetailScraper


@pytest.mark.asyncio
async def test_finite_budget_covers_both_detail_resources_for_each_planned_target(cfg):
    from bama_deep.config import DeepConfig

    async with BamaDetailScraper(cfg, target_count=4501) as scraper:
        assert scraper._deep_cfg.max_requests == 9002
    async with BamaDetailScraper(cfg) as legacy:
        assert legacy._deep_cfg.max_requests == DeepConfig().max_requests


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", [False, True])
async def test_bounded_checks_keep_target_order_and_stop_on_block(cfg, db, blocked):
    cfg.detail.concurrency = 2
    cfg.detail.max_details_per_run = 1
    cfg.detail.check_every_advertisement_daily = True
    started: list[str] = []
    finished_second = asyncio.Event()
    active = peak = 0

    class Scraper:
        async def scrape(self, url):
            nonlocal active, peak
            started.append(url)
            active += 1
            peak = max(peak, active)
            try:
                if url == "a":
                    await finished_second.wait()
                if url == "b":
                    finished_second.set()
                    if blocked:
                        return DetailResult(url, url, False, DetailVerdict.BLOCKED)
                await asyncio.sleep(0)
                return DetailResult(url, url, False, DetailVerdict.HTTP_410)
            finally:
                active -= 1

    @asynccontextmanager
    async def factory():
        yield Scraper()

    runner = DailyRunner(cfg, db=db, detail_scraper_factory=factory)
    context = make_context(runner.repo, cfg, day=1)
    candidates = [StudyObservation(key, key) for key in ("a", "b")]
    absent = [StudyObservation(key, key) for key in ("c", "d")]
    missing = [
        {"id": index, "platform_ad_id": obs.platform_ad_id, "canonical_url": obs.url}
        for index, obs in enumerate(absent, 1)
    ]
    checks = await asyncio.wait_for(
        runner._check_study_details(context, candidates, absent, missing), timeout=2
    )
    expected = ["a", "b"] if blocked else ["a", "b", "c", "d"]
    assert started == expected
    assert [obs.url for obs, *_ in checks] == expected
    assert peak == 2


@pytest.mark.asyncio
async def test_html_block_is_reported_and_stops_the_shared_detail_fetcher(cfg, monkeypatch):
    from bama_deep import ad_api, net

    async def get_json(self, url):
        return {}

    async def get_html(self, url):
        raise net.BlockedError("HTTP 429")

    monkeypatch.setattr(net.DeepFetcher, "fetch_json", get_json)
    monkeypatch.setattr(net.DeepFetcher, "fetch_html", get_html)
    monkeypatch.setattr(ad_api, "parse_ad_api", lambda data, now_ts: {})
    async with BamaDetailScraper(cfg, target_count=1) as scraper:
        result = await scraper.scrape("https://bama.ir/car/detail-abcd1234-example")
        assert result.verdict is DetailVerdict.BLOCKED
        assert not result.ok and scraper._fetcher.aborted
