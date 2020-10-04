import re
import unittest
import urllib.parse
import uuid
from typing import Any, Dict, List, Optional

import scrapy
import scrapy.spidermiddlewares.httperror
import twisted.internet.error
from scrapy.linkextractors import LinkExtractor


def make_response(
    id: str,
    request_url: str,
    response_url: str,
    status: str,
    redirect_urls: List[str],
    redirect_reasons: List[Any],
) -> Dict:

    return {
        "type": "response",
        "id": id,
        "request_url": request_url,
        "response_url": response_url,
        "status": status,
        "redirect_urls": redirect_urls,
        "redirect_reasons": redirect_reasons,
    }


def make_headers(id: str, url: str, status: str, headers: Dict) -> Dict:
    return {
        "type": "headers",
        "id": id,
        "url": url,
        "status": status,
        "headers": headers,
    }


def make_data(id: str, url: str, status: str, data: Dict) -> Dict:
    return {"type": "data", "id": id, "url": url, "status": status, "data": data}


def make_link(id: str, url: str, status: str, target: str, nofollow: bool) -> Dict:
    return {
        "type": "link",
        "id": id,
        "url": url,
        "status": status,
        "target": target,
        "nofollow": nofollow,
    }


def make_dns_error(id: str, request_url: str) -> Dict:
    return {
        "type": "dns_lookup_error",
        "id": id,
        "request_url": request_url,
    }


def make_timeout_error(id: str, request_url: str) -> Dict:
    return {
        "type": "timeout_error",
        "id": id,
        "request_url": request_url,
    }


class ChecksSpider(scrapy.Spider):
    name = "checks"

    # disable error middleware so we can handle all response codes EXCEPT redirects
    # which are handled by the redirect middleware
    custom_settings = {
        "SPIDER_MIDDLEWARES": {
            "scrapy.spidermiddlewares.httperror.HttpErrorMiddleware": None,
            "scrapy.spidermiddlewares.offsite.OffsiteMiddleware": 500,
            "scrapy.spidermiddlewares.referer.RefererMiddleware": 700,
            "scrapy.spidermiddlewares.urllength.UrlLengthMiddleware": 800,
            "scrapy.spidermiddlewares.depth.DepthMiddleware": 900,
        }
    }

    def __init__(
        self,
        start_url: str,
        include_url_re: Optional[str] = None,
        exclude_url_re: Optional[str] = None,
        *args,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.start_urls = [start_url]
        self.include_url_re = include_url_re
        self.exclude_url_re = exclude_url_re
        self.netloc = urllib.parse.urlparse(start_url).netloc

    def start_requests(self):
        for u in self.start_urls:

            # get around inability to yield items in start_requests
            root_link = make_link(
                id="ROOT", url="ROOT", status="200", target=u, nofollow=False
            )

            yield scrapy.Request(
                u,
                callback=self.parse,
                errback=self.errback,
                cb_kwargs={"root_link": root_link},
            )

    def should_follow_url(self, url: str) -> bool:
        if self.exclude_url_re is not None:
            if re.match(self.exclude_url_re, url):
                return False

        if self.include_url_re is not None:
            if re.match(self.include_url_re, url):
                return True
        else:
            url_obj = urllib.parse.urlparse(url)
            if url_obj.netloc == self.netloc:
                return True

        return False

    def get_response_obj(self, id: str, response) -> Dict:

        redirect_urls = response.request.meta.get("redirect_urls", [])
        redirect_reasons = response.request.meta.get("redirect_reasons", [])

        return make_response(
            id,
            response.request.url,
            response.url,
            str(response.status),
            redirect_urls,
            redirect_reasons,
        )

    def get_headers_obj(self, id: str, response) -> Dict:
        header_dict: Dict[str, List[str]] = {}

        for k, vs in response.headers.items():
            header_dict[str(k)] = [str(v) for v in vs]

        return make_headers(id, response.url, str(response.status), header_dict)

    def errback(self, failure, root_link=None):
        if root_link:
            yield root_link

        id = uuid.uuid4().hex

        if failure.check(scrapy.spidermiddlewares.httperror.HttpError):
            yield self.get_response_obj(id, failure.value.response)
            yield self.get_headers_obj(id, failure.value.response)
        elif failure.check(twisted.internet.error.DNSLookupError):
            yield make_dns_error(id, failure.request.url)
        elif failure.check(TimeoutError, twisted.internet.error.TCPTimedOutError):
            yield make_timeout_error(id, failure.request.url)

    def parse(self, response, root_link=None):
        if root_link:
            yield root_link

        id = uuid.uuid4().hex

        try:
            title = response.xpath("//title/text()").get()
            meta_description = response.xpath(
                "//meta[@name='description']/@content"
            ).get()
        except scrapy.exceptions.NotSupported:
            title = None
            meta_description = None

        yield make_data(
            id,
            response.url,
            str(response.status),
            {"title": title, "meta_description": meta_description},
        )

        yield self.get_headers_obj(id, response)

        yield self.get_response_obj(id, response)

        extractor = LinkExtractor(process_value=process_tel_value)

        try:
            links = extractor.extract_links(response)
        except AttributeError:
            links = []

        for link in links:
            url = link.url
            yield make_link(id, response.url, str(response.status), url, link.nofollow)

            if not link.nofollow:

                if self.should_follow_url(url):
                    yield response.follow(
                        link,
                        callback=self.parse,
                        errback=self.errback,
                    )


def process_tel_value(value: str) -> Optional[str]:
    """
    Work around tel: scrapy bug https://github.com/scrapy/scrapy/issues/1381
    """
    if re.match("tel:.+", value):
        return None
    else:
        return value


class Test(unittest.TestCase):
    def test_tel(self):
        self.assertIsNone(process_tel_value("tel:2345234"))
        self.assertIsNone(process_tel_value("tel:+2345234"))
        self.assertEqual(process_tel_value("../link"), "../link")