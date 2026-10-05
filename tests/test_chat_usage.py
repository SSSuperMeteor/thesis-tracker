"""Usage levels, the editable price file, and the read-only balance check."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def store(tmp_path):
    from thesis_tracker.webapp.chat.store import ChatStore

    return ChatStore(tmp_path / "chat.db")


def add_turn(store, conversation_id, *, incoming, outgoing, cached, model="deepseek-flash"):
    message = store.append_message(conversation_id, role="user", text="问题")
    store.append_model_call(message["message_id"], round_no=1,
                            requested_model=model, returned_model=model,
                            fingerprint="fp", input_tokens=incoming,
                            output_tokens=outgoing, cache_hit_tokens=cached)
    return message


# -- the price file ----------------------------------------------------------

def test_prices_are_read_from_the_editable_file(tmp_path):
    from thesis_tracker.webapp.chat.pricing import load_pricing

    path = tmp_path / "pricing.json"
    path.write_text(json.dumps({"input_per_million": 0.30, "output_per_million": 1.20,
                                "cache_hit_per_million": 0.006,
                                "source_url": "https://api-docs.deepseek.com/quick_start/pricing/",
                                "as_of_date": "2026-10-05"}), encoding="utf-8")
    pricing = load_pricing(path)
    assert pricing.input_per_million == 0.30
    assert pricing.output_per_million == 1.20
    assert pricing.cache_hit_per_million == 0.006
    assert pricing.as_of_date == "2026-10-05"
    assert pricing.source_url.endswith("/quick_start/pricing/")


def test_a_missing_price_file_yields_no_prices(tmp_path):
    from thesis_tracker.webapp.chat.pricing import load_pricing

    assert load_pricing(tmp_path / "absent.json") is None


@pytest.mark.parametrize("payload", [
    {}, {"input_per_million": 0.3}, {"input_per_million": 0.3, "output_per_million": 1.2},
    {"input_per_million": "0.3", "output_per_million": 1.2, "cache_hit_per_million": 0.006},
    {"input_per_million": 0.3, "output_per_million": None, "cache_hit_per_million": 0.006},
])
def test_an_incomplete_price_file_yields_no_prices(tmp_path, payload):
    """A partial table is unusable: half a price is a wrong amount."""
    from thesis_tracker.webapp.chat.pricing import load_pricing

    path = tmp_path / "pricing.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_pricing(path) is None


def test_a_broken_price_file_yields_no_prices(tmp_path):
    from thesis_tracker.webapp.chat.pricing import load_pricing

    path = tmp_path / "pricing.json"
    path.write_text("{ not json", encoding="utf-8")
    assert load_pricing(path) is None


def test_the_estimate_counts_cache_hits_at_the_cache_rate(tmp_path):
    from thesis_tracker.webapp.chat.pricing import load_pricing

    path = tmp_path / "pricing.json"
    path.write_text(json.dumps({"input_per_million": 0.30, "output_per_million": 1.20,
                                "cache_hit_per_million": 0.006}), encoding="utf-8")
    pricing = load_pricing(path)
    # 1000 input of which 900 hit the cache, plus 100 output.
    amount = pricing.estimate(input_tokens=1000, output_tokens=100,
                              cache_hit_tokens=900)
    expected = (100 * 0.30 + 900 * 0.006 + 100 * 1.20) / 1_000_000
    assert amount == pytest.approx(expected)
    # A cache count larger than the input can never inflate the bill.
    capped = pricing.estimate(input_tokens=100, output_tokens=0, cache_hit_tokens=999)
    assert capped == pytest.approx(100 * 0.006 / 1_000_000)


def test_the_amount_is_shown_with_its_basis(tmp_path):
    from thesis_tracker.webapp.chat.pricing import cost_label, load_pricing

    path = tmp_path / "pricing.json"
    path.write_text(json.dumps({"input_per_million": 0.30, "output_per_million": 1.20,
                                "cache_hit_per_million": 0.006,
                                "as_of_date": "2026-10-05"}), encoding="utf-8")
    label = cost_label(0.0123, load_pricing(path))
    assert label.startswith("约 $0.012")
    assert "实际可能更低" in label
    assert "2026-10-05" in label


def test_without_prices_only_tokens_are_offered():
    from thesis_tracker.webapp.chat.pricing import cost_label

    assert cost_label(None, None) == "未找到价格配置，只显示 token"


def test_the_image_the_app_ships_with_can_always_price_a_call():
    """Without a local file the committed template still yields amounts."""
    from thesis_tracker.webapp.chat.pricing import TEMPLATE_PRICING_PATH, load_pricing

    template = load_pricing(TEMPLATE_PRICING_PATH)
    assert template is not None
    assert template.input_per_million > 0
    assert template.output_per_million > template.input_per_million
    assert "deepseek.com" in (template.source_url or "")
    assert template.as_of_date
    assert template.off_peak_input_per_million == template.input_per_million / 2
    # The default lookup resolves to something priceable on any checkout.
    assert load_pricing() is not None


# -- the four levels ---------------------------------------------------------

def test_the_four_levels_are_scoped_and_labelled(store, tmp_path):
    from thesis_tracker.webapp.chat.usage import usage_summary

    nvda = store.create_conversation("NVDA")
    other = store.create_conversation("NVDA")
    aapl = store.create_conversation("AAPL")
    message = add_turn(store, nvda["conversation_id"], incoming=1000, outgoing=100,
                       cached=900)
    add_turn(store, nvda["conversation_id"], incoming=200, outgoing=20, cached=0)
    add_turn(store, other["conversation_id"], incoming=3000, outgoing=300, cached=0)
    add_turn(store, aapl["conversation_id"], incoming=7000, outgoing=700, cached=0)
    summary = usage_summary(store, conversation_id=nvda["conversation_id"],
                            message_id=message["message_id"], ticker="NVDA",
                            pricing_path=tmp_path / "none.json")
    assert summary["levels"]["message"]["input_tokens"] == 1000
    assert summary["levels"]["conversation"]["input_tokens"] == 1200
    assert summary["levels"]["company"]["input_tokens"] == 4200
    assert summary["levels"]["today"]["input_tokens"] == 11200
    assert summary["levels"]["company"]["scope"] == "该公司全部对话"
    assert summary["levels"]["today"]["scope"] == "今天"
    assert summary["pricing_available"] is False


def test_the_company_level_never_mixes_in_another_company(store, tmp_path):
    from thesis_tracker.webapp.chat.usage import usage_summary

    nvda = store.create_conversation("NVDA")
    aapl = store.create_conversation("AAPL")
    add_turn(store, nvda["conversation_id"], incoming=100, outgoing=10, cached=0)
    add_turn(store, aapl["conversation_id"], incoming=900, outgoing=90, cached=0)
    summary = usage_summary(store, conversation_id=nvda["conversation_id"],
                            ticker="NVDA", pricing_path=tmp_path / "none.json")
    assert summary["levels"]["company"]["input_tokens"] == 100
    assert summary["levels"]["today"]["input_tokens"] == 1000


def test_the_company_level_defaults_to_the_conversations_own_company(store, tmp_path):
    from thesis_tracker.webapp.chat.usage import usage_summary

    conversation = store.create_conversation("MSFT")
    add_turn(store, conversation["conversation_id"], incoming=50, outgoing=5, cached=0)
    summary = usage_summary(store, conversation_id=conversation["conversation_id"],
                            pricing_path=tmp_path / "none.json")
    assert summary["levels"]["company"]["input_tokens"] == 50


def test_today_uses_the_local_calendar_day(store, tmp_path):
    from datetime import datetime, timezone

    from thesis_tracker.webapp import display
    from thesis_tracker.webapp.chat.usage import start_of_local_day, usage_summary

    display.LOCAL_TIMEZONE = timezone.utc
    try:
        conversation = store.create_conversation("NVDA")
        add_turn(store, conversation["conversation_id"], incoming=100, outgoing=10,
                 cached=0)
        now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
        assert start_of_local_day(now) == "2026-10-05T00:00:00+00:00"
        inside = usage_summary(store, conversation_id=conversation["conversation_id"],
                               ticker="NVDA", pricing_path=tmp_path / "none.json",
                               now=now)
        assert inside["levels"]["today"]["input_tokens"] == 100
        # A "now" far in the future has no spend in its own day.
        later = usage_summary(store, conversation_id=conversation["conversation_id"],
                              ticker="NVDA", pricing_path=tmp_path / "none.json",
                              now=datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc))
        assert later["levels"]["today"]["input_tokens"] == 0
        assert later["levels"]["conversation"]["input_tokens"] == 100
    finally:
        display.LOCAL_TIMEZONE = None


def test_with_prices_every_level_carries_an_amount(store, tmp_path):
    from thesis_tracker.webapp.chat.usage import usage_summary

    pricing_path = tmp_path / "pricing.json"
    pricing_path.write_text(json.dumps({"input_per_million": 0.30,
                                        "output_per_million": 1.20,
                                        "cache_hit_per_million": 0.006,
                                        "as_of_date": "2026-10-05"}), encoding="utf-8")
    conversation = store.create_conversation("NVDA")
    message = add_turn(store, conversation["conversation_id"], incoming=1_000_000,
                       outgoing=1_000_000, cached=0)
    summary = usage_summary(store, conversation_id=conversation["conversation_id"],
                            message_id=message["message_id"], ticker="NVDA",
                            pricing_path=pricing_path)
    assert summary["pricing_available"] is True
    assert summary["levels"]["message"]["cost_usd"] == pytest.approx(1.50)
    assert summary["levels"]["message"]["cost_text"].startswith("约 $1.500")
    assert summary["price_as_of"] == "2026-10-05"


def test_the_analysis_average_comes_from_finished_jobs(tmp_path):
    from thesis_tracker.webapp.chat.usage import analysis_average
    from thesis_tracker.webapp.jobs import JobStore

    jobs = JobStore(tmp_path / "jobs.db")
    for index in range(6):
        job = jobs.create("analyze", {"ticker": "NVDA"})
        jobs.finish(job["job_id"], status="succeeded",
                    result={"usage": {"input_tokens": 1000 * (index + 1),
                                      "output_tokens": 100}})
    failed = jobs.create("analyze", {"ticker": "NVDA"})
    jobs.finish(failed["job_id"], status="failed", result=None)
    average = analysis_average(jobs, window=5)
    # The newest five: 2000..6000 input, 100 output each.
    assert average["analyses"] == 5
    assert average["input_tokens"] == 4000
    assert average["output_tokens"] == 100


def test_without_any_analysis_the_average_says_so(tmp_path):
    from thesis_tracker.webapp.chat.usage import analysis_average
    from thesis_tracker.webapp.jobs import JobStore

    average = analysis_average(JobStore(tmp_path / "jobs.db"))
    assert average == {"analyses": 0, "input_tokens": None, "output_tokens": None}


# -- the balance check -------------------------------------------------------

def test_the_balance_is_read_from_the_documented_endpoint(monkeypatch):
    from thesis_tracker.webapp.chat import balance as balance_module

    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["auth"] = request.get_header("Authorization")
        captured["timeout"] = timeout

        class Response:
            def read(self):
                return json.dumps({
                    "is_available": True,
                    "balance_infos": [{"currency": "USD", "total_balance": "12.34",
                                       "granted_balance": "0.00",
                                       "topped_up_balance": "12.34"}]}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False
        return Response()

    monkeypatch.setattr(balance_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-sentinel-must-not-leak")
    result = balance_module.fetch_balance()
    assert captured["url"] == "https://api.deepseek.com/user/balance"
    assert captured["method"] == "GET"
    assert captured["auth"] == "Bearer sk-sentinel-must-not-leak"
    assert result["is_available"] is True
    assert result["balances"][0]["currency"] == "USD"
    assert result["balances"][0]["total_balance"] == "12.34"
    assert "sk-sentinel-must-not-leak" not in json.dumps(result)


def test_a_balance_failure_is_reported_not_hidden(monkeypatch):
    from thesis_tracker.webapp.chat import balance as balance_module

    def boom(request, timeout=None):
        raise OSError("network down")

    monkeypatch.setattr(balance_module.urllib.request, "urlopen", boom)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
    result = balance_module.fetch_balance()
    assert result["is_available"] is None
    assert result["balances"] == []
    assert result["error"]


def test_without_a_key_the_balance_is_not_requested(monkeypatch):
    from thesis_tracker.webapp.chat import balance as balance_module

    def forbidden(*args, **kwargs):
        raise AssertionError("must not call out without a key")

    monkeypatch.setattr(balance_module.urllib.request, "urlopen", forbidden)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(balance_module, "api_key", lambda: None)
    result = balance_module.fetch_balance()
    assert result["is_available"] is None
    assert result["error"] == "未配置 API key，无法查询余额"
