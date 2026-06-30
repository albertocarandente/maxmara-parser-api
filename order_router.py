"""
Router multi-brand: riconosce il fornitore dal PDF (fingerprint) e instrada al parser
deterministico corretto. Aggiungere un brand = aggiungere una regola + un parser.
"""
import fitz

def _fingerprint(path):
    doc = fitz.open(path)
    meta_creator = (doc.metadata or {}).get("creator", "") or ""
    txt = doc[0].get_text()
    has_text = len(txt.strip()) > 0
    head = txt[:1500]
    return {"creator": meta_creator, "has_text": has_text, "head": head}

def _deshift(t, k=29):
    return "".join(chr(ord(c)+k) for c in t)

def detect_brand(path):
    fp = _fingerprint(path)
    if "JasperReports" in fp["creator"]:
        if "MARA" in fp["head"] or "MARA" in _deshift(fp["head"]):
            return "maxmara"
    if "List & Label" in fp["creator"] and not fp["has_text"]:
        return "pttorino"
    return None

def parse(path, catalogo_modelli=None):
    brand = detect_brand(path)
    if brand == "maxmara":
        from maxmara_order_parser import parse_order
        r = parse_order(path); r["brand"] = "maxmara"; return r
    if brand == "pttorino":
        from pttorino_order_parser import parse_order
        r = parse_order(path, catalogo_modelli=catalogo_modelli); r["brand"] = "pttorino"; return r
    return {"valido": False, "brand": None,
            "errori": [{"globale": "template PDF non riconosciuto: serve un parser dedicato per questo brand"}]}

if __name__ == "__main__":
    import sys
    for f in sys.argv[1:]:
        b = detect_brand(f)
        r = parse(f)
        print(f"{f}\n  brand={b}  valido={r.get('valido')}  capi={r.get('totale_capi')}  importo={r.get('totale_importo')}\n")
