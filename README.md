# Auto Electrix website

## What's in this folder

| File / folder | What it is |
|---|---|
| `site.json` | **Phone number, address, opening hours, social links, reviews.** Change them here and every page updates. |
| `src/pages/` | One file per page. The words for each page live here. |
| `src/layout.html` | The header, menu and footer that every page shares. |
| `src/assets/` | Images, colours (`css/style.css`), fonts and policy PDFs. |
| `build.py` | Puts it all together. Run `python3 build.py`. |
| `dist/` | **The finished website.** This is the folder that goes online. |

## Making an edit

1. Open the file you want to change (see the table above).
2. Change the words and save.
3. Run `python3 build.py`.
4. If the site is on Netlify and connected to GitHub, Netlify rebuilds it for you when you save to GitHub.

### Page files

Each page file starts with a settings block between two `---` lines:

```
---
path: /about-us/                 ← the web address
title: About Auto Electrix...    ← the title Google shows
description: At Auto Electrix... ← the text under the title in Google
banner_title: About Us           ← big text on the banner image
banner_text: Find out more...    ← small text on the banner image
banner_image: banner-1.webp      ← image from src/assets/img/
---
```

Under that is the page text, written in normal HTML.

These shortcuts add ready-made sections:

- `{{phone}}`: the phone number from `site.json`
- `{% enquiry_cards %}`: the 4 enquiry boxes
- `{% reviews 4 %}`: rating plus 4 reviews (`all` shows every review)
- `{% location %}`: address and opening times
- `{% imi %}`: the IMI membership line

### Forms

Each form question is one line: `type | Question | options`

```
text | Full Name*                        ← * makes it required
select | Engine Type | Petrol, Diesel    ← drop-down list
checkboxes | Symptoms | Lights, Battery  ← tick boxes
textarea | Describe the issue*           ← big text box
date | Preferred Date
file | Upload an Image
```

`## Title` starts a new step. `### Title` adds a small heading inside a step.

## Social media links

In `site.json`, under `"social"`, paste the full link for each account, for example:

```
"facebook": "https://www.facebook.com/your-new-page",
```

Leave a link blank (`""`) to hide that icon. The Facebook section on the homepage only shows when a Facebook link is filled in.

## Going live (Netlify, free plan)

1. Make a free account at netlify.com.
2. Easiest way: go to **Add new site → Deploy manually** and drag the `dist` folder onto the page.
   Better way: put this folder on GitHub, then **Add new site → Import from GitHub**. Netlify reads `netlify.toml` and builds the site by itself.
3. **Forms:** go to Site configuration → Forms → **Enable form detection**, then deploy again.
   Then go to Forms → Form notifications → **Add email notification** and enter your email. Each enquiry is sent to you.
4. **Domain:** go to Domain management → **Add a domain** and follow the steps. Netlify gives you DNS records to add where you bought your domain. HTTPS is set up for you.
5. Put your real domain in `site.json` → `"domain"`, then build again. Google uses it.
6. Test each form once it's live.

## Before switching over

- [ ] Put the real domain in `site.json`
- [x] Company number and registered office added (from Companies House)
- [ ] Add your email address in `src/pages/privacy-policy.html`
- [ ] Add the new Facebook and Instagram links in `site.json`
- [ ] Set up form email notifications on Netlify and test all 4 forms
- [ ] Add the new site to Google Search Console and submit `/sitemap.xml`
- [ ] Optional: add Google Analytics with your own ID. If you do, update `src/pages/cookie-policy.html` and add a cookie banner first.
