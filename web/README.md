# Founder workspace — React + Vite + Tailwind

The interactive surface the founder uses daily. Sits on top of the FastAPI
service in `services/web_api/`, which proxies to BigQuery, MongoDB, and the
deployed A2A agents.

## Layout

```
web/
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts                # /api proxy → services/web_api
├── tailwind.config.js             # design tokens (slate + indigo accent)
├── postcss.config.js
└── src/
    ├── main.tsx                   # QueryClientProvider + Router
    ├── App.tsx                    # routes
    ├── index.css                  # Tailwind + design tokens
    ├── lib/
    │   ├── api.ts                 # TanStack Query hooks
    │   ├── types.ts               # shared TS types (mirrors Python Pydantic)
    │   └── utils.ts
    ├── components/
    │   ├── Layout.tsx             # sidebar + main shell
    │   ├── PageHeader.tsx
    │   ├── RubricScores.tsx
    │   └── ui/                    # Button, Card, Badge, Input, Skeleton, Empty
    └── routes/
        ├── Queue.tsx              # daily-driver: approve/edit/reject
        ├── WeeklyReview.tsx       # Monday 15-min ritual
        ├── Drafting.tsx           # run the Research→Content→Review pipeline
        ├── Experiments.tsx        # running/decided/drift
        ├── Skills.tsx             # playbook library + promotion gate
        ├── Voice.tsx              # customer voice corpus browser
        └── Telemetry.tsx          # rubric trend + Model Armor + negatives
```

## Routes

| Route             | Purpose | Backing endpoints |
|---|---|---|
| `/queue`          | Approve, edit, reject pending drafts. The daily-driver. | `GET /api/queue`, `POST /api/decisions` |
| `/weekly-review`  | Monday-morning ritual: memo + critique + promotion + drift in one view. | `GET /api/weekly-review` |
| `/draft`          | Kick off the Research→Content→Review pipeline via A2A; see live trace + scores. | `POST /api/draft` |
| `/experiments`    | Running + decided + drift investigations. | `GET /api/experiments/{running,decided,drift}` |
| `/skills`         | Playbook library; track records; approve/reject promotions + critiques. | `GET /api/skills`, `POST /api/skills/:id/promotion`, `POST /api/self-critique/:id` |
| `/voice`          | Search the customer voice corpus by ICP + theme. | `GET /api/voice` |
| `/telemetry`      | Rubric trend chart + Model Armor activity + negative-example library size. | `GET /api/rubric-trend`, `GET /api/this-week-summary`, `GET /api/negatives` |

## Dev

```bash
cd web
npm install
VITE_API_URL=http://localhost:8080 npm run dev   # against local web_api
# or against deployed web_api:
VITE_API_URL=https://web-api-XXXX-uc.a.run.app npm run dev
```

The Vite dev server proxies `/api/*` to `VITE_API_URL`. If you point it at a
Cloud Run deployment, make sure CORS is open or you're hitting it
authenticated via IAP.

## Build for production

```bash
npm run build
# Output: web/dist — static, deploy anywhere.
```

For Cloud Run hosting alongside `services/web_api`, see `deploy.sh`.

## Design choices

- **Inter** for sans, **JetBrains Mono** for code/IDs.
- Indigo primary (`hsl(239 84% 67%)`), green for approve, red for reject,
  amber for edit/warning. Generous whitespace; subtle borders over heavy shadows.
- shadcn-style components built directly (no external dependency) — Card,
  Button, Badge, Input, Textarea, Select, Skeleton, Empty.
- TanStack Query for all data; mutations invalidate related queries so the
  Queue refreshes when a decision is submitted and the Weekly Review
  refreshes when a promotion is approved.
- Recharts for the rubric trend (lightweight, React-native).
- Lucide icons; consistent stroke weight.
- Dark mode CSS variables defined; toggle wiring is a one-line addition (the
  `.dark` class on `<html>`).

## What the UI proves

- The approval queue closes the loop: every decision flows through the
  edit_capture_handler, so the same learning signal fires whether you
  decide in the Sheet or here.
- The weekly review surface compresses the founder's mandatory cognition
  into a single page — no tab juggling.
- The drafting workspace is how the founder triggers the multi-agent
  pipeline interactively. Trace + scores + research provenance all visible.
- The skills page is where the self-learning loop becomes visible: candidate
  vs incumbent with side-by-side rubric stats; one-click promotion.
