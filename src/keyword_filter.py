import re

# Categories where every post is relevant — skip keyword filtering entirely.
# Match by lowercased category slug; collisions across sources are OK because
# both r/nessus and (hypothetically) a Discourse "nessus" category are equally on-topic.
# These are sources that are *already* topically scoped (vendor communities,
# review categories), so the keyword filter would just discard signal.
ALWAYS_PASS_CATEGORIES = {
    # Reddit
    "nessus", "qualys",
    "patchmanagement", "sccm", "intune",
    # Tenable Community boards (community.tenable.com)
    "vulnerability-watch",
    "tenable-research-release-highlights",
    "product-announcements",
    # PeerSpot review categories
    "vulnerability-management",
    "patch-management",
    "unified-endpoint-management-uem",
    "configuration-management",
    # G2 market segments (vulnerability-management already covered above)
    "exposure-and-asset-management",
    "appsec-and-cloud",
    "endpoint-and-patch-management",
    # Hacker News search-query slugs — query already enforces topic relevance
    "vulnerability-scanner",
    "attack-surface",
    "exposure-management",
    "asset-discovery",
    "endpoint-management",
    "security-hardening",
    # Rapid7 Discuss vendor boards — every post is about a Rapid7 product
    "insightvm",
    "surface-command",
    "insightidr",
    "insightappsec",
    # Gartner Peer Insights — reviews are already scoped to security markets
    "vulnerability-assessment",
    "exposure-management",
    "attack-surface-management",
    "endpoint-management",
    # GitHub repos — every issue is on the OSS scanner / asset / fleet /
    # endpoint / hardening tool
    "projectdiscovery/nuclei",
    "zaproxy/zaproxy",
    "greenbone/openvas-scanner",
    "osquery/osquery",
    "fleetdm/fleet",
    "wazuh/wazuh",
    "complianceascode/content",
    "openscap/openscap",
    "cisofy/lynis",
    # Stack Exchange: NOT listed — security.stackexchange.com and serverfault.com
    # are broad enough that we want the keyword filter to gate them.
}

HIGH_SIGNAL = [
    # Competitor names
    "nessus", "tenable", "tenable.sc", "tenable.io", "tenable.asm",
    "qualys", "rapid7", "nexpose", "insightvm",
    "holm security", "arctic wolf", "falcon spotlight",
    "nucleus security", "vulcan cyber", "brinqa",
    "cycognito", "randori", "runzero",
    "wiz vulnerability", "orca security",
    "censys", "shodan",
    "armis", "servicenow vr",
    "securityscorecard", "bitsight",
    # Endpoint / patch / configuration management competitors
    "ninjaone", "ninja one", "automox", "action1", "action 1",
    "ivanti", "ivanti neurons", "tanium",
    "furl", "furl.ai", "remedio", "gytpol",
    "manageengine", "endpoint central", "patch manager plus",
    "pdq deploy", "pdq connect", "quest kace", "kace",
    "bigfix", "hcl bigfix", "syxsense", "adaptiva", "baramundi",
    "heimdal", "vicarius", "kaseya vsa", "datto rmm", "connectwise automate",
    "jamf", "kandji", "jumpcloud", "scappman",
    "intune", "microsoft intune", "sccm", "mecm", "wsus",
    # Open source / other scanners
    "nuclei", "trivy", "openvas", "greenbone", "nikto",
    "owasp zap", "zaproxy", "burp suite", "acunetix",
    "invicti", "netsparker", "grype", "snyk", "clair",
    # Core domain
    "vulnerability management", "vm program", "vm tool",
    "exposure management", "exposure management platform",
    "attack surface management", "attack surface",
    "vulnerability scanner", "vuln scanner", "vuln management",
    "vuln scan", "vulnerability assessment",
    "asm tool", "asm platform",
    "continuous monitoring",
    # Endpoint / patch / configuration management core domain
    "patch management", "patch management tool", "patching tool",
    "third party patching", "third-party patching", "3rd party patching",
    "endpoint management", "unified endpoint management", "uem",
    "endpoint management tool", "device management",
    "configuration management", "security configuration management",
    "configuration drift", "config drift", "misconfiguration",
    "security baseline", "hardening", "system hardening", "os hardening",
    "cis benchmark", "cis benchmarks", "stig", "disa stig", "security hardening",
    "patch compliance", "patch deployment", "patch ring", "patch rings",
    "patch automation", "automated patching", "autonomous remediation",
    "continuous remediation", "remediation automation",
    "software deployment", "application deployment", "package deployment",
    "rmm tool", "rmm platform", "fleet management",
    "golden image", "image management", "endpoint hardening",
    "config as code", "desired state configuration",
    "external facing", "internet facing", "internet-facing",
    "perimeter scan", "cve",
    # Pain points (how people actually talk)
    "patch prioritization", "remediation workflow", "remediation process",
    "tracking remediation", "remediation tracking",
    "too many findings", "too many vulnerabilities", "too many vulns",
    "alert fatigue", "finding fatigue", "vulnerability fatigue",
    "false positives", "noisy scan", "scan noise",
    "overwhelmed with vulns", "drowning in findings",
    "vulnerability overload", "can't keep up with patching",
    "prioritize vulnerabilities", "which vulns to fix first",
    "risk based vulnerability", "vulnerability triage", "triaging findings",
    "finding owners", "asset owners", "accountability remediation",
    "sla breach", "remediation sla",
    "aging vulnerabilities", "old findings",
    # Endpoint / patch / config pain points
    "failed patches", "patch failures", "patch broke", "patch bricked",
    "patch rollback", "rollback patch", "reboot fatigue", "patch reboots",
    "missing patches", "unpatched machines", "unpatched endpoints",
    "third party app updates", "3rd party app updates", "app updates lagging",
    "patch tuesday scramble", "patch backlog", "patching backlog",
    "config drift", "drifted config", "inconsistent configuration",
    "stig compliance", "cis hardening", "hardening backlog",
    "gpo sprawl", "too many gpos", "group policy mess",
    "sccm co-management", "sccm to intune", "intune migration",
    "agent sprawl", "too many agents", "endpoint agent bloat",
    "can't keep up with patching", "patching is manual",
    "no patch visibility", "patch reporting", "patch compliance reporting",
    # Workflow / integration pain
    "jira tickets security", "vuln ticketing", "ticketing integration",
    "servicenow security", "servicenow vulnerability",
    "vulnerability dashboard", "security dashboard",
    "executive reporting security", "board reporting security",
    "vulnerability metrics", "kpi security",
    "remediation rate", "scan coverage",
    "asset inventory security", "cmdb security",
    # Shopping / switching
    "nessus alternative", "replace tenable", "replace nessus",
    "qualys alternative", "replace qualys", "switching from nessus",
    "looking for a scanner", "recommend a scanner",
    "vulnerability tool recommendation",
    "best vulnerability scanner", "vulnerability scanner comparison",
    "compare security tools", "which scanner", "scanner recommendation",
    "tenable vs", "qualys vs", "rapid7 vs", "nessus vs", "insightvm vs",
    # Endpoint / patch / config shopping & switching
    "patch management recommendation", "best patch management",
    "patch management comparison", "patching tool recommendation",
    "sccm alternative", "wsus alternative", "replace wsus", "replace sccm",
    "ninjaone vs", "automox vs", "action1 vs", "ivanti vs", "tanium vs",
    "intune vs", "manageengine vs",
    "rmm recommendation", "best rmm", "uem recommendation",
    "looking for a patching tool", "recommend a patch tool",
    "endpoint management recommendation",
    # EASM / external exposure
    "external attack surface", "easm", "internet-facing assets",
    "shadow it discovery", "asset discovery",
    "external exposure", "exposed assets", "external scan",
    "outside-in", "unknown assets", "forgotten assets",
    "subdomain discovery", "certificate monitoring", "expired certificates",
]

MEDIUM_SIGNAL = [
    "cve prioritization", "cvss scoring", "cvss score",
    "security tool", "security tooling", "security stack",
    "pen test findings", "pentest results", "penetration test",
    "pentesting", "red team", "purple team", "offensive security",
    "device management", "mobile device management",
    "cloud security posture", "cspm",
    "risk scoring", "risk prioritization", "risk-based",
    "compliance scan", "compliance scanning", "cis benchmark",
    "vulnerability report", "scan results", "scan report",
    "security automation", "automate remediation",
    "sla remediation", "mean time to remediate", "mttr",
    "vulnerability backlog", "finding backlog",
    "appsec",
    # Security operations
    "soc analyst", "security operations",
    "vulnerability disclosure", "responsible disclosure",
    "bug bounty findings",
    # Third-party / supply chain
    "third party risk", "vendor risk", "supply chain security",
    # Cloud / container
    "container scanning", "image scanning",
    "cloud misconfiguration", "infrastructure security",
    # Patch management
    "patch management", "patching cadence", "patch tuesday",
    "unpatched", "missing patches",
    # Endpoint / configuration management (adjacent — LLM confirms relevance)
    "endpoint management", "device management", "mdm", "uem",
    "group policy", "gpo", "sccm", "intune", "wsus", "mecm",
    "configuration management", "config drift", "misconfiguration",
    "security baseline", "hardening", "cis benchmark", "stig",
    "rmm", "remote monitoring and management", "msp tooling",
    "software deployment", "third party patching", "autonomous remediation",
    "fleet management", "endpoint agent", "co-management",
]

DISCARD_PATTERNS = [
    "job posting", "hiring", "salary", "resume", "résumé",
    "ctf", "capture the flag",
    "homework", "assignment", "exam question",
]

MALWARE_ANALYSIS = re.compile(r"malware\s+analysis", re.IGNORECASE)
VM_KEYWORDS = re.compile(
    r"vulnerability management|exposure management|attack surface"
    r"|nessus|tenable|qualys|rapid7|easm|vuln scan"
    r"|patch management|endpoint management|configuration management"
    r"|hardening|misconfiguration|ninjaone|automox|action1|ivanti|tanium"
    r"|intune|sccm|remediation",
    re.IGNORECASE,
)


def _compile_alternation(keywords: list[str]) -> re.Pattern:
    """Compile a list of keyword phrases into a single word-boundary alternation.

    Word boundaries (\\b) prevent false positives like 'easm' matching 'please ask'
    or 'asm' matching 'plasma'. The list is sorted longest-first so multi-word
    phrases match before their substrings.
    """
    sorted_kws = sorted(keywords, key=len, reverse=True)
    pattern = r"\b(?:" + "|".join(re.escape(kw) for kw in sorted_kws) + r")\b"
    return re.compile(pattern, re.IGNORECASE)


_HIGH_RE = _compile_alternation(HIGH_SIGNAL)
_MEDIUM_RE = _compile_alternation(MEDIUM_SIGNAL)
_DISCARD_RE = _compile_alternation(DISCARD_PATTERNS)


def should_process(title: str, body: str, engagement: int,
                   source: str = "reddit", category: str = "") -> bool:
    """Return True if the item should be sent to the relevance scorer.

    `engagement` is the upvote count for Reddit and the reply count for forums.
    Reddit's /new.json returns score=1 for fresh posts (poster's auto-upvote),
    so the engagement gate is meaningless there — it's only applied to forum
    sources where reply_count is a real signal.
    """
    # In high-intent categories, pass everything through
    if category.lower() in ALWAYS_PASS_CATEGORIES:
        return True

    combined = f"{title} {body}"

    # Discard rules
    if _DISCARD_RE.search(combined):
        return False
    if MALWARE_ANALYSIS.search(combined) and not VM_KEYWORDS.search(combined):
        return False

    # High signal — always pass
    if _HIGH_RE.search(combined):
        return True

    # Medium signal — Reddit posts on /new.json always have score=1, so the
    # engagement gate would silently drop everything; let the LLM filter
    # instead. For forum sources, require some replies.
    if _MEDIUM_RE.search(combined):
        if source == "reddit":
            return True
        if engagement > 1:
            return True

    return False
