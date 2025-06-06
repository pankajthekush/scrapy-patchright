import logging
from decimal import Decimal
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

import pytest
from patchright.async_api import Error as PlaywrightError
from scrapy import Spider
from scrapy.http.headers import Headers
from scrapy.settings import Settings
from scrapy_playwright._utils import (
    _NAVIGATION_ERROR_MSG,
    _encode_body,
    _get_float_setting,
    _get_header_value,
    _get_page_content,
    _maybe_await,
)


class TestPageContent(IsolatedAsyncioTestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self, caplog):
        caplog.set_level(logging.DEBUG)
        self._caplog = caplog

    async def test_get_page_content_ok(self):
        expected_content = "lorem ipsum"
        page = AsyncMock()
        page.content.return_value = expected_content
        content = await _get_page_content(
            page=page,
            spider=Spider("foo"),
            context_name="context",
            scrapy_request_url="https://example.org",
            scrapy_request_method="GET",
        )
        assert content == expected_content

    async def test_get_page_content_retry_known_exception(self):
        expected_content = "lorem ipsum"
        page = AsyncMock()
        page.url = "FAKE URL"
        page.content.side_effect = [PlaywrightError(_NAVIGATION_ERROR_MSG), expected_content]
        content = await _get_page_content(
            page=page,
            spider=Spider("foo"),
            context_name="context",
            scrapy_request_url="https://example.org",
            scrapy_request_method="GET",
        )
        assert content == expected_content
        assert (
            "scrapy-playwright",
            logging.DEBUG,
            f"Retrying to get content from page '{page.url}', error: 'Unable to retrieve"
            " content because the page is navigating and changing the content.'",
        ) in self._caplog.record_tuples

    async def test_get_page_content_reraise_unknown_exception(self):
        expected_exception_message = "nope"
        page = AsyncMock()
        page.content.side_effect = PlaywrightError(expected_exception_message)
        with pytest.raises(PlaywrightError, match=expected_exception_message):
            await _get_page_content(
                page=page,
                spider=Spider("foo"),
                context_name="context",
                scrapy_request_url="https://example.org",
                scrapy_request_method="GET",
            )


class TestBodyEncoding(IsolatedAsyncioTestCase):
    @staticmethod
    def body_str(charset: str, content: str = "áéíóú") -> str:
        return f"""
            <!doctype html>
            <html>
            <head>
            <meta charset="{charset}">
            </head>
            <body>
            <p>{content}</p>
            </body>
            </html>
        """.strip()

    async def test_encode_from_headers(self):
        """Charset declared in headers takes precedence"""
        text = self.body_str(charset="gb2312")
        body, encoding = _encode_body(
            headers=Headers({"content-type": "text/html; charset=ISO-8859-1"}),
            text=text,
        )
        assert encoding == "cp1252"
        assert body == text.encode(encoding)

    async def test_encode_from_body(self):
        """No charset declared in headers, use the one declared in the body"""
        text = self.body_str(charset="gb2312")
        body, encoding = _encode_body(headers=Headers({}), text=text)
        assert encoding == "gb18030"
        assert body == text.encode(encoding)

    async def test_encode_fallback_utf8(self):
        """No charset declared, use utf-8 as fallback"""
        text = "<html>áéíóú</html>"
        body, encoding = _encode_body(headers=Headers(), text=text)
        assert encoding == "utf-8"
        assert body == text.encode(encoding)

    async def test_encode_mismatch(self):
        """Charset declared in headers and body do not match, and the headers
        one fails to encode: use the one in the body (first one that works)
        """
        text = self.body_str(charset="gb2312", content="空手道")
        body, encoding = _encode_body(
            headers=Headers({"content-type": "text/html; charset=ISO-8859-1"}),
            text=text,
        )
        assert encoding == "gb18030"
        assert body == text.encode(encoding)


class TestHeaderValue(IsolatedAsyncioTestCase):
    async def test_get_header_value(self):
        async def _identity(x):
            return x

        res1 = AsyncMock()
        res1.header_value = _identity
        assert "asdf" == await _get_header_value(res1, "asdf")
        assert "qwerty" == await _get_header_value(res1, "qwerty")

        res2 = AsyncMock()
        res2.header_value.side_effect = Exception("nope")
        assert await _get_header_value(res2, "asdf") is None
        assert await _get_header_value(res2, "qwerty") is None


class TestMaybeAwait(IsolatedAsyncioTestCase):
    async def test_maybe_await(self):
        async def _awaitable_identity(x):
            return x

        assert await _maybe_await(_awaitable_identity("asdf")) == "asdf"
        assert await _maybe_await(_awaitable_identity("qwerty")) == "qwerty"
        assert await _maybe_await(_awaitable_identity(1234)) == 1234
        assert await _maybe_await("foo") == "foo"
        assert await _maybe_await("bar") == "bar"
        assert await _maybe_await(1234) == 1234


class TestGetFloatSetting(IsolatedAsyncioTestCase):
    async def test_get_float_setting(self):
        settings = Settings(
            {
                "ZERO": 0,
                "FLOAT": 1.5,
                "DECIMAL": Decimal("2.5"),
                "INT": 3,
                "NUMERIC_STRING": "123",
                "NON_NUMERIC_STRING": "asdf",
                "NONE": None,
                "LIST": [1, 2, 3],
            }
        )
        assert _get_float_setting(settings, "ZERO") == 0.0
        assert _get_float_setting(settings, "FLOAT") == 1.5
        assert _get_float_setting(settings, "DECIMAL") == 2.5
        assert _get_float_setting(settings, "INT") == 3.0
        assert _get_float_setting(settings, "NUMERIC_STRING") == 123
        assert _get_float_setting(settings, "NON_NUMERIC_STRING") is None
        assert _get_float_setting(settings, "NONE") is None
        assert _get_float_setting(settings, "LIST") is None
        assert _get_float_setting(settings, "MISSING_KEY") is None
