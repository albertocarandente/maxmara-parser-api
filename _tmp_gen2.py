def _generate_image(prompt, ref_b64, label="immagine"):
    """Genera una immagine via OpenRouter."""
    import requests as req
    body = {"model": IMAGE_MODEL, "modalities": ["image"], "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{ref_b64}"}},
        {"type": "text", "text": prompt},]}]}
    resp = req.post("https://openrouter.ai/api/v1/chat/completions", json=body, headers={
        "Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json",
        "HTTP-Referer": "https://etienne-studio.vercel.app", "X-Title": "ETIENNE Studio",
    }, timeout=90)
    data = resp.json()
    if resp.status_code != 200: raise RuntimeError(f"Image gen error {resp.status_code}: {data}")
    msg = data.get("choices", [{}])[0].get("message", {})

    # Try all possible places the image could be
    data_url = None

    # Format A: message.images
    for img in msg.get("images", []):
        if "image_url" in img: data_url = img["image_url"].get("url", ""); break

    # Format B: content array with image_url
    if not data_url:
        for part in msg.get("content", []):
            if isinstance(part, dict) and part.get("type") == "image_url":
                data_url = part.get("image_url", {}).get("url", ""); break

    # Format C: base64 data URL in text content
    if not data_url:
        text = msg.get("content", "")
        if isinstance(text, str) and "data:image" in text:
            import re
            m = re.search(r'data:image/\w+;base64,[A-Za-z0-9+/=]+', text)
            if m: data_url = m.group(0)

    # Format D: raw text is base64
    if not data_url:
        text = msg.get("content", "") if isinstance(msg.get("content", ""), str) else str(msg)
        if text.startswith("data:"): data_url = text

    if data_url and data_url.startswith("data:"):
        header, b64data = data_url.split(",", 1)
        ext = "png" if "png" in header else "jpg"
        os.makedirs(IMAGES_DIR, exist_ok=True)
        filename = f"{uuid.uuid4().hex}.{ext}"
        with open(os.path.join(IMAGES_DIR, filename), "wb") as f: f.write(base64.standard_b64decode(b64data))
        return f"/studio/images/{filename}"
    return data_url
