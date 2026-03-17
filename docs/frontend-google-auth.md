# Frontend — Google OAuth Integration Guide

This document describes what the frontend needs to implement to support Google Sign-In
via Firebase and the Prodculator backend JWT system.

---

## 1. Install Firebase SDK

```bash
npm install firebase
```

---

## 2. Initialise Firebase

Create a `src/lib/firebase.ts` (or equivalent) config file:

```ts
import { initializeApp } from "firebase/app";
import { getAuth, GoogleAuthProvider } from "firebase/auth";

const firebaseConfig = {
  apiKey: "AIzaSyBAKJzCFVB_l61IyPLBkQkfNLEyRyqN3oY",
  authDomain: "prodculator-aeca5.firebaseapp.com",
  projectId: "prodculator-aeca5",
  storageBucket: "prodculator-aeca5.firebasestorage.app",
  messagingSenderId: "236165092682",
  appId: "1:236165092682:web:51528c758adedd03da9db3",
};

export const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);
export const googleProvider = new GoogleAuthProvider();
```

> ⚠️ These values are safe to commit — they are public Firebase client credentials,
> not secret keys. Access is controlled by Firebase's authorised domain list.

---

## 3. Google Sign-In Function

```ts
import { signInWithPopup } from "firebase/auth";
import { auth, googleProvider } from "@/lib/firebase";

export async function signInWithGoogle(): Promise<void> {
  // Step 1: Firebase handles the Google OAuth popup
  const result = await signInWithPopup(auth, googleProvider);

  // Step 2: Get the short-lived Firebase ID token
  const idToken = await result.user.getIdToken();

  // Step 3: Exchange the Firebase ID token for your own backend JWT
  const response = await fetch("/api/auth/google", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id_token: idToken }),
  });

  if (!response.ok) {
    throw new Error("Google sign-in failed");
  }

  const { access_token, refresh_token } = await response.json();

  // Step 4: Store your backend JWT — use this for all subsequent API calls
  localStorage.setItem("access_token", access_token);
  localStorage.setItem("refresh_token", refresh_token);
}
```

---

## 4. Making Authenticated API Requests

After sign-in, use the backend JWT (not a Firebase token) for every request:

```ts
async function apiFetch(path: string, options: RequestInit = {}) {
  const token = localStorage.getItem("access_token");
  return fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(options.headers ?? {}),
    },
  });
}

// Example usage
const reports = await apiFetch("/api/reports").then((r) => r.json());
```

---

## 5. Creating a Report (multipart/form-data)

The `POST /api/reports` endpoint now accepts **multipart/form-data** — the script file
and JSON metadata are sent together:

```ts
async function createReport(
  scriptFile: File,
  metadata: object,
): Promise<{ report_id: string }> {
  const token = localStorage.getItem("access_token");

  const form = new FormData();
  form.append("script_file", scriptFile); // the PDF/txt/fountain/fdx file
  form.append("body", JSON.stringify(metadata)); // report metadata as JSON string

  const response = await fetch("/api/reports", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` }, // do NOT set Content-Type manually
    body: form,
  });

  return response.json();
}

// Example metadata shape
const metadata = {
  script_title: "My Film",
  report_type: "paid", // "paid" | "b2b" | "preview"
  genre: ["Drama"],
  budget_amount: 3000000,
  budget_currency: "GBP",
  format: "Feature Film",
  country: "UK",
  location_strategy: "domestic",
  production_priority: "full",
};
```

> ⚠️ Do **not** set `Content-Type: multipart/form-data` manually — the browser sets it
> automatically (including the required `boundary` value) when you pass a `FormData` object.

---

## 6. Accessing the PDF

The `pdfUrl` field returned in report responses is a **presigned S3 URL** — it is valid
for 15 minutes and generated fresh on every API call. The frontend should:

- **Not cache** the `pdfUrl` value long-term
- Fetch the report again when the user wants to download the PDF (to get a fresh URL)
- Or open the URL in a new tab immediately after receiving it

```ts
async function downloadPdf(reportId: string) {
  // Fetch a fresh report to get a non-expired presigned URL
  const report = await apiFetch(`/api/reports/${reportId}`).then((r) =>
    r.json(),
  );
  if (report.pdfUrl) {
    window.open(report.pdfUrl, "_blank");
  }
}
```

---

## Full Authentication Flow

```
Frontend                          Backend
────────                          ───────
signInWithPopup(googleProvider)
    │
    ▼
Firebase handles OAuth with Google
    │
    ▼
Gets Firebase ID token
    │
POST /api/auth/google ──────────► Verifies ID token with Firebase Admin SDK
  { id_token: "..." }              Extracts email + google_uid
                                   Creates or fetches user in DB
                      ◄─────────── Returns { access_token, refresh_token }

Stores access_token
Uses it on every API request
as: Authorization: Bearer <token>
```

Firebase is only used **once** — to obtain the initial ID token.
All subsequent requests use the backend's own JWT.

---

## Endpoints Reference

| Method | Path                       | Auth          | Description                                 |
| ------ | -------------------------- | ------------- | ------------------------------------------- |
| `POST` | `/api/auth/google`         | None          | Exchange Firebase ID token for backend JWT  |
| `POST` | `/api/auth/login`          | None          | Email/password sign-in                      |
| `POST` | `/api/auth/register`       | None          | Email/password registration                 |
| `POST` | `/api/auth/refresh`        | Refresh token | Refresh access token                        |
| `POST` | `/api/auth/logout`         | Bearer        | Revoke tokens                               |
| `POST` | `/api/reports`             | Bearer        | Create report (multipart/form-data)         |
| `GET`  | `/api/reports`             | Bearer        | List user's reports                         |
| `GET`  | `/api/reports/{id}`        | Bearer        | Get single report (includes fresh `pdfUrl`) |
| `GET`  | `/api/reports/{id}/status` | Bearer        | Poll processing status                      |
| `GET`  | `/api/reports/{id}/pdf`    | Bearer        | Stream PDF bytes directly                   |
| `POST` | `/api/scripts/validate`    | None          | Validate script file before upload          |
| `POST` | `/api/scripts/analyze`     | Bearer        | Analyze script (returns analysis only)      |
