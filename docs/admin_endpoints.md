# Admin API Endpoints

This document outlines all admin endpoints, their payloads, and response structures for frontend integration.

---

## 1. Users

### List Users

- **Endpoint:** `GET /api/admin/users`
- **Query Params:**
  - `limit` (int, default 50)
  - `offset` (int, default 0)
- **Response:**
  ```json
  {
    "items": [ { ...user fields... } ],
    "total": int,
    "limit": int,
    "offset": int
  }
  ```

---

## 2. Reports

### List Reports

- **Endpoint:** `GET /api/admin/reports`
- **Query Params:**
  - `limit` (int, default 50)
  - `offset` (int, default 0)
- **Response:**
  ```json
  {
    "items": [ { ...report fields... } ],
    "total": int,
    "limit": int,
    "offset": int
  }
  ```

---

## 3. Metrics

### Get Metrics

- **Endpoint:** `GET /api/admin/metrics`
- **Response:**
  ```json
  {
    "total_users": int,
    "active_subscriptions": int,
    "total_reports": int,
    "reports_this_month": int,
    "mrr_usd": float,
    "conversion_rate_percent": float
  }
  ```

---

## 4. Production Signals

### Get Production Signals

- **Endpoint:** `GET /api/admin/production-signals`
- **Query Params:**
  - `territory` (string, optional)
  - `start_date` (string, optional)
  - `end_date` (string, optional)
- **Response:**
  ```json
  {
    "items": [ { ...signal fields... } ],
    "total": int
  }
  ```

---

## 5. Incentives

### List Incentives

- **Endpoint:** `GET /api/admin/incentives`
- **Query Params:**
  - `limit` (int, default 50)
  - `offset` (int, default 0)
- **Response:**
  ```json
  {
    "items": [ { ...incentive fields... } ],
    "total": int,
    "limit": int,
    "offset": int
  }
  ```

### Create Incentive

- **Endpoint:** `POST /api/admin/incentives`
- **Payload:**
  ```json
  {
    "payload": { ...incentive fields... }
  }
  ```
- **Response:**
  ```json
  { ...created incentive fields... }
  ```

### Update Incentive

- **Endpoint:** `PATCH /api/admin/incentives/{item_id}`
- **Payload:**
  ```json
  {
    "payload": { ...incentive fields... }
  }
  ```
- **Response:**
  ```json
  { ...updated incentive fields... }
  ```

### Delete Incentive

- **Endpoint:** `DELETE /api/admin/incentives/{item_id}`
- **Response:**
  ```json
  {
    "message": "incentive item deleted"
  }
  ```

---

## 6. Crew Costs

### List Crew Costs

- **Endpoint:** `GET /api/admin/crew-costs`
- **Query Params:**
  - `limit` (int, default 50)
  - `offset` (int, default 0)
- **Response:**
  ```json
  {
    "items": [ { ...crew cost fields... } ],
    "total": int,
    "limit": int,
    "offset": int
  }
  ```

### Create Crew Cost

- **Endpoint:** `POST /api/admin/crew-costs`
- **Payload:**
  ```json
  {
    "payload": { ...crew cost fields... }
  }
  ```
- **Response:**
  ```json
  { ...created crew cost fields... }
  ```

### Update Crew Cost

- **Endpoint:** `PATCH /api/admin/crew-costs/{item_id}`
- **Payload:**
  ```json
  {
    "payload": { ...crew cost fields... }
  }
  ```
- **Response:**
  ```json
  { ...updated crew cost fields... }
  ```

### Delete Crew Cost

- **Endpoint:** `DELETE /api/admin/crew-costs/{item_id}`
- **Response:**
  ```json
  {
    "message": "crew cost item deleted"
  }
  ```

---

## 7. Comparables

### List Comparables

- **Endpoint:** `GET /api/admin/comparables`
- **Query Params:**
  - `limit` (int, default 50)
  - `offset` (int, default 0)
- **Response:**
  ```json
  {
    "items": [ { ...comparable fields... } ],
    "total": int,
    "limit": int,
    "offset": int
  }
  ```

### Create Comparable

- **Endpoint:** `POST /api/admin/comparables`
- **Payload:**
  ```json
  {
    "payload": { ...comparable fields... }
  }
  ```
- **Response:**
  ```json
  { ...created comparable fields... }
  ```

### Update Comparable

- **Endpoint:** `PATCH /api/admin/comparables/{item_id}`
- **Payload:**
  ```json
  {
    "payload": { ...comparable fields... }
  }
  ```
- **Response:**
  ```json
  { ...updated comparable fields... }
  ```

### Delete Comparable

- **Endpoint:** `DELETE /api/admin/comparables/{item_id}`
- **Response:**
  ```json
  {
    "message": "comparable item deleted"
  }
  ```

---

## 8. Grants

### List Grants

- **Endpoint:** `GET /api/admin/grants`
- **Query Params:**
  - `limit` (int, default 50)
  - `offset` (int, default 0)
- **Response:**
  ```json
  {
    "items": [ { ...grant fields... } ],
    "total": int,
    "limit": int,
    "offset": int
  }
  ```

### Create Grant

- **Endpoint:** `POST /api/admin/grants`
- **Payload:**
  ```json
  {
    "payload": { ...grant fields... }
  }
  ```
- **Response:**
  ```json
  { ...created grant fields... }
  ```

### Update Grant

- **Endpoint:** `PATCH /api/admin/grants/{item_id}`
- **Payload:**
  ```json
  {
    "payload": { ...grant fields... }
  }
  ```
- **Response:**
  ```json
  { ...updated grant fields... }
  ```

### Delete Grant

- **Endpoint:** `DELETE /api/admin/grants/{item_id}`
- **Response:**
  ```json
  {
    "message": "grant item deleted"
  }
  ```

---

## 9. Festivals

### List Festivals

- **Endpoint:** `GET /api/admin/festivals`
- **Query Params:**
  - `limit` (int, default 50)
  - `offset` (int, default 0)
- **Response:**
  ```json
  {
    "items": [ { ...festival fields... } ],
    "total": int,
    "limit": int,
    "offset": int
  }
  ```

### Create Festival

- **Endpoint:** `POST /api/admin/festivals`
- **Payload:**
  ```json
  {
    "payload": { ...festival fields... }
  }
  ```
- **Response:**
  ```json
  { ...created festival fields... }
  ```

### Update Festival

- **Endpoint:** `PATCH /api/admin/festivals/{item_id}`
- **Payload:**
  ```json
  {
    "payload": { ...festival fields... }
  }
  ```
- **Response:**
  ```json
  { ...updated festival fields... }
  ```

### Delete Festival

- **Endpoint:** `DELETE /api/admin/festivals/{item_id}`
- **Response:**
  ```json
  {
    "message": "festival item deleted"
  }
  ```

---

## Generic Resource Endpoints

- **Endpoint:** `/api/admin/{resource}`
- **Supported resources:** incentives, crew-costs, comparables, grants, festivals
- **Methods:** GET, POST, PATCH, DELETE
- **Payload/Response:** Same as above for each resource

---

**Note:** Replace `{ ...fields... }` with the actual fields for each resource as defined in your database schema.
