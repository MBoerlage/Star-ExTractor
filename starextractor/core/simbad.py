"""Online SIMBAD lookup through the CDS Sesame name resolver (stdlib only)."""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

MIRRORS = (
    "https://cds.unistra.fr/cgi-bin/nph-sesame/-oxpF/S?",  # F: include magnitudes (V for the Plan tab)
    "https://vizier.cfa.harvard.edu/viz-bin/nph-sesame/-oxpF/S?",
)
USER_AGENT = "starextractor"
_last_good: str | None = None  # mirror that answered last: tried first next time (one request per click)


class LookupError_(RuntimeError):
    """Network/service failure (not 'not found')."""


@dataclass
class SimbadResult:
    query: str
    found: bool
    main_id: str = ""
    ra_deg: float | None = None
    dec_deg: float | None = None
    otype: str = ""
    sp_type: str = ""
    vmag: float | None = None
    aliases: list[str] = field(default_factory=list)

    @property
    def usable_name(self) -> str:
        return usable_name(self.main_id)


_PREFIX = re.compile(r"^(?:\*\*|\*|V\*|NAME|EM\*|Cl\*)\s+")


def usable_name(main_id: str) -> str:
    """SIMBAD main identifier -> a name that still resolves but is file/yaml friendly.

    '* bet Lyr' -> 'bet Lyr', 'V* V1500 Cyg' -> 'V1500 Cyg', 'NAME Sheliak' -> 'Sheliak'.
    """
    return re.sub(r"\s+", " ", _PREFIX.sub("", main_id.strip()))


def parse_sesame(xml_text: str, query: str) -> SimbadResult:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise LookupError_(f"unexpected answer from resolver: {exc}") from exc
    res = root.find(".//Resolver")
    if res is None:
        if root.find(".//Target") is None:
            raise LookupError_("unexpected answer from resolver")
        return SimbadResult(query, False)

    def txt(tag):
        e = res.find(tag)
        return e.text.strip() if e is not None and e.text else ""

    ra, de = txt("jradeg"), txt("jdedeg")
    v = res.find("mag[@band='V']/v")
    try:
        vmag = float(v.text) if v is not None and v.text else None
    except ValueError:
        vmag = None
    return SimbadResult(
        query=query, found=True, main_id=txt("oname"),
        ra_deg=float(ra) if ra else None, dec_deg=float(de) if de else None,
        otype=txt("otype"), sp_type=txt("spType"), vmag=vmag,
        aliases=[e.text.strip() for e in res.findall("alias") if e.text],
    )


def _fetch(url: str, timeout: float) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def lookup(name: str, timeout: float = 15.0, fetch=_fetch) -> SimbadResult:
    """Resolve ``name`` in SIMBAD. Raises LookupError_ if no mirror answered sensibly."""
    q = name.strip()
    if not q:
        return SimbadResult(q, False)
    global _last_good
    errors = []
    order = sorted(MIRRORS, key=lambda m: m != _last_good)
    for base in order:
        try:
            text = fetch(base + urllib.parse.quote(q), timeout)
            result = parse_sesame(text, q)
            _last_good = base
            return result
        except (OSError, LookupError_) as exc:
            errors.append(f"{base.split('/')[2]}: {exc}")
    raise LookupError_("SIMBAD lookup failed - " + "; ".join(errors))


def simbad_job(ctx: Any, name: str) -> dict[str, Any]:
    """Runs in a worker (Plan, Acquisition): one Sesame request, offline-safe."""
    try:
        r = lookup(name, timeout=4.0)  # per mirror; Sesame answers in well under a second
    except LookupError_ as exc:
        return {"query": name, "found": False, "offline": True, "error": str(exc)}
    if not r.found:
        return {"query": name, "found": False}
    return {"query": name, "found": True, "main_id": r.main_id, "name": r.usable_name,
            "ra": r.ra_deg, "dec": r.dec_deg, "sp_type": r.sp_type, "otype": r.otype, "vmag": r.vmag}
