# System Architecture Overview: Where Tickets?

---

## 1. Application & Technology Stack

- **Mobile App:** React Native (iOS + Android), targeting the latest stable RN release. TypeScript throughout.
- **Backend API:** Python (latest stable) with FastAPI for the user-facing REST API and the SQS events consumer.
- **ORM:** Piccolo (async, Postgres-first).
- **Asynchronous Workers:** Python on AWS Lambda for the PDF/AI pipeline stages; each stage is a small, single-purpose function invoked via SQS.
- **PDF Processing Library:** PyMuPDF (text extraction + page image rendering) inside Lambda.
- **Mobile Offline Store:** WatermelonDB (SQLite-backed) with a custom sync protocol against the FastAPI backend.

---

## 2. Data & Persistence

- **Primary Database:** Amazon Aurora PostgreSQL Serverless v2. Stores users, travelspaces, trips, routes, documents (metadata only), tickets, hotels, custom legs.
- **Blob Storage:** Amazon S3 — original PDFs, rendered page images, raw extracted text, intermediate JSON artefacts produced by each pipeline stage. Lifecycle rules to transition old objects to cheaper tiers.
- **On-device Cache:** WatermelonDB on the mobile client mirrors the user's trips, route, and documents (including downloaded PDFs/images) so the app is fully usable offline.
- **Job/Pipeline State:** SQS queues are the system-of-record for in-flight pipeline events; durable artefacts land in S3 and (when validated) in Aurora.

---

## 3. Infrastructure & Deployment

- **Cloud Provider:** AWS, single account to start; separate environments (dev / staging / prod) via Terraform workspaces.
- **API Hosting:** Amazon ECS on Fargate. Runs the FastAPI service and the SQS events consumer as long-running tasks behind an Application Load Balancer.
- **Async Compute:** AWS Lambda for each pipeline stage (PDF intake, text+image extraction, Bedrock document-type detection, structured field extraction, QR-code extraction, route-update synthesis).
- **Messaging:** Amazon SQS between every pipeline stage; one queue per stage, with dead-letter queues for failed messages.
- **Container Registry:** Amazon ECR for FastAPI and Lambda container images.
- **Networking:** VPC with private subnets for ECS tasks and Aurora; public subnets only for the ALB and NAT gateways.
- **Secrets:** AWS Secrets Manager (DB credentials, Bedrock/third-party keys). No secrets in env vars baked into images.
- **Infrastructure-as-Code:** Terraform. Modules per concern (network, data, compute, messaging, observability).
- **CI/CD:** GitHub Actions — build container images, push to ECR, apply Terraform plan, run DB migrations on deploy.
- **Mobile Distribution:** App Store and Google Play; EAS (Expo Application Services) or Fastlane for build/distribution automation (decision deferred until mobile work starts).

---

## 4. External Services & APIs

- **Authentication:** Amazon Cognito (User Pools). Email/password sign-in for v1; JWT tokens validated by FastAPI via Cognito's JWKS. Invite flow for travelspace sharing leverages Cognito invites + signed deep-links.
- **LLM Provider:** Amazon Bedrock (Anthropic Claude family) for document-type detection, structured field extraction, QR detection assistance, and the LLM route-engine spike.
- **Push Notifications:** Amazon SNS → APNs / FCM (used later when on-the-day surfacing requires server-side nudges; not required for Phase 1).
- **Geolocation:** On-device only (React Native's geolocation APIs). No server-side geo provider in v1.

---

## 5. Observability & Monitoring

- **Logs:** Amazon CloudWatch Logs for FastAPI, Lambdas, and ECS tasks. Structured JSON logs.
- **Metrics & Alarms:** CloudWatch Metrics + CloudWatch Alarms for API latency, Lambda error rate, SQS queue depth and DLQ size, Aurora connection saturation.
- **Tracing:** AWS X-Ray on FastAPI and the Lambda pipeline stages, so a single uploaded PDF can be traced end-to-end through the SQS hops.
- **Error Tracking:** Sentry for both the React Native app (crashes, JS errors, performance) and the Python backend (uncaught exceptions, slow transactions).
- **Dashboards:** CloudWatch dashboards per environment for the engineer-on-call view; pipeline-health board covering SQS depth, Lambda errors, and Bedrock latency/cost.
