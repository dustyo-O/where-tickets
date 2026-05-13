# Product Definition: Where Tickets?

- **Version:** 1.0
- **Status:** Proposed

---

## 1. The Big Picture (The "Why")

### 1.1. Project Vision & Purpose

Give travelers a single, offline-available home for every ticket, booking, and reservation in a trip — and let AI build the trip's route automatically from the documents they upload, so they always know what's next, what's missing, and where to be.

### 1.2. Target Audience

People who travel often and juggle PDFs from many sources (airlines, rail, hotels, Airbnb, tour vendors). The product is designed for individual travelers first, but every trip is built to be shared with travel-mates and family so the same itinerary is on everyone's phone.

### 1.3. User Personas

- **Persona 1: "Nora the Frequent Solo Traveler"**
  - **Role:** Independent traveler who books 5–15 trips a year, often multi-leg, across air/rail/bus + hotels and apartments.
  - **Goal:** Have every ticket and booking accessible offline, organized into a clean route, with the right document surfaced automatically at the right time and place.
  - **Frustration:** Today she digs through email, screenshots, and PDF apps mid-transit. Existing wallet apps don't understand multi-leg trips, miss non-air bookings, and don't tell her when something is missing.

- **Persona 2: "Nora's travel-mates" (secondary)**
  - **Role:** Friends or family who join one leg or the whole trip.
  - **Goal:** See the shared itinerary on their own phone with their own tickets, without re-uploading anything.
  - **Frustration:** Group chats and forwarded PDFs lose track of who has what.

### 1.4. Success Metrics

- **Route accuracy** — ≥ 99% of uploaded documents are parsed into the correct city and placed at the correct position in the route with no manual correction.
- **Trip completeness** — for active trips, the app correctly flags every leg that is missing transit or accommodation for any traveler in the travelspace.
- **Travelspace sharing** — ≥ 10% of v1 trips are shared with at least one other person, driving organic installs through invites.

---

## 2. The Product Experience (The "What")

### 2.1. Core Features

- **PDF document ingest with AI extraction** — upload tickets, hotel/Airbnb bookings, and supplementary documents (vouchers, sightseeing tickets, etc.); AI detects type and extracts structured data, including QR codes.
- **Route engine** — sorted sequence of cities built and updated as documents arrive, preserving any user-added custom notes; tolerates gaps, repeats, and loops.
- **Custom legs** — user can manually add accommodations ("parent's guesthouse") and transportation ("rented car drive") to fill gaps the AI can't extract.
- **Completeness checks** — surface legs where one or more travelers are missing transit or accommodation.
- **Context-aware ticket surfacing** — based on current time and geolocation, the app opens the ticket the user actually needs next.
- **Travelspace sharing** — invite other users to a trip; they get their own tickets in their own app, including invite flow for non-users.
- **Offline access** — all documents and route data are usable without connectivity.

### 2.2. User Journey

A solo traveler installs the app, creates a trip, and uploads PDFs as bookings come in (air, rail, hotel, Airbnb). The engine assembles a route of cities and shows what each leg has and what's missing. She adds a manual "rental car" segment to bridge two cities, then invites her sister to the travelspace for the leg they overlap; her sister installs the app and sees her tickets. On travel day the app auto-opens her boarding pass at the airport; later, at the hotel, it surfaces the check-in confirmation and QR.

---

## 3. Project Boundaries

### 3.1. What's In-Scope for this Version

- Account creation and authentication.
- PDF upload pipeline (Lambda + SQS + S3 + Bedrock LLM) for tickets, hotel/Airbnb bookings, and additional documents.
- Document type detection and structured extraction, including QR code extraction.
- Route engine — **spike both approaches** (LLM-driven updates vs. algorithmic graph) against a curated mock-document corpus, then commit to one for v1.
- Custom accommodation and custom transportation entries.
- Trip completeness checks (per-traveler missing transit/accommodation).
- Context-aware "next ticket" surfacing using time + geolocation.
- Travelspace sharing with invites (existing users and new-user invite flow).
- Offline access to documents and route data.
- React Native mobile app (iOS + Android), backend on AWS with Python/FastAPI, Piccolo ORM, Bedrock for LLM calls.
- App Store and Play Store publication.

### 3.2. What's Out-of-Scope (Non-Goals)

- Email/inbox auto-ingestion of bookings (upload-only in v1).
- Booking, purchasing, or modifying tickets/hotels inside the app.
- Price tracking, fare alerts, or travel recommendations.
- AI-generated travel tips or itinerary suggestions beyond route assembly.
- Expense tracking, budgeting, or currency conversion features.
- Web/desktop client.
- Self-hosted models (e.g., BERT replacement for Bedrock) — Bedrock only in v1.
- Deep calendar/maps integrations beyond what's needed for the "next ticket" surfacing.
- Multi-tenant / B2B / travel-agency features.
