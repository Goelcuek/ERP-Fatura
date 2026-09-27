"""List UI strings that have no Turkish translation.

    python scripts/i18n_check.py          # prints missing strings
    python scripts/i18n_check.py --stubs  # prints them as dict entries to paste into translations_tr.py
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CALL = re.compile(r"""(?<![\w.])_\(\s*(?P<q>["'])(?P<s>(?:\\.|(?!(?P=q)).)*)(?P=q)""", re.S)


def extract():
    found = set()
    for base, _dirs, files in os.walk(os.path.join(ROOT, "app")):
        for f in files:
            if f.endswith((".py", ".html")) and f != "translations_tr.py":
                with open(os.path.join(base, f), encoding="utf-8") as fh:
                    for m in CALL.finditer(fh.read()):
                        found.add(m.group("s").encode().decode("unicode_escape").encode("latin-1").decode("utf-8")
                                  if "\\" in m.group("s") else m.group("s"))
    # strings translated indirectly (label tables, integrator metadata, validation messages)
    from app.models import UNITS
    from app.services.integrators import REGISTRY
    from app.web import KIND_LABELS, PROFILE_LABELS, STATUS_LABELS, TYPE_LABELS

    for table in (STATUS_LABELS, PROFILE_LABELS, TYPE_LABELS, KIND_LABELS, UNITS):
        found.update(table.values())
    from app.services.integrators.base import CAPABILITIES, XmlOptions

    found.update(CAPABILITIES.values())
    found.update(XmlOptions.LABELS.values())
    found.update(["Test", "Production"])
    from app.services.assistant.agent import NOT_UNDERSTOOD, STILL_DOWNLOADING

    found.update([NOT_UNDERSTOOD, STILL_DOWNLOADING])
    for cls in REGISTRY.values():
        found.update([cls.label, cls.description])
        for f in cls.fields:
            found.add(f.label)
            if f.help:
                found.add(f.help)
    found.update(indirect_messages())
    return found


def indirect_messages():
    """Messages raised as exceptions and translated where they are displayed."""
    # ToolError messages go to the language model only, never to the user
    pat = re.compile(r"""(?:raise (?!ToolError)\w+Error\(|errors\.append\(|message=)\s*f?(["'])(.+?)\1""")
    out = set()
    integ_dir = os.path.join(ROOT, "app", "services", "integrators")
    rels = ["app/services/ubl.py", "app/services/invoicing.py", "app/services/backup.py", "app/services/branding.py",
            "app/services/workshop.py"]
    rels += [f"app/services/integrators/{f}" for f in sorted(os.listdir(integ_dir)) if f.endswith(".py")]
    asst_dir = os.path.join(ROOT, "app", "services", "assistant")
    rels += [f"app/services/assistant/{f}" for f in sorted(os.listdir(asst_dir)) if f.endswith(".py")]
    for rel in rels:
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            for line in fh:
                m = pat.search(line)
                if m and "{" not in m.group(2):
                    out.add(m.group(2))
    return out


def missing():
    from app.translations_tr import TR

    return sorted(s for s in extract() if s not in TR)


if __name__ == "__main__":
    miss = missing()
    for s in miss:
        print(f"    {s!r}: {s!r}," if "--stubs" in sys.argv else s)
    print(f"# {len(miss)} missing", file=sys.stderr)
