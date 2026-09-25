#!/usr/bin/env python3
"""Builds the Auto Electrix website.

Reads site.json, src/layout.html and src/pages/*.html, and writes the
finished website to dist/. Run it with:  python3 build.py
"""
import datetime
import html
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "src"
DIST = ROOT / "dist"

site = json.loads((ROOT / "site.json").read_text(encoding="utf-8"))
layout = (SRC / "layout.html").read_text(encoding="utf-8")
version = datetime.datetime.now().strftime("%Y%m%d%H%M")
esc = html.escape

SERVICES = {
    "/services/adas-calibration/": "ADAS Calibration",
    "/services/advanced-module-testing-programming/": "Advanced Module Testing & Programming",
    "/services/advanced-vehicle-diagnostics/": "Advanced Vehicle Diagnostics",
}

ICONS = {
    "facebook": '<path d="M14 8h3V4h-3c-2.8 0-4 1.8-4 4.3V10H7v4h3v8h4v-8h3l1-4h-4V8.6c0-.4.3-.6.6-.6z"/>',
    "instagram": '<path d="M12 7.3A4.7 4.7 0 1 0 16.7 12 4.7 4.7 0 0 0 12 7.3zm0 7.7a3 3 0 1 1 3-3 3 3 0 0 1-3 3zm6-7.9a1.1 1.1 0 1 1-1.1-1.1A1.1 1.1 0 0 1 18 7.1zM21 7a5.4 5.4 0 0 0-1.5-3.8A5.4 5.4 0 0 0 15.7 2C14.2 2 9.8 2 8.3 2a5.4 5.4 0 0 0-3.8 1.5A5.4 5.4 0 0 0 3 7.3c-.1 1.5-.1 5.9 0 7.4a5.4 5.4 0 0 0 1.5 3.8A5.4 5.4 0 0 0 8.3 20c1.5.1 5.9.1 7.4 0a5.4 5.4 0 0 0 3.8-1.5 5.4 5.4 0 0 0 1.5-3.8c.1-1.5.1-5.9 0-7.4zm-2 9.2a3 3 0 0 1-1.7 1.7c-1.2.5-4 .4-5.3.4s-4.1.1-5.3-.4A3 3 0 0 1 5 16.2c-.5-1.2-.4-4-.4-5.3s-.1-4.1.4-5.3A3 3 0 0 1 6.7 4c1.2-.5 4-.4 5.3-.4s4.1-.1 5.3.4A3 3 0 0 1 19 5.7c.5 1.2.4 4 .4 5.3s.1 4.1-.4 5.3z"/>',
    "tiktok": '<path d="M16.6 5.8A4.3 4.3 0 0 1 15.5 3h-3.1v12.4a2.6 2.6 0 1 1-2.6-2.6 2.5 2.5 0 0 1 .8.1V9.7a5.7 5.7 0 0 0-.8-.1 5.7 5.7 0 1 0 5.7 5.7V9a7.3 7.3 0 0 0 4.3 1.4V7.3a4.3 4.3 0 0 1-3.2-1.5z"/>',
    "whatsapp": '<path d="M17.5 14.4c-.3-.1-1.8-.9-2-1s-.5-.1-.7.1-.8 1-.9 1.2-.3.2-.6.1a8.2 8.2 0 0 1-4-3.5c-.3-.5.3-.5.9-1.6a.6.6 0 0 0 0-.5c0-.1-.7-1.6-.9-2.2s-.5-.5-.7-.5h-.6a1.2 1.2 0 0 0-.8.4 3.5 3.5 0 0 0-1.1 2.6 6 6 0 0 0 1.3 3.2 13.9 13.9 0 0 0 5.3 4.7c2 .8 2.7.9 3.7.8a3.1 3.1 0 0 0 2-1.4 2.5 2.5 0 0 0 .2-1.4c-.1-.2-.3-.2-.6-.4zM12 21.5a9.5 9.5 0 0 1-4.8-1.3l-.3-.2-3.6.9 1-3.5-.2-.4a9.5 9.5 0 1 1 7.9 4.5zM12 1a11 11 0 0 0-9.5 16.5L1 23l5.6-1.5A11 11 0 1 0 12 1z"/>',
}


def social_icons():
    links = dict(site["social"])
    if site.get("whatsapp"):  # blank in site.json = no WhatsApp icon
        links["whatsapp"] = f"https://wa.me/{site['whatsapp']}"
    out = []
    for name in ("facebook", "whatsapp", "instagram", "tiktok"):
        if not links.get(name):
            continue  # blank in site.json = icon hidden
        out.append(
            f'<a class="social" href="{links[name]}" target="_blank" rel="noopener" aria-label="{name.title()}">'
            f'<svg viewBox="0 0 24 24" aria-hidden="true">{ICONS[name]}</svg></a>'
        )
    return f'<div class="socials">{"".join(out)}</div>'


def company_line():
    c = site["company"]
    line = f'{esc(site["business_name"])} is a trading name of {esc(c["legal_name"])}.'
    if c["number"]:
        line += f' Registered in {esc(c["registered_in"])}, company no. {esc(c["number"])}.'
    if c["registered_office"]:
        line += f' Registered office: {esc(c["registered_office"])}.'
    return line


def to_fill(value, what):
    return esc(value) if value else f'<span class="fill">TO FILL IN: {what}</span>'


def facebook_section():
    if not site["social"].get("facebook"):
        return ""  # hidden until a Facebook link is added to site.json
    return f"""<section class="container section divider">
  <h3>Follow Auto Electrix on Facebook</h3>
  <p>See our latest jobs, tips and news on our Facebook page.</p>
  <a class="btn" href="{site["social"]["facebook"]}" target="_blank" rel="noopener">Visit Our Facebook Page »</a>
</section>"""


def enquiry_cards():
    cards = [
        ("Initial Assessment", "Got a problem with your car? Book an initial assessment and we will come to you", "/initial-assessment-enquiry/", "Enquire Now »"),
        ("Vehicle Programming & Coding", "Enquire about vehicle programming & coding at Auto Electrix in Bournemouth", "/vehicle-programming-coding-enquiry/", "Enquire Now »"),
        ("Electrical Fault-Finding", "Enquire about electrical fault-finding at Auto Electrix in Bournemouth", "/electrical-fault-finding-enquiry/", "Enquire Now »"),
        ("General Enquiry", "Get in contact with Auto Electrix, we are happy to help...", "/contact-us/", "Get in Touch »"),
    ]
    items = "".join(
        f'<a class="card" href="{url}"><h3>{esc(t)}</h3><p>{esc(d)}</p><span class="btn">{b}</span></a>'
        for t, d, url, b in cards
    )
    return f'<div class="cards">{items}</div>'


def stars():
    return '<span class="stars" aria-hidden="true">★★★★★</span>'


def reviews(arg):
    items = site["reviews"] if arg == "all" else site["reviews"][: int(arg)]
    cards = "".join(
        f'<figure class="review"><figcaption><img src="/assets/img/google.svg" alt="" width="18" height="18">'
        f'<strong>{esc(n)}</strong>{stars()}</figcaption><blockquote>{esc(t)}</blockquote></figure>'
        for n, t in items
    )
    return (
        f'<div class="rating"><span class="rating-score">{site["google_rating"]}</span>{stars()}'
        f'<span class="rating-count">{site["google_review_count"]} on Google</span>'
        f'<a class="btn btn-small" href="{site["google_review_url"]}" target="_blank" rel="noopener">Write a Review</a></div>'
        f'<div class="reviews">{cards}</div>'
    )


def location():
    rows = "".join(f"<tr><td>{d}</td><td>{h}</td></tr>" for d, h in site["hours"])
    return f"""<section class="container section divider location">
  <h3>Where We Work</h3>
  <div class="location-grid">
    <div>
      <h4>Mobile Service</h4>
      <p>We come to you. Our fully equipped mobile service covers {esc(site["service_area"])}.</p>
      <p><strong>{esc(site["workshop_notice"])}.</strong></p>
      <p><a href="tel:{phone_link()}">{site["phone"]}</a></p>
      <a class="btn" href="/contact-us/">Book a Visit »</a>
    </div>
    <div class="hours">
      <h4>Opening Times</h4>
      <table>{rows}</table>
    </div>
  </div>
  <img class="imi" src="/assets/img/imi-logo.png" alt="Institute of the Motor Industry" width="177" height="50" loading="lazy">
</section>"""


def related(paths):
    items = "".join(
        f'<a href="{p}"><strong>{esc(SERVICES[p])}</strong><span>Find out more »</span></a>' for p in paths
    )
    return f'<div class="related">{items}</div>'


def imi():
    return '<p class="imi-note"><em>Auto Electrix is proud to be affiliated with the Institute of the Motor Industry (IMI).</em></p>'


def slug(label):
    return re.sub(r"[^a-z0-9]+", "-", label.lower().replace("*", "")).strip("-")


def form(name, title, spec):
    """Turns the simple form list in a page into a multi-step HTML form."""
    steps = []
    for raw in spec.strip().splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("## "):
            steps.append({"title": line[3:], "html": []})
            continue
        body = steps[-1]["html"]
        if line.startswith("### "):
            body.append(f"<h4 class='form-section'>{esc(line[4:])}</h4>")
            continue
        parts = [p.strip() for p in line.split("|")]
        kind, label = parts[0], parts[1]
        extra = parts[2] if len(parts) > 2 else ""
        required = label.endswith("*")
        clean = label.rstrip("*")
        fid = f"{name}-{slug(label)}"
        req = " required" if required else ""
        star = ' <span class="req" aria-hidden="true">*</span>' if required else ""
        lab = f'<label for="{fid}">{esc(clean)}{star}</label>'
        ph = f' placeholder="{esc(extra)}"' if extra else ""
        if kind in ("text", "tel", "email", "date"):
            auto = {"Full Name": ' autocomplete="name"', "Telephone": ' autocomplete="tel"', "Email": ' autocomplete="email"'}.get(clean, "")
            field = f'<input type="{kind}" id="{fid}" name="{esc(clean)}"{ph}{req}{auto}>'
        elif kind == "textarea":
            field = f'<textarea id="{fid}" name="{esc(clean)}" rows="3"{ph}{req}></textarea>'
        elif kind == "select":
            opts = "".join(f"<option>{esc(o.strip())}</option>" for o in extra.split(","))
            field = f'<select id="{fid}" name="{esc(clean)}"{req}><option value="">Please choose…</option>{opts}</select>'
        elif kind == "checkboxes":
            boxes = "".join(
                f'<label class="check"><input type="checkbox" name="{esc(clean)}[]" value="{esc(o.strip())}"> {esc(o.strip())}</label>'
                for o in extra.split(",")
            )
            body.append(f'<fieldset class="field field-wide"><legend>{esc(clean)}</legend><div class="checks">{boxes}</div></fieldset>')
            continue
        elif kind == "file":
            field = f'<input type="file" id="{fid}" name="{esc(clean)}" accept="image/*,.pdf"><small>Max. file size: 8 MB.</small>'
        else:
            raise ValueError(f"Unknown form field type '{kind}' in line: {line}")
        wide = " field-wide" if kind in ("textarea", "file") else ""
        body.append(f'<div class="field{wide}">{lab}{field}</div>')

    total = len(steps)
    out = [
        f'<form class="enquiry-form" name="{name}" method="POST" action="/thank-you/" '
        f'data-netlify="true" netlify-honeypot="bot-field" enctype="multipart/form-data">',
        f'<input type="hidden" name="form-name" value="{name}">',
        '<p class="hp"><label>Leave this empty: <input name="bot-field"></label></p>',
        f'<div class="form-head"><h2>{esc(title)}</h2><div class="progress" aria-hidden="true"><span></span></div>'
        f'<p class="step-count" aria-live="polite"></p></div>',
    ]
    for i, step in enumerate(steps, 1):
        nav = []
        if i > 1:
            nav.append('<button type="button" class="btn btn-ghost" data-prev>Previous</button>')
        if i < total:
            nav.append('<button type="button" class="btn" data-next>Next</button>')
        else:
            nav.append(
                '<p class="privacy-note">We only use your details to reply to your enquiry. '
                'See our <a href="/privacy-policy/">privacy policy</a>.</p>'
                '<button type="submit" class="btn">Submit »</button>'
            )
        out.append(
            f'<fieldset class="step" data-step="{i}" data-total="{total}"><legend>{esc(step["title"])}</legend>'
            f'<div class="fields">{"".join(step["html"])}</div><div class="step-nav">{"".join(nav)}</div></fieldset>'
        )
    out.append("</form>")
    return "\n".join(out)


def phone_link():
    return site["phone"].replace(" ", "")


def render_tags(text):
    text = re.sub(
        r"\{% form (\S+) \| (.+?) %\}(.*?)\{% endform %\}",
        lambda m: form(m.group(1), m.group(2), m.group(3)),
        text,
        flags=re.S,
    )
    text = text.replace("{% enquiry_cards %}", enquiry_cards())
    text = text.replace("{% location %}", location())
    text = text.replace("{% facebook_section %}", facebook_section())
    text = text.replace("{% imi %}", imi())
    text = re.sub(r"\{% reviews (\w+) %\}", lambda m: reviews(m.group(1)), text)
    text = re.sub(r"\{% related (.+?) %\}", lambda m: related(m.group(1).split()), text)
    return text


def fill(text, values):
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(values.get(m.group(1), m.group(0))), text)


def parse_page(path):
    raw = path.read_text(encoding="utf-8")
    _, head, body = raw.split("---", 2)
    meta = {}
    for line in head.strip().splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, body.strip()


def banner(meta):
    if "banner_title" not in meta:
        return ""
    crumbs = ['<a href="/">Home</a>']
    parts = [p.strip() for p in meta.get("breadcrumb", "").split("|") if p.strip()]
    for i, part in enumerate(parts):
        if part == "Services" and i < len(parts) - 1:
            crumbs.append('<a href="/services/">Services</a>')
        else:
            crumbs.append(f'<span aria-current="page">{esc(part)}</span>')
    return f"""<section class="page-banner" style="background-image:url(/assets/img/{meta.get('banner_image', 'banner-1.webp')})">
  <div class="container"><p class="banner-title">{esc(meta['banner_title'])}</p><p>{esc(meta.get('banner_text', ''))}</p></div>
</section>
<nav class="container breadcrumbs" aria-label="Breadcrumb">{' <span aria-hidden="true">›</span> '.join(crumbs)}</nav>"""


def wrap(meta, body):
    kind = meta.get("layout", "sidebar")
    if kind == "home":
        return body
    if kind == "legal":
        return f'<div class="container section legal"><article class="content">{body}</article></div>'
    if kind == "form":
        return f'<div class="container section form-page">{body}</div>{location()}<div class="container section">{enquiry_cards()}</div>'
    return (
        f'<div class="container section with-sidebar"><article class="content">{body}</article>'
        f'<aside class="sidebar">{enquiry_cards()}</aside></div>{location()}'
    )


def schema():
    days = {"Monday": "Mo", "Tuesday": "Tu", "Wednesday": "We", "Thursday": "Th", "Friday": "Fr"}
    hours = [f"{days[d]} {h.replace(' ', '')}" for d, h in site["hours"] if d in days and h != "Closed"]
    data = {
        "@context": "https://schema.org",
        "@type": "AutoRepair",
        "name": site["business_name"],
        "legalName": site["company"]["legal_name"],
        "url": site["domain"] + "/",
        "telephone": site["phone"],
        "image": site["domain"] + "/assets/img/banner-1.webp",
        "logo": site["domain"] + "/assets/img/favicon.png",
        "areaServed": {"@type": "City", "name": "Bournemouth"},
        "openingHours": hours,
        "aggregateRating": {"@type": "AggregateRating", "ratingValue": site["google_rating"], "reviewCount": "120"},
        "sameAs": [u for u in site["social"].values() if u],
    }
    return json.dumps(data, ensure_ascii=False)


def main():
    if DIST.exists():
        shutil.rmtree(DIST)
    shutil.copytree(SRC / "assets", DIST / "assets")
    base = {
        "phone": site["phone"],
        "phone_link": phone_link(),
        "facebook": site["social"]["facebook"],
        "domain": site["domain"],
        "year": datetime.date.today().year,
        "version": version,
        "social_icons": social_icons(),
        "schema": schema(),
        "workshop_notice": esc(site["workshop_notice"]),
        "service_area": esc(site["service_area"]),
        "legal_name": esc(site["company"]["legal_name"]),
        "company_line": company_line(),
        "company_number": to_fill(site["company"]["number"], "company number"),
        "registered_office": to_fill(site["company"]["registered_office"], "registered office address (from Companies House)"),
    }
    sitemap = []
    for page in sorted((SRC / "pages").glob("*.html")):
        meta, body = parse_page(page)
        url = meta["path"]
        content = fill(render_tags(body), base)
        values = dict(base)
        values.update(
            title=esc(meta["title"]),
            description=esc(meta["description"]),
            canonical=site["domain"] + url,
            banner=banner(meta),
            content=wrap(meta, content),
            body_class="page-" + page.stem,
        )
        out = fill(layout, values)
        if meta.get("noindex") == "yes":
            out = out.replace("<meta charset=\"UTF-8\">", "<meta charset=\"UTF-8\">\n<meta name=\"robots\" content=\"noindex\">")
        else:
            sitemap.append(site["domain"] + url)
        target = DIST / (url.strip("/") or "") / "index.html" if url.endswith("/") else DIST / url.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(out, encoding="utf-8")
        print("built", url)
        if "TO FILL IN" in out:
            print(f"  WARNING: {url} still has TO FILL IN gaps")

    today = datetime.date.today().isoformat()
    urls = "".join(f"<url><loc>{u}</loc><lastmod>{today}</lastmod></url>" for u in sitemap)
    (DIST / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
        encoding="utf-8",
    )
    (DIST / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {site['domain']}/sitemap.xml\n", encoding="utf-8")
    if not (site["company"]["number"] and site["company"]["registered_office"]):
        print("  WARNING: add the company number and registered office to site.json (the law requires them on the website)")
    print(f"Done. {len(sitemap)} pages in the sitemap. Website is in the dist/ folder.")


if __name__ == "__main__":
    main()
