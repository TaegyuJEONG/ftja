"""Search geography shared by job boards and future ATS providers."""

from dataclasses import dataclass

SOURCES = ("linkedin", "indeed", "glassdoor", "zip_recruiter", "bayt", "naukri", "bdjobs", "hellowork")

# JobSpy's Country enum for Indeed. Keep this explicit: an unsupported
# country must never silently fall back to Indeed's default (USA).
INDEED_COUNTRIES = frozenset("""Argentina Australia Austria Bahrain Bangladesh Belgium Bulgaria Brazil Canada Chile
China Colombia Costa_Rica Croatia Cyprus Czech_Republic Denmark Ecuador Egypt Estonia Finland France Germany
Greece Hong_Kong Hungary India Indonesia Ireland Israel Italy Japan Kuwait Latvia Lithuania Luxembourg Malaysia
Malta Mexico Morocco Netherlands New_Zealand Nigeria Norway Oman Pakistan Panama Peru Philippines Poland Portugal
Qatar Romania Saudi_Arabia Singapore Slovakia Slovenia South_Africa South_Korea Spain Sweden Switzerland Taiwan
Thailand Turkey Ukraine United_Arab_Emirates UK USA Uruguay Venezuela Vietnam""".split())
# JobSpy 1.3.0 Country entries with a Glassdoor domain. This selects the
# request domain, not the countries that Glassdoor can search.
GLASSDOOR_COUNTRIES = frozenset("""Argentina Australia Austria Belgium Brazil Canada France Germany
Hong_Kong India Ireland Italy Malaysia Mexico Netherlands New_Zealand Singapore
Spain Switzerland UK USA Vietnam""".split())

EU = ("Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czech Republic",
      "Denmark", "Estonia", "Finland", "France", "Germany", "Greece", "Hungary",
      "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg", "Malta",
      "Netherlands", "Poland", "Portugal", "Romania", "Slovakia", "Slovenia",
      "Spain", "Sweden")
EUROPE = EU + ("Albania", "Andorra", "Belarus", "Bosnia and Herzegovina",
               "Iceland", "Kosovo", "Liechtenstein", "Moldova", "Monaco",
               "Montenegro", "North Macedonia", "Norway", "Russia",
               "San Marino", "Serbia", "Switzerland", "UK", "Ukraine",
               "Vatican City")
# Glassdoor's autocomplete is localized to the request domain. JobSpy takes
# the first result, so use a domain/query pair verified to resolve to a
# COUNTRY, not a similarly named city. The unresolved countries either had
# no country match or returned unrelated postings in a live search.
GLASSDOOR_QUERY = {"Malta": ("France", "Malta"),
                   "UK": ("USA", "United Kingdom")}
GLASSDOOR_UNRESOLVED = frozenset({"Kosovo", "Montenegro", "North Macedonia", "San Marino"})
# MENA has no universally fixed membership. This is an explicit FTJA preset.
MENA = ("Algeria", "Bahrain", "Egypt", "Iran", "Iraq", "Israel", "Jordan",
        "Kuwait", "Lebanon", "Libya", "Morocco", "Oman", "Qatar",
        "Saudi Arabia", "Syria", "Tunisia", "Turkey", "United Arab Emirates", "Yemen")
REGIONS = {"EU": EU, "Europe": EUROPE, "MENA": MENA}
# Checked against Bayt country pages: unknown slugs return 404 and JobSpy
# silently widens the search. Route only countries with a working page.
BAYT_COUNTRIES = frozenset(EUROPE + MENA + ("USA", "Canada", "India", "Bangladesh")) - {
    "Croatia", "Slovakia", "North Macedonia", "Russia", "Vatican City", "Israel"}
ALIASES = {"eu": "EU", "european union": "EU", "europe": "Europe", "mena": "MENA",
           "middle east and north africa": "MENA", "us": "USA", "united states": "USA",
           "united states of america": "USA", "united kingdom": "UK", "uae": "United Arab Emirates"}
COUNTRIES = {name.replace("_", " ").casefold(): name.replace("_", " ") for name in INDEED_COUNTRIES}
COUNTRIES.update({name.casefold(): name for group in REGIONS.values() for name in group})


@dataclass(frozen=True)
class SearchLocation:
    label: str
    country: str = ""
    locality: str = ""
    region: str = ""


def resolve_location(raw: str) -> SearchLocation:
    value = raw.strip()
    if not value:
        raise ValueError("Choose a location before starting a search")
    canonical = ALIASES.get(value.casefold(), value)
    if canonical in REGIONS:
        return SearchLocation(canonical, region=canonical)
    if canonical.casefold() in COUNTRIES:
        country = COUNTRIES[canonical.casefold()]
        return SearchLocation(country, country=country)
    if "," in value:
        locality, country_name = [part.strip() for part in value.rsplit(",", 1)]
        country_name = ALIASES.get(country_name.casefold(), country_name)
        country = COUNTRIES.get(country_name.casefold())
        if locality and country:
            return SearchLocation(f"{locality}, {country}", country=country, locality=locality)
    raise ValueError(f"Unknown location: {raw}. Choose a country, region, or 'City, Country'.")


def search_targets(location: SearchLocation, site: str) -> list[dict]:
    """Return provider arguments. Unsupported regional countries are explicit."""
    if site == "linkedin":
        if location.region == "EU":
            return [{"site": site, "location": "European Union"}]
        countries = REGIONS[location.region] if location.region else (location.country,)
        return [{"site": site, "location": country if location.region else location.label}
                for country in countries]
    if site not in SOURCES:
        raise ValueError(f"Unsupported source: {site}")
    countries = REGIONS[location.region] if location.region else (location.country,)
    if site in ("zip_recruiter", "bayt", "naukri", "bdjobs", "hellowork"):
        # JobSpy 1.3.0's ZipRecruiter adapter requests ziprecruiter.com and
        # parses US/Canada locations; ZipRecruiter's French site is separate.
        available = {"zip_recruiter": {"USA", "Canada"}, "bayt": BAYT_COUNTRIES,
                     "naukri": {"India"}, "bdjobs": {"Bangladesh"},
                     "hellowork": {"France"}}[site]
        supported = [country for country in countries if country in available]
        if site == "bayt":
            return [{"site": site, "location": location.label if location.locality else country}
                    for country in supported]
        if site == "zip_recruiter":
            return [{"site": site, "location": location.label if location.locality else
                     {"USA": "United States", "Canada": "Canada"}[country]} for country in supported]
        return [{"site": site, "location": location.locality if location.locality else "",
                 "display_location": location.label if location.locality else country}
                for country in supported]
    supported = [country for country in countries if country.replace(" ", "_") in INDEED_COUNTRIES] if site == "indeed" else [
        country for country in countries if country not in GLASSDOOR_UNRESOLVED]
    if not supported:
        return []  # other selected boards can still search this place
    if site == "glassdoor":
        return [{"site": site,
                 "country_indeed": GLASSDOOR_QUERY.get(country, (country if country.replace(" ", "_") in GLASSDOOR_COUNTRIES else "USA", country))[0],
                 "location": (location.label if location.locality else GLASSDOOR_QUERY.get(country, ("", country))[1])}
                for country in supported]
    return [{"site": site, "country_indeed": country,
             "location": location.locality if not location.region else ""} for country in supported]


def unsupported_countries(location: SearchLocation, site: str) -> list[str]:
    if site not in SOURCES or site == "linkedin":
        return []
    countries = REGIONS[location.region] if location.region else (location.country,)
    if site in ("zip_recruiter", "bayt", "naukri", "bdjobs", "hellowork"):
        available = {"zip_recruiter": {"USA", "Canada"}, "bayt": BAYT_COUNTRIES,
                     "naukri": {"India"}, "bdjobs": {"Bangladesh"},
                     "hellowork": {"France"}}[site]
        return [country for country in countries if country not in available]
    return [country for country in countries if
            (country.replace(" ", "_") not in INDEED_COUNTRIES if site == "indeed"
             else country in GLASSDOOR_UNRESOLVED)]
