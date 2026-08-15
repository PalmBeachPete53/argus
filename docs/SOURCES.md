# Official sources — research matrix (verified 2026-08)

All findings below were verified against the **official** bank websites. Feeds marked
"verified" were fetched and returned the stated format. Third-party sources
(TradingView, Investing.com, Reuters, Bloomberg, Wikipedia, search engines) are
explicitly excluded.

## Matrix

| Bank | RSS | Sitemap | API (publications) | Archive / calendar | PDF | Primary discovery | Fallback |
|------|-----|---------|--------------------|--------------------|-----|-------------------|----------|
| Fed | ✅ `/feeds/press_monetary.xml`, `/feeds/press_all.xml` | ❌ none | DDP data only | ✅ FOMC calendar + yearly press lists | HTML + PDF pairs | RSS | HTML archive/calendar |
| ECB | ✅ `/rss/press.html`, `/rss/pub.html` | ✅ `/sitemap.xml` (~11k) | SDMX data only | JS-driven listings (do not scrape) | HTML + PDF | RSS | Sitemap |
| BoE | ✅ `/rss/news`, `/rss/publications` (extensionless) | ✅ `/_api/sitemap/getsitemap` | none found | ✅ news hub, `/monetary-policy` | HTML + PDF | RSS | HTML news listing |
| BoJ | ✅ `/en/rss/whatsnew.xml` | ❌ none | none | ✅ `/en/mopo/mpmdeci/` year pages | PDF only | RSS | HTML year archive |
| SNB | ✅ `/public/rss/en/mopo`, `/public/rss/en/pressrel` | ✅ `/public/seo/sitemap.xml` | none | ✅ decisions archive (2000→present, static) | HTML + PDF | RSS | HTML archive |
| BoC | ✅ `/content_type/press-releases/feed/`, `/content_type/announcements/feed/`, `/content_type/mpr/feed/` | ✅ `/sitemap.xml` (WP) | Valet (data) only | ✅ `/press/press-releases/` (+ `/page/N/`) | HTML | RSS | HTML archive (paged) |
| RBA | ✅ `/rss/rss-cb-media-releases.xml`, `/rss/rss-cb-smp.xml`, … | ✅ `/sitemap.xml` (~11k) | none | ✅ `/monetary-policy/int-rate-decisions/` (by year) | HTML + PDF | RSS | HTML archive + sitemap |
| RBNZ | ❌ none | ✅ `/sitemap.xml` | `api.rbnz.govt.nz` (403 from CI) | ✅ OCR decision timeline (static table) | HTML + PDF | HTML timeline | Sitemap |
| Norges | ✅ `/en/rss-feeds/Press-releases---Norges-Bank/`, `/en/rss-feeds/Norges-Bank-Monetary-Policy-Report-.../` | ✅ `/sitemap.xml` | SDMX data only | JS-filtered listings (do not scrape) | HTML + PDF | RSS | Sitemap |
| Riksbank | ✅ `/en-gb/rss/press-releases/`, `/en-gb/rss/minutes-.../`, `/en-gb/rss/calendar/` | ✅ `/sitemap.xml` (~11k) | data API only | ✅ decision archive by year | PDF | RSS | Sitemap |

## Per-bank notes and adapter source IDs

### Federal Reserve (`fed` · federalreserve.gov)
- FOMC calendar / materials: `https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm` (future meetings announced).
- Statement/minutes/projections pages encode date in URL (`monetaryYYYYMMDDa.htm`, `fomcminutesYYYYMMDD.htm`, `fomcprojtablYYYYMMDD.htm`); each has an HTML + PDF pair.
- Publication date appears on the page and in the URL; meeting date in the minutes title.
- Adapter sources: `fed_monetary_press_rss` (1), `fed_press_releases_rss` (2), `fed_fomc_calendar` (4, allow_future).

### European Central Bank (`ecb` · ecb.europa.eu)
- The main publication listings are JS-driven — do **not** scrape; use RSS + sitemap.
- Monetary policy decisions are press releases under `/press/pr/date/<YYYY>/html/ecb.<tag><YYMMDD>~<hash>.en.html`.
- `pubDate` is authoritative; meeting dates come from the GC calendar page.
- Adapter sources: `ecb_press_rss` (1), `ecb_publications_rss` (2), `ecb_sitemap_monetary` (6).

### Bank of England (`boe` · bankofengland.co.uk)
- MPC Summary & Minutes: `/monetary-policy-summary-and-minutes/<YYYY>/<month>-<YYYY>` (HTML + PDF attachment). MPR under `/monetary-policy-report/...`.
- RSS must be extensionless (`/rss/news`); `/rss/news.xml` is rejected by the WAF.
- Adapter sources: `boe_news_rss` (1), `boe_publications_rss` (2), `boe_news_html` (5).

### Bank of Japan (`boj` · boj.or.jp)
- Single RSS feed `whatsnew.xml` (EN). No sitemap. Documents are PDFs per meeting
  (statement, outlook, summary of opinions, minutes); the meeting schedule page
  `/en/mopo/mpmsche_minu/index.htm` lists meeting + release dates.
- Adapter sources: `boj_whatsnew_rss` (1), `boj_mopo_archive` (4, year pages via pagination).

### Swiss National Bank (`snb` · snb.ch)
- Full monetary-policy decisions archive (static HTML, 2000→present):
  `/en/the-snb/mandates-goals/monetary-policy/decisions`; decision press releases
  `pre_<YYYYMMDD>`. RSS carries `dc:date` and PDF enclosures.
- Adapter sources: `snb_mopo_rss` (1), `snb_pressrel_rss` (2), `snb_decision_archive` (4, allow_future).

### Bank of Canada (`boc` · bankofcanada.ca)
- WordPress site: content-type feeds (`content_type/press-releases/feed/`, RDF/RSS1.0),
  `/sitemap.xml`, `/page/N/` pagination. REST API is auth-locked (401).
- FAD press releases: `/2026/07/fad-press-release-2026-07-15/`; fixed announcement
  dates are published in advance on the key-interest-rate page.
- Adapter sources: `boc_press_releases_rss` (1), `boc_announcements_rss` (2),
  `boc_fad_archive` (5), `boc_key_interest_rate_schedule` (7, allow_future).

### Reserve Bank of Australia (`rba` · rba.gov.au)
- Rich RSS set (media releases, SMP, speeches, …), RSS-CB 1.2 with `dc:date`.
  Decisions moved from `/monetary-policy/decisions/` to `/monetary-policy/int-rate-decisions/`.
- Media releases: `/media-releases/<YYYY>/mr-YY-NN.html`; SMP has HTML + PDF.
- Board Minutes: dated leaves under `/monetary-policy/rba-board-minutes/<YYYY>/<YYYY-MM-DD>.html`
  (discovered via `rba_board_minutes_archive`, classified `minutes`).
- Adapter sources: `rba_media_releases_rss` (1), `rba_smp_rss` (2),
  `rba_int_rate_archive` (4), `rba_sitemap_monetary` (6),
  `rba_board_minutes_archive` (7).

### Reserve Bank of New Zealand (`rbnz` · rbnz.govt.nz)
- No RSS. The OCR decision timeline table is static HTML — primary source.
  MPS listings are JS-filtered (not scraped). `api.rbnz.govt.nz` returned 403 from
  our network; sitemap is current.
- Adapter sources: `rbnz_ocr_decisions` (1, HTML), `rbnz_sitemap_monetary` (6).

### Norges Bank (`norges` · norges-bank.no)
- Meeting listings are JS-filtered — use RSS. Press-releases feed contains the
  policy-rate decisions; separate MPR feed. Sitemap is per-language.
- Adapter sources: `norges_press_releases_rss` (1), `norges_mpr_rss` (2),
  `norges_sitemap_monetary` (6).

### Sveriges Riksbank (`riksbank` · riksbank.se)
- Per-meeting decision pages aggregate PDFs (press release, MPR, minutes, slides).
  RSS feeds for press releases, minutes, calendar, speeches.
- Adapter sources: `riksbank_press_releases_rss` (1), `riksbank_minutes_rss` (2),
  `riksbank_sitemap_monetary` (6).

## Known site-specific caveats

- **BoE / RBA**: some paths return 500/403 to non-browser user agents; the verified
  feeds above are plain HTTP and work with a normal user agent.
- **RBNZ**: curl was blocked (403); the sitemap and decision timeline were reachable
  via a browser user agent. The collector honours robots.txt and rate limits.
- **ECB / Norges / RBNZ**: JS-driven listing pages must not be scraped; RSS/sitemap
  are the machine-readable channels.
- **Fed / BoE / BoC / Riksbank**: future meeting dates are announced in advance;
  calendar sources (`allow_future`) capture them so upcoming publications are known
  before they exist as pages.