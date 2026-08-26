# Animal Bite Project

Django application for managing animal bite cases.

## Frontend (Tailwind CSS v4)

```bash
cd static
npm run build   # one-time build
npm run watch   # watch mode
```

## Vaccination Schedule — Patient Management

Sidebar → **Patient Management → Vaccination Schedule** (`/vaccination/schedules/patients/`)
- Search by patient name / PAT-xxx / case / contact
- Filter by barangay / dose status / due (Today / Overdue / Upcoming)
- See next due dose, progress, overdue badge, tap **Manage** to update doses
- Also from `Patients → Schedules` pill or `Patient Detail → Vaccination` column

## SMS Reminders via Semaphore (RHUDumingag)

Automatic SMS **3 days before** and **on the day** of each vaccine dose, plus manual per-dose send.

- **Sender Name:** `RHUDumingag` (approve it at https://semaphore.co/account → Sender Names)
- **API docs:** https://semaphore.co/docs → `POST https://api.semaphore.co/api/v4/messages` with `apikey, number, message, sendername`
- **Number format:** `09XXXXXXXXX` / `639XXXXXXXXX` auto-normalized to `639XXXXXXXXX`

### 1. Configure `.env`

Copy `.env.example` → `.env` and fill:

```
SEMAPHORE_API_KEY=your_key_from_semaphore.co
SEMAPHORE_SENDER_NAME=RHUDumingag
SEMAPHORE_ENABLED=False   # True to actually send (costs credits). False = dry-run / simulated
```

> When `SEMAPHORE_ENABLED=False`, messages are **not sent** but logged as `queued (simulated)` — perfect for testing without spending credits.

### 2. Test manually

```bash
# Dry-run (no credits, shows what would send)
python manage.py send_vaccination_reminders --dry-run --verbose

# Test for a past/future date (e.g., doses scheduled on 2026-05-06)
python manage.py send_vaccination_reminders --date 2026-05-06 --dry-run --verbose

# Actually send (needs valid SEMAPHORE_API_KEY + SEMAPHORE_ENABLED=True)
python manage.py send_vaccination_reminders --verbose

# Force re-send even if already sent today
python manage.py send_vaccination_reminders --force --verbose

# Only 3-days or only on-day
python manage.py send_vaccination_reminders --type 3days --verbose
python manage.py send_vaccination_reminders --type on_day --verbose
```

Web triggers (staff only):
- `Vaccination → SMS Reminders` (`/vaccination/sms/logs/`) → **Trigger Now** / **Trigger 3-days only** / **Dry Run**
- `Vaccination Schedule` detail per dose → **SMS 3-days** / **SMS Today** buttons

### 3. Automate daily (Asia/Manila 08:00)

**Windows Task Scheduler (PowerShell as Admin):**

```powershell
schtasks /create /tn "ABTC_SMS_Reminders" /tr "powershell.exe -c 'cd C:\Users\gerla\2026\animalbite\animalbite_project; C:\Users\gerla\2026\animalbite\animalbite_project\.venv\Scripts\python.exe manage.py send_vaccination_reminders'" /sc daily /st 08:00
schtasks /run /tn "ABTC_SMS_Reminders"   # test run
schtasks /query /tn "ABTC_SMS_Reminders" /v
```

**Linux cron:**

```cron
0 8 * * * cd /path/to/animalbite_project && /path/to/venv/bin/python manage.py send_vaccination_reminders >> /var/log/abtc_sms.log 2>&1
```

### 4. Monitoring

- **SMS Logs:** `/vaccination/sms/logs/` — filter by patient / case / type / status, see `sent_at`, `recipient`, `message`, `api_response`
- **Per-dose history:** `Vaccination Schedule → Manage` — shows past SMS per dose (sent/queued/failed) + manual send
- **Audit:** `AuditLog` entries `SEND_SMS`, `SEND_SMS_DRYRUN`, `TRIGGER_SMS`
- **Admin:** Django admin → `Vaccination → SMS Logs`

### 5. Message templates (Filipino)

```
3 days before: "ABTC Dumingag: Paalala, {Name} ({Case}) may vaccine schedule sa {Date} ({Dose}). Mangyaring pumunta sa RHU 3 araw mula ngayon. Dalhin ang card. Salamat! -RHUDumingag"
On day:       "ABTC Dumingag: Ngayon na ang vaccine schedule ni {Name} ({Case}) - {Dose} ngayong {Date}. Pumunta sa RHU ngayong araw. Dalhin ang vaccination card. -RHUDumingag"
```

Edit in `vaccination/sms.py:build_reminder_message`.

### 6. Troubleshooting

- `SEMAPHORE_API_KEY not configured` → set key + `SEMAPHORE_ENABLED=True`
- `Invalid PH number` → patient `contact_number` must be 11-digit `09...` or `639...`. Check `Patients → Edit`.
- `Already sent today` → duplicate guard prevents spam; use `--force` or `Force` in web to resend
- `Failed` / `Refunded` → check Semaphore dashboard credit balance & sender name approval (must be `RHUDumingag` and status Active)
- Do not start message with `TEST` — Semaphore silently ignores it (code prefixes a space if needed)