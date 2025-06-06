import logging
import platform
import subprocess
from tempfile import NamedTemporaryFile
from unittest import IsolatedAsyncioTestCase

import pytest
from scrapy import Spider, Request
from scrapy.http.response.html import HtmlResponse

from patchright.async_api import Page
from scrapy_playwright.page import PageMethod

from tests import allow_windows, make_handler, assert_correct_response
from tests.mockserver import StaticMockServer


def get_mimetype(file):
    return subprocess.run(
        ["file", "--mime-type", "--brief", file.name],
        stdout=subprocess.PIPE,
        universal_newlines=True,
        check=False,
    ).stdout.strip()


class TestPageMethods(IsolatedAsyncioTestCase):
    @allow_windows
    async def test_page_methods(self):
        screenshot = PageMethod("screenshot", "foo", 123, path="/tmp/file", type="png")
        assert screenshot.method == "screenshot"
        assert screenshot.args == ("foo", 123)
        assert screenshot.kwargs == {"path": "/tmp/file", "type": "png"}
        assert screenshot.result is None
        assert str(screenshot) == "<PageMethod for method 'screenshot'>"


class MixinPageMethodTestCase:
    @pytest.fixture(autouse=True)
    def inject_fixtures(self, caplog):
        caplog.set_level(logging.DEBUG)
        self._caplog = caplog

    @allow_windows
    async def test_page_non_page_method(self):
        async with make_handler({"PLAYWRIGHT_BROWSER_TYPE": self.browser_type}) as handler:
            with StaticMockServer() as server:
                req = Request(
                    url=server.urljoin("/index.html"),
                    meta={
                        "playwright": True,
                        "playwright_page_methods": [
                            "not-a-page-method",
                            5,
                            None,
                        ],
                    },
                )
                resp = await handler._download_request(req, Spider("foo"))

        assert_correct_response(resp, req)
        for obj in req.meta["playwright_page_methods"]:
            assert (
                "scrapy-playwright",
                logging.WARNING,
                f"Ignoring {repr(obj)}: expected PageMethod, got {repr(type(obj))}",
            ) in self._caplog.record_tuples

    @allow_windows
    async def test_page_mixed_page_methods(self):
        async with make_handler({"PLAYWRIGHT_BROWSER_TYPE": self.browser_type}) as handler:
            with StaticMockServer() as server:
                req = Request(
                    url=server.urljoin("/index.html"),
                    meta={
                        "playwright": True,
                        "playwright_page_methods": {
                            "does_not_exist": PageMethod("does_not_exist"),
                            "is_closed": PageMethod("is_closed"),  # not awaitable
                            "title": PageMethod("title"),  # awaitable
                        },
                    },
                )
                resp = await handler._download_request(req, Spider("foo"))

        assert_correct_response(resp, req)
        does_not_exist = req.meta["playwright_page_methods"]["does_not_exist"]
        assert (
            "scrapy-playwright",
            logging.WARNING,
            f"Ignoring {repr(does_not_exist)}: could not find method",
        ) in self._caplog.record_tuples
        assert not req.meta["playwright_page_methods"]["is_closed"].result
        assert req.meta["playwright_page_methods"]["title"].result == "Awesome site"

    @allow_windows
    async def test_page_method_navigation(self):
        async with make_handler({"PLAYWRIGHT_BROWSER_TYPE": self.browser_type}) as handler:
            with StaticMockServer() as server:
                req = Request(
                    url=server.urljoin("/index.html"),
                    meta={
                        "playwright": True,
                        "playwright_page_methods": [PageMethod("click", "a.lorem_ipsum")],
                    },
                )
                resp = await handler._download_request(req, Spider("foo"))

            assert isinstance(resp, HtmlResponse)
            assert resp.request is req
            assert resp.url == server.urljoin("/lorem_ipsum.html")
            assert resp.status == 200
            assert "playwright" in resp.flags
            assert resp.css("title::text").get() == "Lorem Ipsum"
            text = resp.css("p::text").get()
            assert text == "Lorem ipsum dolor sit amet, consectetur adipiscing elit."

    @allow_windows
    async def test_page_method_infinite_scroll(self):
        async with make_handler({"PLAYWRIGHT_BROWSER_TYPE": self.browser_type}) as handler:
            with StaticMockServer() as server:
                req = Request(
                    url=server.urljoin("/scroll.html"),
                    headers={"User-Agent": "scrapy-playwright"},
                    meta={
                        "playwright": True,
                        "playwright_page_methods": [
                            PageMethod("wait_for_selector", selector="div.quote"),
                            PageMethod("evaluate", "window.scrollBy(0, document.body.scrollHeight)"),
                            PageMethod("wait_for_selector", selector="div.quote:nth-child(11)"),
                            PageMethod("evaluate", "window.scrollBy(0, document.body.scrollHeight)"),
                            PageMethod("wait_for_selector", selector="div.quote:nth-child(21)"),
                        ],
                    },
                )
                resp = await handler._download_request(req, Spider("foo"))

            assert_correct_response(resp, req)
            assert len(resp.css("div.quote")) == 30

    @allow_windows
    async def test_page_method_screenshot(self):
        async with make_handler({"PLAYWRIGHT_BROWSER_TYPE": self.browser_type}) as handler:
            with NamedTemporaryFile(mode="w+b", delete=False) as png_file:
                with StaticMockServer() as server:
                    req = Request(
                        url=server.urljoin("/index.html"),
                        meta={
                            "playwright": True,
                            "playwright_page_methods": {
                                "png": PageMethod("screenshot", path=png_file.name, type="png"),
                            },
                        },
                    )
                    await handler._download_request(req, Spider("foo"))

                png_file.file.seek(0)
                assert png_file.file.read() == req.meta["playwright_page_methods"]["png"].result
                if platform.system() != "Windows":
                    assert get_mimetype(png_file) == "image/png"

    @allow_windows
    async def test_page_method_pdf(self):
        if self.browser_type != "chromium":
            pytest.skip("PDF generation is supported only in Chromium")

        async with make_handler({"PLAYWRIGHT_BROWSER_TYPE": self.browser_type}) as handler:
            with NamedTemporaryFile(mode="w+b", delete=False) as pdf_file:
                with StaticMockServer() as server:
                    req = Request(
                        url=server.urljoin("/index.html"),
                        meta={
                            "playwright": True,
                            "playwright_page_methods": {
                                "pdf": PageMethod("pdf", path=pdf_file.name),
                            },
                        },
                    )
                    await handler._download_request(req, Spider("foo"))

                pdf_file.file.seek(0)
                assert pdf_file.file.read() == req.meta["playwright_page_methods"]["pdf"].result
                if platform.system() != "Windows":
                    assert get_mimetype(pdf_file) == "application/pdf"

    @allow_windows
    async def test_page_method_callable(self):

        async def scroll_page(page: Page) -> str:
            await page.wait_for_selector(selector="div.quote")
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await page.wait_for_selector(selector="div.quote:nth-child(11)")
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await page.wait_for_selector(selector="div.quote:nth-child(21)")
            return page.url

        async with make_handler({"PLAYWRIGHT_BROWSER_TYPE": self.browser_type}) as handler:
            with StaticMockServer() as server:
                req = Request(
                    url=server.urljoin("/scroll.html"),
                    meta={
                        "playwright": True,
                        "playwright_page_methods": {
                            "callable": PageMethod(scroll_page),
                        },
                    },
                )
                resp = await handler._download_request(req, Spider("foo"))

            assert_correct_response(resp, req)
            assert len(resp.css("div.quote")) == 30
            assert resp.meta["playwright_page_methods"]["callable"].result == resp.url


class TestPageMethodChromium(IsolatedAsyncioTestCase, MixinPageMethodTestCase):
    browser_type = "chromium"


class TestPageMethodFirefox(IsolatedAsyncioTestCase, MixinPageMethodTestCase):
    browser_type = "firefox"


@pytest.mark.skipif(platform.system() != "Darwin", reason="Test WebKit only on Darwin")
class TestPageMethodWebkit(IsolatedAsyncioTestCase, MixinPageMethodTestCase):
    browser_type = "webkit"
