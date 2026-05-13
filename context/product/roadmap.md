# Product Roadmap: Where Tickets?

_This roadmap outlines our strategic direction based on customer needs and business goals. It focuses on the "what" and "why," not the technical "how."_

---

### Phase 1

_Prove the core promise: a user can upload PDFs and see an accurate, structured trip. Tackle the riskiest unknown (the engine) first; account essentials come last because nothing else depends on them._

- [ ] **Route Engine (Foundation)**
  - [ ] **Mock-Document Corpus:** Curate a representative set of real-world ticket/booking PDFs and expected route outputs to drive engine quality and regression testing.
  - [ ] **Engine Spike & Decision:** Prototype both an LLM-driven and an algorithmic route-building approach against the corpus, then commit to one for v1.
  - [ ] **Trip Route View:** Display the parsed route as an ordered sequence of cities with the documents attached to each leg; preserves prior user edits when new documents arrive.

- [ ] **Document Ingest**
  - [ ] **PDF Upload:** Users can upload travel PDFs (air, rail, bus tickets; hotel and Airbnb bookings; supplementary documents like vouchers) into a trip.
  - [ ] **AI Document Understanding:** The app automatically detects what each PDF is (ticket vs. accommodation vs. supplementary) and extracts the structured data — cities, dates, times, travelers, prices, QR codes.

- [ ] **Account Essentials**
  - [ ] **Sign-Up & Login:** Email/password account creation and authentication so each traveler has a secure, personal space for their trips.

---

### Phase 2

_Close the gaps. Make the trip feel complete and useful on the day of travel._

- [ ] **Filling the Gaps**
  - [ ] **Custom Accommodations:** Let users manually add stays the AI can't extract (e.g., "parent's guesthouse") so every night of the trip is accounted for.
  - [ ] **Custom Transportation:** Let users manually add transit segments (e.g., "rented car drive") to bridge cities without a ticket.
  - [ ] **Completeness Checks:** Surface which legs are missing transit or accommodation so the user knows what still needs attention.

- [ ] **On-the-Day Experience**
  - [ ] **Context-Aware Ticket Surfacing:** Using current time and geolocation, the app opens the ticket the user actually needs next (boarding pass at the airport, check-in confirmation at the hotel).
  - [ ] **Offline Access:** All documents and route data are usable without connectivity, so a flaky airport Wi-Fi never blocks a boarding pass.

---

### Phase 3

_Turn it into a shared, shipped product._

- [ ] **Travelspace Sharing**
  - [ ] **Invite Travel-Mates:** Let users invite friends or family to a trip so they see the shared itinerary and their own tickets on their own phone.
  - [ ] **Invite Non-Users:** Invitees who don't have the app yet get a flow that installs the app and joins them straight to the trip — the primary growth loop.

- [ ] **Launch Readiness**
  - [ ] **Edge-Case Polish:** Address rough edges discovered during real usage — unusual ticket layouts, multi-traveler accommodations, looping routes, conflicting documents.
  - [ ] **App Store & Play Store Publication:** Ship iOS and Android builds through the official stores.
