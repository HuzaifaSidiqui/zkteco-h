# ZKTeco uFace800 to Odoo Online Attendance Relay Service

A production-grade, highly observable biometric attendance relay service designed to connect up to 5 ZKTeco uFace800 terminals (running in ADMS / "Cloud Server" push mode) to an Odoo Online instance, synchronizing every attendance punch in real time via XML-RPC.

---

## Architecture Overview

```
+-----------------------------------------------------------------------------------+
|                        ZKTeco uFace800 Terminals (1 to 5)                         |
|      (ADMS Push Protocol / HTTP POST & GET / Server Address: https://$DOMAIN)     |
+-----------------------------------------------------------------------------------+
                                         │
                                         ▼ HTTPS (Port 443)
+-----------------------------------------------------------------------------------+
|                                Caddy Reverse Proxy                                |
|             (Auto Let's Encrypt TLS Certificate Provisioning via $DOMAIN)         |
+-----------------------------------------------------------------------------------+
                                         │
                                         ▼ HTTP :8000 (Internal Network)
+-----------------------------------------------------------------------------------+
|                     FastAPI Attendance Relay Service (Python 3.11+)               |
|                                                                                   |
|  [Device Router: /iclock/*]                                                       |
|   - Handshake (GET /iclock/cdata) -> Plain-text registration options              |
|   - Punch Push (POST /iclock/cdata?table=ATTLOG) -> Defensive Parser              |
|   - Polling / Ack (GET /iclock/getrequest, POST /iclock/devicecmd) -> OK         |
|   - SN Whitelist & Audit -> Reject unknown SN (200 OK + rejected=true in DB)      |
|   - Raw Audit Logging -> Rotating file + raw_device_requests table               |
|                                                                                   |
|  [Asynchronous Decoupled Ingestion]                                               |
|   - Idempotent insertion -> raw_attendance_events (UNIQUE: device_sn, pin, time) |
|   - Immediate plain-text "OK" response returned to terminal (<10ms)              |
|   - Event enqueued into sync_queue (status: pending)                              |
|                                                                                   |
|  [Background Scheduler (APScheduler)]                                             |
|   - Flush Retry Queue (Every 60s): Consumes sync_queue in chronological order    |
|   - Heartbeat Monitor (Every 15m): Alerts via webhook if last_seen_at > threshold |
|   - Employee Cache Refresh (Every 10m): Syncs {pin: employee_id} from Odoo        |
|                                                                                   |
|  [Admin & Observability: /admin]                                                  |
|   - JSON Health API: GET /admin/status (Bearer token protected)                   |
|   - Live Dashboard: GET /admin?token=... (Auto-refreshes every 30s)               |
|   - Manual Re-sync: POST /admin/refresh-employees                                 |
+-----------------------------------------------------------------------------------+
            │                                                      │
            ▼                                                      ▼
+-----------------------+                             +-------------------------+
| PostgreSQL Persistence|                             |       Odoo Online       |
|                       |                             |       (XML-RPC)         |
| - devices             |                             |                         |
| - raw_device_requests |                             | - hr.employee           |
| - parse_failures      |                             | - hr.attendance         |
| - raw_attendance_events                             |   (check_in/check_out)  |
| - sync_queue          |                             | - x_zk_device_sn        |
| - employee_mappings   |                             +-------------------------+
+-----------------------+
```

---

## Features

- **Strict ADMS Protocol Compliance**: Responds with exact plain-text payloads (`OK\n` or registration option blocks) and HTTP 200 required by ZKTeco firmware to prevent endless terminal retry loops.
- **Defensive ATTLOG Parser**: Attempts tab-delimited splitting first, falls back to whitespace-delimited splitting, and extracts dates with regex. Any malformed line is preserved in `parse_failures` table—nothing is ever silently dropped.
- **Hardware Idempotency**: Unique constraint on `(device_sn, pin, event_dt)` guarantees duplicate pushes caused by flaky cellular/Wi-Fi connections are harmless no-ops.
- **Zero-Timeout Device Responses**: Ingestion is decoupled from Odoo XML-RPC via an asynchronous queue (`sync_queue`). Slow or unreachable Odoo instances never cause device timeouts.
- **Ordered Odoo Sync Engine**: Consumes punches oldest-first. Enforces strict chronological order per employee (preventing out-of-order check-in / check-out), while parallelizing across distinct employees.
- **Smart Dynamic Field Detection**: Automatically probes `hr.attendance` with `fields_get` at startup. If `ODOO_DEVICE_FIELD` (e.g. `x_zk_device_sn`) exists, it is populated; otherwise, it is omitted gracefully without errors.
- **Exponential Backoff & Dead-Letter**: Failed XML-RPC calls back off at 30s, 1m, 2m, 5m, 15m (capped at 15m). After 50 failed attempts, items transition to `dead_letter` for operator inspection.
- **Unmatched PIN Visibility**: If an employee hasn't been assigned a barcode/PIN in Odoo, punches transition to `unmatched` and appear on the admin dashboard instead of blocking the retry pipeline.
- **Multi-Channel Alerting**: Built-in webhook poster compatible with Slack, Discord, Telegram-relays, and PagerDuty webhooks when terminals stop sending heartbeats beyond `HEARTBEAT_MINUTES`.
- **Live HTML Observability Dashboard**: Single-pane dashboard at `/admin?token=...` displaying online/offline badges, 24h sync counts, unmatched PINs, and manual cache sync triggers.

---

## Local Development Setup

### Prerequisites
- Python 3.11 or newer
- SQLite (default for local runs) or PostgreSQL

### 1. Clone & Set Up Virtual Environment
```bash
git clone <repo-url> zkteco-odoo-relay
cd zkteco-odoo-relay

python -m venv venv
# Linux / macOS:
source venv/bin/activate
# Windows PowerShell:
.\venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### 2. Configure Local Environment
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Edit `.env` for local testing:
```ini
DOMAIN=localhost
DATABASE_URL=sqlite:///./relay_local.db
ADMIN_TOKEN=my-local-secret-token
ODOO_URL=https://yourcompany.odoo.com
ODOO_DB=yourcompany-db
ODOO_USERNAME=user@example.com
ODOO_PASSWORD=your-api-key
ODOO_EMPLOYEE_PIN_FIELD=barcode
DEVICES='[{"sn": "UF800000001", "site_name": "Test Site", "label": "Dev uFace800"}]'
```

### 3. Run Alembic Migrations
```bash
alembic upgrade head
```

### 4. Start the Application
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Access the admin dashboard at: `http://127.0.0.1:8000/admin?token=my-local-secret-token`

### 5. Run Automated Tests
```bash
pytest tests -v
```

---

## Production Deployment on Fresh Ubuntu VPS

### Step 1: Install Docker and Docker Compose
On a clean Ubuntu 22.04 / 24.04 LTS VPS:
```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg lsb-release

# Add Docker GPG key & repository
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Enable Docker service
sudo systemctl enable --now docker
```

### Step 2: Open Firewall Ports
Ensure ports `80` and `443` are open to the world for Caddy and terminal communication:
```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw allow 22/tcp
sudo ufw enable
```

### Step 3: Clone and Configure
```bash
git clone <repo-url> /opt/zkteco-odoo-relay
cd /opt/zkteco-odoo-relay

cp .env.example .env
nano .env
```

Fill in your production variables:
- `DOMAIN`: Point your DNS A record (e.g. `relay.yourdomain.com`) to this VPS IP before starting. Caddy will automatically obtain a trusted Let's Encrypt SSL/TLS certificate.
- `POSTGRES_PASSWORD`: Strong generated password.
- `ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_PASSWORD`: Your Odoo Online instance credentials.
- `ADMIN_TOKEN`: A strong secret token for admin dashboard access.
- `ALERT_WEBHOOK_URL`: Slack, Discord, or generic webhook for downtime alerts.
- `DEVICES`: The exact Serial Numbers of your 5 uFace800 units.

### Step 4: Launch the Stack
```bash
sudo docker compose up -d
```

Check running containers:
```bash
sudo docker compose ps
sudo docker compose logs -f web
```
The startup sequence automatically runs `alembic upgrade head`, seeds the 5 devices into PostgreSQL, loads employee mappings, probes Odoo custom fields, and starts the scheduler.

---

## Configuring the ZKTeco uFace800 Terminal

To direct a physical ZKTeco uFace800 terminal to this relay:

1. On the terminal touch screen, press the **M/OK** button to enter the main menu (authenticate with supervisor credentials if prompted).
2. Navigate to **Comm.** (Communication Settings) &rarr; **Cloud Server Setting** (on some firmware versions this is labeled **ADMS** or **Web Server**).
3. Configure the following fields:
   - **Enable Domain Name**: `ON` (Checked)
   - **Server Address**: `relay.yourdomain.com` (Enter the exact domain configured in `DOMAIN`—do not prefix with `https://`)
   - **Server Port**: `443`
   - **Enable Proxy Server**: `OFF`
   - **HTTPS**: `ON` (If available in firmware; otherwise, port 443 directs traffic over TLS to Caddy)
4. Press **ESC / OK** to save.
5. Check the network icon on the terminal home screen:
   - Within 15–30 seconds, the cloud/globe icon with an upward arrow will turn solid green or uncrossed, confirming active ADMS connection.
6. Open your relay dashboard at `https://relay.yourdomain.com/admin?token=<ADMIN_TOKEN>` to verify the terminal status changes from `OFFLINE` to `ONLINE`.

---

## Odoo Online Configuration

### 1. User & Permissions
Create a dedicated integration user in Odoo (e.g., `attendance_relay@yourcompany.com`) with:
- Access Rights: **Attendance / Administrator**
- Type: **Internal User**
- Generate an **API Key** under the user's Preferences &rarr; Account Security (recommended over plain password).

### 2. Employee PIN Mapping
- By default, the relay matches terminal user PINs to Odoo's `barcode` field on `hr.employee`.
- In Odoo: Open **Employees**, select an employee, open the **HR Settings** tab, and enter their terminal PIN in the **Badge ID / Barcode** field.
- If you use a different field, set `ODOO_EMPLOYEE_PIN_FIELD` in `.env` (e.g. `pin` or `identification_id`).

### 3. Optional Device SN Tracking
- If you wish to track which physical terminal recorded each punch, create a custom Char field on `hr.attendance`:
  - Technical Name: `x_zk_device_sn`
  - Field Label: `ZKTeco Device SN`
- The relay probes this field once on startup via `fields_get`. If present, it writes the terminal serial number to every check-in/check-out record automatically.

---

## Testing & Verification

The repository includes an automated test suite verifying all required scenarios:

```bash
pytest tests -v
```

### Test Coverage Highlights
- `tests/test_attlog_parser.py`:
  - Tab-delimited ATTLOG parsing (`PIN \t DateTime \t Status \t VerifyMode`)
  - Whitespace-delimited ATTLOG parsing (`PIN DateTime Status ...`)
  - Preservation of leading zeros in PINs
  - Defensive fallback logging into `parse_failures` for malformed lines
- `tests/test_iclock_endpoints.py`:
  - Rejection of unknown serial numbers with HTTP 200 and `rejected=True` in `raw_device_requests`
  - Zero-data injection for unauthorized devices
  - Standard registration option handshake response for known SNs
  - Database-enforced idempotency against repeated device pushes
  - Logging and acknowledgement of `/iclock/getrequest` and `/iclock/devicecmd`
- `tests/test_odoo_sync.py`:
  - XML-RPC network failures caught and marked `status=failed` with exponential backoff
  - Unmatched PINs transitioned to `status=unmatched` without blocking retry queues
  - Proper sequence generation: Check-in created when none open; Check-out updated when open record exists
  - Maximum retries threshold (50 attempts) transitioning to `dead_letter`
- `tests/test_admin.py`:
  - Bearer token and `?token=...` authentication enforcement (HTTP 401 on unauthorized access)
  - JSON status reporting for devices and 24h queue metrics
  - On-demand employee PIN cache refresh endpoint

---

## Assumptions & Things to Verify Against Your Real Device

1. **ATTLOG Line Delimiters**:
   - *Assumption*: Different uFace800 firmware builds vary between `\t` (tab) and ` ` (space). The relay parser evaluates tab-delimited first, then whitespace-delimited, and falls back to regex date matching.
   - *Verification*: Check the `raw_device_requests` table or `logs/raw_inbound_requests.log` after the first punch to inspect the exact wire format emitted by your firmware.
2. **Terminal Timezone**:
   - *Assumption*: ZKTeco biometric terminals store naive local time on device RTC and transmit `YYYY-MM-DD HH:MM:SS` without timezone offset headers.
   - *Action*: Set `DEVICE_TIMEZONE` in `.env` (e.g. `UTC`, `America/New_York`, `Asia/Dubai`) to ensure punches are converted to UTC before sending to Odoo.
3. **Punches without In/Out Status Buttons**:
   - *Assumption*: Employees frequently scan their face/finger without manually selecting "Check In" or "Check Out" on the terminal screen.
   - *Resolution*: The relay automatically derives attendance state: if an open attendance record exists with `check_in < event_time`, it marks `check_out`; otherwise, it opens a new `check_in`.
4. **Registration Handshake Values**:
   - *Assumption*: Standard ADMS default parameters (`Delay=30`, `Realtime=1`, `TransInterval=1`) are appropriate for near real-time synchronization. Every parameter can be tuned via `ICLOCK_*` environment variables in `.env` if needed.
