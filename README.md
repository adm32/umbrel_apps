# Vitals for umbrelOS

The system monitor umbrelOS doesn't have, with a liquid-glass design that matches umbrelOS.

- **Temperatures**: every sensor (CPU package and cores, NVMe, chipset, drives), with 24-hour history
- **Power**: live watts from the CPU's own energy counters (Intel RAPL), an estimate for the whole PC, and cost per day, month and year. You can connect a Shelly or Tasmota smart plug for exact wall power.
- **Storage**: every drive, including external USB drives, with S.M.A.R.T. health in plain English, temperature, wear, live read/write speeds, a built-in speed benchmark and space used by each app
- **CPU & memory**: per-core usage, clock speed and load average, with an explanation of what the numbers mean
- **Network**: live traffic, internet speed test with a bufferbloat grade, daily and monthly data usage, a latency monitor, and interface details
- **Activity monitor**: CPU and memory per Umbrel app and per process, searchable and sortable
- **Cleaner**: finds unused app images, oversized logs, old temp files, leftover data from uninstalled apps, and `.DS_Store`/`._` junk. It only deletes what you tick.
- **Network Lab** (for studying networking): LAN device scanner with nicknames and Wake-on-LAN, ping, traceroute, DNS lookup and comparison, port check, the services listening on your Umbrel, a subnet calculator with binary view, a subnetting practice quiz and a LAN speed test
- **Studio** (for music producers): sample-library scanner (formats, sample rates, hours of audio), duplicate-sample finder, DAW backup cleanup, recording-space calculator, tap tempo with a delay/reverb time table, and a note-to-frequency converter
- **Notifications** to your phone (ntfy, Discord, Telegram or any webhook) when a drive is failing, the CPU overheats, the internet drops, the Umbrel restarts or an unknown device joins your network
- **Activity timeline**: internet outages (with how long they lasted), restarts, drives plugged in or removed, alerts and cleanups, plus internet uptime for the last 24 hours and 7 days
- **Search** everything with ⌘K / Ctrl K, and **export** history, speed tests and events as CSV
- **8 home-screen widgets**, including one you can customise
- **Themes**: Light (white with orange accents), **Home** (looks like Apple's Home app), Dark and Auto. You can also use any umbrelOS wallpaper.

---

## Install (about 10 minutes, one time)

Umbrel installs apps from app stores on GitHub. This folder is your own small app store, and GitHub builds the app for you automatically.

### 1. Put this folder on GitHub
1. Create a free account at <https://github.com> if you don't have one.
2. Create a **new public repository** named **`umbrel_apps`**. Don't add a README.
3. In a terminal, inside this folder, run (replace `YOUR_NAME` with your GitHub username):

```bash
./setup.sh YOUR_NAME
```

```bash
git init -b main && git add -A && git commit -m "Vitals 1.0.0"
```

```bash
git remote add origin https://github.com/YOUR_NAME/umbrel_apps.git && git push -u origin main
```

### 2. Let GitHub build the app
- Open your repository → **Actions**. A run called **Build Vitals image** starts by itself and takes about 5–8 minutes. Wait for the green tick.
- **Important:** go to your GitHub profile → **Packages** → **umbrel-vitals** → **Package settings** → **Change visibility** → **Public**. If you skip this, your Umbrel can't download the app.

### 3. Add your app store to Umbrel
1. Open umbrelOS → **App Store**.
2. Click the **•••** button (top right) → **Community App Stores**.
3. Paste `https://github.com/YOUR_NAME/umbrel_apps` and click **Add**.
4. Open **Adam's Apps**, click **Vitals**, then **Install**.

### 4. Add widgets
On the umbrelOS home screen, right-click (or long-press) → **Edit widgets** → **Vitals**. You can show up to 3 widgets. To choose what the custom widget shows, open Vitals → **Widgets**.

---

## Updating
Change the code, increase `version` in `adam-vitals/umbrel-app.yml`, put the same number in `adam-vitals/docker-compose.yml` (in both the `image:` tag and `APP_VERSION`), then push. GitHub builds the new image and umbrelOS offers the update.

## Try it without the app store
On your Umbrel (over SSH), from this folder, run:

```bash
docker compose -f docker-compose.test.yml up -d --build
```

Then open `http://umbrel.local:7468`. This test mode has no Umbrel login in front of it, so stop it when you're done:

```bash
docker compose -f docker-compose.test.yml down
```

## Preview on a Mac (fake data)

```bash
cd app && pip3 install -r requirements.txt && VITALS_DEMO=1 VITALS_DATA=/tmp/vitals PORT=8471 python3 -m vitals
```

Then open <http://localhost:8471>.

---

## Tests
Every push runs two kinds of tests on GitHub (see the **Actions** tab):

1. **Unit tests** (`app/tests/`, 48 of them): kernel-file and S.M.A.R.T. parsing, outage detection, notifications, device registry, widgets, the API, and the safety rules that stop the cleaner and Studio from ever deleting the wrong file.
2. **Real-container test** (`.github/smoke_test.py`): builds the Docker image, runs it on a Linux machine with the same permissions it gets on umbrelOS, and checks every feature (sensors, drives, host network, processes, network tools, cleaner, benchmark, widgets).

A new image is only published when both pass. To run the unit tests yourself:

```bash
cd app && python3 -m unittest discover -s tests -t .
```

## Opening Vitals on your own domain
If you publish Vitals on a domain (for example `vitals.example.com` through the Cloudflare Tunnel app), it asks for a password there. On your home network and over Tailscale it opens without one.

1. At home, open Vitals → **Settings → Security** → **Set password**. Until you do, Vitals refuses every visit through the domain.
2. Optional but recommended: turn on **Two-factor codes** and scan the QR code with your authenticator app.
3. Point the tunnel at `http://umbrel.local:7468` (or your Umbrel's IP and port 7468).

Setting the password from outside your home network needs the one-time setup code. Find it in umbrelOS under **Files → Apps → adam-vitals → data → SETUP-CODE.txt**.

How it's protected:
- Passwords are stored as scrypt hashes, and sessions as hashed tokens.
- Login cookies are HttpOnly, SameSite and Secure over HTTPS.
- After 5 wrong attempts a visitor is locked out for 1 minute, doubling up to 1 hour. Many failures from different addresses slow everyone down.
- Failed attempts and sign-ins from new addresses show up in the timeline and as notifications.
- Pages get a strict Content-Security-Policy, and the domain can't be framed (no clickjacking).
- Home-screen widgets can only be read from inside the Umbrel, never through the tunnel.

For extra safety you can also put **Cloudflare Access** in front of the domain (free, emails you a login code).

## Permissions
To read hardware sensors, drive health and every app's activity, Vitals needs deep access to your Umbrel:

| Access | Why |
|---|---|
| `privileged: true` | CPU power counters, S.M.A.R.T. health, and USB drives plugged in later |
| `pid: host` | Activity monitor; running network tools from the Umbrel's own network |
| `/` mounted at `/host` | Filesystems, logs and leftover files for the cleaner |
| Docker socket | CPU and memory per app; removing unused images and trimming logs |

That's why it can't go in the official Umbrel App Store, but a personal community store is fine. The app stays behind your Umbrel login. The cleaner only acts on items its own scan found, never touches installed apps (stopped apps included), and asks before deleting anything that can't be undone.

Only outside connections: the speed test and public-IP lookup (Cloudflare), and app icons from Umbrel's gallery.

## Project layout
```
umbrel-app-store.yml        app store id + name
adam-vitals/                the Umbrel app package (manifest, compose, icon)
app/                        the app itself: Python backend (vitals/) + web UI (web/)
.github/workflows/build.yml builds the Docker image for amd64 + arm64
```

Icons: [Tabler Icons](https://tabler.io/icons) (MIT). Font: [Inter](https://rsms.me/inter/) (SIL OFL).
