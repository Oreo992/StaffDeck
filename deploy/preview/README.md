# StaffDeck preview deployment

This deployment publishes the `preview` branch at
`https://preview.agentteam.neospark.cn` without sharing application state with other
NeoSpark services.

## Server layout

- Checkout: `/opt/staffdeck-preview`
- Persistent data: `/data/staffdeck-preview/app`
- Private environment: `/data/staffdeck-preview/backend.env`
- Loopback upstream: `127.0.0.1:18173`
- Container: `staffdeck-preview`
- Nginx vhost: `/etc/nginx/conf.d/preview.agentteam.neospark.cn.conf`

The private environment file must be mode `0600` and must never be committed. It needs a
random `APP_SECRET` plus an OpenAI-compatible model URL, model name, and API key.

## Deploy or update

```bash
cd /opt/staffdeck-preview
git fetch origin preview
git checkout preview
git merge --ff-only origin/preview
docker compose -f deploy/preview/compose.yaml build
docker compose -f deploy/preview/compose.yaml up -d
curl --fail http://127.0.0.1:18173/api/health
```

After the first launch, rotate the seeded `admin` account password before exposing the
application publicly. Keep the replacement credential in a private server-side credential
file, not in Git.
