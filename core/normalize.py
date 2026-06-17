VALID_FUNCTIONS = {"Engineering", "Sales", "Marketing", "Operations", "Finance"}
VALID_LOCATIONS = {"Remote", "In Person", "Hybrid"}


def normalize_function(raw: str) -> str:
    """Map a raw job function/team string to one of the 5 allowed values, or ''."""
    if not raw:
        return ""
    r = raw.lower().strip()
    for f in VALID_FUNCTIONS:
        if f.lower() == r:
            return f
    # HR abbreviation — "human res" keyword misses "HR Manager", "VP of HR", etc.
    if r == "hr" or r.startswith("hr ") or r.endswith(" hr"):
        return "Operations"
    # Finance first — must precede Engineering to catch "Financial Analyst", "Accounting", etc.
    if any(k in r for k in ["financ", "accounting", "invest", "treasury", "fp&a", "controller", "audit", "tax", "payroll"]):
        return "Finance"
    # "developer" not "develop" — avoids matching "Sales Development Representative"
    # "program manag" checked in Operations; "architect" here catches Solutions/Data Architects
    if any(k in r for k in ["engineer", "developer", "software", "data", "product", "design", "qa", "devops", "infra", "security", "techni", "tech lead", "tech manager", "ai", "ml", "machine learn", "platform", "ux", "research", "robotics", "hardware", "mechanical", "electrical", "embedded", "firmware", "architect", "analytic", "scientist", "manufactur", "programmer"]):
        return "Engineering"
    if any(k in r for k in ["sale", "business dev", "account exec", "revenue", "bdr", "sdr"]):
        return "Sales"
    if any(k in r for k in ["market", "growth", "brand", "content", "seo", "demand", "communicat", "public relation", "social media", "advert"]):
        return "Marketing"
    if any(k in r for k in ["operat", "people", "human res", "recruit", "admin", "customer success", "support", "legal", "compli", "implement", "general manager", "event", "relation", "coordinator", "specialist", "project", "program manag", "procure", "supply chain", "logistic", "talent", "facilit"]):
        return "Operations"
    return ""


def normalize_location(raw: str) -> str:
    """Map a raw location string to Remote / Hybrid / In Person. Defaults to In Person."""
    r = (raw or "").lower().strip()
    if any(k in r for k in ["remote", "work from home", "wfh", "anywhere", "distributed", "virtual", "nationwide", "us only", "worldwide", "global"]):
        return "Remote"
    if "hybrid" in r:
        return "Hybrid"
    return "In Person"
