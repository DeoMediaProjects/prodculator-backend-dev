"""Default scrape sources, seeded into the scrape_sources table on first run.

Covers 18 territories: US (GA, NY, CA), CA, GB, IE, FR, DE, ES, IT, CZ, HU,
AU, NZ, ZA, NG, MT, IS + multi-territory (EU).

source_authority values
-----------------------
  "government"           — direct government department (gov.uk, revenue.ie, …)
  "government_agency"    — government-funded screen agency / film commission
                           (BFI, CNC, Screen Australia, FFA, …)
  "national_statistics"  — national statistics bureau (BLS, ONS, INSEE, …)

NOTE: Union/CBA sources (SAG-AFTRA, ACTRA, Equity, MEAA, BECTU, etc.) have
been removed from crew_costs. Their rate schedules are copyrighted and using
them commercially without a data licence creates IP exposure. Crew/cast rate
data now uses ONLY government statistical agency sources published under
open licences.

NOTE: All third-party aggregators (Vitrina, Olffi, EP, etc.) have been removed
from incentives, grants, and festivals. Every source below is either a direct
government site or an officially mandated film agency / commission.
"""

DEFAULT_SOURCES: list[dict] = [

    # ═════════════════════════════════════════════════════════════
    # INCENTIVES — Government / Official Film Commission Only
    # ═════════════════════════════════════════════════════════════

    # ── United States (state-level — no federal film incentive exists) ────
    {
        "resource_type": "incentives",
        "url": "https://www.georgia.org/industries/film-entertainment/georgia-film-tv-production/production-incentives",
        "label": "Georgia Film Tax Credit (20–30 %)",
        "territory": "United States",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },
    {
        "resource_type": "incentives",
        "url": "https://esd.ny.gov/new-york-state-film-tax-credit-program-production",
        "label": "New York State Film Tax Credit (Production)",
        "territory": "United States",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },
    # NOTE: California film.ca.gov returns 403 to automated requests.
    # Keeping the source for manual sync / future scraper bypass.
    {
        "resource_type": "incentives",
        "url": "https://film.ca.gov/tax-credit/",
        "label": "California Film & TV Tax Credit Program",
        "territory": "United States",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── United Kingdom ────────────────────────────────────────────────────
    {
        "resource_type": "incentives",
        "url": "https://www.gov.uk/guidance/claim-audio-visual-expenditure-credits-for-corporation-tax",
        "label": "UK Audio-Visual Expenditure Credit (AVEC)",
        "territory": "United Kingdom",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government",
    },
    {
        "resource_type": "incentives",
        "url": "https://www.bfi.org.uk/apply-british-certification-tax-relief",
        "label": "BFI Cultural Test & Certification",
        "territory": "United Kingdom",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Canada ────────────────────────────────────────────────────────────
    {
        "resource_type": "incentives",
        "url": "https://www.canada.ca/en/canadian-heritage/services/funding/cavco-tax-credits.html",
        "label": "Canadian Film or Video Production Tax Credit (CAVCO)",
        "territory": "Canada",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government",
    },

    # ── Australia ─────────────────────────────────────────────────────────
    {
        "resource_type": "incentives",
        "url": "https://www.screenaustralia.gov.au/funding-and-support/producer-offset",
        "label": "Screen Australia Producer Offset",
        "territory": "Australia",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },
    {
        "resource_type": "incentives",
        "url": "https://www.screenaustralia.gov.au/funding-and-support/producer-offset/location-and-pdv-offsets",
        "label": "Screen Australia Location & PDV Offsets (International)",
        "territory": "Australia",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Ireland ───────────────────────────────────────────────────────────
    {
        "resource_type": "incentives",
        "url": "https://www.revenue.ie/en/personal-tax-credits-reliefs-and-exemptions/investment/film-relief/index.aspx",
        "label": "Ireland Section 481 Film Relief",
        "territory": "Ireland",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government",
    },

    # ── France ────────────────────────────────────────────────────────────
    {
        "resource_type": "incentives",
        "url": "https://www.cnc.fr/web/en/tax-rebate/the-tax-rebate-for-international-productions-trip_190742",
        "label": "France TRIP Tax Rebate (CNC)",
        "territory": "France",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Germany ───────────────────────────────────────────────────────────
    {
        "resource_type": "incentives",
        "url": "https://dfff-ffa.de/en.html",
        "label": "German Federal Film Fund (DFFF / FFA)",
        "territory": "Germany",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Spain ─────────────────────────────────────────────────────────────
    {
        "resource_type": "incentives",
        "url": "https://www.cultura.gob.es/cultura/areas/cine/ayudas.html",
        "label": "Spain ICAA Film Tax Incentives",
        "territory": "Spain",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government",
    },

    # ── Italy ─────────────────────────────────────────────────────────────
    {
        "resource_type": "incentives",
        "url": "https://cinema.cultura.gov.it/tax-credit/",
        "label": "Italy MiC Film Tax Credit",
        "territory": "Italy",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government",
    },

    # ── Czech Republic ────────────────────────────────────────────────────
    {
        "resource_type": "incentives",
        "url": "https://www.filmcommission.cz/en/production-incentives/",
        "label": "Czech Film Commission Production Incentive",
        "territory": "Czech Republic",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Hungary ───────────────────────────────────────────────────────────
    # NOTE: nfi.hu/en/filming-in-hungary/hungarian-film-incentive is blocked
    # by robots.txt and returns only Hungarian content. Already disabled via
    # _DEPRECATED_SOURCE_URLS.
    {
        "resource_type": "incentives",
        "url": "https://nfi.hu/en/filming-in-hungary/hungarian-film-incentive",
        "label": "NFI Hungarian Film Incentive (30 %)",
        "territory": "Hungary",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Malta ─────────────────────────────────────────────────────────────
    {
        "resource_type": "incentives",
        "url": "https://www.maltafilmcommission.com/incentives/",
        "label": "Malta Film Commission Cash Rebate",
        "territory": "Malta",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Iceland ───────────────────────────────────────────────────────────
    # NOTE: icelandicfilmcentre.is restructured — old /support/production-incentive/
    # path is dead. Disabled via _DEPRECATED_SOURCE_URLS until a working URL
    # is found.
    {
        "resource_type": "incentives",
        "url": "https://www.icelandicfilmcentre.is/support/production-incentive/",
        "label": "Iceland Film Reimbursement Scheme (35 %)",
        "territory": "Iceland",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── New Zealand ───────────────────────────────────────────────────────
    # NOTE: nzfilm.co.nz restructured — old /incentives/ paths are dead.
    # Disabled via _DEPRECATED_SOURCE_URLS until a working URL is found.
    {
        "resource_type": "incentives",
        "url": "https://www.nzfilm.co.nz/incentives/new-zealand-screen-production-rebate",
        "label": "New Zealand Screen Production Rebate (NZSPG)",
        "territory": "New Zealand",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── South Africa ──────────────────────────────────────────────────────
    # NOTE: DTIC restructured their site — old specific incentive paths
    # return 404. Using the film incentive landing page.
    {
        "resource_type": "incentives",
        "url": "https://www.thedtic.gov.za/financial-and-non-financial-support/incentives/film-incentive/",
        "label": "South Africa DTIC Film & TV Production Incentive",
        "territory": "South Africa",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government",
    },

    # ── Nigeria ───────────────────────────────────────────────────────────
    # NOTE: Nigeria does not currently operate a formal production rebate.
    # The NFC site is thin on structured incentive data but kept for
    # completeness — the scraper extracts whatever is available.
    {
        "resource_type": "incentives",
        "url": "https://www.nfc.gov.ng",
        "label": "Nigerian Film Corporation",
        "territory": "Nigeria",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government",
    },


    # ═════════════════════════════════════════════════════════════
    # GRANTS — Government Cultural Funds & Official Screen Agencies
    # ═════════════════════════════════════════════════════════════

    # ── Multi-territory / Supranational ───────────────────────────────────
    {
        "resource_type": "grants",
        "url": "https://culture.ec.europa.eu/creative-europe/creative-europe-media-strand",
        "label": "Creative Europe MEDIA Programme",
        "territory": None,
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government",
    },
    {
        "resource_type": "grants",
        "url": "https://www.coe.int/en/web/eurimages",
        "label": "Eurimages — Council of Europe Co-production Fund",
        "territory": None,
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government",
    },

    # ── United States ─────────────────────────────────────────────────────
    {
        "resource_type": "grants",
        "url": "https://www.arts.gov/grants",
        "label": "US National Endowment for the Arts Grants",
        "territory": "United States",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government",
    },

    # ── United Kingdom ────────────────────────────────────────────────────
    {
        "resource_type": "grants",
        "url": "https://www.bfi.org.uk/get-funding-support",
        "label": "BFI Film Funding",
        "territory": "United Kingdom",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Canada ────────────────────────────────────────────────────────────
    {
        "resource_type": "grants",
        "url": "https://telefilm.ca/en/we-finance-and-support/our-programs",
        "label": "Telefilm Canada Funding Programs",
        "territory": "Canada",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },
    {
        "resource_type": "grants",
        "url": "https://cmf-fmc.ca/our-programs/",
        "label": "Canada Media Fund (CMF)",
        "territory": "Canada",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Australia ─────────────────────────────────────────────────────────
    {
        "resource_type": "grants",
        "url": "https://www.screenaustralia.gov.au/funding-and-support",
        "label": "Screen Australia Funding & Support",
        "territory": "Australia",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Ireland ───────────────────────────────────────────────────────────
    {
        "resource_type": "grants",
        "url": "https://www.screenireland.ie/funding",
        "label": "Screen Ireland Funding",
        "territory": "Ireland",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── France ────────────────────────────────────────────────────────────
    {
        "resource_type": "grants",
        "url": "https://www.cnc.fr/professionnels/aides-et-financements",
        "label": "CNC Aides & Financements (France)",
        "territory": "France",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Germany ───────────────────────────────────────────────────────────
    {
        "resource_type": "grants",
        "url": "https://www.ffa.de/foerderungen.html",
        "label": "FFA Förderungen (Germany)",
        "territory": "Germany",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Spain ─────────────────────────────────────────────────────────────
    {
        "resource_type": "grants",
        "url": "https://www.cultura.gob.es/cultura/areas/cine/ayudas.html",
        "label": "Spain ICAA Film Grants (Ayudas)",
        "territory": "Spain",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government",
    },

    # ── Italy ─────────────────────────────────────────────────────────────
    {
        "resource_type": "grants",
        "url": "https://cinema.cultura.gov.it/contributi/",
        "label": "Italy MiC Film Contributions & Grants",
        "territory": "Italy",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government",
    },

    # ── Czech Republic ────────────────────────────────────────────────────
    {
        "resource_type": "grants",
        "url": "https://fondkinematografie.cz/en/grants/",
        "label": "Czech Film Fund (SFK) Grants",
        "territory": "Czech Republic",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Hungary ───────────────────────────────────────────────────────────
    {
        "resource_type": "grants",
        "url": "https://nfi.hu/en/national-film-institute/funding",
        "label": "NFI Hungary Funding",
        "territory": "Hungary",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Malta ─────────────────────────────────────────────────────────────
    {
        "resource_type": "grants",
        "url": "https://www.maltafilmcommission.com/funding/",
        "label": "Malta Film Commission Funding",
        "territory": "Malta",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── New Zealand ───────────────────────────────────────────────────────
    {
        "resource_type": "grants",
        "url": "https://www.nzfilm.co.nz/funding-and-support",
        "label": "New Zealand Film Commission Funding",
        "territory": "New Zealand",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Iceland ───────────────────────────────────────────────────────────
    {
        "resource_type": "grants",
        "url": "https://www.icelandicfilmcentre.is/support/",
        "label": "Icelandic Film Centre Grants",
        "territory": "Iceland",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── South Africa ──────────────────────────────────────────────────────
    {
        "resource_type": "grants",
        "url": "https://www.nfvf.co.za/funding/",
        "label": "NFVF South Africa Funding",
        "territory": "South Africa",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },


    # ═════════════════════════════════════════════════════════════
    # FESTIVALS — Official Festival Websites Only
    # ═════════════════════════════════════════════════════════════
    # Each festival's own website is the authoritative source for
    # submission deadlines, dates, and programme information.

    # ── Multi-territory / Accreditation ───────────────────────────────────
    {
        "resource_type": "festivals",
        "url": "https://fiapf.org/festivals/accredited-festivals/competitive-feature-film-festivals/",
        "label": "FIAPF Accredited A-List Festivals",
        "territory": None,
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── United States ─────────────────────────────────────────────────────
    {
        "resource_type": "festivals",
        "url": "https://www.sundance.org/festivals/sundance-film-festival/",
        "label": "Sundance Film Festival",
        "territory": "United States",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },
    {
        "resource_type": "festivals",
        "url": "https://tribecafilm.com/festival",
        "label": "Tribeca Film Festival",
        "territory": "United States",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── United Kingdom ────────────────────────────────────────────────────
    {
        "resource_type": "festivals",
        "url": "https://www.bfi.org.uk/bfi-london-film-festival",
        "label": "BFI London Film Festival",
        "territory": "United Kingdom",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },
    {
        "resource_type": "festivals",
        "url": "https://www.edfilmfest.org.uk/",
        "label": "Edinburgh International Film Festival",
        "territory": "United Kingdom",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Canada ────────────────────────────────────────────────────────────
    {
        "resource_type": "festivals",
        "url": "https://www.tiff.net/",
        "label": "Toronto International Film Festival (TIFF)",
        "territory": "Canada",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── France ────────────────────────────────────────────────────────────
    {
        "resource_type": "festivals",
        "url": "https://www.festival-cannes.com/en/",
        "label": "Cannes Film Festival",
        "territory": "France",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Germany ───────────────────────────────────────────────────────────
    {
        "resource_type": "festivals",
        "url": "https://www.berlinale.de/en/home.html",
        "label": "Berlin International Film Festival (Berlinale)",
        "territory": "Germany",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Italy ─────────────────────────────────────────────────────────────
    {
        "resource_type": "festivals",
        "url": "https://www.labiennale.org/en/cinema",
        "label": "Venice International Film Festival",
        "territory": "Italy",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Spain ─────────────────────────────────────────────────────────────
    {
        "resource_type": "festivals",
        "url": "https://www.sansebastianfestival.com/",
        "label": "San Sebastián International Film Festival",
        "territory": "Spain",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Australia ─────────────────────────────────────────────────────────
    {
        "resource_type": "festivals",
        "url": "https://www.sff.org.au/",
        "label": "Sydney Film Festival",
        "territory": "Australia",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },
    {
        "resource_type": "festivals",
        "url": "https://miff.com.au/",
        "label": "Melbourne International Film Festival (MIFF)",
        "territory": "Australia",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Ireland ───────────────────────────────────────────────────────────
    {
        "resource_type": "festivals",
        "url": "https://galwayfilmfleadh.com/",
        "label": "Galway Film Fleadh",
        "territory": "Ireland",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },
    {
        "resource_type": "festivals",
        "url": "https://www.diff.ie/",
        "label": "Dublin International Film Festival",
        "territory": "Ireland",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Czech Republic ────────────────────────────────────────────────────
    {
        "resource_type": "festivals",
        "url": "https://www.kviff.com/en/",
        "label": "Karlovy Vary International Film Festival",
        "territory": "Czech Republic",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Hungary ───────────────────────────────────────────────────────────
    {
        "resource_type": "festivals",
        "url": "https://biff.hu/",
        "label": "Budapest International Film Festival",
        "territory": "Hungary",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Malta ─────────────────────────────────────────────────────────────
    {
        "resource_type": "festivals",
        "url": "https://vallettafilmfestival.com/",
        "label": "Valletta Film Festival",
        "territory": "Malta",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── New Zealand ───────────────────────────────────────────────────────
    {
        "resource_type": "festivals",
        "url": "https://www.nziff.co.nz/",
        "label": "New Zealand International Film Festival",
        "territory": "New Zealand",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },

    # ── Iceland ───────────────────────────────────────────────────────────
    {
        "resource_type": "festivals",
        "url": "https://www.riff.is/",
        "label": "Reykjavík International Film Festival (RIFF)",
        "territory": "Iceland",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "source_authority": "government_agency",
    },


    # ═════════════════════════════════════════════════════════════
    # CREW & CAST COSTS — Government Statistical Agencies Only
    # ═════════════════════════════════════════════════════════════
    # Union collective bargaining agreements (SAG-AFTRA, ACTRA,
    # Equity, MEAA, BECTU, etc.) are copyrighted. Using their rate
    # schedules commercially without a data licence creates IP
    # exposure. All sources below are open-licence government
    # statistical agencies.
    #
    # USA — BLS OEWS (public domain, 17 USC §105)
    {
        "resource_type": "crew_costs",
        "url": "https://api.bls.gov/publicAPI/v2/timeseries/data/",
        "label": "BLS Occupational Employment & Wage Statistics (OEWS) — NAICS 5121",
        "territory": "United States",
        "is_pdf": False,
        "use_bls_api": True,
        "use_rest_api": False,
        "source_authority": "national_statistics",
    },
    # Canada — Statistics Canada (Open Government Licence Canada)
    {
        "resource_type": "crew_costs",
        "url": "https://www150.statcan.gc.ca/n1/en/type/data",
        "label": "Statistics Canada Labour Force Survey — NOC occupational wages",
        "territory": "Canada",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": True,
        "api_slug": "statcan",
        "source_authority": "national_statistics",
    },
    # UK — ONS ASHE (Open Government Licence v3)
    {
        "resource_type": "crew_costs",
        "url": "https://api.ons.gov.uk/v1/datasets/ashe-table-7/editions/time-series/versions/2/observations",
        "label": "ONS Annual Survey of Hours & Earnings (ASHE) — UK occupational wages",
        "territory": "United Kingdom",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": True,
        "api_slug": "ons",
        "source_authority": "national_statistics",
    },
    # Ireland — CSO (public use, attribution required)
    {
        "resource_type": "crew_costs",
        "url": "https://data.cso.ie/",
        "label": "CSO Ireland — Earnings and Labour Costs Survey",
        "territory": "Ireland",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": True,
        "api_slug": "cso_ie",
        "source_authority": "national_statistics",
    },
    # France — INSEE (Licence Ouverte v2.0)
    {
        "resource_type": "crew_costs",
        "url": "https://www.insee.fr/fr/statistiques",
        "label": "INSEE Enquête Emploi — French occupational wages",
        "territory": "France",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": True,
        "api_slug": "insee",
        "source_authority": "national_statistics",
    },
    # Germany — Destatis (Data Licence Germany v2.0)
    {
        "resource_type": "crew_costs",
        "url": "https://www.destatis.de/EN/Themes/Labour/Earnings/_node.html",
        "label": "Destatis — German earnings by occupation",
        "territory": "Germany",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": True,
        "api_slug": "destatis",
        "source_authority": "national_statistics",
    },
    # Spain — INE (open access, attribution required)
    {
        "resource_type": "crew_costs",
        "url": "https://www.ine.es/jaxiT3/Tabla.htm?t=28188",
        "label": "INE Encuesta Anual de Estructura Salarial — Spanish occupational wages",
        "territory": "Spain",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": True,
        "api_slug": "ine_es",
        "source_authority": "national_statistics",
    },
    # Italy — ISTAT (public access)
    {
        "resource_type": "crew_costs",
        "url": "https://www.istat.it/en/archivio/wages",
        "label": "ISTAT — Italian occupational wage statistics",
        "territory": "Italy",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": True,
        "api_slug": "istat",
        "source_authority": "national_statistics",
    },
    # Australia — ABS Labour Force (CC BY 4.0)
    {
        "resource_type": "crew_costs",
        "url": "https://www.abs.gov.au/statistics/labour/earnings-and-working-conditions",
        "label": "ABS Employee Earnings and Hours — Australian occupational wages",
        "territory": "Australia",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": True,
        "api_slug": "abs",
        "source_authority": "national_statistics",
    },
    # New Zealand — Stats NZ LEED (CC BY 4.0)
    {
        "resource_type": "crew_costs",
        "url": "https://www.stats.govt.nz/topics/income",
        "label": "Stats NZ Linked Employer-Employee Data — NZ occupational wages",
        "territory": "New Zealand",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": True,
        "api_slug": "stats_nz",
        "source_authority": "national_statistics",
    },
    # South Africa — Stats SA QLFS (open access)
    {
        "resource_type": "crew_costs",
        "url": "https://www.statssa.gov.za/?page_id=1854&PPN=P0211",
        "label": "Stats SA Quarterly Labour Force Survey — SA occupational wages",
        "territory": "South Africa",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": True,
        "api_slug": "stats_sa",
        "source_authority": "national_statistics",
    },
    # Czech Republic — CZSO (free public use)
    {
        "resource_type": "crew_costs",
        "url": "https://www.czso.cz/csu/czso/labour-and-earnings-statistics",
        "label": "CZSO — Czech occupational earnings statistics",
        "territory": "Czech Republic",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": True,
        "api_slug": "czso",
        "source_authority": "national_statistics",
    },
    # Hungary — KSH (free public use)
    {
        "resource_type": "crew_costs",
        "url": "https://www.ksh.hu/stadat_eng",
        "label": "KSH — Hungarian earnings by economic activity",
        "territory": "Hungary",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": True,
        "api_slug": "ksh",
        "source_authority": "national_statistics",
    },
    # Iceland — Hagstofa (open access)
    {
        "resource_type": "crew_costs",
        "url": "https://www.statice.is/statistics/society/wages-and-income/",
        "label": "Hagstofa Íslands — Icelandic wage statistics",
        "territory": "Iceland",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": True,
        "api_slug": "hagstofa",
        "source_authority": "national_statistics",
    },
    # Malta — NSO Malta (open access)
    {
        "resource_type": "crew_costs",
        "url": "https://nso.gov.mt/labour-market/",
        "label": "NSO Malta — Maltese earnings and labour market statistics",
        "territory": "Malta",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": True,
        "api_slug": "nso_mt",
        "source_authority": "national_statistics",
    },
]


def _validate_source_territories() -> None:
    """Assert every territory in DEFAULT_SOURCES is a canonical Territory label.

    Called at import-time to catch typos / drift early. ``None`` is allowed
    for multi-territory / supranational sources.
    """
    from app.core.territories import resolve_territory

    for src in DEFAULT_SOURCES:
        t = src.get("territory")
        if t is None:
            continue
        resolved = resolve_territory(t)
        if resolved is None:
            raise ValueError(
                f"Scrape source '{src.get('label')}' has unrecognised territory "
                f"'{t}'. Add it to app.core.territories.Territory or fix the typo."
            )
        if resolved.label != t:
            raise ValueError(
                f"Scrape source '{src.get('label')}' uses non-canonical territory "
                f"'{t}'. Use the canonical label '{resolved.label}' instead."
            )


_validate_source_territories()

