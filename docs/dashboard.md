# DocDrift Dashboard

The React dashboard is a first UI slice for authenticated maintainers. It shows the signed-in GitHub account, repositories authorized for both the DocDrift installation and that user, persisted pull-request runs, finding evidence, published suggestion status, explicit feedback, and feedback acceptance metrics. It does not display a fix diff because the backend currently stores suggestion identity and publication metadata, not proposed replacement text. Settings are a placeholder; repository noise controls remain in `.docdrift.yml`.

## Prerequisites and install

Use Node.js 20.19+ or 22.12+ (Node.js 24 is supported) and npm. From `apps/dashboard`:

```powershell
npm install
Copy-Item .env.example .env.local
npm run typegen
npm run dev
```

The API must be running at `http://localhost:8000` for OpenAPI generation and dashboard API requests. Set `VITE_API_BASE_URL` in `.env.local` to another API origin if needed. Configure `DOCDRIFT_DASHBOARD_ORIGIN` in the API environment to the exact frontend origin; the API allows credentialed CORS only for that origin and redirects successful OAuth callbacks there. The UI sends cookies with requests and reads only the non-HttpOnly `docdrift_csrf` cookie to add `X-CSRF-Token` for mutations. OAuth tokens and session credentials are not stored in browser storage.

## Run the API and sign in

Follow the API setup in the repository README. Configure the OAuth application's `DOCDRIFT_GITHUB_OAUTH_CLIENT_ID`, `DOCDRIFT_GITHUB_OAUTH_CLIENT_SECRET`, `DOCDRIFT_SESSION_ENCRYPTION_KEY`, and `DOCDRIFT_GITHUB_OAUTH_REDIRECT_URI`, plus the separate GitHub App's `DOCDRIFT_GITHUB_APP_ID` and `DOCDRIFT_GITHUB_APP_PRIVATE_KEY`. The OAuth session token is used to list repositories visible to the signed-in user. A server-generated GitHub App JWT lists current App installations and creates installation tokens to check their repository inventories; credentials stay server-side. Register the exact callback URL, normally `http://localhost:8000/auth/github/callback` for local HTTP, and set `DOCDRIFT_DASHBOARD_COOKIE_SECURE=false` only for local HTTP development. The API intersects both live GitHub views with persisted repository associations before listing repositories. Local UI tests do not perform OAuth or contact GitHub.

Start the API from the repository root in its configured environment, including `DOCDRIFT_DASHBOARD_ORIGIN=http://localhost:5173`:

```powershell
.\.venv\Scripts\uvicorn.exe docdrift_api.main:app --reload
```

The unauthenticated dashboard displays a GitHub sign-in action that redirects to `GET /auth/github/login`. The API validates OAuth state, stores the encrypted access token server-side, sets an HttpOnly session cookie and a CSRF cookie, then redirects back to `/` on the dashboard origin. For local development this assumes the dashboard and API share localhost cookies; configure the OAuth redirect and cookie settings consistently.

## API contracts

Generated TypeScript types are derived from the live FastAPI OpenAPI document. With the API running, `npm run typegen` updates `src/api/schema.d.ts` using `openapi-typescript`. The frontend uses these generated route types through `openapi-fetch` and Zod-validates the responses at runtime.

The dashboard API includes `GET /auth/session`, `GET /repositories`, `POST /repositories/sync`, `GET /repositories/{repository_id}/runs`, and `GET /runs/{run_id}`. Repository reads require a valid OAuth session and confirm access against GitHub. Sync requires the session's `docdrift_csrf` cookie in `X-CSRF-Token`. It lists the user's visible repositories with the OAuth token, discovers current App installations with an App JWT, and retrieves each relevant inventory with short-lived App installation tokens. The browser cannot submit an installation ID. A successful sync invalidates the repository list; API failures stay visible as errors. This flow uses the current schema and adds no migration. Existing feedback endpoints remain available for writes.

## Development and checks

```powershell
npm run dev
npm run lint
npm run typecheck
npm test
npm run build
npm run e2e
```

Vitest/Testing Library tests use MSW fixtures based on the API response shapes; they validate client states and workflows, not live OAuth, live GitHub repository authorization, PostgreSQL persistence, or cross-origin cookie deployment. Playwright provides a browser-level unauthenticated smoke test. A live dashboard acceptance check requires configured OAuth credentials, a migrated dedicated database with an installed repository, and access to GitHub; no such credentials or services are bundled with this app.
