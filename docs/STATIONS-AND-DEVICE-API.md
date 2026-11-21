# Stations and device API

## Purpose

A station represents an operational endpoint such as a register, kitchen, bar, or custom workstation. It is not a separate tenant and does not require a separate employee login. A station can be associated with a physical location and has a server-enforced capability list.

## Supported station capabilities

| Capability | Meaning |
|---|---|
| `catalog.view` | Read active products for the station's business |
| `orders.view` | Read orders within the station's business/location scope |
| `orders.create` | Create orders; the endpoint also requires `catalog.view` |
| `orders.status.update` | Change order status; the endpoint also requires `orders.view` |

Default capability suggestions in `core/permissions.py`:

- `register`: catalog view, order view, order creation
- `kitchen`: order view, order status update
- `bar`: order view, order status update
- `custom`: no capabilities by default

These are capability defaults, not a full kitchen/bar workflow implementation.

## Pairing lifecycle

1. The business owner creates a station in Management.
2. The owner issues a pairing code. Previous active unconsumed codes for that station are expired.
3. Code validity is five minutes.
4. The device sends the code and a non-empty label to the pairing endpoint.
5. The code is consumed exactly once.
6. The response returns a bearer token. The database stores only its SHA-256 digest.
7. The device sends the bearer token on each station API request.
8. The owner can revoke the device from Management.

## HTTP endpoints

All station endpoints are rooted at `/api/stations/`.

### Pair device

`POST /api/stations/pair/`

Request JSON:

```json
{
  "code": "one-time-pairing-code",
  "label": "Kitchen tablet"
}
```

Success response (`201`):

```json
{
  "device_token": "returned-once",
  "station": {
    "id": "station-uuid",
    "name": "Kitchen",
    "kind": "kitchen",
    "capabilities": ["orders.view", "orders.status.update"]
  }
}
```

Pairing endpoint is CSRF-exempt because it is a device API; it is rate-limited by source IP. The response uses `Cache-Control: no-store`. Keep the code and returned token private.

### Authenticate paired device

For the remaining endpoints, send:

```http
Authorization: Bearer <device-token>
```

The token is bound to the station identified in the URL. A token for another station does not authorize access by changing the station UUID.

### Read catalog

`GET /api/stations/<station-uuid>/catalog/`

Requires `catalog.view`. Returns active products for the station's business.

### Read orders

`GET /api/stations/<station-uuid>/orders/`

Requires `orders.view`. Optional query parameter `status` filters by a valid order status. The response is limited to the first 100 orders ordered by creation time. If the station has a location, results are filtered to that location.

### Create order

`POST /api/stations/<station-uuid>/orders/create/`

Requires both `catalog.view` and `orders.create`.

Example body:

```json
{
  "idempotency_key": "unique-request-uuid",
  "order_type": "takeaway",
  "table_label": "",
  "note": "",
  "items": [
    {"product_id": "product-uuid", "quantity": 2}
  ]
}
```

The service resolves product records and prices on the server. The endpoint accepts between 1 and 100 items and requires an idempotency key.

### Update order status

`POST /api/stations/<station-uuid>/orders/<order-uuid>/status/`

Requires both `orders.view` and `orders.status.update`.

Example body:

```json
{"status": "preparing"}
```

The service validates the order's business/location scope and status transition.

## Response and error behavior

The device API returns JSON. Typical status codes:

- `201`: pairing or successful order creation
- `200`: successful read or status update
- `400`: malformed JSON, invalid input, expired/invalid pairing code, invalid status, or idempotency validation failure
- `401`: missing, invalid, revoked, or inactive device session
- `403`: missing station capability or forbidden operation
- `404`: order not found in the station's allowed scope
- `429`: too many pairing attempts

## Important limitations

- No token refresh/rotation endpoint is present.
- No device-specific WebSocket consumer is present.
- No printer assignment, print queue, KDS UI, or print-job retry system was found.
- The API does not implement employee identity attribution; station-originated actions are associated with the paired device.
