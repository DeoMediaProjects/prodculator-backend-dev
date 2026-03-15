"""Territory-specific data attribution lines and mandatory disclaimers.

All crew and cast rate data is derived from government statistical agency
sources published under open licences. No union/CBA rate data is included.

These constants are injected into report output by ReportValidator._patch_attributions().

NOTE: ISO codes here must match Territory.iso values in app.core.territories.
"""

# Territory ISO code → attribution text for report footer
TERRITORY_ATTRIBUTIONS: dict[str, str] = {
    "US": (
        "Crew/cast rate estimates include data from the U.S. Bureau of Labor Statistics, "
        "Occupational Employment and Wage Statistics."
    ),
    "CA": (
        "Adapted from Statistics Canada, Labour Force Survey. "
        "This does not constitute an endorsement by Statistics Canada of this product."
    ),
    "GB": (
        "Source: Office for National Statistics, Annual Survey of Hours & Earnings, "
        "licensed under Open Government Licence v3."
    ),
    "IE": (
        "Source: Central Statistics Office Ireland, Earnings and Labour Costs Survey, "
        "publicly available data."
    ),
    "FR": (
        "Source: INSEE Enquete Emploi, data.gouv.fr, "
        "Licence Ouverte / Open Licence v2.0."
    ),
    "DE": (
        "Source: Statistisches Bundesamt (Destatis), govdata.de, "
        "Data Licence Germany — Attribution — Version 2.0."
    ),
    "ES": (
        "Source: Instituto Nacional de Estadistica (INE), "
        "Encuesta Anual de Estructura Salarial, publicly available data."
    ),
    "IT": (
        "Source: Istituto Nazionale di Statistica (ISTAT), "
        "publicly available occupational wage data."
    ),
    "AU": (
        "Source: Australian Bureau of Statistics, Labour Force Survey, "
        "licensed under Creative Commons Attribution 4.0."
    ),
    "NZ": (
        "Source: Stats NZ, Linked Employer-Employee Data, "
        "licensed under Creative Commons Attribution 4.0."
    ),
    "ZA": (
        "Source: Statistics South Africa, Quarterly Labour Force Survey, "
        "publicly available data."
    ),
    "CZ": (
        "Source: Czech Statistical Office (CZSO), "
        "publicly available earnings statistics."
    ),
    "HU": (
        "Source: Hungarian Central Statistical Office (KSH), "
        "publicly available earnings data."
    ),
    "IS": (
        "Source: Statistics Iceland (Hagstofa Islands), "
        "publicly available wage statistics."
    ),
    "MT": (
        "Source: National Statistics Office Malta (NSO), "
        "publicly available labour market data."
    ),
}

# Rate basis → calibration note shown in report crew/cast cost sections
RATE_BASIS_NOTES: dict[str, str] = {
    "government_stats_median": (
        "Film and television industry rates on union productions typically range "
        "20-40% above the general occupational median. Rates shown are derived from "
        "government occupational statistics and have not been adjusted for "
        "film-specific union agreements."
    ),
    "union_minimum": (
        "Rates shown are verified union scale minimums from licensed data. "
        "Actual rates may be negotiated above scale based on experience, "
        "budget tier, and market conditions."
    ),
}

# Mandatory disclaimer — must appear in every report containing crew/cast rate data
MANDATORY_DISCLAIMER: str = (
    "All crew and cast rate estimates in this report are derived from publicly available "
    "government occupational wage statistics and government-body film commission guidance. "
    "They represent indicative ranges for budgeting purposes only and do not constitute "
    "union minimum rates, guaranteed rates, or legal obligations of any kind. Actual rates "
    "will vary based on union status, project budget tier, individual experience, and market "
    "conditions at the time of production. Fringe and employer contribution rates shown "
    "separately are indicative — verify with a qualified production accountant before budget "
    "lock. Prodculator accepts no liability for production decisions made in reliance on "
    "rate data in this report."
)
