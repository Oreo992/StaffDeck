# Agent Team preview deployment

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
`configure_rc_model.py` keeps `claude-opus-4-8` as the default and idempotently adds
`claude-sonnet-4-6` as an enabled user-selectable RC route.

Docker builds default to the DaoCloud base-image proxy, npmmirror for npm packages, and the
Aliyun PyPI mirror. Override `NODE_IMAGE`, `PYTHON_IMAGE`, `NPM_REGISTRY`, or `PIP_INDEX_URL`
at build time when a mirror is unavailable; BuildKit caches npm and pip downloads between
source-only rebuilds.

## Deploy or update

```bash
cd /opt/staffdeck-preview
git fetch origin preview
git checkout preview
git merge --ff-only origin/preview
docker compose -f deploy/preview/compose.yaml build
docker compose -f deploy/preview/compose.yaml up -d
curl --fail http://127.0.0.1:18173/api/health
python3 deploy/preview/configure_rc_model.py
python3 deploy/preview/verify_server.py
```

After the first launch, rotate the seeded `admin` account password before exposing the
application publicly. Keep the replacement credential in a private server-side credential
file, not in Git.

## Import Agent Team capabilities

The migration imports Agent Team employee personas, canonical `SKILL.md` packages,
deterministic node-based SOPs, read-only SellerSprite/Sorftime MCP tools, resource bindings,
and the preview's default RC model binding. It does not create a GitHub pull request.

Configure secret references in the private environment, recreate the preview app so it reads
them, then run the importer in a one-off container:

```bash
cd /opt/staffdeck-preview
python3 deploy/preview/configure_agent_team_secrets.py
docker compose -f deploy/preview/compose.yaml up -d --force-recreate app
docker run --rm --network host --user 0:0 \
  -v /opt/cc-platform:/opt/cc-platform:ro \
  -v /data/staffdeck-preview/login-credentials.json:/run/secrets/login.json:ro \
  staffdeck-preview:${STAFFDECK_IMAGE_TAG:-local} \
  python -m app.agent_team_migration \
    --source-root /opt/cc-platform \
    --credential-file /run/secrets/login.json \
    --apply
```

Run without `--apply` first to get deterministic inventory counts and the manifest hash.
External script-backed capabilities remain draft until an Agent Team-native tool adapter exists;
the importer never copies source credentials into skill packages.
