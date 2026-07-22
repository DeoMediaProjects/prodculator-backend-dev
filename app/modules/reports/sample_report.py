"""Canned sample report used by the public marketing sample page.

Rendered through the real ``report_base.html`` template (same code path as a
paid report) so the website's ``/sample`` always matches the live report
output — no separate hand-maintained mock to drift out of sync.
"""
from __future__ import annotations

SAMPLE_TITLE = "EKO VIBES EP ONE PILOT"
SAMPLE_CREATED_AT = "2026-07-06T09:00:00Z"

SAMPLE_REPORT_DATA: dict = {
    "genre": "TV Pilot · Drama",
    "tone": "Vibrant, celebratory, urban",
    "scale": "High-budget · £30,000,000",
    "complexity": "High · 45 est. shoot days",
    "executiveSummary": {
        "format": "TV Pilot · Drama",
        "budget": "£30,000,000",
        "scale": "High-budget",
        "shootDays": 45,
        "recommendedTerritory": "United Kingdom",
        "recommendedTerritoryScore": 88,
        "recommendedTerritoryRebate": "£5.74M",
        "headlineNetBudget": "£24,262,500",
        "keyFlags": [
            "AVEC cultural test must be secured before principal photography",
            "Music-licensing counsel required for the Fuji performance sequences",
            "GBP/NGN exposure should be locked before committing Nigerian spend",
        ],
        "keyInsights": (
            "**Script Overview**\n"
            "This pilot follows its lead from the Fuji music venues of Lagos into a collision with the "
            "city's police and music-business establishments, with a London strand pulling the industry "
            "stakes international. The central conflict runs between artistic ambition and the machinery, "
            "commercial and institutional, that decides who gets heard. The primary production challenge "
            "is a music-heavy, interior-dominant shoot that has to feel authentically Lagos while anchoring "
            "its qualifying spend somewhere with a bankable incentive.\n\n"
            "**Primary Recommendation**\n"
            "United Kingdom, FRS: 88 (Bankable). Estimated net rebate of £5.74M at a 25.5% net rate, paid "
            "3 to 6 months after certification. The UK wins for this production because it pairs world-class "
            "music post-production with the deepest crew base for an interior-heavy, performance-driven drama.\n\n"
            "**Second Territory**\n"
            "Hungary, FRS: 79 (Bankable). A 30% cash rebate with no cultural test and Korda/Origo set-build "
            "capability suited to an interior-heavy shoot. The producer gains schedule certainty and set control "
            "but gives up some of the UK's music-post depth.\n\n"
            "**Third Territory**\n"
            "Malta, FRS: 71 (Verify First). The highest headline rate of any ranked territory, a 40% cash rebate "
            "on a smaller allocation, with interim payments improving cash flow. It becomes the right choice for a "
            "second-unit or coastal block rather than the whole production.\n\n"
            "**Production Complexity Snapshot**\n"
            "79% of scenes are interiors (offices, a police station, living rooms and Fuji venues), 45 estimated "
            "shoot days, and two principal languages. The music-performance sequences and their licensing are the "
            "dominant complexity driver.\n\n"
            "**Strategic Recommendations**\n"
            "Anchor production in the UK under AVEC, build interiors in Hungary or Malta, and capture Lagos on "
            "location. Secure the AVEC cultural test and NFI Hungary registration early, and engage music-licensing "
            "counsel in pre-production."
        ),
        "actionTimeline": [
            {"action": "Apply for AVEC cultural test certification", "deadline": "12–16 weeks before the UK shoot", "note": "Late certification delays the rebate claim."},
            {"action": "Register with NFI Hungary before any Hungarian production activity", "deadline": "Before pre-production in Hungary", "note": "Late registration disqualifies the rebate."},
            {"action": "Submit the Malta Film Commission application", "deadline": "Before principal photography", "note": ""},
            {"action": "Engage music-licensing counsel for the Fuji performance sequences", "deadline": "Pre-production", "note": ""},
            {"action": "Lock GBP/NGN exposure before committing Nigerian spend", "deadline": "On budget approval", "note": ""},
        ],
    },
    "scriptStats": {
        "sceneCount": 112,
        "interiorPct": 79,
        "exteriorPct": 21,
        "dayScenes": 61,
        "nightScenes": 44,
        "otherScenes": 7,
        "estShootingDays": 45,
        "principalCast": 8,
        "crowdScenes": 9,
        "languages": ["English", "Yoruba"],
        "namedLocations": [
            {"name": "Lagos — Fuji venues", "scenes": 34, "pct": 30},
            {"name": "Lagos — police station", "scenes": 18, "pct": 16},
            {"name": "London — label offices", "scenes": 22, "pct": 20},
            {"name": "Domestic interiors", "scenes": 15, "pct": 13},
        ],
        "productionChallenges": [
            "Live Fuji music performances require licensing and playback planning",
            "Interior-heavy schedule favours controlled stage builds",
            "Bilingual dialogue (English / Yoruba) affects casting and post",
            "Lagos location work needs permits and currency planning",
        ],
    },
    "scoringMethodology": {
        "overview": "Every territory is scored across six weighted dimensions.",
        "weightingNote": "Incentive value and reliability carry the most weight for bankability.",
        "dimensions": [
            {"name": "Incentive value", "description": "Headline rate and effective net return on qualifying spend."},
            {"name": "Incentive reliability", "description": "Track record of on-time payment and programme stability."},
            {"name": "Cost efficiency", "description": "Local cost base relative to comparable territories."},
            {"name": "Currency advantage", "description": "FX position of the production's home currency."},
            {"name": "Crew depth", "description": "Availability of experienced heads of department and crew."},
            {"name": "Infrastructure", "description": "Stages, post facilities and transport links."},
        ],
    },
    "locationRankings": [
        {
            "name": "United Kingdom", "country": "United Kingdom", "score": 88,
            "costEfficiency": 70, "crewDepth": 96, "crewDepthTier": "Tier 1",
            "infrastructure": 93, "incentiveStrength": 86, "currencyAdvantage": 62,
            "incentiveReliability": 90, "bankabilityLabel": "BANKABLE",
            "rebatePercent": "25.5% net", "paymentSpeed": "3–6 months after certification",
            "financialReturnScore": 88, "financialReturnVerdict": "Bankable",
            "scheduleViabilityScore": 82, "contingencyDaysEstimate": 4,
            "reasoning": [
                "Deepest crew base for a music-heavy, interior-dominant drama.",
                "World-class music post-production capacity.",
                "AVEC gives a bankable, well-understood net rebate.",
            ],
        },
        {
            "name": "Hungary", "country": "Hungary", "score": 79,
            "costEfficiency": 84, "crewDepth": 78, "crewDepthTier": "Tier 2",
            "infrastructure": 82, "incentiveStrength": 82, "currencyAdvantage": 74,
            "incentiveReliability": 80, "bankabilityLabel": "BANKABLE",
            "rebatePercent": "30% cash", "paymentSpeed": "6–9 months",
            "financialReturnScore": 79, "financialReturnVerdict": "Bankable",
            "scheduleViabilityScore": 76, "contingencyDaysEstimate": 6,
            "reasoning": [
                "30% cash rebate with no cultural test.",
                "Korda/Origo set-build capability suits interiors.",
            ],
        },
        {
            "name": "Malta", "country": "Malta", "score": 71,
            "costEfficiency": 76, "crewDepth": 58, "crewDepthTier": "Tier 3",
            "infrastructure": 66, "incentiveStrength": 88, "currencyAdvantage": 70,
            "incentiveReliability": 68, "bankabilityLabel": "VERIFY FIRST",
            "rebatePercent": "40% cash", "paymentSpeed": "post-audit, with interim payments",
            "financialReturnScore": 71, "financialReturnVerdict": "Verify First",
            "scheduleViabilityScore": 69, "contingencyDaysEstimate": 8,
            "reasoning": [
                "Highest headline rate of any ranked territory.",
                "Interim payments improve cash flow on a smaller allocation.",
            ],
        },
    ],
    "scriptOriginCallout": {
        "territory": "Nigeria",
        "hasIncentiveProgramme": False,
        "scenesPct": 46,
        "currencyAdvantage": 88,
        "crewDepthTier": "Tier 3",
    },
    "financialAnalysis": {
        "budgetScenarios": [
            {
                "territory": "United Kingdom", "programme": "AVEC (Audio-Visual Expenditure Credit)",
                "totalBudget": "£30,000,000", "qualifyingSpendPct": "80%", "qualifyingSpend": "£24,000,000",
                "atlDeduction": "£4,500,000", "netQualifyingSpend": "£19,500,000",
                "rateGross": "34%", "rateNet": "25.5%", "grossRebate": "£6,630,000",
                "netRebate": "£5,737,500", "netBudget": "£24,262,500",
                "currencySymbol": "£",
                "totalBudgetValue": 30000000, "qualifyingSpendValue": 24000000,
                "grossRebateValue": 6630000, "netRebateValue": 5737500, "netBudgetValue": 24262500,
                "rateGrossValue": 34, "rateNetValue": 25.5,
                "notes": "AVEC is taxable, so the usable net rate is below the 34% headline.",
            },
            {
                "territory": "Hungary", "programme": "NFI (National Film Institute)",
                "totalBudget": "£15,000,000", "qualifyingSpendPct": "85%", "qualifyingSpend": "£12,750,000",
                "rateGross": "30%", "rateNet": "30%", "grossRebate": "£3,825,000",
                "netRebate": "£3,825,000", "netBudget": "£11,175,000",
                "currencySymbol": "£",
                "totalBudgetValue": 15000000, "qualifyingSpendValue": 12750000,
                "grossRebateValue": 3825000, "netRebateValue": 3825000, "netBudgetValue": 11175000,
                "rateGrossValue": 30, "rateNetValue": 30,
                "notes": "Cash rebate: gross equals net, so the chart has one fewer bar.",
            },
            {
                "territory": "Malta", "programme": "MFC (Malta Film Commission)",
                "totalBudget": "£7,500,000", "qualifyingSpendPct": "80%", "qualifyingSpend": "£6,000,000",
                "rateGross": "40%", "rateNet": "40%", "grossRebate": "£2,400,000",
                "netRebate": "£2,400,000", "netBudget": "£5,100,000",
                "currencySymbol": "£",
                "totalBudgetValue": 7500000, "qualifyingSpendValue": 6000000,
                "grossRebateValue": 2400000, "netRebateValue": 2400000, "netBudgetValue": 5100000,
                "rateGrossValue": 40, "rateNetValue": 40,
                "notes": "Cash rebate on a smaller allocation, suited to a second-unit block.",
            },
        ],
        "paymentTiming": [
            {"territory": "United Kingdom", "totalWeeksMin": 13, "totalWeeksMax": 26, "suspended": False},
            {"territory": "Malta", "totalWeeksMin": 20, "totalWeeksMax": 40, "suspended": False},
            {"territory": "Hungary", "totalWeeksMin": 26, "totalWeeksMax": 39, "suspended": False},
        ],
    },
    "incentiveEstimates": [
        {
            "territory": "United Kingdom", "program": "AVEC (Audio-Visual Expenditure Credit)",
            "rate": "34% gross / 25.5% net", "cap": "80% of core spend",
            "qualifyingSpend": "£24,000,000", "estimatedRebate": "£5,737,500",
            "requirements": ["Pass the AVEC cultural test", "Minimum UK spend threshold"],
            "disclaimer": "Estimate only; final value depends on certified qualifying spend.",
            "dataSource": "Prodculator database", "lastUpdated": "2026-06-01",
            "bankabilityLabel": "BANKABLE",
        },
        {
            "territory": "Hungary", "program": "NFI Cash Rebate",
            "rate": "30% cash", "cap": "No cultural test",
            "qualifyingSpend": "£12,750,000", "estimatedRebate": "£3,825,000",
            "requirements": ["Register with NFI before production activity"],
            "disclaimer": "Estimate only; registration timing is critical.",
            "dataSource": "Prodculator database", "lastUpdated": "2026-05-20",
            "bankabilityLabel": "BANKABLE",
        },
        {
            "territory": "Malta", "program": "MFC Cash Rebate",
            "rate": "40% cash", "cap": "Subject to Maltese spend",
            "qualifyingSpend": "£6,000,000", "estimatedRebate": "£2,400,000",
            "requirements": ["Malta Film Commission application before principal photography"],
            "disclaimer": "Verify current terms; paid post-audit with interim payments.",
            "dataSource": "Prodculator database", "lastUpdated": "2026-04-30",
            "bankabilityLabel": "VERIFY FIRST",
        },
    ],
    "comparables": [
        {"title": "Sound of Metal", "genre": "Drama, Music", "budgetRange": "~$5.4M", "location": "United States", "year": 2019, "source": "Industry records", "relevanceDescription": "Music-driven character drama with heavy sound-design demands."},
        {"title": "Rocks", "genre": "Drama", "budgetRange": "~£1.5M", "location": "United Kingdom", "year": 2019, "source": "BFI", "relevanceDescription": "London-set ensemble drama shot largely in interiors."},
        {"title": "The Harder They Come", "genre": "Drama, Music", "budgetRange": "~$2M", "location": "Jamaica", "year": 1972, "source": "Industry records", "relevanceDescription": "Music-industry story anchored in a specific local scene."},
    ],
    "festivalRecommendations": [
        {"name": "BFI London Film Festival", "tier": "A-list", "location": "London, UK", "oscarQualifying": False, "deadlinePattern": "Submissions open spring, close early summer", "whyMatched": "Strong platform for UK-anchored, music-led drama."},
        {"name": "Toronto International Film Festival", "tier": "A-list", "location": "Toronto, Canada", "oscarQualifying": False, "deadlinePattern": "Deadlines in spring", "whyMatched": "International launch for cross-market titles."},
        {"name": "Series Mania", "tier": "Specialist", "location": "Lille, France", "oscarQualifying": False, "deadlinePattern": "Autumn submissions", "whyMatched": "Leading festival for TV pilots and series."},
    ],
    "distributorRecommendations": [
        {"name": "BBC Film", "verified": True, "primaryMarket": "United Kingdom", "rightsType": "all_rights", "whyMatched": "Backs UK-originated, culturally specific drama.", "submissionProcess": "Via producer or agent introduction.", "scoutsRecommendedFestivals": ["BFI London Film Festival"]},
        {"name": "A24", "verified": True, "primaryMarket": "United States", "rightsType": "north_america", "whyMatched": "Track record with music-led auteur drama.", "submissionProcess": "Festival acquisitions team."},
    ],
    "weatherLogistics": [
        {"territory": "United Kingdom", "bestMonths": ["May", "June", "September"], "weatherRisk": "Medium", "avgTempRange": "10–20°C", "daylightHours": "8–16 hrs seasonal", "infrastructure": "Excellent stages and transport links.", "travelVisa": "Straightforward for most crew.", "seasonalConsiderations": "Interiors de-risk weather exposure."},
        {"territory": "Hungary", "bestMonths": ["May", "June", "September"], "weatherRisk": "Low", "avgTempRange": "12–26°C", "daylightHours": "9–16 hrs seasonal", "infrastructure": "Korda/Origo stages, strong build capacity.", "travelVisa": "EU access simplifies movement.", "seasonalConsiderations": "Stable summer window."},
        {"territory": "Malta", "bestMonths": ["April", "May", "October"], "weatherRisk": "Low", "avgTempRange": "16–30°C", "daylightHours": "10–14 hrs seasonal", "infrastructure": "Water tanks and coastal access.", "travelVisa": "EU access.", "seasonalConsiderations": "Avoid peak-summer heat for interiors."},
    ],
    "fundingOpportunities": [
        {"type": "Fund", "name": "BFI Filmmaking Fund", "genre": ["Drama"], "deadline": "Rolling", "notes": "Development and production support for UK-qualifying projects.", "website": "https://www.bfi.org.uk", "tier": "National"},
        {"type": "Fund", "name": "NFI Hungary Support", "genre": ["Drama"], "deadline": "Quarterly", "notes": "Production support alongside the cash rebate.", "website": "https://nfi.hu", "tier": "National"},
        {"type": "Festival", "name": "Series Mania Forum", "genre": ["Drama"], "deadline": "Autumn", "notes": "Co-production and buyer introductions for series.", "website": "https://seriesmania.com", "tier": "International"},
    ],
    "nextSteps": [
        {"action": "Apply for AVEC cultural test certification", "deadline": "12–16 weeks before the UK shoot", "priority": "URGENT"},
        {"action": "Register with NFI Hungary before any Hungarian production activity", "deadline": "Before Hungarian pre-production", "priority": "HIGH"},
        {"action": "Submit the Malta Film Commission application", "deadline": "Before principal photography", "priority": "HIGH"},
        {"action": "Engage music-licensing counsel for the Fuji performance sequences", "deadline": "Pre-production", "priority": "MEDIUM"},
        {"action": "Lock GBP/NGN exposure before committing Nigerian spend", "deadline": "On budget approval", "priority": "MEDIUM"},
    ],
    "alternativeStrategy": (
        "If music-post depth is less critical than headline rate, lead with Hungary for the interior build and "
        "use Malta for a coastal second unit, keeping the UK for finishing only."
    ),
}
