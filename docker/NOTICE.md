# Chromium sandbox profile

`chromium-seccomp.json` is the unmodified seccomp profile from Microsoft Playwright
v1.63.0: <https://github.com/microsoft/playwright/blob/v1.63.0/utils/docker/seccomp_profile.json>.
The upstream Apache 2.0 license is included in `LICENSE.playwright`.

Playwright documents this Docker profile for Chromium sandboxing with an unprivileged
user: <https://playwright.dev/python/docs/docker#crawling-and-scraping>.
It permits namespace creation needed by Chromium while retaining syscall filtering.
