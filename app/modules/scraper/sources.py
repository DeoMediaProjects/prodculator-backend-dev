"""Default scrape sources, seeded into the scrape_sources table on first run."""

DEFAULT_SOURCES: list[dict] = [
    # ── Incentives ────────────────────────────────────────────────────────────
    # Aggregators (multi-country)
    {
        "resource_type": "incentives",
        "url": "https://vitrina.ai/blog/film-production-tax-incentives-by-country",
        "label": "Vitrina.ai Global Incentives Tracker",
        "territory": None,
        "is_pdf": False,
        "use_bls_api": False,
    },
    {
        "resource_type": "incentives",
        "url": "https://www.olffi.com/",
        "label": "Olffi Film Financing Database",
        "territory": None,
        "is_pdf": False,
        "use_bls_api": False,
    },
    # United Kingdom
    {
        "resource_type": "incentives",
        "url": "https://www.bfi.org.uk/apply-british-certification-tax-relief",
        "label": "BFI UK Film Tax Relief",
        "territory": "United Kingdom",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Canada
    {
        "resource_type": "incentives",
        "url": "https://www.canada.ca/en/canadian-heritage/services/funding/cavco-tax-credits/canadian-film-video-production.html",
        "label": "CAVCO Canadian Film Tax Credits",
        "territory": "Canada",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # USA — kept broad, Vitrina aggregator covers state-level
    # Australia
    {
        "resource_type": "incentives",
        "url": "https://www.screenaustralia.gov.au/funding-and-support/producer-offset",
        "label": "Screen Australia Producer Offset",
        "territory": "Australia",
        "is_pdf": False,
        "use_bls_api": False,
    },
    {
        "resource_type": "incentives",
        "url": "https://www.ausfilm.com.au/incentives/",
        "label": "Ausfilm Incentives",
        "territory": "Australia",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Malta
    {
        "resource_type": "incentives",
        "url": "https://www.maltafilmcommission.com/incentives/",
        "label": "Malta Film Commission Incentives",
        "territory": "Malta",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Ireland
    {
        "resource_type": "incentives",
        "url": "https://www.revenue.ie/en/personal-tax-credits-reliefs-and-exemptions/investment/film-relief/index.aspx",
        "label": "Ireland Section 481 Film Relief",
        "territory": "Ireland",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # France
    {
        "resource_type": "incentives",
        "url": "https://www.cnc.fr/web/en/tax-rebate/the-tax-rebate-for-international-productions-trip_190742",
        "label": "CNC TRIP (France)",
        "territory": "France",
        "is_pdf": False,
        "use_bls_api": False,
    },
    {
        "resource_type": "incentives",
        "url": "https://www.filmfrance.net/en/tax-rebate/tax-rebate-for-international-productions/",
        "label": "Film France Tax Rebate",
        "territory": "France",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Germany
    {
        "resource_type": "incentives",
        "url": "https://dfff-ffa.de/en.html",
        "label": "FFA DFFF (Germany)",
        "territory": "Germany",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Spain
    {
        "resource_type": "incentives",
        "url": "https://spainfilmcommission.com/en/blog/spain-film-tax-rebate/",
        "label": "Spain Film Commission Tax Rebate",
        "territory": "Spain",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Czech Republic
    {
        "resource_type": "incentives",
        "url": "https://www.filmcommission.cz/en/production-incentives/",
        "label": "Czech Film Commission Incentives",
        "territory": "Czech Republic",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Hungary
    {
        "resource_type": "incentives",
        "url": "https://nfi.hu/en/filming-in-hungary/hungarian-film-incentive",
        "label": "NFI Hungary Film Incentive",
        "territory": "Hungary",
        "is_pdf": False,
        "use_bls_api": False,
    },

    # ── Grants ────────────────────────────────────────────────────────────────
    # Aggregators (multi-country)
    {
        "resource_type": "grants",
        "url": "https://culture.ec.europa.eu/creative-europe/creative-europe-media-strand",
        "label": "Creative Europe MEDIA Programme",
        "territory": None,
        "is_pdf": False,
        "use_bls_api": False,
    },
    {
        "resource_type": "grants",
        "url": "https://www.coe.int/en/web/eurimages/coproduction",
        "label": "Eurimages Co-production Fund",
        "territory": None,
        "is_pdf": False,
        "use_bls_api": False,
    },
    # United Kingdom
    {
        "resource_type": "grants",
        "url": "https://www.bfi.org.uk/get-funding-support",
        "label": "BFI Funding",
        "territory": "United Kingdom",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # USA
    {
        "resource_type": "grants",
        "url": "https://www.arts.gov/grants",
        "label": "NEA Film Grants",
        "territory": "United States",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Canada
    {
        "resource_type": "grants",
        "url": "https://telefilm.ca/en/we-finance-and-support/our-programs",
        "label": "Telefilm Canada Programs",
        "territory": "Canada",
        "is_pdf": False,
        "use_bls_api": False,
    },
    {
        "resource_type": "grants",
        "url": "https://cmf-fmc.ca/our-programs/",
        "label": "Canada Media Fund",
        "territory": "Canada",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Australia
    {
        "resource_type": "grants",
        "url": "https://www.screenaustralia.gov.au/funding-and-support",
        "label": "Screen Australia Funding",
        "territory": "Australia",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Malta
    {
        "resource_type": "grants",
        "url": "https://artscouncilmalta.gov.mt/en/film-distribution-grants-programme/",
        "label": "Arts Council Malta Film Grants",
        "territory": "Malta",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Ireland
    {
        "resource_type": "grants",
        "url": "https://www.screenireland.ie/funding",
        "label": "Screen Ireland Funding",
        "territory": "Ireland",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Germany
    {
        "resource_type": "grants",
        "url": "https://www.ffa.de/funding.html",
        "label": "FFA Funding Programs (Germany)",
        "territory": "Germany",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Hungary
    {
        "resource_type": "grants",
        "url": "https://nfi.hu/en/national-film-institute/funding",
        "label": "NFI Hungary Funding",
        "territory": "Hungary",
        "is_pdf": False,
        "use_bls_api": False,
    },

    # ── Festivals ─────────────────────────────────────────────────────────────
    # Aggregator (tier classification)
    {
        "resource_type": "festivals",
        "url": "https://fiapf.org/festivals/accredited-festivals/competitive-feature-film-festivals/",
        "label": "FIAPF Accredited Festivals",
        "territory": None,
        "is_pdf": False,
        "use_bls_api": False,
    },
    # United Kingdom
    {
        "resource_type": "festivals",
        "url": "https://www.bfi.org.uk/bfi-london-film-festival",
        "label": "BFI London Film Festival",
        "territory": "United Kingdom",
        "is_pdf": False,
        "use_bls_api": False,
    },
    {
        "resource_type": "festivals",
        "url": "https://www.edfilmfest.org.uk/",
        "label": "Edinburgh International Film Festival",
        "territory": "United Kingdom",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # USA
    {
        "resource_type": "festivals",
        "url": "https://www.sundance.org/festivals/sundance-film-festival/about/",
        "label": "Sundance Film Festival",
        "territory": "United States",
        "is_pdf": False,
        "use_bls_api": False,
    },
    {
        "resource_type": "festivals",
        "url": "https://tribecafilm.com/",
        "label": "Tribeca Film Festival",
        "territory": "United States",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Canada
    {
        "resource_type": "festivals",
        "url": "https://www.tiff.net/about",
        "label": "Toronto International Film Festival",
        "territory": "Canada",
        "is_pdf": False,
        "use_bls_api": False,
    },
    {
        "resource_type": "festivals",
        "url": "https://viff.org/",
        "label": "Vancouver International Film Festival",
        "territory": "Canada",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # France
    {
        "resource_type": "festivals",
        "url": "https://www.festival-cannes.com/en/",
        "label": "Cannes Film Festival",
        "territory": "France",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Germany
    {
        "resource_type": "festivals",
        "url": "https://www.berlinale.de/en/home.html",
        "label": "Berlin International Film Festival",
        "territory": "Germany",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Australia
    {
        "resource_type": "festivals",
        "url": "https://www.sff.org.au/",
        "label": "Sydney Film Festival",
        "territory": "Australia",
        "is_pdf": False,
        "use_bls_api": False,
    },
    {
        "resource_type": "festivals",
        "url": "https://miff.com.au/",
        "label": "Melbourne International Film Festival",
        "territory": "Australia",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Ireland
    {
        "resource_type": "festivals",
        "url": "https://galwayfilmfleadh.com/",
        "label": "Galway Film Fleadh",
        "territory": "Ireland",
        "is_pdf": False,
        "use_bls_api": False,
    },
    {
        "resource_type": "festivals",
        "url": "https://www.diff.ie/",
        "label": "Dublin International Film Festival",
        "territory": "Ireland",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Spain
    {
        "resource_type": "festivals",
        "url": "https://www.sansebastianfestival.com/",
        "label": "San Sebastian International Film Festival",
        "territory": "Spain",
        "is_pdf": False,
        "use_bls_api": False,
    },
    {
        "resource_type": "festivals",
        "url": "https://sitgesfilmfestival.com/",
        "label": "Sitges Film Festival",
        "territory": "Spain",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Czech Republic
    {
        "resource_type": "festivals",
        "url": "https://www.kviff.com/en/",
        "label": "Karlovy Vary International Film Festival",
        "territory": "Czech Republic",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Hungary
    {
        "resource_type": "festivals",
        "url": "https://biff.hu/",
        "label": "Budapest International Film Festival",
        "territory": "Hungary",
        "is_pdf": False,
        "use_bls_api": False,
    },
    # Malta
    {
        "resource_type": "festivals",
        "url": "https://vallettafilmfestival.com/",
        "label": "Valletta Film Festival",
        "territory": "Malta",
        "is_pdf": False,
        "use_bls_api": False,
    },

    # ── Crew Costs ────────────────────────────────────────────────────────────
    # NOTE: Crew cost data for countries without public rate cards (Ireland,
    # France, Germany, Spain, Czech Republic, Hungary, Malta) remains
    # manual-entry only. Union rate cards are published as PDFs for
    # UK (BECTU), Canada (IATSE), and Australia (MEAA).
    # USA — BLS API (structured, no AI extraction needed)
    {
        "resource_type": "crew_costs",
        "url": "https://api.bls.gov/publicAPI/v2/timeseries/data/",
        "label": "BLS Occupational Wage Survey (API)",
        "territory": "United States",
        "is_pdf": False,
        "use_bls_api": True,
    },
    # USA/UK — HTML tabular data
    {
        "resource_type": "crew_costs",
        "url": "https://www.freelancevideocollective.com/filmmaker-resources/production-crew-rates/",
        "label": "Freelance Video Collective Crew Rates",
        "territory": None,
        "is_pdf": False,
        "use_bls_api": False,
    },
    # UK — PDF rate cards
    {
        "resource_type": "crew_costs",
        "url": "https://bectu.org.uk/get-involved-in-the-union/ratecards/",
        "label": "BECTU Rate Cards (UK)",
        "territory": "United Kingdom",
        "is_pdf": True,
        "use_bls_api": False,
    },
    # Canada — PDF rate cards
    {
        "resource_type": "crew_costs",
        "url": "https://www.iatse.com/producers/current_production_rates.aspx",
        "label": "IATSE Production Rates (Canada/US)",
        "territory": "Canada",
        "is_pdf": True,
        "use_bls_api": False,
    },
    # Australia — PDF rate card
    {
        "resource_type": "crew_costs",
        "url": "https://www.meaa.org/download/mppa-rates-and-allowances-jan-1-2025/",
        "label": "MEAA MPPA Rates (Australia)",
        "territory": "Australia",
        "is_pdf": True,
        "use_bls_api": False,
    },
]
