# Analytics

One documented event set, emitted once per logical action.

Switch it on at **Theme settings → Integrations → Emit analytics events**.
Turn on **Log events to the console** while testing, and off before launch.

## How events are delivered

Every event goes to two places at once, so a tag manager, a Shopify Web Pixel
and a bespoke listener can all consume the same stream:

```js
window.dataLayer.push({ event: 'greenside_add_to_cart', ... });

document.addEventListener('greenside:analytics', (e) => {
  e.detail.name;     // 'add_to_cart'
  e.detail.payload;  // { variant_id, quantity, ... }
});
```

Events are namespaced `greenside_*` in `dataLayer` so they never collide with
Shopify's own or an app's.

## Deduplication

Identical events fired within 800ms are suppressed. A double-counted
`add_to_cart` corrupts conversion reporting, and the UI can legitimately trigger
the same action twice (a click plus a form submit, for instance). The dedupe key
is the event name plus its serialised payload, so the same event with a genuinely
different payload always gets through.

Analytics is also wrapped so it can never throw: an unserialisable payload is
handled rather than propagated. Nothing in the reporting layer is allowed to
break a purchase.

## The event set

| Event | Fired when | Payload |
|---|---|---|
| `page_view` | Every page load | `page_type`, `template`, `title`, `path`, `logged_in` |
| `competition_view` | A competition page loads | `id`, `handle`, `title`, `vendor`, `type`, `price`, `currency`, `available`, `prize_value`, `collection` |
| `search` | A search is run | `search_term`, `results`, `source` (`predictive` or `modal`) |
| `filter` | A filter, sort or clear | `type`, `name`, `value`, `checked` |
| `entry_quantity_selected` | Entry amount changes | `quantity`, `method` (`preset`, `stepper`, `bundle`), `variant_id`, `product_id` |
| `add_to_cart` | An entry is added | `product_id`, `variant_id`, `product_title`, `quantity`, `price`, `currency` |
| `remove_from_cart` | A line is removed | `line_key`, `product_title`, `quantity` |
| `view_cart` | Cart page or drawer opens | `source`, `value`, `currency` |
| `begin_checkout` | Checkout is clicked | — |
| `competition_card_click` | A card is clicked | `product_id`, `position` |
| `hero_cta_click` | The hero action is clicked | — |
| `major_prize_click` | The headline competition is clicked | — |
| `category_click` | A category tile is clicked | — |
| `email_signup` | A signup renders its success state | `location` |
| `signup` | The registration form is submitted | — |
| `login` | The login form is submitted | — |
| `winner_view` | A winner video is opened | — |
| `referral_click` | A referral link is copied | — |
| `referral_share` | A share control is used | `channel` |

`purchase` is deliberately absent. It belongs to Shopify's checkout, which the
theme does not control; take it from Shopify's own analytics or a Web Pixel, and
you will avoid double-counting.

## Adding an event without touching JavaScript

Any element can emit one declaratively:

```html
<a href="/pages/winners"
   data-track="winner_view"
   data-track-payload='{"source":"footer"}'>All winners</a>
```

Forms use `data-track-submit="event_name"`.

## Connecting Google Analytics 4

Add GA4 through Shopify's own channel, then in Google Tag Manager create a
trigger on the `greenside_*` events you care about. Nothing needs to change in
the theme.

## What is never emitted

No personal data. No email addresses, names or addresses. Customer state appears
only as a `logged_in` boolean.
