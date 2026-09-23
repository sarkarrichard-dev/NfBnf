# Running QuantHawk on your own AWS server

This is the **personal** move: the same app you run on your PC today, on an
AWS EC2 server with a fixed (Elastic) IP, reachable over HTTPS with a
password. It is not yet the multi-customer version.

## What you need

- An EC2 instance (Ubuntu, `t3.medium` or bigger) with an **Elastic IP**
  attached. Whitelist that IP on your Dhan and Delta API settings. That also
  covers SEBI's static-IP rule.
- A domain or sub-domain (for example `algo.yourdomain.com`) pointing at that
  Elastic IP. HTTPS certificates are fetched automatically.
- Security group: allow ports **80 and 443** from anywhere, and **22 (SSH)
  from your own IP only**. Never open port 8000.
- Docker installed on the server (`sudo apt install docker.io docker-compose-v2`).

## One-time setup

1. Copy the code to the server (`git clone` the repo into `/opt/algo-bnf`).
2. Copy your **`.env`** from your PC to `/opt/algo-bnf/.env`, and make sure it
   has a strong `DASHBOARD_PASSWORD=` line (12+ characters). The server
   refuses to start in cloud mode without one, because this app can arm
   live orders.
3. Copy your **`memory/`** folder from your PC to `/opt/algo-bnf/memory/`.
   It holds your trade journals, recorded market data and trained models.
   Stop the app on your PC first so nothing is mid-write.
4. Create `/opt/algo-bnf/deploy/.env.cloud` containing one line:
   `DOMAIN=algo.yourdomain.com`
5. Start it:

   ```
   cd /opt/algo-bnf
   docker compose --env-file deploy/.env.cloud -f deploy/docker-compose.yml up -d --build
   ```

6. Open `https://algo.yourdomain.com`. Your browser will ask for the
   password. Any username works; the password is the one from step 2.

## Everyday

- **Update to the latest code:** `git pull`, then the same `docker compose ... up -d --build` command.
- **See the log:** `docker compose -f deploy/docker-compose.yml logs -f app`
- **Restart:** `docker compose -f deploy/docker-compose.yml restart app`

## Important

- **Only run it in one place at a time.** If the PC copy and the cloud copy
  both run, both will trade and both will write journals.
- Settings you change in the dashboard are saved to `/opt/algo-bnf/.env` on
  the server and survive restarts.
- Back up `memory/`. The existing S3 backup (`ENABLE_S3_BACKUP`) works in
  the container.
