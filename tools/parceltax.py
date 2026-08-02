"""Reconcile a parcel's ACTUAL assessment against the model's tax estimate.

latax.py estimates post-sale taxes from parish-average millage. Listings and
assessor sites publish the parcel's real current assessment - this tool closes
the loop: what the seller pays now, what YOU will pay after reassessment, and
how far the parish-average estimate might drift from the parcel's actual
taxing district.

    python3 tools/parceltax.py --parish livingston --assessment 24637 --price 1699000
    python3 tools/parceltax.py --parish ascension  --assessment 25900 --price 525000 --commercial-share 0

Assessment = the assessor's TOTAL assessed value (land + improvements), the
number printed on LoopNet listings under "Property Taxes". LA residential
improvements assess at 10% of FMV; the implied-FMV line shows what value the
assessor is carrying - a big gap vs your price is normal (assessors lag) and
is exactly why reassessment risk is real.
"""

import argparse
import sys

from latax import MILLAGE, ASSESSMENT_RATIO_RESIDENTIAL, ASSESSMENT_RATIO_COMMERCIAL


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parish", required=True, help="parish name as in latax.py")
    ap.add_argument("--assessment", type=float, required=True,
                    help="current TOTAL assessed value from the assessor/listing")
    ap.add_argument("--price", type=float, required=True, help="your purchase price")
    ap.add_argument("--millage", type=float,
                    help="parcel's actual district millage if known; defaults to parish average")
    ap.add_argument("--commercial-share", type=float, default=0.0,
                    help="fraction of value assessed at the 15%% commercial ratio")
    args = ap.parse_args()

    parish = args.parish.lower().strip()
    if parish not in MILLAGE:
        raise SystemExit(f"Unknown parish {parish!r}. Known: {', '.join(sorted(MILLAGE))}")
    mills = args.millage if args.millage else MILLAGE[parish]
    source = "parcel district" if args.millage else "parish average"

    ratio = (ASSESSMENT_RATIO_RESIDENTIAL * (1 - args.commercial_share)
             + ASSESSMENT_RATIO_COMMERCIAL * args.commercial_share)
    implied_fmv = args.assessment / ratio
    current_tax = args.assessment * mills / 1000
    new_assessment = args.price * ratio
    new_tax = new_assessment * mills / 1000

    print(f"Parish              : {parish.title()}  ({mills:.2f} mills, {source})")
    print(f"Current assessment  : ${args.assessment:,.0f}  "
          f"(assessor carries the property at ~${implied_fmv:,.0f} FMV)")
    print(f"Seller's tax bill   : ${current_tax:,.0f}/yr")
    print(f"Post-sale assessment: ${new_assessment:,.0f}  (at your ${args.price:,.0f})")
    print(f"YOUR tax bill       : ${new_tax:,.0f}/yr")
    print(f"Reassessment jump   : ${new_tax - current_tax:+,.0f}/yr "
          f"({new_tax / current_tax:,.1f}x)" if current_tax else "")
    if implied_fmv < args.price * 0.6:
        print("NOTE: assessor's FMV is far below your price - expect the full "
              "reassessment jump; a broker pro forma using the seller's tax "
              "line understates your bill materially.")


if __name__ == "__main__":
    main()
