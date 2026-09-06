"""A local people-search provider that needs no credentials.

This is the default, and it is not a stub. Every provider named in the
brief either charges for API access, has shut down, or withholds contact
values on its free tier, so a demo that only works with a paid key is a
demo that does not work. This one runs offline and filters properly, so
the search screen can be judged on its behaviour rather than on whether
somebody remembered to top up an account.

The records are invented. Names are drawn from across India rather than
one region, and every phone status is ``PRESENT_MASKED``, matching what a
real free-tier search returns: the provider will tell you a mobile exists
and will not tell you what it is.
"""

from __future__ import annotations

from typing import Any

from app.people.models import PhoneStatus
from app.people.providers.base import Prospect, SearchPage, dedupe_key_for
from app.people.schemas import SearchFilters

__all__ = ["FixtureProvider"]

#: (name, title, seniority, company, industry, city, years, skills)
_PEOPLE: tuple[tuple[str, str, str, str, str, str, int, tuple[str, ...]], ...] = (
    (
        "Aditya Menon",
        "Senior Backend Engineer",
        "senior",
        "Razorpay",
        "financial services",
        "Bengaluru",
        7,
        ("python", "fastapi", "postgresql", "aws", "kafka"),
    ),
    (
        "Sneha Kulkarni",
        "Forward Deployed Engineer",
        "senior",
        "Fractal Analytics",
        "software",
        "Bengaluru",
        6,
        ("python", "typescript", "sql", "customer facing"),
    ),
    (
        "Rahul Bhatia",
        "Staff Software Engineer",
        "lead",
        "Swiggy",
        "software",
        "Bengaluru",
        11,
        ("go", "kubernetes", "postgresql", "distributed systems"),
    ),
    (
        "Priya Raghavan",
        "Backend Engineer",
        "ic",
        "Zerodha",
        "financial services",
        "Bengaluru",
        3,
        ("python", "django", "mysql"),
    ),
    (
        "Imran Qureshi",
        "Solutions Engineer",
        "senior",
        "Postman",
        "software",
        "Bengaluru",
        8,
        ("typescript", "api design", "customer facing", "python"),
    ),
    (
        "Ananya Iyer",
        "Data Engineer",
        "senior",
        "Meesho",
        "e-commerce",
        "Bengaluru",
        6,
        ("python", "spark", "airflow", "sql"),
    ),
    (
        "Vikram Shetty",
        "Engineering Manager",
        "manager",
        "PhonePe",
        "financial services",
        "Bengaluru",
        13,
        ("leadership", "java", "system design"),
    ),
    (
        "Deepika Nair",
        "Senior Backend Engineer",
        "senior",
        "CRED",
        "financial services",
        "Bengaluru",
        8,
        ("kotlin", "postgresql", "aws"),
    ),
    (
        "Arjun Deshpande",
        "Platform Engineer",
        "senior",
        "Freshworks",
        "software",
        "Chennai",
        7,
        ("python", "terraform", "aws", "kubernetes"),
    ),
    (
        "Meera Krishnan",
        "Backend Engineer",
        "ic",
        "Zoho",
        "software",
        "Chennai",
        4,
        ("java", "mysql", "spring"),
    ),
    (
        "Karthik Subramanian",
        "Principal Engineer",
        "lead",
        "Ather Energy",
        "manufacturing",
        "Chennai",
        14,
        ("embedded", "c++", "python"),
    ),
    (
        "Nikhil Rao",
        "Full Stack Engineer",
        "senior",
        "Darwinbox",
        "software",
        "Hyderabad",
        6,
        ("typescript", "react", "node", "postgresql"),
    ),
    (
        "Shreya Reddy",
        "Senior Data Scientist",
        "senior",
        "Microsoft",
        "software",
        "Hyderabad",
        9,
        ("python", "machine learning", "llm apis", "sql"),
    ),
    (
        "Rohan Malhotra",
        "Backend Engineer",
        "senior",
        "Amazon",
        "e-commerce",
        "Hyderabad",
        5,
        ("java", "aws", "distributed systems"),
    ),
    (
        "Aishwarya Pillai",
        "Forward Deployed Engineer",
        "senior",
        "Palantir",
        "software",
        "Mumbai",
        7,
        ("python", "typescript", "customer facing", "data integration"),
    ),
    (
        "Sameer Joshi",
        "Engineering Lead",
        "lead",
        "Jio",
        "telecom",
        "Mumbai",
        12,
        ("python", "leadership", "system design"),
    ),
    (
        "Tanvi Shah",
        "Backend Engineer",
        "ic",
        "Zepto",
        "e-commerce",
        "Mumbai",
        3,
        ("python", "fastapi", "redis"),
    ),
    (
        "Aditi Chatterjee",
        "Senior Engineer",
        "senior",
        "Tata 1mg",
        "healthcare",
        "Mumbai",
        8,
        ("python", "django", "postgresql", "aws"),
    ),
    (
        "Harsh Agarwal",
        "Solutions Architect",
        "director",
        "Salesforce",
        "software",
        "Pune",
        15,
        ("architecture", "java", "customer facing"),
    ),
    (
        "Neha Bhosale",
        "Backend Engineer",
        "senior",
        "Mastercard",
        "financial services",
        "Pune",
        6,
        ("java", "spring", "kafka"),
    ),
    (
        "Gaurav Kapoor",
        "DevOps Engineer",
        "senior",
        "Persistent Systems",
        "software",
        "Pune",
        9,
        ("terraform", "kubernetes", "aws", "python"),
    ),
    (
        "Ritika Sharma",
        "Senior Frontend Engineer",
        "senior",
        "Zomato",
        "e-commerce",
        "Gurugram",
        7,
        ("typescript", "react", "next.js"),
    ),
    (
        "Abhishek Yadav",
        "Backend Engineer",
        "senior",
        "Paytm",
        "financial services",
        "Noida",
        6,
        ("java", "kafka", "mysql"),
    ),
    (
        "Kavya Menon",
        "Machine Learning Engineer",
        "senior",
        "Sarvam AI",
        "software",
        "Bengaluru",
        5,
        ("python", "llm apis", "pytorch"),
    ),
    (
        "Siddharth Bose",
        "VP Engineering",
        "vp",
        "Groww",
        "financial services",
        "Bengaluru",
        17,
        ("leadership", "architecture"),
    ),
    (
        "Pooja Verma",
        "QA Automation Engineer",
        "ic",
        "Infosys",
        "software",
        "Pune",
        4,
        ("selenium", "python", "testing"),
    ),
    (
        "Manish Tiwari",
        "Site Reliability Engineer",
        "senior",
        "Flipkart",
        "e-commerce",
        "Bengaluru",
        8,
        ("kubernetes", "go", "observability"),
    ),
    (
        "Lakshmi Narayanan",
        "Senior Product Engineer",
        "senior",
        "Chargebee",
        "software",
        "Chennai",
        7,
        ("ruby", "postgresql", "api design"),
    ),
    (
        "Farhan Ahmed",
        "Backend Engineer",
        "ic",
        "Dream11",
        "gaming",
        "Mumbai",
        3,
        ("go", "redis", "postgresql"),
    ),
    (
        "Divya Suresh",
        "Analytics Engineer",
        "senior",
        "Udaan",
        "e-commerce",
        "Bengaluru",
        6,
        ("sql", "dbt", "python"),
    ),
)

#: Free-tier reality: the provider confirms a mobile exists and withholds
#: the value. A handful have none on file at all, because that is also
#: true of real result sets and the UI has to render it.
_NO_PHONE_EVERY = 7


class FixtureProvider:
    """Filters a built-in set of invented professionals."""

    name = "fixture"

    def supports_phone_reveal(self) -> bool:
        """False, matching every free tier in the brief.

        The UI reads this to say "mobile on file, not available on this
        plan" rather than showing a blank column that looks like a bug.
        """
        return False

    def build_query(self, filters: SearchFilters, limit: int) -> dict[str, Any]:
        """Report the filters as the query, since there is no upstream."""
        return {
            "provider": "fixture",
            "note": "matched locally against a built-in set; no request was sent",
            "titles": filters.titles,
            "cities": filters.cities,
            "seniorities": filters.seniorities,
            "skills": filters.skills_required + filters.skills_nice,
            "min_years": filters.min_years,
            "require_phone": filters.require_phone,
            "size": limit,
        }

    async def search(self, filters: SearchFilters, limit: int) -> SearchPage:
        scored: list[tuple[float, Prospect]] = []

        for index, row in enumerate(_PEOPLE):
            name, title, seniority, company, industry, city, years, skills = row

            if filters.cities and not any(_matches_city(city, wanted) for wanted in filters.cities):
                continue
            if filters.seniorities and seniority not in filters.seniorities:
                continue
            if filters.min_years is not None and years < filters.min_years:
                continue
            if filters.max_years is not None and years > filters.max_years:
                continue
            if any(excluded.lower() in title.lower() for excluded in filters.excluded_titles):
                continue
            if any(excluded.lower() == company.lower() for excluded in filters.exclude_companies):
                continue

            relevance = _relevance(filters, title, skills, industry)
            # A title filter that matches nothing should narrow the set
            # rather than quietly returning everyone.
            if filters.titles and relevance <= 0:
                continue

            phone_status = (
                PhoneStatus.ABSENT
                if index % _NO_PHONE_EVERY == _NO_PHONE_EVERY - 1
                else PhoneStatus.PRESENT_MASKED
            )
            if filters.require_phone and phone_status is PhoneStatus.ABSENT:
                continue

            handle = name.lower().replace(" ", "-")
            key, confidence = dedupe_key_for(
                linkedin_url=f"https://linkedin.com/in/{handle}",
                phone_e164=None,
                full_name=name,
                company_name=company,
                country="india",
            )

            scored.append(
                (
                    relevance,
                    Prospect(
                        provider="fixture",
                        provider_person_id=f"fixture-{index}",
                        dedupe_key=key,
                        dedupe_confidence=confidence,
                        full_name=name,
                        headline=f"{title} at {company}",
                        job_title=title,
                        seniority=seniority,
                        company_name=company,
                        industry=industry,
                        years_experience=years,
                        skills=list(skills),
                        location_city=city,
                        location_country="India",
                        linkedin_url=f"https://linkedin.com/in/{handle}",
                        phone_status=phone_status,
                        phone_e164=None,
                        email_status=PhoneStatus.PRESENT_MASKED,
                        raw={
                            "source": "fixture",
                            "job_title": title,
                            "job_company_name": company,
                            "location_locality": city,
                        },
                        redacted_fields=[],
                    ),
                )
            )

        scored.sort(key=lambda pair: pair[0], reverse=True)
        selected = [prospect for _, prospect in scored[:limit]]

        return SearchPage(
            prospects=selected,
            total_estimated=len(scored),
            credits_charged=0,
            provider_query=self.build_query(filters, limit),
        )

    async def aclose(self) -> None:
        return None


def _matches_city(city: str, wanted: str) -> bool:
    """Match a city, allowing for the names Indian cities go by."""
    aliases = {
        "bengaluru": {"bengaluru", "bangalore", "blr"},
        "mumbai": {"mumbai", "bombay"},
        "gurugram": {"gurugram", "gurgaon"},
        "chennai": {"chennai", "madras"},
        "kolkata": {"kolkata", "calcutta"},
        "pune": {"pune"},
        "hyderabad": {"hyderabad", "hyd"},
        "noida": {"noida"},
    }
    left, right = city.strip().lower(), wanted.strip().lower()
    if left == right:
        return True
    return any(left in group and right in group for group in aliases.values())


def _relevance(filters: SearchFilters, title: str, skills: tuple[str, ...], industry: str) -> float:
    """Rank by how well a record answers the filters, not at random."""
    score = 0.0
    lowered_title = title.lower()
    lowered_skills = {skill.lower() for skill in skills}

    for wanted in filters.titles:
        candidate = wanted.lower()
        if candidate == lowered_title:
            score += 6
        elif candidate in lowered_title or lowered_title in candidate:
            score += 3
        else:
            overlap = set(candidate.split()) & set(lowered_title.split())
            score += len(overlap) * 0.75

    score += sum(3 for skill in filters.skills_required if skill.lower() in lowered_skills)
    score += sum(1 for skill in filters.skills_nice if skill.lower() in lowered_skills)
    score += sum(2 for wanted in filters.industries if wanted.lower() in industry.lower())

    # A deterministic nudge so equally-matched records keep a stable order
    # between requests instead of shuffling under the reader.
    score += (len(title) % 10) / 100
    return score
