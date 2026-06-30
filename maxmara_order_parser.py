"""
Parser deterministico per ordini MaxMara (PDF JasperReports).
Zero LLM, zero allucinazioni: ricostruzione per coordinate + validazione aritmetica.

Uso:
    from maxmara_order_parser import parse_order
    result = parse_order("Ordine_3_MaxMara.pdf")
    if result["valido"]:
        for capo in result["capi"]: ...
    else:
        print(result["errori"])   # righe che NON quadrano: si fermano, non passano sporche
"""
import re
import fitz  # PyMuPDF

SIZE_LABELS_B = {"XS", "S", "M", "L", "XL", "2XL"}

def _deshift(text, k):
    return "".join(chr(ord(c) + k) for c in text)

def _spans(page, k):
    """Span de-shiftati con coordinate. (xc = centro orizzontale)"""
    out = []
    for blk in page.get_text("dict")["blocks"]:
        for line in blk.get("lines", []):
            for s in line.get("spans", []):
                t = _deshift(s["text"], k).strip()
                if t:
                    x0, y0, x1, y1 = s["bbox"]
                    out.append({"x": x0, "xc": (x0 + x1) / 2, "y": y0, "t": t})
    return out

def _detect_shift(page):
    """Rileva l'offset dell'encoding: prova k=29 (tipico), poi cerca quello che produce 'MARA'/'EUR'."""
    for k in [29] + list(range(0, 64)):
        txt = "".join(_deshift(s["text"], k) for blk in page.get_text("dict")["blocks"]
                       for line in blk.get("lines", []) for s in line.get("spans", []))
        if "MARA" in txt and "EUR" in txt:
            return k
    return 29

def _build_colmap(spans):
    """Mappa colonne taglia: (xcenter, taglia_A_numerica, taglia_B_lettera)."""
    num = [s for s in spans if s["t"].isdigit() and len(s["t"]) == 2
           and 33 <= int(s["t"]) <= 52 and s["y"] < 315]
    let = [s for s in spans if s["t"] in SIZE_LABELS_B and s["y"] < 315]
    colmap = []
    for s in sorted(num, key=lambda z: z["xc"]):
        cand = [z for z in let if abs(z["xc"] - s["xc"]) < 11]
        b = min(cand, key=lambda z: abs(z["xc"] - s["xc"])) if cand else None
        colmap.append((s["xc"], s["t"], b["t"] if b else None))
    return colmap

def _cluster_rows(spans, tol=3.0):
    """Raggruppa span in righe visive con tolleranza sulla y (i numeri sono ~0.4px sopra il testo)."""
    rows, cur, last = [], [], None
    for s in sorted(spans, key=lambda z: (z["y"], z["x"])):
        if last is None or abs(s["y"] - last) <= tol:
            cur.append(s)
        else:
            rows.append(cur); cur = [s]
        last = s["y"]
    if cur:
        rows.append(cur)
    return rows

def _assign_size(colmap, xc, scale):
    i = min(range(len(colmap)), key=lambda j: abs(colmap[j][0] - xc))
    return colmap[i][1] if scale == "A" else (colmap[i][2] or colmap[i][1])

def parse_order(path):
    doc = fitz.open(path)
    k = _detect_shift(doc[0])

    capi, errori = [], []
    colmap = None
    footer_capi = footer_importo = None

    for page in doc:
        spans = _spans(page, k)
        cm = _build_colmap(spans)
        if cm:
            colmap = cm
        # footer: "Totale Capi" e "Importo Merce"
        for s in spans:
            if footer_importo is None and re.fullmatch(r"\d{1,3}(?:\.\d{3})*\.\d{2}", s["t"]):
                pass
        if colmap is None:
            continue

        for row in _cluster_rows(spans):
            row = sorted(row, key=lambda z: z["x"])
            code = [s for s in row if re.fullmatch(r"\d{13}", s["t"])]
            if not code:
                continue
            name  = [s for s in row if re.fullmatch(r"[A-Z]{2,}\d*", s["t"]) and 60 < s["x"] < 150]
            var   = [s for s in row if re.fullmatch(r"\d{3}", s["t"]) and 160 < s["x"] < 175]
            art   = [s for s in row if re.fullmatch(r"\w{3,4}", s["t"]) and 175 < s["x"] < 195]
            scale = [s for s in row if s["t"] in ("A", "B") and 195 < s["x"] < 205]
            qty   = [s for s in row if re.fullmatch(r"\d+", s["t"]) and 205 < s["x"] < 360]
            right = [s for s in row if s["x"] > 420]
            tot   = [s for s in right if re.fullmatch(r"\d+", s["t"]) and s["x"] < 455]
            money = sorted([s for s in right if re.fullmatch(r"\d+\.\d{2}", s["t"])], key=lambda z: z["x"])

            sc = scale[0]["t"] if scale else "A"
            sizes = {}
            for q in qty:
                lbl = _assign_size(colmap, q["xc"], sc)
                sizes[lbl] = sizes.get(lbl, 0) + int(q["t"])

            capo = {
                "modello": code[0]["t"],
                "nome":    name[0]["t"] if name else None,
                "var":     var[0]["t"] if var else None,
                "art":     art[0]["t"] if art else None,
                "scala":   sc,
                "taglie":  sizes,
                "tot_capi": int(tot[0]["t"]) if tot else None,
                "prezzo":   float(money[0]["t"]) if money else None,
                "importo":  float(money[1]["t"]) if len(money) > 1 else None,
            }

            # --- GATE DI VALIDAZIONE PER RIGA ---
            problemi = []
            somma = sum(sizes.values())
            if capo["tot_capi"] is None or somma != capo["tot_capi"]:
                problemi.append(f"somma taglie {somma} != tot_capi {capo['tot_capi']}")
            if capo["prezzo"] and capo["tot_capi"] and capo["importo"]:
                if round(capo["prezzo"] * capo["tot_capi"], 2) != capo["importo"]:
                    problemi.append("prezzo*tot != importo")
            if problemi:
                errori.append({"modello": capo["modello"], "nome": capo["nome"], "problemi": problemi})
            capi.append(capo)

    # footer reali del documento
    full = "\n".join(_deshift(s["text"], k) for p in doc for blk in p.get_text("dict")["blocks"]
                     for line in blk.get("lines", []) for s in line.get("spans", []))
    # Frase riepilogo (pag. finale) -> fonte piu' robusta; fallback sul footer di pag.1
    m_capi = re.search(r"totale di:?\s*(\d{1,5})\s*capi", full, re.I) or re.search(r"Totale\s+Capi:?\s*(\d{1,5})", full, re.I)
    m_imp  = re.search(r"valore di\s*([\d.]+?\.\d{2})", full, re.I) or re.search(r"Importo\s+Merce:?\s*([\d.]+?\.\d{2})", full, re.I)
    footer_capi = int(m_capi.group(1)) if m_capi else None
    footer_importo = float(m_imp.group(1)) if m_imp else None

    tot_capi = sum(c["tot_capi"] or 0 for c in capi)
    tot_imp  = round(sum(c["importo"] or 0 for c in capi), 2)

    # --- GATE GLOBALI ---
    if footer_capi is not None and tot_capi != footer_capi:
        errori.append({"globale": f"capi calcolati {tot_capi} != footer {footer_capi}"})
    if footer_importo is not None and tot_imp != footer_importo:
        errori.append({"globale": f"importo calcolato {tot_imp} != footer {footer_importo}"})

    return {
        "valido": len(errori) == 0,
        "shift_encoding": k,
        "n_capi_righe": len(capi),
        "totale_capi": tot_capi,
        "totale_importo": tot_imp,
        "footer_capi": footer_capi,
        "footer_importo": footer_importo,
        "capi": capi,
        "errori": errori,
    }

if __name__ == "__main__":
    import json, sys
    r = parse_order(sys.argv[1] if len(sys.argv) > 1 else "Ordine_3_MaxMara.pdf")
    print(json.dumps({k: v for k, v in r.items() if k != "capi"}, indent=2, ensure_ascii=False))
    print("\n--- CAPI ---")
    for c in r["capi"]:
        print(f"{c['modello']} {c['nome']:<11} [{c['scala']}] var{c['var']} art{c['art']} "
              f"{c['taglie']}  tot={c['tot_capi']} {c['prezzo']}€ = {c['importo']}€")
