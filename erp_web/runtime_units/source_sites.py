"""后端商品解析装配；站点识别与页面诊断由两端共享。"""
from erp_shared.source_sites import (
    AMAZON_VERIFY_MARKERS, VERIFY_MARKERS, GENERIC_SOURCE_SITE, SOURCE_SITES,
    SourceSiteSpec, detect_source_site, source_site,
)
from .source_collect_parsers import parse_1688_product, parse_amazon_product, parse_generic_product


def parse_source_snapshot(platform, snapshot, page_url=""):
    parser = {"1688": parse_1688_product, "amazon": parse_amazon_product}.get(platform, parse_generic_product)
    return parser(snapshot, page_url)


__all__ = ["SOURCE_SITES", "SourceSiteSpec", "GENERIC_SOURCE_SITE", "detect_source_site", "source_site", "parse_source_snapshot", "VERIFY_MARKERS", "AMAZON_VERIFY_MARKERS"]
