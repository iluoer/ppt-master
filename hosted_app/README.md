# Render + Supabase single-user deployment

This additive wrapper keeps the upstream PPT Master engine unchanged. The hosted
app lives under `hosted_app/`; the root `Dockerfile` starts this wrapper.

## Architecture

- Render runs the Docker web service and Gradio UI.
- Supabase Storage persists project snapshots and latest PPTX files.
- The Codex/text relay and image relay stay separate.

The Render filesystem is treated as ephemeral. The app uploads
`projects/<project_name>/project.zip`, `latest.pptx`, `manifest.json`, and a
small `projects/index.json` into Supabase Storage after generation, re-export,
manual SVG save, or recoverable agent failure.

## Supabase setup

Create one Supabase project, then either let the app create a private bucket or
create it manually:

```text
Bucket name: ppt-master
Public: off
```

Use the server-side service role key only in Render environment variables. Do
not expose it in browser JavaScript.

## Render setup

Create a Render Web Service:

- Runtime: Docker
- Dockerfile: `Dockerfile`
- Plan: Free is enough for a first single-user test
- Port: the app listens on `7860`

You can also use the root `render.yaml` blueprint and then fill the secret
values in Render.

## Required environment variables

Agent relay:

```env
CODEX_BASE_URL=https://your-codex-relay.example.com/v1
CODEX_API_KEY=your-codex-relay-key
CODEX_MODEL=your-codex-model
```

Image relay:

```env
IMAGE_OPENAI_BASE_URL=https://your-image-relay.example.com/v1
IMAGE_OPENAI_API_KEY=your-image-relay-key
IMAGE_OPENAI_MODEL=gpt-image-2
```

Supabase persistence:

```env
PERSISTENCE_BACKEND=supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
SUPABASE_BUCKET=ppt-master
```

`SUPABASE_SERVICE_ROLE_KEY` accepts either the legacy `service_role` JWT or a
new server-side `sb_secret_...` key. Do not use an anon/publishable key.

App login:

```env
APP_USERNAME=choose-a-login-name
APP_PASSWORD=choose-a-strong-password
```

The wrapper maps the image relay to the existing PPT Master `image_gen.py`
environment only when tool commands run:

```env
IMAGE_BACKEND=openai
OPENAI_BASE_URL=$IMAGE_OPENAI_BASE_URL
OPENAI_API_KEY=$IMAGE_OPENAI_API_KEY
OPENAI_MODEL=$IMAGE_OPENAI_MODEL
```

## Optional variables

```env
AGENT_MAX_STEPS=120
TOOL_COMMAND_TIMEOUT=900
TOOL_READ_MAX_CHARS=32000
PPT_MASTER_RUNS_DIR=/tmp/ppt-master-hosted
CODEX_CHAT_PATH=/chat/completions
CODEX_TIMEOUT=120
```

## Usage

1. Open the Render service URL.
2. Enter a topic or revision request in Generate / Chat.
3. Leave "Auto-confirm recommended design choices" enabled for one-shot hosted
   generation.
4. Download the latest PPTX from the file output.
5. Use "Restore saved project" after a Render restart to pull a project snapshot
   back from Supabase.
6. Use Preview / Edit to inspect slides, edit SVG source, save, and re-export.
7. Continue the chat to ask for page-level or global revisions.

## Notes

- This hosted version is for a private single-user deployment. It queues one
  generation at a time.
- Render gives the service a public URL. Set `APP_USERNAME` and `APP_PASSWORD`
  so strangers cannot spend your relay credits.
- Supabase Free storage is limited, so periodically delete old project folders
  from the `ppt-master` bucket.
- The Codex relay must expose an OpenAI-compatible chat-completions endpoint.
- The image relay must expose an OpenAI-compatible image generation endpoint
  for `image_gen.py` to use it.
