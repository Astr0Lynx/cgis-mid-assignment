# Deploying on an Azure VM (always on, real HTTPS)

Result: `https://<label>.<region>.cloudapp.azure.com/` serves the map page and the WPS with a real certificate, and
visitors can log in to **their own** Bhoonidhi account on the page to fetch scenes. No ngrok, no laptop needed.
About 30 minutes. Cost: a 2 vCPU / 4 GB VM is roughly US$30 a month, so the student credit lasts; **stop the VM when you
are done** (step 8).

## 1. Create the VM (Azure portal)
1. Virtual machines > Create > Azure virtual machine.
2. Region: one your student subscription allows (try Central India or South India; if the size is unavailable, pick another region).
3. Image: **Ubuntu Server 24.04 LTS**. Size: **Standard_B2s** (2 vCPU, 4 GiB). Not smaller: GDAL/rasterio need memory.
4. Authentication: SSH public key, user `azureuser`; let Azure generate a key and **download the .pem file** (keep it private).
5. Inbound ports: allow **SSH (22)**, **HTTP (80)** and **HTTPS (443)**. Disk: the default 30 GB is enough.
6. Create. When it is running, open the VM > Networking and change the SSH rule's source from "Any" to **My IP address**
   (the page you are on shows it). Ports 80 and 443 must stay open to everyone: Let's Encrypt needs them.

## 2. Give it a stable name
VM > Overview > **DNS name: Not configured** > set a DNS name label, for example `bhoonidhi-wps-yourname`. The public IP
should be Static (Configuration). Your host name is then `bhoonidhi-wps-yourname.<region>.cloudapp.azure.com`.

## 3. Install Docker on the VM
```bash
ssh -i path/to/key.pem azureuser@<host name>
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker azureuser && exit          # log in again so the group applies
```

## 4. Get the code and the prepared scenes
`cache/` is git-ignored (it is derived from licensed scenes), so it is copied from your laptop, not from GitHub.
```bash
# on the VM
git clone https://github.com/Astr0Lynx/cgis-mid-assignment.git && cd cgis-mid-assignment
# on your laptop (repo folder; about 70 MB)
scp -i path/to/key.pem -r cache azureuser@<host name>:~/cgis-mid-assignment/cache
```
(If the repository is private, make it public for grading or copy the folder with `scp -r` instead of cloning.)

## 5. Start it
```bash
# on the VM, inside cgis-mid-assignment
cp deploy/.env.example deploy/.env && nano deploy/.env        # set DOMAIN=<your host name>
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
docker compose -f deploy/docker-compose.yml logs -f caddy      # wait for "certificate obtained successfully", then Ctrl+C
```

## 6. Check it
- `https://<host name>/` shows the map page with a **Log in** button in the sidebar (padlock in the browser: real certificate).
- `https://<host name>/wps?service=WPS&request=GetCapabilities` returns XML whose links say `https://<host name>/...`.
- `python scripts/owslib_check.py https://<host name>/wps` and `python scripts/validate_schema.py https://<host name>/wps` from your laptop.
- Draw a box outside the outlined scenes, press Run, log in on the page, and fetch a scene.

## 7. What is and is not safe here
- Visitors type their Bhoonidhi password into **your** server, over HTTPS, once per login. It is used only to log in to
  Bhoonidhi, never stored or logged, but the host (you) can in principle see it. Say this plainly if asked; the dialog says
  it too. Do not log in with an account you cannot afford to expose to your own server, and never put credentials in
  files on the VM (there is no `.env` with credentials: `deploy/.env` holds only the host name).
- Fetched scenes are shared with every visitor and are deleted after 6 hours (`FETCH_TTL_HOURS`) or above 3 GB.
- The app container is not reachable from the internet, only through Caddy.
- Logins are rate-limited per visitor address, and each session fetches one scene at a time, at most two at once overall.

## 8. Updating, stopping, cost
```bash
git pull && docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build    # new code
docker compose -f deploy/docker-compose.yml --env-file deploy/.env down                          # stop the service
```
Portal: VM > **Stop** (deallocate) stops the compute charge; the disk and static IP still cost a little. Delete the
resource group when the course is over. Update the README's "Try it" address afterwards.

## Troubleshooting
| Symptom | Likely cause |
|---|---|
| Browser: connection refused / timeout | ports 80/443 not open in the network security group; or the VM is stopped |
| Certificate error, Caddy logs show an ACME failure | the DNS name does not point at this VM yet, or port 80 is blocked; fix and `docker compose ... restart caddy` |
| Links in WPS replies say `http://localhost:5000` | `DOMAIN` missing in `deploy/.env`; fix it and run the `up -d` line again |
| Page shows no Log in button | `BROWSER_LOGIN` not set: you are not running this compose file |
| `scp` says permission denied | wrong key or user; the key file must not be readable by others (`chmod 600 key.pem`) |
