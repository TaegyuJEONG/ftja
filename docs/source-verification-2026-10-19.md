# Source and location verification — 2026-10-19

Scope: small live JobSpy 1.3.0 searches from this machine in France, Germany,
the USA, and the primary market of sources outside those locations. Queries
used `Product Manager`, up to three results, and postings from the past week.
The links below are **URLs returned by the scraper**, paired with its returned
location. A successful sample is not a claim that every country or every future
request will work. Job listings may expire. Source-site documentation and the
current JobSpy adapter are separate evidence.

| Source | Company source for location behavior | Live query → returned location and posting URL | Current disposition |
| --- | --- | --- | --- |
| LinkedIn | [LinkedIn location filter help](https://www.linkedin.com/help/linkedin/answer/a507441/filter-and-sort-job-search-results?lang=en) says city, state, province, or country | France → Neuilly-sur-Seine, France: [posting](https://www.linkedin.com/jobs/view/4480951691); Germany → Munich, Germany: [posting](https://www.linkedin.com/jobs/view/4478298123); USA → Seattle, WA, USA: [posting](https://www.linkedin.com/jobs/view/4467278136) | Selectable; three countries sampled |
| Indeed | [Indeed country and language directory](https://www.indeed.com/countries) lists the country sites | France → Paris, France: [posting](https://fr.indeed.com/viewjob?jk=b65dcb4715c8ee9a); Germany → Berlin, Germany: [posting](https://de.indeed.com/viewjob?jk=d9bba3f62379fa65); USA → Fremont, CA, USA: [posting](https://www.indeed.com/viewjob?jk=8e94fbd97310132f) | Selectable; three countries sampled |
| Glassdoor | [Glassdoor's international site list](https://www.glassdoor.com/blog/responding-to-negative-glassdoor-reviews-faqs/) includes the USA, France, and Germany | France → Strasbourg, France: [posting](https://www.glassdoor.fr/job-listing/j?jl=1010298151625); Germany → Hannover, Germany: [posting](https://www.glassdoor.de/job-listing/j?jl=1010299970344); USA → Columbia, SC, USA: [posting](https://www.glassdoor.com/job-listing/j?jl=1010297997037) | Selectable; three countries sampled |
| Bayt | [Bayt's company description](https://www.bayt.com/en/about/) focuses on MENA; its [job search](https://www.bayt.com/en/international/jobs/) also has country filters outside MENA | France → France: [posting](https://www.bayt.com/en/france/jobs/senior-manager-product-design-security-75326258/); Germany → Germany: [posting](https://www.bayt.com/en/germany/jobs/policy-communications-lead-europe-75329808/); USA → USA: [posting](https://www.bayt.com/en/usa/jobs/sr-ai-product-manager-crm-portals-and-apps-75320957/) | Selectable; three countries sampled, country-level location only in these samples |
| HelloWork | [HelloWork's France search guide](https://www.hellowork.com/fr-fr/emploi.html) describes French cities and postcodes | Paris → Paris, France: [posting](https://www.hellowork.com/fr-fr/emplois/84562271.html) | Selectable for France only |
| BDJobs | [BDJobs about page](https://bdjobs.com/about/) describes its Bangladesh job market; the [home page](https://www.bdjobs.com/default.asp?lanset=eng) also links to overseas jobs | Dhaka → Dhaka, Bangladesh: [posting](https://bdjobs.com/h/details/1547289) | Selectable for Bangladesh only; overseas job coverage in its site is not proof that this JobSpy adapter filters overseas locations |
| Naukri | [Naukri's own guide](https://www.naukri.com/blog/how-to-use-naukri-portal/) says it serves India and overseas job seekers | Recheck Mumbai → 0, JobSpy logged HTTP 406. Query USA → Bengaluru, India: [posting](https://www.naukri.com/job-listings-qa-engineer-coxvi-technologies-bengaluru-2-to-5-years-191026020380) | Removed from selectable list until reliable location filtering and responses are verified |
| ZipRecruiter | [US job search](https://www.ziprecruiter.com/find-a-job) and [French job search](https://www.ziprecruiter.fr/jobs/search) are both live sites | JobSpy query United States → 0, HTTP 307 redirect from its `.com` request on this machine. JobSpy's [documented adapter scope](https://github.com/speedyapply/JobSpy#readme) is US/Canada; it does not parse `.fr`. | Removed from selectable list until a working adapter returns a posting URL |

Negative location checks: the current BDJobs adapter returned Bangladesh jobs
for both Germany and USA queries (same [Dhaka posting](https://bdjobs.com/h/details/1547289));
the current HelloWork adapter returned Strasbourg, France for a Berlin query
([posting](https://www.hellowork.com/fr-fr/emplois/84517578.html)). Naukri's
Germany query returned HTTP 406. These checks are why a website's existence or
an accepted location string alone is insufficient evidence of coverage.

Next gate for a new source/country: (1) company documentation or an official
search page for that country, (2) a live scraper response with a posting URL,
(3) the returned posting's location matches the intended country, and (4) a
repeat check to catch transient blocks or redirects. Keep failures visible in
the verification record instead of marking the whole job board unsupported.

## EU and USA country-by-country probe

The following links are individual posting URLs returned by JobSpy on 2026-10-19.
The search used `Product Manager` from the past week, then retried zero-result
countries with `manager` and no date restriction. A dash means no posting URL
was returned; it does not prove the website has no jobs in that country.

| Country | Indeed | Glassdoor | Bayt |
| --- | --- | --- | --- |
| Austria | [Graz, ST, Austria](https://at.indeed.com/viewjob?jk=6e6e738801d3268a) | [Wien, Austria](https://www.glassdoor.at/job-listing/j?jl=1010295329979) | [Austria](https://www.bayt.com/en/austria/jobs/sr-manager-commercial-operations-central-eastern-europe-healthcare-austria-75322854/) |
| Belgium | [Brussels, BRU, Belgium](https://be.indeed.com/viewjob?jk=b1dedcad71de407e) | [Wilsele, Belgium](https://fr.glassdoor.be/job-listing/j?jl=1010295441299) | [Belgium](https://www.bayt.com/en/belgium/jobs/microsoft-copilot-lead-75330417/) |
| Bulgaria | — | — | [Yambol, Bulgaria](https://www.bayt.com/en/bulgaria/jobs/hr-supervisor-75334161/) |
| Croatia | — | — | — |
| Cyprus | — | — | [Cyprus](https://www.bayt.com/en/cyprus/jobs/cleaner-75326716/) |
| Czech Republic | [Brno, JM, Czech Republic](https://cz.indeed.com/viewjob?jk=659c03b2a3051de5) | [Brno, Czech Republic](https://www.glassdoor.com/job-listing/j?jl=1010293233642) | [Czech Republic](https://www.bayt.com/en/czech-republic/jobs/field-technical-engineer-fte-75316707/) |
| Denmark | [København, D84, Denmark](https://dk.indeed.com/viewjob?jk=177bfd652d518422) | [Copenhagen, Denmark](https://www.glassdoor.com/job-listing/j?jl=1010299707987) | [Denmark](https://www.bayt.com/en/denmark/jobs/international-consultancy-remote-based-supply-chain-cost-to-serve-framework-supply-chain-management-unit-scmu-copenhagen-denmark-75327153/) |
| Estonia | — | — | [Estonia](https://www.bayt.com/en/estonia/jobs/tiktok-shop-growth-manager-75260603/) |
| Finland | [Rovaniemi, F11, Finland](https://fi.indeed.com/viewjob?jk=466e437d1d497c97) | [Rovaniemi, Finland](https://www.glassdoor.com/job-listing/j?jl=1010296541672) | [Finland](https://www.bayt.com/en/finland/jobs/named-core-account-executive-finland-75329839/) |
| France | [Rennes, A2, France](https://fr.indeed.com/viewjob?jk=b334aef7b6296f61) | [Strasbourg, France](https://www.glassdoor.fr/job-listing/j?jl=1010298151625) | [France](https://www.bayt.com/en/france/jobs/senior-manager-product-design-security-75326258/) |
| Germany | [Berlin, BE, Germany](https://de.indeed.com/viewjob?jk=0e71962cc4090c49) | [Giebelstadt, Germany](https://www.glassdoor.de/job-listing/j?jl=1010296596838) | [Germany](https://www.bayt.com/en/germany/jobs/policy-communications-lead-europe-75329808/) |
| Greece | [Άλιμος, GRI, Greece](https://gr.indeed.com/viewjob?jk=90101cf06d62e5f1) | [Athens, Greece](https://www.glassdoor.com/job-listing/j?jl=1010296705336) | [Greece](https://www.bayt.com/en/greece/jobs/commercial-training-operations-specialist-75322824/) |
| Hungary | [Budapest, PE, Hungary](https://hu.indeed.com/viewjob?jk=c772a7188446b88b) | [Budapest, Hungary](https://www.glassdoor.com/job-listing/j?jl=1010299648584) | [Hungary](https://www.bayt.com/en/hungary/jobs/clm-head-hungary-75318948/) |
| Ireland | [Dublin, D, Ireland](https://ie.indeed.com/viewjob?jk=48933e4c40c1f9dd) | [Waterford, Munster, Ireland](https://www.glassdoor.ie/job-listing/j?jl=1010297856325) | [Ireland](https://www.bayt.com/en/ireland/jobs/operations-core-project-lead-analyst-75334669/) |
| Italy | [Osio Sotto, LOM, Italy](https://it.indeed.com/viewjob?jk=a4af3a4042ab5c71) | [Torino, Italy](https://www.glassdoor.it/job-listing/j?jl=1010295530215) | [Turin, Italy](https://www.bayt.com/en/italy/jobs/product-developer-75316697/) |
| Latvia | — | — | [Latvia](https://www.bayt.com/en/latvia/jobs/tiktok-shop-growth-manager-75260602/) |
| Lithuania | — | — | [Lithuania](https://www.bayt.com/en/lithuania/jobs/senior-operations-manager-74542718/) |
| Luxembourg | [Bertrange, L0L, Luxembourg](https://lu.indeed.com/viewjob?jk=47f086ed1c215a23) | [Mamer, Luxembourg](https://www.glassdoor.com/job-listing/j?jl=1010209240691) | [Luxembourg](https://www.bayt.com/en/luxembourg/jobs/financial-crimes-investigator-luxembourg-75313239/) |
| Malta | — | — | [Malta](https://www.bayt.com/en/malta/jobs/business-development-executive-75318369/) |
| Netherlands | [Amsterdam, NH, Netherlands](https://nl.indeed.com/viewjob?jk=5a538acd88542173) | [Zeist, Netherlands](https://www.glassdoor.nl/job-listing/j?jl=1010297854772) | [Netherlands](https://www.bayt.com/en/netherlands/jobs/product-manager-hybrid-75321759/) |
| Poland | [Warszawa, MZ, Poland](https://pl.indeed.com/viewjob?jk=d41af6c7da577087) | [Masovia, Poland](https://www.glassdoor.com/job-listing/j?jl=1010294957010) | [Warsaw, Poland](https://www.bayt.com/en/poland/jobs/it-project-manager-m-f-d-75333004/) |
| Portugal | [Lisboa, P11, Portugal](https://pt.indeed.com/viewjob?jk=87c241de20a1458f) | [Lisbon, Portugal](https://www.glassdoor.com/job-listing/j?jl=1010298503828) | [Portugal](https://www.bayt.com/en/portugal/jobs/salesforce-business-owner-75335402/) |
| Romania | [București, IF, Romania](https://ro.indeed.com/viewjob?jk=d61118a7ad1cfc0c) | [Bucharest, Romania](https://www.glassdoor.com/job-listing/j?jl=1010106466966) | [Romania](https://www.bayt.com/en/romania/jobs/collections-supervisor-with-spanish-and-english-evening-shift-75331477/) |
| Slovakia | — | — | — |
| Slovenia | — | — | [Slovenia](https://www.bayt.com/en/slovenia/jobs/senior-product-manager-sovereign-hybrid-deployments-75330438/) |
| Spain | [VC, Spain](https://es.indeed.com/viewjob?jk=19fd23d86eb125a4) | [Spain](https://www.glassdoor.es/job-listing/j?jl=1010297216977) | [Spain](https://www.bayt.com/en/spain/jobs/senior-product-manager-75331994/) |
| Sweden | [Stockholm, AB, Sweden](https://se.indeed.com/viewjob?jk=3d0a9bd0c6c9a37b) | [Ludvika, Sweden](https://www.glassdoor.com/job-listing/j?jl=1010296631670) | [Sweden](https://www.bayt.com/en/sweden/jobs/lead-solutions-architect-generative-ai-emea-emerging-dnb-75325993/) |
| USA | [OR, USA](https://www.indeed.com/viewjob?jk=769073cb44b3c0fe) | [San Ramon, CA, USA](https://www.glassdoor.com/job-listing/j?jl=1010294883057) | [USA](https://www.bayt.com/en/usa/jobs/sr-ai-product-manager-crm-portals-and-apps-75320957/) |

Summary: Indeed and Glassdoor returned a country-matching posting URL for
18 EU countries plus the USA. Both returned no URL for Bulgaria, Croatia,
Cyprus, Estonia, Latvia, Lithuania, Malta, Slovakia, and Slovenia. Indeed
logged HTTP 400 for those nine countries in both runs. Glassdoor returned
zero without an error from the same queries; those countries need a different
location-resolution investigation. Bayt returned a URL for 25 EU countries
plus the USA; Croatia and Slovakia were not routed by the current adapter.

Raw records, including query, timestamp, result count, and URL:
[first pass](source-probes-eu-us-2026-10-19.jsonl),
[zero-result recheck](source-probes-eu-us-recheck-2026-10-19.jsonl).
