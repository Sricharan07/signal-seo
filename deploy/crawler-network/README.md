# Crawler Network Test Image

This directory is an authority-free test fixture for Slice 0034. The image carries
only the crawl URL/fetch modules and a synthetic HTTP origin. It is built and
removed by `scripts/run-crawler-network-tests.py`; it is not a deployable crawler
worker and must not receive credentials, customer data, host mounts, public ports,
or production network access.
