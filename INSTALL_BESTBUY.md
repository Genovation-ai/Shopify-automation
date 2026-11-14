# Best Buy condition-specific preview

Amazon remains the comparison source for Excellent targets. Best Buy is for
non-Excellent targets, with exact condition matching: Good is not Very Good.
The pilot supports exact labels Good, Very Good, Fair, Acceptable and Premium
only when present in the catalogue and explicitly confirmed on the listing.
These labels are not equivalences or conversions between grades.

## One command for several captures

The v2 bookmark stays the same. Extract the updated package into your project
folder, including bestbuy_batch_preview.py and tests/test_bestbuy_batch.py.
For each selected colour, use the regular-Chrome v2 bookmark on the correct
Good listing and move the downloaded capture into your project folder. Keep
each download as a separate file: do not overwrite another colour's capture.
Names beginning with bestbuy and ending with .json are scanned. Chrome's
bestbuy_capture (1).json names and accidental bestbuy_pink.json.json names work.
Renaming by colour is optional; the contents identify the product.

For the reviewed first three iPhone 13 Good targets (Blue, Pink, Midnight):

```powershell
python bestbuy_batch_preview.py --model "iPhone 13" --condition "Good" --start 0 --limit 3
```

The command reads captures beside the script, even if PowerShell was opened
in another working folder. It creates one bestbuy_report.csv containing a row
for every selected target, in a new bestbuy_runs subfolder. The full path is
printed. Seller, source URL, main offer price, proposed price, actual capture
time and condition-specific variant ID remain in each row. capture_inputs.json
records which input files were selected, rejected or superseded.

- All captures must still be within 15 minutes of their original capture time.
  Running the command does not refresh a saved offer. A previous Blue capture
  from an older day remains on HOLD until a new one is collected.
- The newest capture for a target is used, never the cheapest saved price.
  If it fails stock, condition, price or freshness checks, an older capture
  is not substituted. Conflicting captures at the same time remain on HOLD.
- A missing capture is HOLD, not evidence that the website has no candidate.
- Historical report/evidence files cannot be used as browser captures.
- One HOLD does not discard valid rows for other colours. Exit code 1 means
  the report has unresolved targets. No Shopify prices are applied.
- Only the selected model and exact grade are matched. Good cannot substitute
  for Very Good, and Excellent stays with Amazon. No colour fallback is applied.

To select Pink and Midnight only, use --start 1 --limit 2. The maximum is five
targets per batch. To keep captures in a separate folder, use
--capture-dir "bestbuy_captures"; relative folder paths start beside the script.
No browser opens and no website is contacted during batch verification.

Extract all files from this ZIP into the existing tech-genius-price-agent folder,
replacing the previous Best Buy files. Keep the tests files inside tests.
Do not change the existing Amazon scripts or agent_support.py.

## Capture the main offer in regular Chrome (when the agent browser is blocked)

Delete the old Capture Best Buy Main Offer bookmark and import the updated
bestbuy_bookmarks.html. Select the bookmark ending in v2, not an older copy.
The v2 collector uses the primary delivery tab, summary title and Web Code, and
pricing container observed in the saved page. Hidden sticky-cart and modal
buttons do not qualify as the main offer. A price skeleton remains on HOLD.

This is a user-operated browser capture with automatic extraction, followed by
offline Python verification and reporting. It does not provide unattended browsing
or bypass Best Buy's access controls. Use it only on a product page you can open
normally. It never opens Compare More Offers or uses its advertised prices.

1. Import bestbuy_bookmarks.html into your regular Chrome: press Ctrl+Shift+O
   to open Bookmark Manager; use its top-right three-dot menu, Import bookmarks,
   then select bestbuy_bookmarks.html from the extracted package. Do not just open
   the HTML file and click its link there. Look in Imported bookmarks for
   Capture Best Buy Main Offer v2. You only need to import this once.
2. Open the desired Best Buy product in that Chrome browser. Refresh it, expand
   Specifications and About This Product, and wait until its main price, seller
   and availability finish loading. Do not open Compare More Offers.
3. While the product tab is active, click Capture Best Buy Main Offer v2 in your
   bookmarks. This reads product text and downloads bestbuy_capture.json. It
   makes no network requests and reads no cookies, credentials or browser storage.
   It does not change the cart. Missing or ambiguous sections show CAPTURE HOLD.
4. Move the newly downloaded JSON into your project folder. If Chrome added
   (1) or another suffix, rename this fresh file to bestbuy_capture.json, replacing
   an older capture. Do not reuse a previous capture or the screenshots' old price.
5. Within 15 minutes of capture, run this for the reviewed iPhone 13 Good target:

```powershell
python bestbuy_preview.py --model "iPhone 13" --condition "Good" --start 0 --limit 1 --capture-file "bestbuy_capture.json"
```

The command confirms the captured identity and condition against the actual
catalogue variant; stale or mismatched captures remain on HOLD. No Playwright
browser opens and no website is contacted during import. PREVIEW_ONLY means
the captured offer passed the checks, not that its price was applied.

The CSV records MAIN_DISPLAYED_OFFER and USER_BROWSER_CAPTURE alongside seller,
URL, target condition, original capture time and variant ID. The scoped capture
is retained as evidence. If the main price is $379.88, the preview is $376.88;
this example is not a stored live price. Lower 'from' prices below Shop Outlet
Deals are excluded, and multiple prices inside the main offer still cause HOLD.

The tool has not yet been verified on a live regular-Chrome capture. If it shows
CAPTURE HOLD, paste that exact error; do not disable the checks.

## First: check catalogue conditions offline

Run in PowerShell from the project folder:

```powershell
python bestbuy_preview.py --model "iPhone 14" --list-conditions
```

This command does not open a browser, contact Shopify or change prices.
If no non-Excellent targets are found, the existing CSV is insufficient.
Provide a variant catalogue containing the real non-Excellent Shopify variant
IDs. Do not change an Excellent row's condition while keeping its variant ID.

An alternative CSV can be supplied with --catalogue. Required columns are
product_title, storage, color, condition, variant_id. product_title is the exact
catalogue model; variant_id is the condition-specific gid://shopify/ProductVariant/ID.
No condition or variant IDs are invented or copied from another grade.

## Then: inspect selection offline

If Good targets exist:

```powershell
python bestbuy_preview.py --model "iPhone 14" --condition "Good" --limit 1 --list-targets
```

For Very Good, use --condition "Very Good". Excellent is rejected. Each selected
identity prints its model, storage, colour, condition and variant ID.

## Browser pilot

Best Buy previously denied access in the agent browser. This correction fixes
condition selection; it does not fix or bypass that access restriction.
Access Denied is recorded as HOLD, never as a verified offer or no-match.
Do not repeatedly retry the browser pilot while that restriction remains.

When access is available, omit --list-targets to run a single preview:

```powershell
python bestbuy_preview.py --model "iPhone 14" --condition "Good" --limit 1
```

The search uses model, storage and colour plus refurbished, without forcing a
condition keyword. Select the product and paste its canonical Best Buy URL into
PowerShell. Enter n for no matching product or q to stop at that prompt. Open
Specifications and About This Product; wait for price and seller to appear,
then press Enter in PowerShell. The listing title and specifications must both
explicitly confirm the target's exact refurbished grade. Missing, conflicting,
unsupported or mismatched grades remain on HOLD.

The primary product must also match model, storage, colour and unlocked status,
have one unambiguous item price, a visible seller and an enabled visible primary
Add to Cart button. The tool never clicks Add to Cart. Standard iPhone 14 needs
explicit Nano-SIM + eSIM evidence; Nano SIM alone and eSIM-only do not qualify.

Each run saves bestbuy_report.csv and scoped text evidence to a new folder
under bestbuy_runs. The terminal prints the exact report path. PREVIEW_ONLY
shows a Best Buy item price and proposed price $3 CAD below it, never an applied
Shopify update. Saved results are historical and cannot be applied by this tool.

There is no Shopify access, --apply flag, colour fallback or comparison with
Amazon's prices in this pilot. It does not modify Amazon CSVs, reports, approvals
or credentials. Live browser collection remains unverified because of the site
access restriction.

## Tests

```powershell
python -m unittest discover -s tests -p test_bestbuy.py -v
```
