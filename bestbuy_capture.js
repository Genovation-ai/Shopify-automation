/* User-invoked local capture. Reads visible product text only. No requests,
   cookies, storage, credentials, cart actions or third-party transmission. */
(() => {
  try {
    if (location.protocol !== "https:" || !["bestbuy.ca", "www.bestbuy.ca"].includes(location.hostname)
        || !/^\/en-ca\/product\/[a-zA-Z0-9-]+\/\d{7,10}\/?$/.test(location.pathname)) {
      throw new Error("Open a Best Buy Canada product page first.");
    }
    const visible = element => !!element.getClientRects().length
      && getComputedStyle(element).visibility !== "hidden"
      && getComputedStyle(element).display !== "none";
    const unique = selector => {
      const nodes = Array.from(document.querySelectorAll(selector)).filter(visible);
      if (nodes.length !== 1) throw new Error("Missing or ambiguous product section: " + selector);
      return nodes[0];
    };
    const text = selector => unique(selector).innerText;
    const specs = unique('[data-automation="specifications-expandable-button"]');
    if (specs.getAttribute("aria-expanded") !== "true") {
      throw new Error("Expand Specifications on this product page, then click the bookmark again.");
    }
    const deliverySelector = '[data-automation="layout-product-offer-details"] [data-automation="delivery-tab"]';
    const actions = unique(deliverySelector);
    const buttons = Array.from(actions.querySelectorAll('button[data-automation="add-to-cart-button"]'))
      .filter(button => visible(button) && !button.closest('section.modal, [role="dialog"]'));
    if (buttons.length !== 1) throw new Error("Missing or ambiguous main delivery Add to Cart button.");
    const offerText = text('[data-automation="layout-product-offer-details"] [class*="pricingContainer_"]');
    if (!offerText.includes("$")) throw new Error("Main price has not loaded. Wait for it to appear, then capture again.");
    const checkedAt = new Date().toISOString();
    const snapshot = {
      url: location.origin + location.pathname,
      title: text('[data-automation="layout-product-summary"] h1'),
      sku_text: text('[data-automation="layout-product-summary"] [data-automation="SKU_ID"]'),
      seller_text: text('[data-automation="layout-product-summary"] [data-testid="SoldByBestBuyOrMarketplaceSellerTestId"]'),
      offer_text: offerText,
      availability_text: text(deliverySelector + ' [data-testid="shipping-container"]'),
      cart_enabled: buttons.some(button => visible(button) && !button.disabled
        && button.getAttribute("aria-disabled") !== "true"),
      evidence_text: text('[data-automation="layout-product-overview"]'),
      specs_text: specs.parentElement.innerText
    };
    const data = {schema_version: 1, collector_version: 2, source: "BESTBUY_VISIBLE_MAIN_OFFER", checked_at: checkedAt, snapshot};
    const blob = new Blob([JSON.stringify(data, null, 2)], {type: "application/json"});
    const href = URL.createObjectURL(blob);
    const download = document.createElement("a");
    download.href = href;
    download.download = "bestbuy_capture.json";
    document.body.appendChild(download);
    download.click();
    download.remove();
    setTimeout(() => URL.revokeObjectURL(href), 5000);
    alert("Downloaded bestbuy_capture.json. This is captured page text, not a verified price yet. "
      + "Run the Python preview with --capture-file within 15 minutes. No cart or Shopify changes.");
  } catch (error) {
    alert("CAPTURE HOLD: " + error.message + "\nNo offer file was saved. No store prices changed.");
  }
})();
