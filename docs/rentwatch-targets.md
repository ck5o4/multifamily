# Rentwatch Targets — BR Metro Suburbs

Directly-managed property websites for tools/marketwatch.py (properties' OWN sites,
never aggregators — see tools/README.md "Market watch"). These are the professionally-
managed complexes that set the market rent ceiling in each town; small properties
rarely have sites. Researched 2026-08.

Verification notes: "WebFetch OK" = page loaded and showed floorplans directly.
"403/bot-block" = site is live (confirmed via search results showing current plans/
pricing) but blocks server-side fetch — marketwatch's fetcher may need a browser
User-Agent; verify on first snapshot. Rents shown are asking at research time.

## Baker, LA — NO own-site targets found

Baker stock is older and aggregator-only. Camellia Trace's site (camelliabaker.com)
refused connection — likely retired. Market intel: RentCast + Zumper are the only
rent signals in Baker (~$1,068 1BR / ~$1,293 2BR per aggregators). This is itself
useful — no professionally-marketed Class A ceiling in town.

## Zachary, LA

1. **Audubon Park Apartment Homes** — 1/2/3BR (e.g., A1 684sf, B1 982sf). 403/bot-block.
   `python3 tools/marketwatch.py add "Audubon Park Zachary" https://www.audubonparkzachary.com/floorplans`
2. **The Palms at Sunset Lakes** — 1BR from ~$1,290, 2BR ~$1,575, 3BR ~$1,690; 656-1330sf. 403/bot-block.
   `python3 tools/marketwatch.py add "Palms at Sunset Lakes Zachary" https://www.thepalmsatsunsetlakesla.com/floorplans`

## Denham Springs, LA

3. **Parc at Denham Springs** — 5 plans, 1BR 737sf to 3BR 1183sf. WebFetch OK (prices dynamic).
   `python3 tools/marketwatch.py add "Parc at Denham Springs" https://www.parcatdenhamsprings.com/Floor-Plans.aspx`
4. **Arden Park** — 1BR ~$1,137 to 3BR ~$1,495; 766-1150sf. 403/bot-block.
   `python3 tools/marketwatch.py add "Arden Park Denham Springs" https://www.ardenparkla.com/floorplans`
5. **Spring Tree Apartments** — 1-4BR, ~$886-$1,335. Homepage loads; /models is floorplans path.
   `python3 tools/marketwatch.py add "Spring Tree Denham Springs" https://springtreedenham.com/models`
6. **Village at Juban Lakes** — 1-3BR ~$1,124-$1,360; 712-1079sf. 403/bot-block.
   `python3 tools/marketwatch.py add "Village at Juban Lakes" https://www.villageatjubanlakes.com/floorplans`

## Walker, LA

7. **Creekside Crossing** — 168 units, 2016 build, ~$1,099-$1,302; 765-1273sf. Homepage loads; /models is floorplans path.
   `python3 tools/marketwatch.py add "Creekside Crossing Walker" https://www.liveatcreeksidecrossing.com/models`
8. **Cade's Lake Apartments** — small family-operated, 10799 Florida Blvd. Site refused
   connection (down or blocking). [VERIFY manually before adding]
   `python3 tools/marketwatch.py add "Cades Lake Walker" https://www.cadeslakeapartments.com/`

## Gonzales / Prairieville, LA

9. **Sawgrass Point** — 1-3BR ~$1,150-$2,500; 729-1427sf (Arlington Properties). 403/bot-block.
   `python3 tools/marketwatch.py add "Sawgrass Point Gonzales" https://www.sawgrasspoint.com/`
10. **Silver Oaks Apartments** — 1BR ~$1,250 / 2BR ~$1,485 / 3BR ~$1,660; 752-1238sf.
    14496 Airline Hwy, Gonzales (site says Prairieville). 403/bot-block.
    `python3 tools/marketwatch.py add "Silver Oaks Gonzales" https://www.liveatsilveroaks.com/floorplans`
11. **Mansions at Ivy Lake** — 6 plans, A1 1BR 818sf to C2 3BR 1524sf; upper-market comp.
    WebFetch OK (prices via RealPage widget — dynamic).
    `python3 tools/marketwatch.py add "Mansions at Ivy Lake Gonzales" https://www.mansionsativylake.com/Floor-plans.aspx`

## Add-all block (excludes Cade's Lake pending verification)

```
python3 tools/marketwatch.py add "Audubon Park Zachary" https://www.audubonparkzachary.com/floorplans
python3 tools/marketwatch.py add "Palms at Sunset Lakes Zachary" https://www.thepalmsatsunsetlakesla.com/floorplans
python3 tools/marketwatch.py add "Parc at Denham Springs" https://www.parcatdenhamsprings.com/Floor-Plans.aspx
python3 tools/marketwatch.py add "Arden Park Denham Springs" https://www.ardenparkla.com/floorplans
python3 tools/marketwatch.py add "Spring Tree Denham Springs" https://springtreedenham.com/models
python3 tools/marketwatch.py add "Village at Juban Lakes" https://www.villageatjubanlakes.com/floorplans
python3 tools/marketwatch.py add "Creekside Crossing Walker" https://www.liveatcreeksidecrossing.com/models
python3 tools/marketwatch.py add "Sawgrass Point Gonzales" https://www.sawgrasspoint.com/
python3 tools/marketwatch.py add "Silver Oaks Gonzales" https://www.liveatsilveroaks.com/floorplans
python3 tools/marketwatch.py add "Mansions at Ivy Lake Gonzales" https://www.mansionsativylake.com/Floor-plans.aspx
```

Caveat: sites returning 403 to plain HTTP fetches may also 403 marketwatch's fetcher.
After adding, run `python3 tools/marketwatch.py snapshot` once and drop any target
whose raw HTML in market-data/ comes back empty or blocked.
