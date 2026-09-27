"""Minimal SOAP 1.1 client helpers built on lxml + requests (no WSDL tooling needed).

Most Turkish integrators expose SOAP services. Their schemas differ, so every adapter
builds its own body element; this module only handles the envelope, WS-Security,
transport, faults and namespace-agnostic response parsing.
"""

from lxml import etree

SOAP_ENV = "http://schemas.xmlsoap.org/soap/envelope/"
WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSSE_PW_TEXT = ("http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0"
                "#PasswordText")


def el(tag, text=None, ns=None, attrib=None, children=()):
    """Create an element. `tag` may be 'local' (with ns) or a Clark name '{ns}local'."""
    name = tag if tag.startswith("{") or ns is None else f"{{{ns}}}{tag}"
    node = etree.Element(name, {k: str(v) for k, v in (attrib or {}).items()})
    if text is not None:
        node.text = str(text)
    for c in children:
        if c is not None:
            node.append(c)
    return node


def sub(parent, tag, text=None, ns=None, attrib=None):
    node = el(tag, text, ns, attrib)
    parent.append(node)
    return node


def wsse_header(username, password):
    sec = el("Security", ns=WSSE, attrib={"{%s}mustUnderstand" % SOAP_ENV: "1"})
    tok = sub(sec, "UsernameToken", ns=WSSE)
    sub(tok, "Username", username, ns=WSSE)
    sub(tok, "Password", password, ns=WSSE, attrib={"Type": WSSE_PW_TEXT})
    return sec


def envelope(body, headers=()):
    env = etree.Element("{%s}Envelope" % SOAP_ENV, nsmap={"soapenv": SOAP_ENV})
    head = etree.SubElement(env, "{%s}Header" % SOAP_ENV)
    for h in headers:
        head.append(h)
    b = etree.SubElement(env, "{%s}Body" % SOAP_ENV)
    b.append(body)
    return etree.tostring(env, xml_declaration=True, encoding="UTF-8")


def local(node):
    return etree.QName(node).localname


def find(node, name):
    """First descendant (or self) whose local name is `name`, ignoring namespaces."""
    if node is None:
        return None
    for d in node.iter():
        if isinstance(d.tag, str) and local(d) == name:
            return d
    return None


def find_all(node, name):
    if node is None:
        return []
    return [d for d in node.iter() if isinstance(d.tag, str) and local(d) == name]


def text(node, name, default=""):
    f = find(node, name)
    return (f.text or "").strip() if f is not None and f.text else default


def fault_message(root):
    fault = find(root, "Fault")
    if fault is None:
        return None
    parts = [text(fault, "faultstring"), text(fault, "Text")]
    detail = find(fault, "detail")
    if detail is None:
        detail = find(fault, "Detail")
    if detail is not None:
        parts += [t.strip() for t in detail.itertext() if t.strip()]
    msg = " — ".join(dict.fromkeys(p for p in parts if p))
    return msg or "SOAP fault"
