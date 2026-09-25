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

## Hosting (Cloudflare, free plan)

The site runs on Cloudflare as a Worker with static assets (`wrangler.jsonc`).
Cloudflare rebuilds it every time GitHub changes: build command `python3 build.py`,
deploy command `npx wrangler deploy`.

`worker/index.js` shows the website and emails form enquiries (with photo attached).
It needs two settings in the Cloudflare dashboard (the Worker → Settings → Variables and Secrets):

- `TO_ADDRESS`: where enquiries are sent. It must be a verified address in Email Routing.
- `FROM_ADDRESS`: an address on the website's domain, e.g. `website@yourdomain.uk`

Email Routing must be turned on for the domain.

## Before switching over

- [ ] Put the real domain in `site.json`
- [x] Company number and registered office added (from Companies House)
- [ ] Add your email address in `src/pages/privacy-policy.html`
- [ ] Add the new Facebook and Instagram links in `site.json`
- [ ] Turn on Email Routing, set TO_ADDRESS and FROM_ADDRESS, and test all 5 forms
- [ ] Add the new site to Google Search Console and submit `/sitemap.xml`
- [ ] Optional: add Google Analytics with your own ID. If you do, update `src/pages/cookie-policy.html` and add a cookie banner first.
