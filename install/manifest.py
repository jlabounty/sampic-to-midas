"""What gets registered under /Custom, and the naming rules that constrain it.

Every page AND every asset gets its own /Custom/<key> entry holding an ABSOLUTE
path to the file in this checkout. /Custom/Path is never read or written, which
is what lets these pages be a guest in an experiment that already has its own
custom pages -- setting /Custom/Path would redirect everybody else's too.

The key is not free-form. mhttpd uses it as the page name in
`?cmd=custom&page=<key>`, and the side menu matches it, so:

* at most 31 characters (MIDAS NAME_LENGTH);
* no spaces and no regex metacharacters -- the sidenav does an unescaped
  search() against the key;
* a MENU key must contain NO DOT. A dot-less key is served by
  show_custom_page(), which sets no cache headers; a dotted one goes through
  send_fp(), which stamps `Expires: +24h`. A menu page behind a day of browser
  cache is a page whose edits do not appear.
* conversely the ASSETS must keep their dotted filenames, because the HTML
  references them by name -- and their 24 h cache is exactly why every <script>
  and <link> in the HTML carries a ?v= query string.
* nothing may contain `midas.js`, `mhttpd.js`, `controls.js`, `midas.css`,
  `mplot.js` or `mhistory.js` as a substring: mhttpd intercepts those names
  before it ever looks at /Custom.

A trailing '!' on the key hides the entry from the side menu, which is how the
assets stay out of it.
"""

import os
import re
from dataclasses import dataclass
from typing import List, Tuple

NAME_LENGTH = 31

RESERVED_SUBSTRINGS = ("midas.js", "mhttpd.js", "controls.js", "midas.css",
                       "mplot.js", "mhistory.js", "midas.css")

_BAD_KEY_CHARS = re.compile(r"[^A-Za-z0-9_.\-]")


@dataclass(frozen=True)
class Entry:
    key: str
    relpath: str
    in_menu: bool
    description: str

    @property
    def odb_name(self) -> str:
        """'!' suffix keeps an entry out of the side menu."""
        return self.key if self.in_menu else self.key + "!"


ENTRIES: Tuple[Entry, ...] = (
    Entry("SampicScope", "sampic-scope.html", True,
          "live waveforms from the event buffer (no backend)"),
    Entry("SampicStrips", "sampic-strips.html", True,
          "per-strip occupancy across the plane stack (no backend)"),
    Entry("SampicRates", "sampic-rates.html", True,
          "generator rate, health and backlog (no backend)"),
    Entry("sampic-common.js", "js/sampic-common.js", False,
          "shared ODB/geometry helpers"),
    Entry("sampic-banks.js", "js/sampic-banks.js", False,
          "AD00/AT00 decoder"),
    Entry("sampic-scope.js", "js/sampic-scope.js", False, "scope page"),
    Entry("sampic-strips.js", "js/sampic-strips.js", False, "strip map page"),
    Entry("sampic-rates.js", "js/sampic-rates.js", False, "rates page"),
    Entry("sampic.css", "css/sampic.css", False, "the little midas.css does not cover"),
)


def pages_dir(base: str = None) -> str:
    """Absolute path of the pages/ directory in this checkout."""
    if base:
        return os.path.abspath(base)
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "pages")


def check_key(key: str, in_menu: bool) -> List[str]:
    problems = []
    if not key:
        problems.append("empty key")
        return problems
    if len(key) > NAME_LENGTH:
        problems.append(f"{key!r} is {len(key)} chars, over the {NAME_LENGTH} limit")
    if _BAD_KEY_CHARS.search(key):
        problems.append(f"{key!r} has characters that are unsafe in a page name/regex")
    if in_menu and "." in key:
        problems.append(f"{key!r} is a menu key containing a dot, so mhttpd would "
                        "serve it with a 24 h Expires header")
    low = key.lower()
    for bad in RESERVED_SUBSTRINGS:
        if bad in low:
            problems.append(f"{key!r} contains {bad!r}, which mhttpd intercepts "
                            "before /Custom")
    return problems


def check_entry(entry: Entry, base: str = None) -> List[str]:
    problems = check_key(entry.key, entry.in_menu)
    path = os.path.join(pages_dir(base), entry.relpath)
    if not os.path.isfile(path):
        problems.append(f"{entry.key}: no such file {path}")
    return problems


def check_all(base: str = None) -> List[str]:
    problems: List[str] = []
    seen = set()
    for e in ENTRIES:
        problems.extend(check_entry(e, base))
        if e.odb_name in seen:
            problems.append(f"duplicate ODB key {e.odb_name!r}")
        seen.add(e.odb_name)
    return problems


def resolved(base: str = None, prefix: str = "") -> List[Tuple[str, str, Entry]]:
    """(odb key, absolute path, entry) for every entry, honouring a name prefix."""
    root = pages_dir(base)
    out = []
    for e in ENTRIES:
        key = (prefix + e.key) if (prefix and e.in_menu) else e.key
        name = key if e.in_menu else key + "!"
        out.append((name, os.path.join(root, e.relpath), e))
    return out
