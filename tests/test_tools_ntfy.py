"""Tests for agent/tools/ntfy.py — notification tools."""

import json

from home_ops_agent.agent.tools.ntfy import publish


async def test_publish_basic(httpx_mock, mock_settings):
    httpx_mock.add_response(status_code=200)
    result = json.loads(await publish({"message": "Test notification"}))
    assert result["status"] == "ok"
    assert result["topic"] == "home-ops-agent"

    request = httpx_mock.get_request()
    assert "home-ops-agent" in str(request.url)
    assert request.headers["Title"] == "Home-Ops Agent"
    assert request.headers["Priority"] == "3"


async def test_publish_with_token_header(httpx_mock, mock_settings):
    httpx_mock.add_response(status_code=200)
    await publish({"message": "test"})

    request = httpx_mock.get_request()
    assert "Authorization" in request.headers
    assert "Bearer" in request.headers["Authorization"]


async def test_publish_with_tags_list(httpx_mock, mock_settings):
    httpx_mock.add_response(status_code=200)
    await publish({"message": "test", "tags": ["warning", "robot"]})

    request = httpx_mock.get_request()
    assert request.headers["Tags"] == "warning,robot"


async def test_publish_with_tags_string(httpx_mock, mock_settings):
    httpx_mock.add_response(status_code=200)
    await publish({"message": "test", "tags": "white_check_mark"})

    request = httpx_mock.get_request()
    assert request.headers["Tags"] == "white_check_mark"


async def test_publish_with_click_url(httpx_mock, mock_settings):
    httpx_mock.add_response(status_code=200)
    await publish({"message": "test", "click_url": "https://github.com/pr/42"})

    request = httpx_mock.get_request()
    assert request.headers["Click"] == "https://github.com/pr/42"


async def test_publish_custom_topic(httpx_mock, mock_settings):
    httpx_mock.add_response(status_code=200)
    result = json.loads(await publish({"message": "alert!", "topic": "alertmanager"}))
    assert result["topic"] == "alertmanager"
    request = httpx_mock.get_request()
    assert "alertmanager" in str(request.url)


async def test_publish_http_error(httpx_mock, mock_settings):
    httpx_mock.add_response(status_code=500)
    result = json.loads(await publish({"message": "test"}))
    assert "error" in result
