# Setup Guide

This guide takes you from "I have a recommendation" to "it runs on my machine". It is written for
every skill level. Where a step needs the terminal, the exact command is given, and you can copy
it as is.

You do not need the recommended hardware to start. Part 4 explains what to do with the machine
you already have, and with other kinds of hardware.

## Pick your path

| Path | Time | Good for | What you get |
|---|---|---|---|
| **A. Try it today** | 15–30 min | Anyone, before deciding anything | A private chat model on your computer. No document search, no access rules. |
| **B. Private stack** | 1–3 hours | One person or a household; anyone the Advisor put in tier 1 or 2 | Everything the Advisor picked: the Gateway, Recall (answers from your documents and history, with sources), memory, backups, outbound traffic blocked. |
| **C. Office server** | A day | Offices; tier 2 or 3; someone comfortable with Linux | Path B on a shared server, plus sign-in for each person, shared folders with access rules, monitoring and backups. |

Path A is a good first step for everyone. It shows how fast a local model is on your machine,
and you can keep it or replace it with Path B later. Nothing in Path A gets in the way of Path B.

## Part 1. Before you start (all paths)

1. **Know your plan.** Open the Advisor page and describe your needs, or run `make wizard`
   later in Path B. The result names the model, how much memory it needs, and the disk space.
2. **Turn on disk encryption.** Your conversations and documents will live on this disk.
   - Mac: System Settings → Privacy & Security → FileVault → Turn On.
   - Windows: Settings → Privacy & security → Device encryption (or BitLocker on Pro).
   - Linux: encryption is chosen at install time (LUKS). If your disk is not encrypted, store
     the project on an encrypted drive.
3. **Check free disk space.** Models take 3–40 GB each. The Advisor's "Disk" figure includes
   a year of growth.
4. **For a computer that will serve others** (Path C), stop it from sleeping and plug it into
   the router with a cable.

## Part 2. Path A: try a local model in 15 minutes

Ollama is the free, open-source program that runs the model. It only answers requests from
this computer by default (address `127.0.0.1`).

### Mac (Apple Silicon: M1 or later)

1. Download Ollama from https://ollama.com/download and drag it into Applications. Open it once;
   a llama icon appears in the menu bar.
2. Open **Terminal** (press Cmd + Space and type "Terminal") and download a model. Use the one
   the Advisor named. For a 16 GB Mac, for example:
   ```
   ollama pull qwen3:8b
   ```
3. Chat with it:
   ```
   ollama run qwen3:8b
   ```
   Type a question and press Enter. Type `/bye` to leave.

### Windows 10 or 11

1. Download and run the Windows installer from https://ollama.com/download.
2. Open **PowerShell** from the Start menu and run the same two commands as on the Mac:
   `ollama pull qwen3:8b`, then `ollama run qwen3:8b`.

If you have an NVIDIA graphics card, install the latest driver from nvidia.com first. Ollama
uses the card automatically.

### Linux

```
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:8b
ollama run qwen3:8b
```

### Is it using the graphics chip?

While a model is loaded, run `ollama ps`. The PROCESSOR column should say `100% GPU`. If it says
`CPU`, or a split like `48%/52% CPU/GPU`, the model does not fit in graphics memory and will be
slow. Pick a smaller model; see Part 4.

### What Path A does not do

Path A is a chat model and nothing else. It does not search your documents, keep a searchable
history with sources, enforce who can see what, or block the program from going online (Ollama
checks for updates). Path B adds all of that.

## Part 3. Path B: the private stack

You will install three free tools, download this project, describe your needs, and start it. The
project then downloads only the models and services your plan needs.

### Step 1. Install the tools

**Mac**

1. **Command line tools (includes git and make).** In Terminal run `xcode-select --install` and
   click Install.
2. **Python 3.10 or newer.** The Mac's built-in Python is too old. Install the latest Python 3
   from https://www.python.org/downloads/macos/.
3. **Docker Desktop** from https://www.docker.com/products/docker-desktop/. Open it once and wait
   until it says it is running.
4. **Ollama**, as in Path A. On a Mac, Ollama must run as a normal app. Docker on a Mac cannot use
   the graphics chip, so the project connects to the Ollama app instead of running its own.

**Windows**

The project's commands are written for Linux, so on Windows they run inside WSL, Windows' built-in
Linux environment.

1. Open PowerShell **as administrator** and run `wsl --install`. Restart when asked. An Ubuntu
   window opens; choose a user name and password.
2. **Docker Desktop** from https://www.docker.com/products/docker-desktop/. In Docker Desktop →
   Settings → Resources → WSL integration, switch on **Ubuntu**.
3. **NVIDIA card:** install the normal Windows driver from nvidia.com. Do not install a driver
   inside Ubuntu; WSL uses the Windows one.
4. In the **Ubuntu** window, install the rest:
   ```
   sudo apt update && sudo apt install -y git make python3-venv python3-pip openssl
   ```

From here on, run every command in the **Ubuntu** window, not PowerShell.

**Linux (Ubuntu or Debian)**

```
sudo apt update && sudo apt install -y git make python3-venv python3-pip openssl
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER        # then log out and back in
```

- **NVIDIA card:** install the driver (`sudo ubuntu-drivers install`), then the NVIDIA Container
  Toolkit by following https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html,
  and finish with `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`.
- **AMD card:** install ROCm by following AMD's guide for your card. The project uses Ollama's
  ROCm build automatically when your plan says AMD.
- **No graphics card:** nothing extra. The project leaves out the GPU settings automatically.

### Step 2. Download the project

```
git clone https://github.com/tapankrath/private-ai-stack.git
cd private-ai-stack
python3 -m venv .venv && source .venv/bin/activate
make install
```

If you open a new terminal later, run `cd private-ai-stack && source .venv/bin/activate` first.

### Step 3. Describe your needs

Choose one:

- **From the Advisor page:** click **Copy profile** at the bottom of your result, then save it
  as `profile.yaml` in the project folder. On a Mac: `pbpaste > profile.yaml`. In WSL or Linux,
  run `nano profile.yaml`, paste, and press Ctrl+O, Enter, then Ctrl+X.
- **Answer questions in the terminal:** `make wizard`.
- **Start from an example:** `cp profiles/solo-researcher-16gb.yaml profile.yaml`, then edit it.

Then run:

```
make advise
```

Read `generated/recommendation.md`. It lists what will be installed and what is left out, and
it flags anything to double-check.

### Step 4. Create passwords and download the models

```
make secrets        # random passwords and keys, stored in ./secrets (never shared)
make pull           # downloads ONLY the models in your plan; the only step that goes online
```

On a Mac, use `make pull-native` instead of `make pull`; it downloads into the Ollama app.

If you plan to search your own documents, set up the folder list now:

```
cp config/collections.example.yaml config/collections.yaml
```

Edit it to name your folders (see Step 7), and put the documents in a `documents` folder inside
the project. To use another folder, add the line `DOCUMENTS_DIR=/path/to/your/folder` to a file
named `.env.local` in the project folder. The project only reads that folder; it never changes
your files.

> **Your own settings go in `.env.local`.** `make advise` rewrites the generated settings each
> time, but never touches `.env.local`, and anything in it wins.

### Step 5. Start it

```
make up
```

The first start builds the Gateway and takes a few minutes. Then open **https://localhost:8443**
in your browser.

- The browser warns about the certificate the first time. It was made on your machine, so this
  is expected: choose **Advanced → Continue** (or **Show details → visit this website**).
- **Create your account.** The first account becomes the administrator.
- In the model menu you will see your model three ways: the plain name (just chat),
  `… + Recall` (answers from your documents and history, with sources), and `… + Web` (private
  web search, if you turned it on).

### Step 6. Prove nothing leaks

```
make egress-test
```

Every line should say `ok` or `skip` (named exceptions such as private web search). If any line
says `LEAK` or `BYPASS`, stop and fix it before adding real data.

On a Mac, the containers are cut off, but the Ollama app itself can still go online (it checks
for updates). To block that too, use an outbound firewall such as LuLu (free, open source,
objective-see.org) and deny Ollama's outgoing connections; allow them again only while you run
`make pull-native`.

### Step 7. Add your documents

Each entry in `config/collections.yaml` is a folder and its rules. `sensitivity` is one of
`public`, `personal`, `confidential` or `restricted`. Restricted folders are never searched until
you unlock them for a session.

```
make ingest
```

After that, the background worker picks up new and changed files on its own.

### Step 8. Your control page

```
make ui-token
```

This prints a sign-in token for **https://localhost:8443/gateway/ui**. There you can see and
search your history, approve or delete memory cards, export everything, and delete conversations
for good.

### Step 9. Backups

Choose an encrypted backup location (an external drive, or a second machine), then run:

```
export RESTIC_REPOSITORY=/Volumes/Backup/private-ai      # example: an external drive
export RESTIC_PASSWORD_FILE=$PWD/secrets/backup_password
openssl rand -hex 24 > secrets/backup_password
make backup
make restore-drill        # proves the backup can actually be restored
```

Keep a copy of `secrets/backup_password` somewhere safe and offline. Without it the backup cannot
be read.

### Everyday use

| To | Run |
|---|---|
| Stop everything | `make down` |
| Start again | `make up` |
| Change your needs | edit `profile.yaml`, then `make advise && make pull && make up` |
| Try a new model safely | `make eval` before and after; keep the new model only if it scores no worse |

## Part 4. When your hardware is not the recommended one

The Advisor recommends the *smallest* setup that does the whole job well. You can run on less, on
something different, or on more. Start by telling the Advisor what you actually have: on the page,
choose your machine under **Hardware**. In the terminal, set `hardware.override` in `profile.yaml`
and run `make advise`. It re-fits everything to that machine and says what changes.

### Less memory than recommended

Use these levers in order, from cheapest to most costly in quality:

1. **Shorter context.** Set `usage.context_needs: standard` (about 8,000 tokens, enough for chat
   and most questions). Memory for the conversation grows with context length.
2. **Fewer people at once.** Lower `users.peak_concurrent`. Each simultaneous conversation needs
   its own working memory.
3. **Fewer extra models.** Leave out the jobs you can live without, such as photos, coding or
   transcription. Each adds a model that stays in memory.
4. **A smaller model.** The Advisor does this on its own when nothing bigger fits, and tells you
   if the model is below the size it recommends for your tier.

What to expect from smaller machines:

| Machine | What runs well | Notes |
|---|---|---|
| 8 GB Mac or laptop | A 3–4B model; short answers | Fine for drafting and quick questions; weak at long documents |
| 16 GB Mac, or a PC with an 8–12 GB card | 7–9B models | A good personal assistant; document search works well |
| 32–64 GB Mac, or a 24 GB card | 14–30B models, including fast "mixture of experts" models | Office-quality answers |
| No graphics card | 3–8B models, slowly | Expect a few words per second. Good for a trial or overnight batch jobs, not for chat all day |

### Different kinds of hardware

| Hardware | Strengths | Watch out for |
|---|---|---|
| **Mac (Apple Silicon)** | Quiet, low power, large shared memory; very easy | Memory cannot be upgraded later, so buy enough up front. Ollama runs as an app, not in Docker |
| **PC with an NVIDIA card** | Fastest for the money; the only choice for vLLM (many users at once) | Memory is the card's memory, not the PC's RAM. A used RTX 3090 (24 GB) is the usual bargain |
| **PC with an AMD card** | Good value for memory | Linux with ROCm works best; check that your card is on AMD's ROCm support list |
| **AMD Ryzen AI Max or NVIDIA DGX Spark boxes** | Up to 128 GB shared memory in a small box; runs large models | Slower per word than a desktop card of the same price; best when model size matters more than speed |
| **A spare server with no GPU** | Already paid for | Only for small models or batch work |

### Two machines instead of one

Use a desktop or mini PC as the "brain" and reach it from your laptop or phone at home or in the
office:

1. Run Path B on the desktop.
2. In `.env.local`, add `BIND_ADDRESS=192.168.1.20`, using the desktop's own local network
   address, then run `make up`.
3. On other devices, open `https://192.168.1.20:8443`.

Never forward this port on your router to the internet. For access from outside, use a private
network tool such as WireGuard or Tailscale.

### Using what you already own first

Before buying anything, run Path A or B on the best machine you have for two weeks. Note what is
too slow or does not fit, then tell the Advisor your budget. Its "Spend more, get a stronger
model" table shows what each step up buys you.

### Simpler alternatives to this project

If you only need chat on one computer, these desktop apps are easier to install. None of them
adds the Gateway's access rules, audit log, Restricted folders or outbound blocking.

| App | Open source | Notes |
|---|---|---|
| Ollama's own app | Yes | Includes a simple chat window on Mac and Windows |
| Jan (jan.ai) | Yes | Chat app that runs models locally |
| AnythingLLM Desktop | Yes | Adds document chat on one computer |
| LM Studio | No (free) | Polished; can use Apple's MLX for speed on a Mac |

For work that is not sensitive, a hosted service can be cheaper. The Advisor's three-year cost
table compares the options honestly. Many people keep the sensitive work local and use a hosted
service for the rest.

## Part 5. Path C: office server (additions to Path B)

- **Run it on Linux.** Use a desktop or server that stays on, with full-disk encryption and an
  outbound firewall that denies by default (`sudo ufw default deny outgoing`, then allow only what
  `generated/config/policy.yaml` lists).
- **Sign-in for each person.** If your plan includes Authentik, open
  `https://<server>:9443/if/flow/initial-setup/` after `make up` to create the admin account,
  then turn on MFA. Map groups to folders in `config/collections.yaml` with `grants`.
- **Shared folders.** Give each team its own collection. Access rules are enforced inside the
  database, so a person never sees results from a folder they cannot read.
- **Monitoring.** If your plan includes it, Grafana is at `https://<server>:8443/grafana/`.
- **Larger offices (20+ people, several GPUs):** `deploy/k3s/` holds manifests for a small
  Kubernetes cluster with default-deny network rules.
- **Before go-live:** work through `docs/SECURITY_CHECKLIST.md`, and run `make egress-test` and
  `make restore-drill`.

## Part 6. Troubleshooting

| What you see | Likely cause | What to do |
|---|---|---|
| `make: command not found` | Tools not installed | Mac: `xcode-select --install`. Windows: run commands in the Ubuntu window. Linux: `sudo apt install make` |
| `Cannot connect to the Docker daemon` | Docker is not running | Open Docker Desktop and wait until it says running. Linux: `sudo systemctl start docker` |
| `make advise` says no profile | `profile.yaml` is missing | Run `make wizard`, or save the profile from the Advisor page (Step 3) |
| Answers are very slow | The model is partly on the processor | Run `ollama ps` (Mac) or `docker compose exec ollama ollama ps`. If it is not `100% GPU`, choose a smaller model or shorter context (Part 4) |
| "Out of memory", or the model stops loading | Too much for the machine | Lower `peak_concurrent` or context, or drop an extra job, then `make advise && make up` |
| The page will not open | Something else uses port 8443 | Add `HTTPS_PORT=8444` to `.env.local`, run `make up`, and open `https://localhost:8444` |
| Mac: chat shows a connection error | The Ollama app is closed | Open Ollama from Applications; it must stay running |
| A `LEAK` line in `make egress-test` | A container can reach the internet | Do not add data. Check that you have not edited the `networks:` sections of `docker-compose.yml` |
| Recall answers "I don't know" | Nothing relevant was indexed | Check the paths in `config/collections.yaml` (they start with `/documents/`), then run `make ingest` and read its summary of files added |

If you are stuck, open an issue on the project's GitHub page and include the output of
`make advise` and the failing command. Never include anything from the `secrets` folder.
