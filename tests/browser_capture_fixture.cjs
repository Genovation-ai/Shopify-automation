// Offline DOM fixture for the local bookmark. All unexpected actions fail.
const fs = require('node:fs');
const vm = require('node:vm');
const [script, mode] = process.argv.slice(2);
const clicks = [], alerts = [];
let file = null;
function element(innerText, attributes = {}) {
  return {innerText, disabled: false, getClientRects: () => [{}],
    getAttribute: key => attributes[key] ?? null,
    closest: () => null,
    click: () => {throw new Error('Unexpected product/cart click');}};
}
const button = element('Add to Cart');
button.disabled = mode === 'disabled-cart';
const modalButton = element('Add to Cart');
modalButton.closest = () => ({});
const actions = element('5 available\nAdd to Cart\nCompare more offers from $367.96');
actions.querySelectorAll = selector => {
  if (selector !== 'button[data-automation="add-to-cart-button"]') throw new Error('Unexpected query');
  return mode === 'ambiguous-cart' ? [button, element('Add to Cart'), modalButton] : [button, modalButton];
};
const specs = element('Specifications', {'aria-expanded': mode === 'collapsed' ? 'false' : 'true'});
specs.parentElement = element('Product Condition\nRefurbished Good\nWireless Carrier\nUnlocked\nStorage\n128 GB');
const nodes = {
  '[data-automation="specifications-expandable-button"]': specs,
  '[data-automation="layout-product-offer-details"] [data-automation="delivery-tab"]': actions,
  '[data-automation="layout-product-offer-details"] [data-automation="delivery-tab"] [data-testid="shipping-container"]': element('Available online only\n5 available'),
  '[data-automation="layout-product-summary"] h1': element('Refurbished (Good) - Apple iPhone 13 128GB - Blue - Unlocked'),
  '[data-automation="layout-product-summary"] [data-automation="SKU_ID"]': element('Web Code: 15922548'),
  '[data-automation="layout-product-summary"] [data-testid="SoldByBestBuyOrMarketplaceSellerTestId"]': element('Sold and shipped by Example Seller | 4.0 seller rating'),
  '[data-automation="layout-product-offer-details"] [class*="pricingContainer_"]': element(mode === 'loading-price' ? '' : 'SAVE $220\n$379.88'),
  '[data-automation="layout-product-overview"]': element('Refurbished product in Good condition.')
};
global.location = {protocol: 'https:', hostname: mode === 'wrong-host' ? 'example.com' : 'www.bestbuy.ca',
  origin: 'https://www.bestbuy.ca', pathname: '/en-ca/product/refurbished-good-apple-iphone-13/15922548'};
global.getComputedStyle = () => ({visibility: 'visible', display: 'block'});
global.document = {
  querySelectorAll: selector => {
    if (!(selector in nodes)) throw new Error('Unscoped or unknown query: ' + selector);
    return mode === 'ambiguous' && selector.includes('SKU_ID') ? [nodes[selector], nodes[selector]] : [nodes[selector]];
  },
  createElement: tag => {
    if (tag !== 'a') throw new Error('Unexpected element');
    return {click: () => clicks.push('download'), remove: () => {}};
  },
  body: {appendChild: () => {}}
};
global.Blob = class {constructor(parts) {file = parts.join('');}};
global.URL = {createObjectURL: () => 'blob:local', revokeObjectURL: () => {}};
global.alert = message => alerts.push(message);
global.setTimeout = fn => fn();
vm.runInThisContext(fs.readFileSync(script, 'utf8'));
process.stdout.write(JSON.stringify({file, clicks, alerts}));
