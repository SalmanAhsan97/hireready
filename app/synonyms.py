"""Common abbreviations and alternate names for the same skill, so "K8s" on a resume counts for "Kubernetes".

Curated from the synonym map in sunnypatell/ats-screener (MIT). Groups that aren't true synonyms
(data science ≠ data analytics) and two-letter forms that collide with ordinary words (go, es, pa, ma)
are left out. The first entry of each group is the name most postings use.
"""
import re

GROUPS = [
    # languages and frameworks
    ["javascript", "js", "ecmascript"],
    ["typescript"],
    ["c++", "cpp"],
    ["c#", "csharp"],
    ["golang", "go lang"],
    ["objective-c", "objc", "obj-c"],
    ["react", "reactjs", "react.js"],
    ["angular", "angularjs", "angular.js"],
    ["vue", "vuejs", "vue.js"],
    ["next.js", "nextjs"],
    ["node.js", "nodejs"],
    ["express", "expressjs", "express.js"],
    ["django rest framework", "drf"],
    ["spring boot", "springboot"],
    [".net", "dotnet", "asp.net"],
    ["ruby on rails", "rails"],
    ["fastapi", "fast api"],
    # data stores
    ["postgresql", "postgres"],
    ["microsoft sql server", "mssql", "sql server", "t-sql", "tsql"],
    ["mongodb", "mongo"],
    ["elasticsearch", "elastic search"],
    ["dynamodb", "dynamo db"],
    # cloud and infrastructure
    ["amazon web services", "aws"],
    ["google cloud platform", "gcp", "google cloud"],
    ["microsoft azure", "azure"],
    ["kubernetes", "k8s"],
    ["ci/cd", "cicd", "continuous integration", "continuous delivery", "continuous deployment"],
    ["infrastructure as code", "iac"],
    ["github actions", "gh actions"],
    # data and ML
    ["machine learning", "ml"],
    ["artificial intelligence", "ai"],
    ["natural language processing", "nlp"],
    ["large language models", "llm", "llms"],
    ["scikit-learn", "sklearn", "scikit learn"],
    ["pytorch", "torch"],
    ["tensorflow"],
    ["business intelligence"],
    ["etl", "extract transform load"],
    ["data warehouse", "data warehousing", "dwh"],
    ["power bi", "powerbi", "microsoft power bi"],
    ["tableau", "tableau desktop"],
    # finance
    ["gaap", "generally accepted accounting principles"],
    ["ifrs", "international financial reporting standards"],
    ["p&l", "profit and loss", "pnl"],
    ["kpi", "key performance indicator", "kpis"],
    ["erp", "enterprise resource planning"],
    ["dcf", "discounted cash flow"],
    ["m&a", "mergers and acquisitions"],
    ["aml", "anti-money laundering", "anti money laundering"],
    ["kyc", "know your customer"],
    # healthcare
    ["ehr", "electronic health records", "emr", "electronic medical records"],
    ["hipaa"],
    ["acls", "advanced cardiovascular life support"],
    ["bls", "basic life support"],
    ["gmp", "good manufacturing practice"],
    # marketing and sales
    ["seo", "search engine optimization"],
    ["sem", "search engine marketing"],
    ["ppc", "pay per click", "pay-per-click"],
    ["crm", "customer relationship management"],
    ["salesforce", "sfdc"],
    ["google analytics", "ga4"],
    ["social media marketing", "smm"],
    ["cms", "content management system"],
    ["a/b testing", "ab testing", "split testing"],
    ["conversion rate optimization", "cro"],
    ["ltv", "customer lifetime value", "clv"],
    ["nps", "net promoter score"],
    ["mql", "marketing qualified lead"],
    # people and project
    ["human resources", "hr"],
    ["hris", "human resource information system"],
    ["ats", "applicant tracking system"],
    ["dei", "diversity equity and inclusion", "de&i"],
    ["pmp", "project management professional"],
    ["csm", "certified scrum master"],
    ["jira", "atlassian jira"],
    ["six sigma", "6 sigma", "6sigma"],
    ["scm", "supply chain management"],
    ["rfp", "request for proposal"],
    ["sla", "service level agreement"],
    ["gdpr", "general data protection regulation"],
    ["microsoft office", "ms office", "office 365", "microsoft 365"],
    ["microsoft excel", "ms excel", "excel"],
    ["microsoft powerpoint", "ms powerpoint", "powerpoint"],
]


def _key(term: str) -> str:
    return re.sub(r"[^a-z0-9+#.&/]+", " ", term.lower()).strip()


_INDEX = {_key(t): g for g in GROUPS for t in g}


def variants(term: str) -> list[str]:
    """Other names for the same skill (not including the term itself)."""
    group = _INDEX.get(_key(term), [])
    return [t for t in group if _key(t) != _key(term)]
