# Restaurant + FSSAI Licence Data for Kolkata: Data-Access Feasibility Research

**Question.** Can a system find food businesses selling a given dish (e.g. biryani) in a Kolkata
locality (e.g. Garia, Baghajatin) and return their **address** *and* their **FSSAI licence number**?

**Date of research:** 10 October 2026. All fetches performed on that date.

**Method / honesty note.** Everything below was established by fetching primary sources directly
(curl / WebFetch). Where a fact comes from reading a *client JavaScript bundle* rather than
published documentation, it is explicitly labelled **[REVERSE-ENGINEERED]**. Where I could not
verify something, it is labelled **UNVERIFIED**. Two things limited coverage and are called out
rather than papered over: (1) DuckDuckGo HTML search served me a CAPTCHA partway through, and
(2) `indiacode.nic.in` returned HTTP 403 and no desktop browser session was attached to this
session, so some SPA-rendered pages (FoSCoS search form, FSSAI disclaimer) could not be rendered.

---

## Bottom line

- **Yes in principle, no as a clean build.** The two halves of the question have completely
  different difficulty profiles: *licence numbers* are public regulatory data that FSSAI
  deliberately exposes; *"sells biryani"* has no clean data source anywhere.
- **FSSAI is the only legitimate source of licence numbers.** FoSCoS's public "Advance FBO Search"
  exists and is filterable by State → District → **Taluka (sub-district)** → Village/PIN, which is
  exactly the granularity "Garia" and "Baghajatin" require. Zomato and Swiggy do **not** publish
  FSSAI licence numbers (refuted, see §2, §3).
- **Your hypothesis is confirmed on the licence-number half.** FSSAI = primary. Zomato/Swiggy =
  licensed-partner-or-nothing. And the dish-matching step is indeed the hard part — worse than
  "hard", it is *not solvable with any available structured field*.
- **Zomato is effectively closed to third parties.** Its `robots.txt` is `User-agent: * →
  Disallow: /`, and its ToS separately bans "automated means (including use of scripts or web
  crawlers)". Its *licensed* API contract then forbids caching, storing, bulk download,
  statistical analysis of the content, and **comingling it with third-party content** — which is
  precisely what joining Zomato to FSSAI would be. [PRIMARY, quoted]
- **Swiggy is also closed by contract**, despite a permissive `robots.txt`: its ToS bans any
  `"page-scrape", "robot", "spider"` or "other automatic device, program, algorithm or
  methodology". robots.txt being open does **not** make scraping permitted.
- **OpenStreetMap is a dead end for Kolkata — measured, not assumed.** Overpass returns **195**
  restaurant nodes/ways in the entire Kolkata administrative area, and **0** elements worldwide
  carrying an `fssai` tag. It cannot be a source for either half of the question.
- **Google Places API (New) can give you name + address + geolocation, but you may not store
  it.** Google's policy: "You must not pre-fetch, cache, or store Places API content beyond the
  allowed exceptions, although the place_id is exempt" — so `place_id` is storable indefinitely and
  everything else is not. This single constraint reshapes the whole architecture. [PRIMARY, quoted]
- **The dish problem has no filterable answer.** `"biryani"` is **not** a Google Places place type
  (verified absent from Table A). There is no menu/dish field in Places API (New). The closest
  thing is `places.generativeSummary`, an opaque AI summary on the top-cost SKU. Every workable
  dish-detection approach is either inference or an image/OCR problem — **this is the weakest
  link by a wide margin**, and it is weakest *even if every other part is solved for free*.

---

## 1. FSSAI licence numbers — the key question

### 1.1 Is a restaurant's FSSAI licence number publicly available?

**Yes.** FSSAI operates FoSCoS (Food Safety Compliance System) at
<https://foscos.fssai.gov.in>, and publishes a citizen-facing verification feature described as:
"Public FoSCoS facility to verify FSSAI licence or registration details. It helps consumers,
businesses and authorities check the authenticity/status of an FSSAI licence or registration."
— indexed at <https://fssai.gov.in/citizen/about-license-verification>.

The relevant UI route is **`/advance-fbo-search`** on foscos.fssai.gov.in. A cached render of the
sibling training instance (<https://fcstraining.fssai.gov.in/advance-fbo-search>) shows the form
affordances: *"Commodity, Category Or Area Specific Search"* with *"License / Registration
Certificate"*, *"Search"*, *"Reset"*, and a total-count readout. The phrase "**Area** Specific
Search" is the direct confirmation that area filtering is a first-class, supported feature.

> **Spike required (see §Open questions).** I could not render the live search form (no browser
> session available) and therefore could not visually confirm the *result columns*. That the
> result grid contains the licence number is **strongly indicated** by the endpoint
> `getqrlicensedetails/{licenseNo}` (a licence-number → details lookup) and by the excel-export
> endpoints, but I am marking the exact column set **UNVERIFIED**.

### 1.2 Observed API surface **[REVERSE-ENGINEERED — grey, not documented]**

These paths were read out of FoSCoS's own front-end bundle
(`https://foscos.fssai.gov.in/main.374e09dc2ab315e8a510.bundle.js`, 3.5 MB). **They are not
published API documentation.** Using them programmatically is not authorised by anything I could
find, and §1.3 shows they are actively hardened.

Two gateways are hardcoded in the bundle's config object:

```js
{api:{baseUrl:"/gateway", baseUrlNew:"/webgateway", fsmBaseUrl:"https://mitra.fssai.gov.in"}}
```

Search-relevant endpoints, all under the `commonauth_readonly` namespace on `/webgateway`:

| Method | Path (relative to `https://foscos.fssai.gov.in/webgateway`) | Notes |
|---|---|---|
| GET | `/commonauth_readonly/getstatelist/` | State dropdown |
| GET | `/commonauth_readonly/getdistrictlist/` | District dropdown |
| GET | `/commonauth_readonly/getdistrictlistusingstatecode/` | District by state |
| POST | `/commonauth_readonly/address/gettaluklist` | **Taluka / sub-district** — the "Garia / Baghajatin" level |
| POST | `/commonauth_readonly/address/getvillagelist/` | Village |
| GET | `/commonauth_readonly/commonapi/getallsearchkoblist` | "Kind of Business" list (e.g. Restaurant) |
| POST | `/commonauth_readonly/commonapi/getadvancesearchapplicationdetails/{searchKob}` | **Main search.** Body carries `pinCode`, `category` |
| GET | `/commonauth_readonly/commonapi/getadvancesearchapplicationcount` | Result count |
| POST | `/commonauth_readonly/commonapi/getadvancesearchapplicationdetailsexcel` | **Excel export** |
| POST | `/commonauth_readonly/commonapi/getadvancesearchapplicationdetailsmap` | Map view |
| POST | `/commonauth_readonly/commonapi/getFboListNearMe` | Lat/lng "near me" |
| GET | `/commonauth_readonly/commonapi/captcha` | **CAPTCHA image** (`responseType:"blob"`) |

One endpoint sits outside `commonauth_readonly`, on the other gateway, and looks like a
*first-class integration surface* rather than a UI helper **[REVERSE-ENGINEERED]**:

```
POST https://foscos.fssai.gov.in/gateway/extranet/thirdpartyapi/LicenseOrRegistraionInformation
```

(note FSSAI's own typo, "Registraion"). Because it is not under `commonauth_readonly`, it is
presumably authenticated. **This is the single highest-value thing to email FSSAI about** — if
FSSAI offers a sanctioned third-party licence API, the whole grey area in §1.2 evaporates.

**Answers to the sub-questions:**
- **(a) Search by name/area/locality returning an FBO record with a licence number?** By *area*:
  yes, at State → District → Taluka → Village/PIN granularity. By *business name*: the endpoint
  naming (`getadvancesearchapplicationdetails`) and the `searchKob` path parameter suggest
  kind-of-business scoping; a free-text name field is **UNVERIFIED**.
- **(b) Documented HTTP/API endpoint?** No documented public API was found. Everything above is
  reverse-engineered.
- **(c) robots.txt / anti-bot?** See §1.3.
- **(d) CAPTCHA?** **Yes** — `/commonauth_readonly/commonapi/captcha`, with the response header
  `Access-Control-Expose-Headers: captcha` explicitly configured to surface the challenge.

### 1.3 Anti-automation measures — observed directly

- **There is no `robots.txt` on FoSCoS.** `GET https://foscos.fssai.gov.in/robots.txt` returns
  **HTTP 200, `Content-Type: text/html`, 4181 bytes, `Server: FoSCoS`** — i.e. the SPA's
  `index.html` catch-all, not a robots file. Same on `https://www.fssai.gov.in/robots.txt`.
  `https://www.eatrightindia.gov.in/robots.txt` returns **404**. So *absence of robots restrictions
  is not the same as permission* — see §6.
- **CSRF handshake required.** `GET /gateway/api/auth/csrf-token` returns
  `{"message":"Token generated successfully","status":"success"}` and sets a `csrf_token` cookie
  (`HttpOnly; Secure; SameSite=Strict`).
- **401 without the handshake.** Requesting `/webgateway/commonauth_readonly/commonapi/getallsearchkoblist`
  with no cookie returned **HTTP 401**; the same request with the cookie returned **200**.
- **CORS is allowlisted.** `Access-Control-Allow-Origin` is a fixed list:
  `https://foscos.fssai.gov.in, https://fics.fssai.gov.in, https://prod-api-static.razorpay.com,
  https://secure.payu.in, https://api.razorpay.com, https://eatrightindia.gov.in,
  https://infolnet.fssai.gov.in, https://cleanwater.fssai.gov.in, https://fciqms.in`.
  **Consequence: you cannot call this from your own website's JavaScript.** It must be
  server-side, which also means it is squarely "collection and storage", not a casual user action.
- **Responses are encrypted.** After the CSRF handshake the endpoint returned
  `{"encryptedResponse":"Hpyj9UGZKjPFCq1skx1e…","signature":"fL41zezZDq4+zsnwnlNT…"}` — not JSON.
  `assets/env.js` confirms `window.__env.isEncrypted = true`. The bundle loads
  `/assets/keys/resPublic.pem` and `/assets/keys/encPublic.pem`, but a search of the 3.5 MB
  bundle for `-----BEGIN…PRIVATE KEY-----` returned **0 matches**, so I did not locate the
  decryption key. **UNVERIFIED** whether it sits in a lazy-loaded chunk; I did not chase it.
- **CAPTCHA** as above.

Net: FoSCoS is *hardened against automated collection*. That is a deliberate signal about how
FSSAI wants its data used, and it should be read as one — not as a puzzle to route around.

### 1.4 Bulk / open-data route

- **No FSSAI bulk/open-data endpoint was found.** `data.gov.in` content on FSSAI is
  **UNVERIFIED** — I fetched `https://www.data.gov.in/search?title=fssai` and got only a Nuxt
  client-side shell (no server-rendered results), and the only FSSAI-adjacent string in the page
  bundle was the generic footer: *"The content published on data.gov.in is owned by the respective
  Ministry/State/Department/Organization and licensed under the Government Open Data License"*.
  I found **no** evidence of a published licensed-establishment list for West Bengal or Kolkata.
  I also found **no** published FSSAI "public API for businesses" documentation page.
- The closest thing to a bulk route is the **Excel export endpoint** in §1.2 — but it sits behind
  the same CAPTCHA/CORS/CSRF/encryption stack, and `getadvancesearchapplicationcount` strongly
  suggests a result cap is enforced before export. Treat as *not* a bulk feed.
- **UNVERIFIED:** whether FSSAI's annual reports or public notices publish counts/lists of licensed
  FBOs by state. I did not obtain the annual report PDFs (see §1.5).

### 1.5 Licence tiers and number format

The three tiers are Basic Registration, State Licence, and Central Licence. The commonly cited
threshold is that **Basic Registration applies up to ₹12 lakh annual turnover**, with higher tiers
mandatory above it and for specified business categories (manufacturers, importers, exporters,
bulk suppliers, e-commerce operators).

> **UNVERIFIED — flag for a spike.** I could **not** obtain the governing text. The primary
> instrument is the **Food Safety and Standards (Licensing and Registration of Food Businesses)
> Regulations, 2011**. Every `fssai.gov.in/upload/regulation/*.pdf` path I tried
> (`fss_licensing_regulation_23_07_2024.pdf`, `food_safety_and_standards_licensing_registration_of_food_businesses_regulations_2011.pdf`,
> `food_safety_display_board_regulation_27122021.pdf`, and variants) now returns
> **HTTP 200 but `Content-Type: text/html`, 3221 bytes** — the React SPA shell. fssai.gov.in has
> migrated to a client-rendered SPA and the legacy document paths are dead to non-browser clients.
> The regulations index now lives behind the client route
> `/food-safety-and-standards-regulations` (its old server path `/cms/food-safety-and-standards-regulations.php`
> also now returns the same 3221-byte SPA shell). **What to check:** open
> <https://www.fssai.gov.in> in a real browser, navigate to the Regulations section, and read the
> current consolidated Licensing Regulations PDF for (i) the exact turnover thresholds and
> (ii) **Schedule 2**, which specifies the form of the licence and the licence-number layout.
>
> I am deliberately **not** asserting the digit-level layout of FSSAI licence numbers here. The
> widely-repeated description (state code + year + tier digit + serial) is plausible and commonly
> cited, but "commonly cited" is not "verified", and a wrong number format would silently corrupt
> the matching logic. **Do not build a parser on this until someone reads Schedule 2 directly.**

Also relevant, and the reason this is a workable product at all: FSSAI has required licensed food
businesses to **display** their licence details at the premises via **Food Safety Display Boards**,
which include the licence/registration number and a QR code. I could not fetch the governing
regulation PDF (§1.5 above), so this is **UNVERIFIED** in its current amended form — but it
matters because it means the licence number is, by design, *physically public at the storefront*,
which is what makes a "walk in and read the board" human-in-the-loop fallback legitimate.

---

## 2. Zomato

### 2.1 Is there a public API to third parties?

**There is an API, but it is contract-gated and severely restricted.** Zomato publishes a formal
**API Policy** at <https://www.zomato.com/policies/api-policy> ("Last updated on April 22, 2020").
Key terms, quoted **[PRIMARY]**:

- Access is by agreement: *"You must create an account in order to use Zomato's Licensed Content,
  Marks and API"*; credentials are issued by Zomato; contact `api@zomato.com`.
- Hard cap: *"you will be permitted to make a maximum of thousand (1000) calls to the API per day"*.
- Content scope is discretionary, and restaurant-only: *"Licensed Content shall include such
  restaurant information as may be provided by Zomato **at its sole discretion** from time to time."*
- Attribution is mandatory: a *"~Powered By Zomato"* notice and a **do-follow** link to zomato.com.

Critically, section 4.5 "Data received from Zomato" forbids almost everything this product needs
**[PRIMARY, quoted]**:

> *"ii. You cannot cache or store, record, pre-fetch or otherwise store any portion of the Licensed
> Content or undertake any bulk download operations."*
>
> *"iv. You shall not create or disclose metrics about, or perform any statistical analysis of, the
> Licensed Content."* … *"for a further period of 3 years following termination"*
>
> *"vii. You shall not, directly or indirectly, create enhancements, derivatives, teaser content …"*
>
> *"viii. You shall not comingle the Licensed Content with third party content."*

Read literally: you may not build a searchable database from it, you may not run analysis over it,
you may not derive a "biryani restaurants" index from it, and you may not join it with FSSAI
data. **Even the paid, licensed route does not authorise the proposed product.**

### 2.2 Scraping — closed on two independent grounds

- **robots.txt:** `https://www.zomato.com/robots.txt` contains, under `User-agent: *`,
  literally **`Disallow: /`** — a blanket ban for every non-allowlisted agent. Thirty named
  agents are carved out (Googlebot, bingbot, GPTBot, ClaudeBot, OAI-SearchBot, PerplexityBot,
  etc.); a generic scraper is not among them.
- **ToS:** <https://www.zomato.com/policies/terms-of-service> **[PRIMARY, quoted]**:

> *"d. You agree not to access (or attempt to access) any of the Services by any means other than
> the interface that is provided by Zomato … You specifically agree not to access (or attempt to
> access) any of the Services through any automated means (including use of scripts or web
> crawlers) and shall ensure that you comply with the instructions set out in any robots.txt file."*

And listed as prohibited conduct:

> *"w. Violates the restrictions in any robot exclusion headers on the Services, if any, or bypasses
> or circumvents other measures employed to prevent or limit access to the Services"*
>
> *"ab. Modifies, copies, scrapes or crawls, displays, publishes, licenses, sells, rents, leases,
> lends, transfers or otherwise commercialize any rights to the Services or Our Content"*

Plus, on permitted use:

> *"You may use information made available through the Services solely for your personal,
> non-commercial use."*

and on imagery:

> *"No other party is authorized to reproduce or republish these digital versions in any format
> whatsoever without the prior written permission of Zomato."*

### 2.3 Does Zomato publish FSSAI licence numbers?

**Refuted.** The API Policy defines Licensed Content as "restaurant information" and explicitly
disclaims it: *"ZOMATO MAKES NO REPRESENTATION OR WARRANTY … ZOMATO EXPRESSLY DISCLAIMS ANY AND
ALL WARRANTIES … WITH RESPECT TO … THE LICENSED CONTENT."* The ToS states Zomato *"does not
guarantee the quality of the food products, the prices listed in menus or the availability of all
menu items."* No FSSAI/licence field appears anywhere in Zomato's published API policy or ToS.
**Verdict: Zomato is not a licence-number source. Ever.**

---

## 3. Swiggy

### 3.1 Official APIs

**None found that a third party can sign up for.** Swiggy's partner surface
(<https://partner.swiggy.com>) and <https://partner-with-us.swiggy.com/onboard> are merchant
onboarding, not developer platforms. I found **no public developer portal, no published API
reference, and no pricing page**. **UNVERIFIED** whether internal Partner / Dineout / InstaMart
APIs are ever licensed to external parties — none are advertised, and the absence of any
advertised offer is itself the answer to "can a third party get one?"

### 3.2 robots.txt — permissive, and it does not matter

`https://www.swiggy.com/robots.txt` (HTTP 200, 547 bytes) is genuinely open:

```
User-agent: *
Allow: /
Disallow: /product
Disallow: /product-category
Disallow: /?attachment_id=*
Disallow: /wp-admin
Disallow: /wp-includes
Disallow: /wp-content/plugins/
Disallow: /category/*
Disallow: /track-order/*
Disallow: /order-track/*
Disallow: /ofo/track/*
Disallow: /auth*
Disallow: /web-payments
Disallow: /invoice/*
Disallow: /home
Disallow: /cart
Disallow: /payment
Disallow: /checkout
Disallow: /my-account
Disallow: /rate
```

No restaurant-listing path is disallowed. **This is a trap, not an opportunity.** Swiggy's ToS is
the binding constraint, and it is unambiguous — <https://www.swiggy.com/terms-and-conditions>
**[PRIMARY, quoted]**:

> *"You shall not use any "deep-link", "page-scrape", "robot", "spider" or other automatic
> device, program, algorithm or methodology, or any similar or equivalent manual process, to
> access, acquire, copy or monitor any portion of the Platform…"*

That clause is *broader* than Zomato's — it extends to "any similar or **equivalent manual
process**". It also contains:

> *"You may print off one copy, and may download extracts, of any page(s) from the Platform for
> your personal reference…"*
>
> *"You must not use any part of the materials on the Platform for commercial purposes without
> obtaining a licence to do so from us or our licensors."*

### 3.3 Swiggy Dineout specifically

Dineout is now a Swiggy service (`/dineout`, per the site footer and §9b of its own T&C). Its terms
carry a **separate, stricter IP grant** **[PRIMARY, quoted]**:

> *"The features, information, and materials provided and depicted through the DineOut Services …
> (collectively, the Swiggy Content) are provided to User by Swiggy or its partners or licensors
> solely to support User's permitted use of the DineOut Services. Except as expressly set forth
> herein, **no license is granted to User for any other purpose**, and any other use of the DineOut
> Services or the Swiggy Content by User shall constitute a **material breach** of this T&C."*

Note also that `https://www.swiggy.com/` returned **HTTP 202 with a zero-byte body** to a plain
curl — i.e. there is active bot mitigation in front of the site independent of the ToS.

### 3.4 FSSAI licence numbers in Swiggy data?

**Refuted.** Nothing in Swiggy's ToS, Dineout T&C, or partner surfaces references FSSAI
registration. Swiggy's T&C disclaims accuracy generally and describes restaurants as independent
Merchants it does not guarantee. Swiggy is a discovery/ordering layer, not a licensing registry.

---

## 4. Legitimate third-party geo/POI sources

### 4.1 Google Maps Platform — Places API (New)

**Endpoint (documented, current):**

```
POST https://places.googleapis.com/v1/places:searchText
Content-Type: application/json
X-Goog-Api-Key: <KEY>
X-Goog-FieldMask: <field list>     # REQUIRED — omitting it is an error
{"textQuery": "...", ...}
```

Also: `places:searchNearby`, Place Details, Place Photos, Autocomplete. Source:
<https://developers.google.com/maps/documentation/places/web-service/text-search>.

**Status note:** as of October 2026, *Places API (Legacy) "can no longer be enabled for new Google
Cloud projects"*; existing projects remain supported. Build against **(New)**.

**What it returns for "restaurant + address":** `places.id`, `places.displayName`,
`places.formattedAddress`, `places.addressComponents`, `places.location` (lat/lng),
`places.types`, `places.primaryType`, `places.websiteUri`, `places.nationalPhoneNumber`,
`places.businessStatus`, `places.googleMapsUri`, `places.userRatingCount`, `places.rating`,
`places.currentOpeningHours`, `places.priceLevel`.

**Does it return any licence/registration data? No.** The complete field list contains nothing
resembling a business licence, registration number, or government identifier. Google is not a
licensing registry.

**Billing model — four SKUs, chosen by which fields you mask** (documented at
<https://developers.google.com/maps/documentation/places/web-service/text-search>):
Text Search **Essentials ID-Only** (just `places.id`/`places.name`), **Pro** (address, location,
types, photos, `primaryType`, …), **Enterprise** (rating, hours, phone, website, priceLevel),
and **Enterprise + Atmosphere** (the "experience" fields: `dineIn`, `takeout`, `delivery`,
`servesVegetarianFood`, `editorialSummary`, **`generativeSummary`**, `reviews`, …).
If a single request mixes fields from two tiers it is billed at the higher tier.
**SKU unit prices: UNVERIFIED** — I fetched
<https://developers.google.com/maps/billing-and-pricing/sku-details> and
<https://developers.google.com/maps/documentation/places/web-service/sku-details> but only
recovered the SKU *structure*, not the price table. **Check:** the **Pricing (India)** page under
<https://developers.google.com/maps/documentation/places/web-service/overview> → Pricing, which
Google maintains per-country (India has its own price list). Do not assume US/global rates.

**The constraint that shapes the architecture — caching** [PRIMARY, quoted], from
<https://developers.google.com/maps/documentation/places/web-service/policies>:

> *"You must not pre-fetch, cache, or store Places API content beyond the allowed exceptions,
> although the place_id is exempt from caching restrictions."*
> *"You can therefore store place ID values indefinitely."*
> *"Content, except for place IDs, cannot be cached or stored unless permitted by agreement."*

Also required: *"Applications using the Places API must provide publicly available Terms of Use and
a Privacy Policy, explicitly referencing Google's terms"*, and *"Places API results must display on
a Google Map, if a map is used. When displaying data without a map, the Google logo must be
present."*

**Consequence:** you may build a durable `place_id`-keyed index, but every user-facing request
must re-fetch name/address live from Google. That is workable (and is what the recommendation in
§7 does) but it is not free and it caps how much you can precompute.

### 4.2 OpenStreetMap / Overpass API — free, and empty here

Free and no key required. Endpoints: `https://overpass-api.de/api/interpreter` (POST Overpass QL),
mirrors at `overpass.kumi.systems` etc. Licence ODbL.

Measured coverage for this exact use case:

```
[out:json][timeout:60];
area["name"="Kolkata"]["boundary"="administrative"]->.a;
(node["amenity"="restaurant"](area.a); way["amenity"="restaurant"](area.a););
out count;
```
→ `{ "nodes": "184", "ways": "11", "relations": "0", "areas": "0", "total": "195" }`

**195 restaurants in the whole of Kolkata** (queried 2026-10-10; `timestamp_osm_base`
`2026-10-10T16:46:21Z`). A metro city of that size has orders of magnitude more. And:

```
(node["fssai"](0,0,90,180); way["fssai"](0,0,90,180);); out count;
```
→ `{ "nodes": "0", "ways": "0", "relations": "0", "total": "0" }`

**Zero OSM elements worldwide carry an `fssai` tag.** OSM is useful for *address normalisation and
locality geometry* (Kolkata sub-district boundaries, pincode↔locality mapping) but is **not** a
restaurant source and has no licence data at all.

### 4.3 Justdial / Sulekha / Magicpin / IndiaMART

- **Justdial** — `https://www.justdial.com/robots.txt` is unusually explicit **[PRIMARY]**:
  ```
  User-agent: *
  Disallow: /api/
  Disallow: /jdmart/api/
  Disallow: /real-estate/services/api/
  Disallow: /api/india_api_write
  Disallow: /api/india_api_read
  ```
  So the *crawlable* listing surface is permitted but the **API surface is expressly disallowed**.
  The file also hand-allowlists AI crawlers (`OAI-SearchBot`, `ChatGPT-User`, `Claude-User`,
  `Claude-SearchBot`, `anthropic-ai`) while disallowing `ClaudeBot`. Whether Justdial exposes any
  public/partner API: **UNVERIFIED** — I found no developer portal. It is a lead to *email*, not a
  dependency to plan on.
- **Sulekha, Magicpin, IndiaMART** — I did not obtain their robots.txt or ToS in this pass.
  **UNVERIFIED.** Note Magicpin was acquired by Justdial; IndiaMART is B2B industrial trade, not a
  restaurant directory, so treat it as irrelevant to this use case.
- **Do any of them publish FSSAI numbers?** **UNVERIFIED — and assume not.** Nothing surfaced in
  any search. Indian local directories are aggregators of listings and reviews; the licence number
  is regulator data and there is no general practice of publishing it.

### 4.4 Government open geo data (India Post / Bhuvan / FASNET / NIC)

**Not verified as a source for this.** I found no NIC/Bhuvan/India Post dataset that enumerates
licensed food businesses with premises addresses. India Post and Bhuvan are about postal and
geospatial infrastructure, not business licensing. **UNVERIFIED** — if this matters, the right
search is with the **West Bengal Food Safety Commissioner** (the state licensing authority for
State-licensed FBOs), not with open-data portals. That body is a genuinely promising route and
appears in §Open questions.

---

## 5. The dish-matching problem — "sells biryani"

**This is the weakest link, and it is weak in a way the other sections are not.** Every other part
of this system has at least one clean, documented, licensed source. This part has none.

### 5.1 Menu/dish data availability by source

| Source | Menu / dish text available? |
|---|---|
| **Google Places API (New)** | **No menu field exists.** No per-dish data in any field. |
| **FSSAI / FoSCoS** | **No.** Licensing data contains no product information. |
| **Zomato** | Yes on-site (menus), but §2 forbids using it for this. |
| **Swiggy** | Yes on-site, but §3 forbids it. |
| **OSM** | Technically `cuisine` tags exist, but with 195 restaurants in Kolkata there is nothing to tag. |

### 5.2 Google Places `type` / `generative_types` — can you filter biryani?

**No.** Verified by inspecting the full Place Types (New) documentation
(<https://developers.google.com/maps/documentation/places/web-service/place-types>): the string
`biryani` **does not occur** in Table A or Table B. The nearest food types are
`indian_restaurant`, `north_indian_restaurant`, `south_indian_restaurant`, `fast_food_restaurant`,
`chinese_restaurant`, `italian_restaurant`, `hamburger_restaurant`, `pizza_restaurant`,
`chinese_noodle_restaurant`, `pizza_delivery`.

Note the Table A / Table B distinction, which is easy to get wrong: **only Table A values may be
used as `includedType` filters**; Table B values "may NOT be used as part of a request filter".
Biryani is in neither.

On `generative_types`: the Places API (New) field is **`places.generativeSummary`**, an opaque
AI-generated text summary. It sits on the **Enterprise + Atmosphere** SKU (the most expensive
tier). It is *not* a filter — it is a generated blob — and there is **no `generative_types` field**
in Places API (New) (that belonged to the retired Legacy API). Its reliability for asserting
"this restaurant sells biryani" is unmeasured and I would not stake the product on it.

You *can* run `textQuery: "biryani restaurant in Garia, Kolkata"` — Google will rank accordingly.
That is **free-text relevance ranking, not a structured dish filter**, and results are biased by
whatever users typed into search and reviews.

### 5.3 Anything exposing menu items as text?

Only Zomato and Swiggy, both contractually closed (§2, §3). There is no third option. If the
dish signal must be licensed data, the realistic candidates are (i) a direct commercial agreement
with Zomato/Swiggy written specifically to permit this use — which their current published terms
refuse, so it would require them to agree to an exception — or (ii) the restaurants themselves.

### 5.4 LLM/VLM-on-storefront-photo

This is the approach most people reach for first, and it deserves an honest cost/legality note.

- **Google Street View** — there is no per-restaurant "menu photo" product. Street View Static API
  gives you panoramas/imagery along a path. Indian Street View coverage is **UNVERIFIED** (it was
  rolled out in India from 2022 in a limited set of cities — whether Garia/Baghajatin are covered
  is exactly the thing to check). Menu boards on storefronts are frequently legible at
  Street-View-ish resolutions on Indian roads, but coverage and legibility are both unverified.
  Using Street View imagery is also governed by Google Maps Platform ToS.
- **Mapillary** — free, community-contributed, with a permissive (CC-BY-SA) licence and a real API
  (<https://www.mapillary.com/platform/docs>). Coverage in Kolkata is **UNVERIFIED** but there is
  typically *some* contributor imagery in Indian metros. This is the more legally comfortable
  option of the two, and the one I would spike first.
- **Scraping storefront photos from Zomato/Swiggy/Google** — **do not do this.** It is squarely
  inside the Zomato §2.2 and Swiggy §3.2 clauses quoted above, *and* inside Zomato's express
  restriction on republishing its digitised images. There is no reading of those ToS under which
  this is permitted without a licence.
- **Ask the restaurant / crowd-source.** A mobile capture flow — user photographs the FSSAI
  display board + a menu board — is legally clean, needs no third-party licence, and has a
  pleasant property: **the FSSAI display board and the menu are in the same photo.** This
  collapses the dish problem and the licence-number problem into one citizen action.

### 5.5 Indian government food-image datasets

**UNVERIFIED — I could not confirm a usable one.** FSSAI runs food-safety initiatives and a
laboratory network, and "FoodLab" is referenced in FSSAI's site navigation (I observed
`/cms/mobile-labs.php` in the fssai.gov.in front-end bundle), but:

- I could not confirm any **published, licensed image dataset** of Indian food dishes that could
  train or validate a biryani classifier.
- FSSAI's regulatory scope is *safety and labelling*, not dish recognition, so I regard a
  dish-recognition dataset from FSSAI as unlikely to exist.
- **Practical consequence:** a biryani classifier would most likely be built on a general-purpose
  vision model (which already handles "biryani" as a well-represented food concept) and
  zero-/few-shot prompted, not on bespoke Indian training data.

---

## 6. Legal / compliance reality check

**This section describes instruments. It is not legal advice.** Several of the questions below —
especially whether a specific use infringes a specific ToS, and whether a given dataset can be
republished — need an Indian IP/technology lawyer before you ship commercially. Flagged in the
table and in §Open questions.

### 6.1 The instruments

- **Information Technology Act, 2000.** The sections named in the brief do exist and are relevant
  to *hosting and transmitting* rather than to *collecting*:
  - **s.43** — compensation for damages due to wrongful acts / negligence by an intermediary.
  - **s.70** — liability of an intermediary to an order under s.69.
  - **s.72** — liability of an intermediary for observance of due diligence (in relation to
    s.69A directions; for s.79 safe harbour, see s.79).
  - The **IT (Intermediary Guidelines) Rules, 2021** are the operative obligation set for
  intermediaries. A read-only directory of public business-licence numbers is very unlikely to be
  a "social media intermediary" or "online marketplace" under Rule 2, i.e. most of the Rules
  probably do not bite — **UNVERIFIED**, I could not fetch the text (indiacode.nic.in returned
    **HTTP 403** to every request from this session; the authoritative text is at
    <https://www.indiacode.nic.in/handle/123456789/1979> and needs a browser).
- **Digital Personal Data Protection Act, 2023.** The key point for *this* design is what it does
  **not** cover: it applies to **digital personal data**, i.e. data about identified natural
  persons. A restaurant's business name, premises address and FSSAI licence number are **not**
  personal data of the FBO's proprietor or staff. **The moment you start storing owner/manager
  names, phone numbers of individuals, reviewer names, or photos containing identifiable people,
  DPDP obligations engage** (consent, purpose limitation, retention, and — for any commercial
  scale — Data Protection Board registration). Official source: <https://www.meity.gov.in>
  (**UNVERIFIED** — I did not fetch the Act text; confirm commencement status and the current
  compliance timeline with counsel, as the DPDP Rules were still being phased in).
- **Copyright Act, 1957** — relevant to menu text, photographs and review content (§2.2, §3.2).
- **Contract** — ToS and API agreements are contracts whether or not they are "published".

### 6.2 "Commercial purpose" — why it is the hinge

Both platforms draw the line at commercialisation, and both draw it explicitly:

- Zomato ToS: *"You may use information made available through the Services solely for your
  **personal, non-commercial** use."*
- Swiggy ToS: *"You must not use any part of the materials on the Platform for **commercial
  purposes** without obtaining a licence to do so."*

So the practical spectrum is:

| Use | Zomato | Swiggy |
|---|---|---|
| Personal curiosity, one-off | Contested — robots.txt still `Disallow: /`, and the "automated means" ban is not limited to commercial use | Contested — "page-scrape"/"robot" ban has no commercial qualifier |
| Non-commercial hobby project, stored locally | Breaches ToS + robots.txt; realistically tolerated, not permitted | Breaches ToS; robots.txt technically permits |
| Anything monetised, ad-supported, or used in a business | Clearly prohibited | Clearly prohibited |

**Is a "public-only, non-commercial, no-personal-data tool" meaningfully safer?** **Marginally, and
only against one theory of liability — not against the ToS breaches themselves.** Being
non-commercial helps against *copyright infringement* (fair dealing / private study arguments are
stronger) and against a damages claim framed on commercial appropriation. It does **not** help
against: (a) the express "automated means"/"page-scrape" prohibitions, which are not
commerciality-conditioned; (b) robots.txt, which Zomato applies to `*` with no commerciality
qualifier; (c) **contract** claims under the API agreements, where Zomato's API Policy forbids
storing content **regardless** of whether you charge for it. Do not mistake "I made no money" for
"I complied".

### 6.3 Registration / consent obligations if you store or republish FSSAI data

- **FSSAI side:** I found **no published open-data licence** covering FoSCoS FBO records, and
  FoSCoS is hardened (§1.3). FSSAI's own `/disclaimer` page exists as a client route but I could
  not render it, so **whether FSSAI publishes terms governing automated use is UNVERIFIED** — this
  is a real gap and should be closed by emailing FSSAI (see §Open questions).
- Because there is no open licence, the default legal position for republication is
  **copyright-uncertain**: licence numbers are official facts (facts are not copyrightable) but the
  **compilation/database** may be, and FSSAI asserts database rights. Republicating a handful of
  factual licence numbers with attribution is very low risk; republishing a **bulk dump** is a
  materially different question.
- **No personal-data registration is triggered by the licence data itself** — see the DPDP point
  in §6.1. Registration under the IT Rules turns on intermediary status, which a read-only public
  licence-number lookup almost certainly is not.
- **Practical safe harbour:** attribute to FoSCoS, link back to the FoSCoS verification page, publish
  the verification date with every number, do not let it go stale silently, and stay small.

---

## 7. Verdict on the stated hypothesis

> *"The correct primary source for licence numbers is FSSAI (public regulatory data), and
> Zomato/Swiggy should only ever be used at most as a licensed partner feed or not at all; and the
> dish-matching step is the hard part."*

**Confirmed on all three counts, with one sharpening.**

1. **FSSAI is the correct primary source — and the only correct one.** Zomato and Swiggy provably
   do not carry licence numbers (§2.3, §3.4), so there is no alternative even in principle.
2. **"Licensed partner feed or not at all" is generous to Zomato.** Their *current published*
   contract forbids this use outright — no storing, no analysis, no comingling (§2.1). A partner
   feed is only available if Zomato grants a bespoke exception, which nothing suggests they do.
   Swiggy has no public API at all. So: **not at all**, for now.
3. **The dish-matching step is the hard part — and it is worse than merely hard.** It is not a
   solvable engineering problem against available data; it is a problem where **the two
   compliant sources contain no dish information and the two dish-rich sources are unusable.**
   This is the finding that should drive scoping, not the licence-number part.

---

## Recommended architecture

### Diagram

```
                    ┌──────────────────────────────────────────────┐
                    │  USER: "biryani in Garia / Baghajatin"        │
                    └───────────────────┬──────────────────────────┘
                                        │
                    ┌───────────────────▼──────────────────────────┐
                    │  1. LOCALITY GEOMETRY  (OSM Overpass — free)  │
                    │     Garia / Baghajatin  ->  bbox + pincodes   │
                    │     [195 restaurants: confirmed unusable]     │
                    └───────────────────┬──────────────────────────┘
                                        │  locality scope
                    ┌───────────────────▼──────────────────────────┐
                    │  2. CANDIDATE DISCOVERY                        │
                    │     Google Places API (New) searchText         │
                    │     fieldMask: id,displayName,formattedAddress│
                    │     STORES ONLY place_id  (Google policy)     │
                    └───────┬───────────────────────┬──────────────┘
                            │                       │
                            │ place_id              │ (optional, dish hint)
                            ▼                       ▼
        ┌───────────────────────────────┐  ┌──────────────────────────┐
        │ 3. LICENSE RESOLUTION          │  │ 4. DISH SIGNAL (weakest)  │
        │    FoSCoS, server-side only    │  │  - generativeSummary      │
        │    /webgateway/commonauth_     │  │    (Enterprise+Atmos SKU)│
        │      readonly/...             │  │  - free-text rank         │
        │    CSRF + cookie + CAPTCHA     │  │  - user-submitted photo   │
        │    + ENCRYPTED responses       │  │  - restaurant self-decl   │
        │    PREFER: sanctioned 3P API   │  │    / walked-in survey     │
        └───────────┬───────────────────┘  └───────────┬──────────────┘
                    │ licence no + status + validity     │ dish score (0..1)
                    └─────────────────┬──────────────────┘
                                      ▼
                    ┌──────────────────────────────────────────────┐
                    │  5. JOIN + SERVE  (live fetch at request time) │
                    │     google.maps.com/place/<place_id>  <-- the  │
                    │     canonical link; satisfies Google ToS       │
                    │     name+address re-fetched per request        │
                    └──────────────────────────────────────────────┘
```

### Data-flow description

1. **Resolve locality → geometry (free, clean).** OSM Overpass gives the Garia / Baghajatin
   boundary as a bounding box plus pincodes. Verified working and free; verified that OSM's own
   restaurant data is too sparse to use (195 citywide), but its *administrative geometry* is
   exactly right for this.
2. **Discover candidates (paid, documented, no FSSAI here).** `POST /v1/places:searchText` with
   `textQuery` = the dish + locality, `locationRestriction` = the locality rectangle, field mask
   `places.id,places.displayName,places.formattedAddress,places.types`. Per §4.1, **persist only
   `places.id`.** Everything else is re-fetched at serve time.
3. **Resolve licence numbers (the primary value).** Query FoSCoS for the locality
   (State=West Bengal → District=Kolkata → **Taluka**=Garia/Baghajatin) and reconcile against your
   candidate list by **address string / premises name**, since FoSCoS gives you an FBO record but
   not a Google Place ID. Store `licenceNumber`, `licenceStatus`, `validityDate`, `premisesAddress`,
   `fboName`, `retrievedAt`.
   **Two reconciliation routes, in order of preference:**
   - **(a) Ask FSSAI for the sanctioned third-party API** (`/extranet/thirdpartyapi/…`). Cleanest.
     Email `api@zomato.com`-style: FSSAI's FoSCoS/FSSAI public-information contact. Do this first.
   - **(b) Operate FoSCoS server-side through the documented-by-nobody UI**, at low volume, with a
     human solving the CAPTCHA, caching results for 30 days. **This is legal only if FSSAI's
     disclaimer permits it — which is UNVERIFIED (§6.3) and is a blocker, not a formality.**
4. **Dish signal (weakest).** Layer the signals in §5 in increasing order of cost, ship the cheap
   ones first, and **be explicit in the UI that dish presence is a guess**, not a fact.
5. **Serve live, link to Google.** Every response re-fetches display name + address from Google,
   and links to `https://www.google.com/maps/place/?q=place_id:<ID>`. This satisfies both Google's
   caching policy and its attribution requirement.

### Ordered build steps

| # | Step | Effort | Legal risk | Notes |
|---|---|---|---|---|
| 1 | Locality geometry via Overpass (Garia, Baghajatin) | **0.5 d** | **None** | Free, ODbL, verified working |
| 2 | Google Places API (New) candidate discovery, `place_id`-only store | **1–2 d** | **Low** | Documented API. Confirm **India** price list first |
| 3 | **Email FSSAI** re: sanctioned third-party API + `/disclaimer` terms | **0.5 d** | **None** | **Highest value per hour in this whole plan.** Blocks step 4 |
| 4 | FoSCoS licence resolution — via (a) sanctioned API, else (b) low-volume UI + human CAPTCHA | **3–5 d** | **Medium** — the hinge | Reverse-engineered surface; encrypted responses; no robots.txt but also no permission found |
| 5 | Address/name reconciliation between Places and FSSAI records | **2–4 d** | **Low** | The fiddliest *engineering* task; Indian address variance is the enemy |
| 6 | Dish signal v1: `generativeSummary` + free-text rank + cuisine-type priors | **1–2 d** | **Low** | Cheap. Expect poor precision — this is why it's step 6, not step 1 |
| 7 | Dish signal v2: user-submitted storefront photo → VLM | **3–5 d** | **Low** if consented | Cleanest path to real accuracy; also yields the licence number from the display board |
| 8 | Serving layer: live re-fetch, attribution, FoSCoS verification deep-link, `retrievedAt` | **1–2 d** | **Low** | Required by Google policy |
| 9 | Lawyer review before any monetisation | **—** | **—** | Gate, not a task |

**Critical path:** step 3 → step 4. Everything else is tractable. **If FSSAI says no, the licence
half of this product does not have a clean source, and that is a go/no-go decision, not a
workaround.**

---

## Legal flags

| Instrument | What it constrains | How this design complies / doesn't |
|---|---|---|
| **Zomato ToS** — "not to access … through any automated means (including use of scripts or web crawlers)" (§2.2) | Any scripted access to zomato.com | **Complies: Zomato is not used at all.** robots.txt `* → Disallow: /` reinforces this |
| **Zomato ToS** — "solely for your personal, non-commercial use" | Commercial reuse | **Complies: no Zomato data used** |
| **Zomato ToS** — prohibited conduct (w) robot-exclusion bypass; (ab) "scrapes or crawls" | Scraping and circumvention | **Complies: none** |
| **Zomato API Policy** — no storing, no bulk download, no statistical analysis (3 yrs post-term), no derivatives, **no comingling with third-party content** (§2.1) | Even the *licensed* API route | **Does NOT comply — this is why the licensed route is also rejected.** Clauses (ii)/(iv)/(viii) independently block building a biryani index and joining to FSSAI |
| **Swiggy ToS** — no "deep-link", "page-scrape", "robot", "spider" or equivalent automatic/ manual process; no commercial use without a licence (§3.2) | Scraping and monetised reuse | **Complies: Swiggy is not used at all.** Note robots.txt is permissive but irrelevant — ToS governs |
| **Swiggy DineOut T&C** — "no license is granted to User for any other purpose … shall constitute a material breach" (§3.3) | Any Dineout content reuse | **Complies: none used** |
| **Google Maps Platform Policies** — "must not pre-fetch, cache, or store Places API content … place_id is exempt"; attribution + ToS/Privacy Policy referencing Google required; results on a Google Map or with logo (§4.1) | Storage and display of Places data | **Complies by design:** persist `place_id` only; re-fetch name/address per request; link to `google.com/maps/place/?q=place_id:` |
| **Google Maps Platform ToS** — <https://cloud.google.com/maps-platform/terms> | General use of Maps Platform content | **Needs review** for India + any caching-relief agreement; **UNVERIFIED** whether a caching exception is available on a small-project plan |
| **FSSAI / FoSCoS — published terms on automated access** | Whether FoSCoS may be queried programmatically | **⚠️ UNRESOLVED AND BLOCKING.** No robots.txt exists; `/disclaimer` exists but is client-rendered and unreadable. Absence of robots.txt ≠ permission. Resolve in build step 3 |
| **IT Act 2000 s.43, s.70, s.72 + IT (Intermediary Guidelines) Rules, 2021** | Obligations of intermediaries | **Likely not engaged.** A read-only public-record lookup is very unlikely to be an "online marketplace"/"social media intermediary" under Rule 2. **UNVERIFIED** — indiacode.nic.in returned 403; confirm with counsel |
| **DPDP Act, 2023** | Processing of **digital personal data** about identified natural persons | **Complies if scoped correctly:** licence no. + business address is not personal data. **Breaches if** you store owner/manager names, personal phone numbers, reviewer identities, or identifiable faces in submitted photos. **UNVERIFIED** — commencement/notification status needs counsel |
| **Copyright Act, 1957** | Menu text, photos, review text | **Complies: no third-party menu/photo content stored.** User-submitted photos must be consented to and licensed to you explicitly |
| **ODbL (OSM)** | Attribution + share-alike on derived databases | **Complies:** attribute OpenStreetMap contributors; ODbL share-alike applies if you publish a derived *geographic database* — worth a lawyer's eye on whether your locality index triggers it |

---

## Open questions

**Needs a hands-on spike (blocking):**

1. **Does the live FoSCoS Advance FBO Search result grid actually show the licence number, and
   what are the exact column names?** I could not render it (no browser session). Open
   <https://foscos.fssai.gov.in/advance-fbo-search> in a real browser, filter West Bengal →
   Kolkata → Garia, and record the columns. *Everything downstream depends on this.*
2. **Is there a sanctioned third-party FSSAI API?** Email FSSAI about
   `/extranet/thirdpartyapi/LicenseOrRegistraionInformation`. If yes, the licence half becomes
   trivially clean. **If no, that is the go/no-go for the product.**
3. **What does FoSCoS's `/disclaimer` say about automated access?** (`https://foscos.fssai.gov.in/disclaimer`,
   also `/privacy-policy` — both client-rendered routes that I could not fetch.) Determines whether
   even low-volume UI-driven collection is permissible.
4. **Where does the response-decryption key live?** Responses are
   `{"encryptedResponse":…,"signature":…}`. No private key in the 3.5 MB main bundle; possibly in a
   lazy chunk. Relevant only if (2) fails — it defines how hard a *technical* bypass would be, which
   is itself an argument for not doing it.

**Needs a human decision:**

5. **Is the dish signal worth the product?** If the answer to "can we get reliable menu data from a
   permissible source" is no, then shipping "biryani in Garia" is publishing a guess with a licence
   number attached — which reads as authoritative and is not. Consider reframing the product as
   *"all licensed food businesses in a locality, verified"* with dish as a community signal, which
   is both defensible and genuinely useful.
6. **Monetised or not?** Determines whether §6.2's "commercial purpose" line is crossed, which
   determines how much lawyer time is needed up front.
7. **Kick off the lawyer review** — specifically: (i) republishing a set of FSSAI licence numbers
   with attribution; (ii) FoSCoS automated access; (iii) ODbL share-alike on a derived locality
   database; (iv) DPDP scope on user-submitted photos containing identifiable people.

**Secondary spikes:**

8. **FSSAI licence-number format** — read Schedule 2 of the Licensing Regulations (§1.5). Do not
   write a parser before this is read.
9. **Google Places pricing for India** and whether a small-project plan's monthly credit covers
   your query volume — plus whether `generativeSummary` (Enterprise+Atmosphere) is affordable at all.
10. **Mapillary / Google Street View coverage in Garia & Baghajatin**, and menu-board legibility.
11. **West Bengal Food Safety Commissioner** as a direct route to State-licensed FBO records for
    Kolkata (no open-data portal found; this may simply be the right human contact).
12. **Justdial / Sulekha / Magicpin partner APIs** — email rather than scrape. Their robots.txt
    disallows their API paths explicitly.

---

## Sources

Every URL below was fetched or directly relied on during this research on 2026-10-10.

**FSSAI / FoSCoS**
1. <https://foscos.fssai.gov.in/> — SPA shell; no robots.txt (returns index.html, HTTP 200)
2. <https://foscos.fssai.gov.in/robots.txt> — HTTP 200 `text/html` 4181 B, `Server: FoSCoS` ⇒ **no robots file**
3. <https://foscos.fssai.gov.in/assets/env.js> — `window.__env.isEncrypted = true`
4. <https://foscos.fssai.gov.in/main.374e09dc2ab315e8a510.bundle.js> — endpoint paths, `/gateway` + `/webgateway` config **[REVERSE-ENGINEERED]**
5. <https://foscos.fssai.gov.in/assets/js/encmd5.js> — md5 helpers
6. <https://foscos.fssai.gov.in/gateway/api/auth/csrf-token> — CSRF handshake, `Set-Cookie: csrf_token`
7. <https://foscos.fssai.gov.in/webgateway/commonauth_readonly/commonapi/getallsearchkoblist> — 401 without cookie, 200 with; encrypted response body
8. <https://foscos.fssai.gov.in/webgateway/commonauth_readonly/commonapi/captcha> — CAPTCHA endpoint; `Access-Control-Expose-Headers: captcha`
9. <https://fcstraining.fssai.gov.in/advance-fbo-search> — cached form render: "Commodity, Category Or Area Specific Search"
10. <https://fssai.gov.in/citizen/about-license-verification> — licence-verification feature description
11. <https://www.fssai.gov.in/robots.txt> — SPA shell ⇒ no robots file
12. <https://www.eatrightindia.gov.in/robots.txt> — HTTP 404
13. <https://www.fssai.gov.in/assets/index-9q5yo8Y4.js> — SPA route/CMS map (`/disclaimer`, `/food-safety-and-standards-regulations`, `/cms/mobile-labs.php`)
14. <https://www.fssai.gov.in/upload/regulation/fss_licensing_regulation_23_07_2024.pdf> — **dead**, returns SPA shell
15. <https://www.fssai.gov.in/upload/regulation/food_safety_and_standards_licensing_registration_of_food_businesses_regulations_2011.pdf> — **dead**
16. <https://www.fssai.gov.in/upload/regulation/food_safety_display_board_regulation_27122021.pdf> — **dead**

**Zomato**
17. <https://www.zomato.com/robots.txt> — `User-agent: * → Disallow: /`
18. <https://www.zomato.com/policies/api-policy> — full API Policy text (last updated 22 Apr 2020)
19. <https://www.zomato.com/policies/terms-of-service> — clauses (d), (w), (ab); "personal, non-commercial use"
20. <https://www.zomato.com/policies> — policy index (source of the above URLs)

**Swiggy**
21. <https://www.swiggy.com/robots.txt> — `User-agent: * → Allow: /` with limited disallows (547 B)
22. <https://www.swiggy.com/terms-and-conditions> — "page-scrape"/"robot"/"spider" clause; commercial-use clause
23. <https://www.swiggy.com/dineout/terms-and-conditions> — DineOut IP grant; §9b/9d/9e structure
24. <https://www.swiggy.com/privacy-policy> — HTTP 200
25. `GET https://www.swiggy.com/` — HTTP 202, zero-byte body (bot mitigation)
26. <https://partner.swiggy.com>, <https://partner-with-us.swiggy.com/onboard> — merchant onboarding, no developer portal

**Google**
27. <https://developers.google.com/maps/documentation/places/web-service/text-search> — endpoint, field masks, SKU tiers, 60-result cap
28. <https://developers.google.com/maps/documentation/places/web-service/place-types> — Table A/B; **no `biryani` type**
29. <https://developers.google.com/maps/documentation/places/web-service/policies> — caching restrictions; place_id exemption; attribution rules
30. <https://developers.google.com/maps/documentation/places/web-service/overview> — Places API (New) nav; Legacy deprecation notice
31. <https://developers.google.com/maps/billing-and-pricing/sku-details> — SKU structure only; **prices not recovered**
32. <https://cloud.google.com/maps-platform/terms> — Maps Platform ToS

**OpenStreetMap**
33. `POST https://overpass-api.de/api/interpreter` — Kolkata admin-area restaurant count = **195** (nodes 184, ways 11)
34. `POST https://overpass-api.de/api/interpreter` — global `["fssai"]` count = **0**

**Other**
35. <https://www.justdial.com/robots.txt> — `Disallow: /api/`, `/api/india_api_read`, `/api/india_api_write`; AI-agent allowlist
36. <https://www.data.gov.in/search?title=fssai> — Nuxt shell only; GODL footer text
37. <https://www.indiacode.nic.in/handle/123456789/1979> — **HTTP 403**, could not verify IT Act / IT Rules text
38. <https://www.meity.gov.in> — DPDP Act 2023 authoritative source (not fetched)
39. <https://html.duckduckgo.com/html/> — used for discovery; served a CAPTCHA partway through
40. <https://www.bing.com/search> — used for discovery; ignores `site:` filters
