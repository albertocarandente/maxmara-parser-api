"""
Parser per ordini PT TORINO / COVER 50 (PDF combit List & Label, SENZA layer di testo).
Pipeline: rasterizzazione 400 DPI -> OCR pagina per ancore (header, righe, prezzi) ->
OCR cella-per-cella della griglia taglie (psm10, whitelist cifre) -> validazione
aritmetica sui totali del documento (per-riga e globale).

I numeri sono cio' che conta (taglie, quantita', prezzi) e sono validati al 100% dai
gate. Il codice stile (alfanumerico) puo' avere ambiguita' OCR 0<->O: passare
`catalogo_modelli` (lista SKU validi dall'anagrafica CRM) per agganciarlo in automatico.
"""
import io, re
import fitz
import pytesseract
from PIL import Image

DPI = 400
CELL_CFG = "--psm 10 -c tessedit_char_whitelist=0123456789"

def _img(doc, i):
    pix = doc[i].get_pixmap(dpi=DPI)
    return Image.open(io.BytesIO(pix.tobytes("png")))

def _words(img):
    d = pytesseract.image_to_data(img, lang="eng", config="--psm 6",
                                  output_type=pytesseract.Output.DICT)
    return [{"x": d["left"][i], "y": d["top"][i], "w": d["width"][i], "h": d["height"][i],
             "xc": d["left"][i]+d["width"][i]/2, "yc": d["top"][i]+d["height"][i]/2,
             "t": d["text"][i].strip(), "c": d["conf"][i]}
            for i in range(len(d["text"])) if d["text"][i].strip()]

def _cell(img, xc, yc, h):
    crop = img.crop((int(xc-30), int(yc-h*0.7), int(xc+30), int(yc+h*0.7)))
    s = re.sub(r"\D", "", pytesseract.image_to_string(crop, config=CELL_CFG))
    return int(s) if s else 0

def _money(t):
    u = t.replace(".", "").replace(",", ".")
    return float(u) if re.fullmatch(r"\d+\.\d{2}", u) else None

def _size_columns(words):
    cand = sorted([w for w in words if re.fullmatch(r"\d{2}", w["t"])
                   and 40 <= int(w["t"]) <= 70 and int(w["t"]) % 2 == 0],
                  key=lambda w: w["xc"])
    if len(cand) < 8:
        return None
    best, run = [], [cand[0]]
    for a, b in zip(cand, cand[1:]):
        if b["xc"] - a["xc"] < 150:
            run.append(b)
        else:
            if len(run) > len(best): best = run
            run = [b]
    if len(run) > len(best): best = run
    return [(w["xc"], w["t"], w["y"], w["h"]) for w in best]

def _similar(a, b):
    if len(a) != len(b): return False
    sub = {("0","O"),("O","0"),("1","I"),("I","1"),("1","L"),("L","1"),
           ("8","B"),("B","8"),("5","S"),("S","5"),("2","Z"),("Z","2")}
    diff = 0
    for x, y in zip(a, b):
        if x != y:
            if (x, y) not in sub and (y, x) not in sub: return False
            diff += 1
    return diff <= 2

def parse_order(path, catalogo_modelli=None):
    doc = fitz.open(path)
    capi, errori = [], []
    footer_importo = None
    all_money = []

    for i in range(doc.page_count):
        img = _img(doc, i)
        words = _words(img)
        all_money += [m for m in (_money(w["t"]) for w in words) if m is not None]
        cols = _size_columns(words)
        if cols is None:
            continue
        x_lo, x_hi = cols[0][0]-45, cols[-1][0]+45

        codes = sorted([w for w in words if w["t"].startswith("CO-")
                        and len(w["t"]) >= 8 and "TORINO" not in w["t"]],
                       key=lambda w: w["yc"])
        for c in codes:
            yc, h = c["yc"], c["h"]
            band = [w for w in words if abs(w["yc"] - yc) <= h*0.9]

            sizes = {}
            for (xc, size, _, _) in cols:
                v = _cell(img, xc, yc, h)
                if v: sizes[size] = v

            right = [w for w in band if w["xc"] > x_hi]
            money = sorted([w for w in right if _money(w["t"]) is not None], key=lambda w: w["xc"])
            qta_tok = sorted([w for w in right if re.fullmatch(r"\d{1,3}", w["t"])], key=lambda w: w["xc"])
            qta = int(qta_tok[0]["t"]) if qta_tok else None
            prezzo = _money(money[0]["t"]) if money else None
            importo = _money(money[-1]["t"]) if money else None

            mid = [w for w in band if c["xc"] < w["xc"] < x_lo-550]
            articolo = next((w["t"] for w in mid if re.fullmatch(r"[A-Z]{2}\d{2}", w["t"])), None)
            colore   = next((w["t"] for w in mid if re.fullmatch(r"[A-Z0-9]\d{3}", w["t"])), None)

            modello = c["t"]
            if catalogo_modelli:
                if modello not in catalogo_modelli:
                    cand = [m for m in catalogo_modelli if _similar(m, modello)]
                    if len(cand) == 1: modello = cand[0]

            capo = {"modello": modello, "articolo": articolo, "colore": colore,
                    "taglie": sizes, "qta": qta, "prezzo": prezzo, "importo": importo,
                    "ocr_conf_codice": c["c"]}

            s = sum(sizes.values()); probs = []
            if qta is None or s != qta: probs.append(f"somma taglie {s} != qta {qta}")
            if prezzo and qta and importo and round(prezzo*qta, 2) != importo:
                probs.append("prezzo*qta != importo")
            if catalogo_modelli and modello not in catalogo_modelli:
                probs.append(f"codice '{c['t']}' non agganciato al catalogo")
            if probs: errori.append({"modello": modello, "colore": colore, "problemi": probs})
            capi.append(capo)

    tot_capi = sum(c["qta"] or 0 for c in capi)
    tot_imp  = round(sum(c["importo"] or 0 for c in capi), 2)
    footer_importo = max(all_money) if all_money else None
    if footer_importo is not None and tot_imp != round(footer_importo, 2):
        errori.append({"globale": f"importo {tot_imp} != totale documento {footer_importo}"})

    return {"valido": len(errori) == 0, "n_capi_righe": len(capi),
            "totale_capi": tot_capi, "totale_importo": tot_imp,
            "footer_importo": footer_importo, "capi": capi, "errori": errori}

if __name__ == "__main__":
    import sys, json
    r = parse_order(sys.argv[1] if len(sys.argv)>1 else "ordine.pdf")
    print(json.dumps({k:v for k,v in r.items() if k!="capi"}, indent=2, ensure_ascii=False))
